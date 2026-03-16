import os
import re
import unicodedata
from typing import Any, Dict, List, Set

try:
    from ..agents.claude_agent_optimized import claude_agent
except Exception:  # pragma: no cover
    try:
        from backend.agents.claude_agent_optimized import claude_agent
    except Exception:
        claude_agent = None


MAX_FACTS = 9
MAX_FACTS_TRACE = 10
MAX_LINES_COMPACT = 12
USE_CLAUDE_OPTIMIZED = str(os.getenv("SYNTHESIS_USE_CLAUDE_OPTIMIZED", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MANDATORY_RESPONSE_PREFIXES = (
    "sources:",
    "source:",
    "date:",
    "options de precision:",
    "options de précision:",
)

NOISE_PREFIXES = (
    "scoring deterministe",
    "mode audit / detail",
    "score breakdown",
    "ponderations",
    "donnees utilisees",
    "dates",
    "sources web disponibles",
    "source web disponibles",
    "agent retenu:",
    "intent:",
    "moteur:",
    "profil retenu:",
    "scores:",
    "ecart de confiance:",
    "note version:",
    "changelog",
    "release version:",
    "contribution ",
    "kpi ",
    "web ",
    "darwin ",
    "rag marche",
    "rag darwin",
    "base sql kpi",
    "complement sql kpi",
    "complement rag darwin",
    "complement rag marche",
    "simu",
    "points cles",
)

NOISE_PATTERNS = [
    re.compile(r"^\s*https?://\S+\s*$", flags=re.IGNORECASE),
    re.compile(r"^\s*-+\s*$"),
    re.compile(r"^\s*={2,}\s*$"),
]


def _clean(text: str) -> str:
    return (text or "").strip()


def _normalize_ascii(text: str) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower()
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _question_tokens(question: str) -> Set[str]:
    tokens = re.findall(r"[a-z0-9]+", _normalize_ascii(question))
    stopwords = {
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "un",
        "une",
        "et",
        "ou",
        "a",
        "au",
        "aux",
        "pour",
        "en",
        "sur",
        "avec",
        "est",
        "que",
        "qui",
        "quoi",
        "quel",
        "quelle",
        "quelles",
        "quels",
        "top",
        "donne",
        "moi",
    }
    return {tok for tok in tokens if len(tok) > 2 and tok not in stopwords}


def _line_is_noise(line: str) -> bool:
    compact = _clean(line)
    if not compact:
        return True

    lowered = _normalize_ascii(compact)
    if lowered.startswith(tuple(NOISE_PREFIXES)):
        return True

    for pattern in NOISE_PATTERNS:
        if pattern.match(compact):
            return True
    return False


def _strip_bullet_prefix(line: str) -> str:
    return re.sub(r"^\s*[-*•\d\.\)\(]+\s*", "", line).strip()


def _split_candidate_facts(material: str) -> List[str]:
    lines = material.splitlines()
    candidates: List[str] = []
    for raw in lines:
        line = _strip_bullet_prefix(raw)
        if _line_is_noise(line):
            continue
        if len(line) < 12:
            continue
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        candidates.append(line)
    return candidates


def _score_fact(question_tokens: Set[str], fact: str, index: int) -> float:
    fact_norm = _normalize_ascii(fact)
    tokens = set(re.findall(r"[a-z0-9]+", fact_norm))
    overlap = len(question_tokens & tokens)

    has_number = bool(re.search(r"\d", fact))
    has_percent = "%" in fact
    has_year_or_period = bool(re.search(r"\b(20\d{2}|t[1-4])\b", fact_norm))

    # Score déterministe orienté question + faits chiffrés.
    score = float(overlap * 3)
    if has_number:
        score += 1.5
    if has_percent:
        score += 1.5
    if has_year_or_period:
        score += 1.0

    # Pénalise les lignes trop longues et le bruit conversationnel.
    if len(fact) > 260:
        score -= 1.0
    if any(k in fact_norm for k in ("n'hesite pas", "contacte", "si tu veux")):
        score -= 1.5

    # Légère priorité aux lignes plus hautes.
    score += max(0.0, 1.0 - (index * 0.03))
    return score


def _dedupe_lines(lines: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for line in lines:
        key = _normalize_ascii(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _is_transition_or_followup_line(line: str) -> bool:
    if not line:
        return False
    compact = _clean(line)
    norm = _normalize_ascii(compact)
    if norm.startswith(MANDATORY_RESPONSE_PREFIXES):
        return True
    if compact.startswith(("🎯", "💡", "🔍", "👉", "💼")):
        return True
    if compact.endswith("?") and _clean(compact) and len(compact) >= 12:
        return True
    return False


def _extract_sources(material: str, max_items: int = 12) -> List[str]:
    urls = re.findall(r"https?://[^\s\)\]]+", material or "", flags=re.IGNORECASE)
    dedup = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        dedup.append(u.rstrip(".,;"))
        if len(dedup) >= max_items:
            break
    return dedup


def _claude_optimized_available() -> bool:
    if not USE_CLAUDE_OPTIMIZED:
        return False
    if claude_agent is None:
        return False
    checker = getattr(claude_agent, "is_available", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return True


def _used_facts_from_answer(answer: str, max_items: int = MAX_FACTS_TRACE) -> List[str]:
    lines = []
    for raw in (answer or "").splitlines():
        line = _strip_bullet_prefix(raw)
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 8:
            continue
        if _line_is_noise(line):
            continue
        lines.append(line)
    return _dedupe_lines(lines)[: max(1, max_items)]


def _build_answer(
    question: str,
    selected_facts: List[str],
    warnings: List[str],
) -> str:
    if not selected_facts:
        lines = [
            "Je n’ai pas trouvé l’information demandée dans les sources disponibles.",
            "Je peux répondre dès que tu précises la période, la SCPI ou la source à prioriser.",
        ]
        return "\n".join(lines)

    intro = f"Réponse courte: {selected_facts[0]}"
    bullet_facts = selected_facts[1:MAX_FACTS]

    lines = [intro]
    for fact in bullet_facts:
        lines.append(f"- {fact}")

    if warnings:
        lines.append(f"Note: {warnings[0]}")

    # Hard cap 6-12 lignes.
    return "\n".join(lines[:MAX_LINES_COMPACT]).strip()


def _build_details(filtered_lines: List[str], sources: List[str], mode: str) -> str:
    line_cap = 40 if mode == "full" else 22
    content = filtered_lines[:line_cap]

    parts: List[str] = []
    if content:
        parts.append("Matiere filtree")
        parts.extend([f"- {line}" for line in content])

    if sources:
        parts.append("Sources")
        parts.extend([f"- {url}" for url in sources])

    return "\n".join(parts).strip()


def synthesize_answer(question: str, material: str, mode: str = "compact") -> Dict[str, Any]:
    """
    V1 déterministe:
    - supprime les sections bruitées,
    - extrait des faits pertinents,
    - construit une réponse courte + détails "Voir plus".
    """
    question = _clean(question)
    material = material or ""
    question_tokens = _question_tokens(question)

    candidate_facts = _split_candidate_facts(material)
    candidate_facts = _dedupe_lines(candidate_facts)

    scored: List[Dict[str, Any]] = []
    for idx, fact in enumerate(candidate_facts):
        score = _score_fact(question_tokens, fact, idx)
        scored.append({"fact": fact, "score": score, "index": idx})

    scored.sort(key=lambda item: (item["score"], -item["index"]), reverse=True)
    scored_selected = [item["fact"] for item in scored if item["score"] > 0.5]
    mandatory = [fact for fact in candidate_facts if _is_transition_or_followup_line(fact)]
    sources = _extract_sources(material)

    if _claude_optimized_available():
        try:
            answer = _clean(
                claude_agent.query(
                    user_query=question,
                    context=material,
                    question_type=None,
                )
            )
            used_facts = _used_facts_from_answer(answer, max_items=MAX_FACTS_TRACE)
            if not used_facts:
                used_facts = candidate_facts[: min(MAX_FACTS_TRACE, max(1, len(candidate_facts)))]
            warnings: List[str] = []

            # Guardrail: si la matière est pauvre / bruitée, on ne laisse pas le LLM "inventer".
            # On force une réponse "pas trouvé" + demande de précision.
            if len(candidate_facts) < 2:
                warnings.append("info non trouvée dans les sources disponibles")
                answer = _build_answer(question=question, selected_facts=[], warnings=warnings)
                details = _build_details(filtered_lines=candidate_facts, sources=sources, mode=mode)
                return {
                    "answer": answer,
                    "details": details,
                    "used_facts": [],
                    "warnings": warnings,
                }

            if not answer:
                warnings.append("info non trouvée dans les sources disponibles")
                answer = _build_answer(question=question, selected_facts=candidate_facts[:MAX_FACTS], warnings=warnings)

            details = _build_details(filtered_lines=candidate_facts, sources=sources, mode=mode)
            return {
                "answer": answer,
                "details": details,
                "used_facts": used_facts[:MAX_FACTS_TRACE],
                "warnings": warnings,
            }
        except Exception:
            pass

    selected: List[str] = []
    selected_keys = set()

    if scored_selected:
        top_fact = scored_selected[0]
        top_key = _normalize_ascii(top_fact)
        selected.append(top_fact)
        selected_keys.add(top_key)

    for item in mandatory:
        key = _normalize_ascii(item)
        if key in selected_keys:
            continue
        selected.append(item)
        selected_keys.add(key)
        if len(selected) >= MAX_FACTS:
            break

    for item in scored_selected[1:]:
        if len(selected) >= MAX_FACTS:
            break
        key = _normalize_ascii(item)
        if key in selected_keys:
            continue
        selected.append(item)
        selected_keys.add(key)

    warnings: List[str] = []
    if not selected:
        warnings.append("info non trouvée dans les sources disponibles")

    # Trace de 3-10 faits max effectivement utilisés.
    used_facts = selected[:MAX_FACTS_TRACE]
    if len(used_facts) < 3 and selected:
        for item in selected[len(used_facts):]:
            used_facts.append(item)
            if len(used_facts) >= 3:
                break
    used_facts = used_facts[:MAX_FACTS_TRACE]

    answer = _build_answer(question=question, selected_facts=selected, warnings=warnings)
    details = _build_details(filtered_lines=candidate_facts, sources=sources, mode=mode)

    return {
        "answer": answer,
        "details": details,
        "used_facts": used_facts,
        "warnings": warnings,
    }
