"""
CGP Business Layer (deterministic).

Short term (v1):
- add hard business guardrails before synthesis,
- reduce generic outputs,
- force a compact CGP answer shape,
- block unverified numeric claims.

Long term (v2+):
- versioned business rule catalog,
- configurable rules (YAML/JSON),
- audit trail per rule and confidence scoring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional


RuleOutput = Dict[str, Any]

NOISE_PREFIXES = (
    "scoring deterministe",
    "mode audit / detail",
    "score breakdown",
    "ponderations",
    "donnees utilisees",
    "dates",
    "sources web disponibles",
    "source web disponibles",
    "agent retenu",
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
)

FRESHNESS_KEYWORDS = [
    "temps reel",
    "temps réel",
    "live",
    "aujourd",
    "maintenant",
    "dernier",
    "derniere",
    "mis a jour",
    "mise a jour",
]
TOP_QUERY_KEYWORDS = ["top", "classement", "palmares", "meilleure", "meilleur"]
TOP_CRITERIA_KEYWORDS = ["objectif", "horizon", "risque", "frais", "zone", "liquidite", "liquidité"]
EXPLICIT_SCORING_CRITERIA_KEYWORDS = [
    "taux de distribution",
    "td",
    "rendement",
    "capitalisation",
    "tof",
    "walt",
    "frais",
    "ticket",
    "budget",
    "secteur",
    "fiscalite",
    "fiscalité",
    "liquidite",
    "liquidité",
    "anciennete",
    "ancienneté",
]
SPECIALIZED_SCPI_DOMAINS = {"meilleures.scpi.com", "meilleurescpi.com", "france-scpi.com"}
REGULATORY_DOMAINS = {"amf-france.org", "aspim.fr"}
PRESS_DOMAINS = {"lerevenu.com", "invest-immo.fr"}
TRANSITION_AFFINER = [
    "🎯 Je peux affiner ce classement selon...",
    "💡 Pour personnaliser cette réponse...",
    "🔍 Souhaitez-vous que je précise...",
]
TRANSITION_CONSEILLER = [
    "👉 Pour vous recommander précisément...",
    "🎯 Pour adapter à votre situation...",
    "💼 Pour un conseil sur-mesure...",
]
TRANSITION_ENGAGER = [
    "Quel aspect vous intéresse particulièrement ?",
    "Sur quel critère souhaitez-vous approfondir ?",
    "Quelle information serait la plus utile pour vous ?",
]
EXTERNAL_BRANDS = [
    "iroko",
    "corum",
    "primopierre",
    "epargne pierre",
    "novapierre",
    "pfo2",
    "immorente",
    "sofidy",
    "perial",
    "paref",
    "praemia",
    "remake",
]
DARWIN_TERMS = ["darwin", "re01"]
COMPARISON_TERMS = ["compare", "comparer", "vs", "versus", "difference", "meilleur", "classement", "top"]
NUMERIC_KPI_TERMS = ["td", "tof", "walt", "taux", "rendement", "collecte", "capitalisation", "pga"]
INTENT_NEEDS_WEB = {"FACTUAL_KPI", "COMPARISON", "REGULATORY"}
GENERIC_CLAIM_PHRASES = [
    "cela pourrait",
    "on peut s'attendre",
    "on peut s attendre",
    "devrait",
    "permettra",
    "souvent",
    "en general",
    "probablement",
]
CALC_MODE_KEYWORDS = [
    "calcul",
    "projection",
    "tri",
    "irr",
    "taux interne de rendement",
    "sensibilite",
    "sensibilité",
    "decomposition",
    "décomposition",
    "cash-flow",
    "cash flow",
    "rendement net",
]
SENSITIVITY_KEYWORDS = ["sensibilite", "sensibilité", "scenario", "scénario", "stress"]


# --------- common helpers ---------
def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _normalize_ascii(text: Optional[str]) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().strip()
    raw = re.sub(r"\s+", " ", raw)
    return raw


def _contains_any(text: str, words: List[str]) -> bool:
    return any(w in text for w in words)


def _looks_like_calc_question(question: str) -> bool:
    qn = _normalize_ascii(question)
    calc_patterns = [
        r"\bcalcul(?:e|er|s)?\b",
        r"\bprojection\b",
        r"\btri\b",
        r"\birr\b",
        r"taux interne de rendement",
        r"\bsensibilite\b",
        r"\bdecomposition\b",
        r"\bcash[- ]?flow\b",
        r"\brendement net\b",
        r"\bsimulation\b",
        r"\bsimule(?:r)?\b",
    ]
    return any(re.search(pattern, qn) for pattern in calc_patterns)


def extract_numbers(text: str) -> List[str]:
    """
    Extrait les nombres textuels d'une chaîne.
    Exemple: \"7,5% en 2025\" -> [\"7,5\", \"2025\"]
    """
    return re.findall(r"\d+(?:[.,]\d+)?", text or "")


def _normalize_number_token(token: str) -> str:
    value = _clean(token).replace(" ", "").replace(",", ".")
    value = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", "", value)
    return value


def numbers_supported(numbers: List[str], material: str) -> Dict[str, bool]:
    """
    Vérifie si chaque nombre est supporté par la matière disponible.
    Retourne un mapping: {nombre: bool}
    """
    material_numbers = extract_numbers(material or "")
    material_exact = set(material_numbers)
    material_normalized = {_normalize_number_token(n) for n in material_numbers}

    support: Dict[str, bool] = {}
    for number in numbers:
        normalized = _normalize_number_token(number)
        support[str(number)] = (str(number) in material_exact) or (normalized in material_normalized)
    return support


def _extract_numbers(text: str) -> List[str]:
    return extract_numbers(text)


def _extract_year_tokens(text: str) -> List[str]:
    return re.findall(r"\b20\d{2}\b", _normalize_ascii(text))


def _dedupe(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        key = _normalize_ascii(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _has_layer_sources(sources_by_layer: Dict[str, List[Any]], layer: str) -> bool:
    items = (sources_by_layer or {}).get(layer) or []
    return isinstance(items, list) and len(items) > 0


def _has_any_sources(sources_by_layer: Dict[str, List[Any]]) -> bool:
    return any(_has_layer_sources(sources_by_layer, layer) for layer in ("sql_kpi", "rag_market", "rag_darwin"))


def _question_tokens(question: str) -> List[str]:
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
        "donne",
        "moi",
    }
    return [tok for tok in tokens if len(tok) > 2 and tok not in stopwords]


def _extract_candidate_facts(question: str, material: str, k: int = 6) -> List[str]:
    tokens = _question_tokens(question)
    scored: List[Dict[str, Any]] = []

    for idx, raw_line in enumerate((material or "").splitlines()):
        line = re.sub(r"^\s*[-*•\d\.\)\(]+\s*", "", raw_line).strip()
        if not line:
            continue

        norm = _normalize_ascii(line)
        if norm.startswith(NOISE_PREFIXES):
            continue
        if norm.startswith("http://") or norm.startswith("https://"):
            continue
        if len(line) < 12:
            continue

        overlap = sum(1 for token in tokens if token in norm)
        score = float(overlap * 3)
        if _extract_numbers(line):
            score += 1.2
        if _extract_year_tokens(line):
            score += 1.0
        score += max(0.0, 1.0 - idx * 0.03)
        scored.append({"line": line, "score": score, "idx": idx})

    scored.sort(key=lambda item: (-item["score"], item["idx"]))
    selected = [item["line"] for item in scored[: max(1, k * 2)] if item["score"] > 0.2]
    selected = _dedupe(selected)
    return selected[:k]


def _mask_numbers(text: str) -> str:
    return re.sub(r"\d+(?:[.,]\d+)?", "n/d", text or "")


def _single_line(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", _clean(text)).strip()


def _tag_unsupported_numbers(text: str, unsupported_numbers: List[str]) -> str:
    if not unsupported_numbers:
        return text
    out = text
    for number in _dedupe([str(n) for n in unsupported_numbers]):
        pattern = rf"(?<![\d.,]){re.escape(number)}(?![\d.,])"
        replacement = f"{number} (à confirmer)"
        out = re.sub(pattern, replacement, out)
    return out


def _truncate_non_empty_lines(text: str, max_lines: int) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[:max_lines]).strip()


def _fmt_money_or_nd(value: Any) -> str:
    try:
        return f"{float(value):,.2f} €".replace(",", " ")
    except Exception:
        return "n/d"


def _fmt_rate_or_nd(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "n/d"


def _sources_to_text(sources_by_layer: Dict[str, List[Any]]) -> str:
    parts: List[str] = []
    if not isinstance(sources_by_layer, dict):
        return ""
    for layer in ("sql_kpi", "rag_market", "rag_darwin"):
        for item in (sources_by_layer.get(layer) or []):
            parts.append(str(item))
    return "\n".join(parts)


def _looks_like_url(text: str) -> bool:
    return bool(re.match(r"^https?://", _clean(text), flags=re.IGNORECASE))


def _coerce_url(text: str) -> str:
    value = _clean(text)
    if not value:
        return ""
    if _looks_like_url(value):
        return value
    if re.match(r"^www\.[a-z0-9.-]+\.[a-z]{2,}([/?].*)?$", value, flags=re.IGNORECASE):
        return f"https://{value}"
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}([/?].*)?$", value, flags=re.IGNORECASE):
        return f"https://{value}"
    return ""


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    match = re.match(r"^https?://([^/\s]+)", url.strip(), flags=re.IGNORECASE)
    return (match.group(1).lower() if match else "").lstrip("www.")


def _domain_matches(domain: str, accepted: set[str]) -> bool:
    if not domain:
        return False
    return any(domain == item or domain.endswith(f".{item}") for item in accepted)


def _date_or_default(value: Any) -> str:
    txt = _clean(str(value or ""))
    if not txt or txt.lower() == "non_renseigne":
        return "non renseignée"
    return txt


def _extract_source_entries(sources_by_layer: Dict[str, List[Any]]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []

    for item in (sources_by_layer.get("rag_market") or []):
        if isinstance(item, str):
            url = _coerce_url(item)
            domain = _extract_domain(url)
            entries.append(
                {
                    "origin": "rag_market",
                    "source": domain or _clean(item) or "web",
                    "date": "non renseignée",
                    "url": url,
                    "domain": domain,
                }
            )
            continue
        if isinstance(item, dict):
            raw_url = item.get("url") or item.get("href") or item.get("source")
            url = _coerce_url(str(raw_url or ""))
            domain = _extract_domain(url)
            source = _clean(str(item.get("source") or item.get("title") or domain or "web"))
            entries.append(
                {
                    "origin": "rag_market",
                    "source": source,
                    "date": _date_or_default(item.get("date") or item.get("updated_at") or item.get("published_at")),
                    "url": url,
                    "domain": domain,
                }
            )

    for item in (sources_by_layer.get("sql_kpi") or []):
        if not isinstance(item, dict):
            continue
        raw_source = _clean(str(item.get("source") or "sql_kpi"))
        url = _coerce_url(raw_source)
        domain = _extract_domain(url)
        entries.append(
            {
                "origin": "sql_kpi",
                "source": raw_source or "sql_kpi",
                "date": _date_or_default(item.get("date")),
                "url": url,
                "domain": domain,
            }
        )

    for item in (sources_by_layer.get("rag_darwin") or []):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        raw_source = _clean(
            str(
                metadata.get("source")
                or metadata.get("title")
                or metadata.get("file_name")
                or "documents Darwin internes"
            )
        )
        raw_url = (
            metadata.get("url")
            or metadata.get("source_url")
            or raw_source
        )
        url = _coerce_url(str(raw_url or ""))
        if not url and "darwin" in _normalize_ascii(raw_source):
            url = "https://darwin.fr/documentation"
        domain = _extract_domain(url)
        entries.append(
            {
                "origin": "rag_darwin",
                "source": raw_source,
                "date": _date_or_default(
                    metadata.get("date")
                    or metadata.get("published_at")
                    or metadata.get("updated_at")
                ),
                "url": url,
                "domain": domain,
            }
        )

    dedup: List[Dict[str, str]] = []
    seen = set()
    for entry in entries:
        key = (
            _normalize_ascii(entry.get("origin")),
            _normalize_ascii(entry.get("source")),
            _normalize_ascii(entry.get("date")),
            _normalize_ascii(entry.get("url")),
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(entry)
    return dedup


def _source_priority(entry: Dict[str, str], is_darwin_query: bool) -> int:
    origin = _clean(entry.get("origin"))
    domain = _clean(entry.get("domain")).lower()

    if is_darwin_query and origin == "rag_darwin":
        return 0
    if _domain_matches(domain, SPECIALIZED_SCPI_DOMAINS):
        return 1
    if _domain_matches(domain, REGULATORY_DOMAINS):
        return 2
    if _domain_matches(domain, PRESS_DOMAINS):
        return 3
    if origin == "rag_darwin":
        return 4
    if origin == "rag_market":
        return 5
    if origin == "sql_kpi":
        return 6
    return 7


def _reference_fallback_link(question: str, intent: str) -> str:
    qn = _normalize_ascii(question)
    if intent == "REGULATORY" or _contains_any(qn, ["amf", "aspim", "reglement", "réglement"]):
        return "https://www.amf-france.org/fr"
    if _contains_any(qn, DARWIN_TERMS):
        return "https://darwin.fr/documentation"
    return "https://france-scpi.com"


def _select_prioritized_sources(
    question: str,
    intent: str,
    sources_by_layer: Dict[str, List[Any]],
    limit: int = 2,
) -> List[Dict[str, str]]:
    entries = _extract_source_entries(sources_by_layer)
    is_darwin_query = intent == "DARWIN_SPECIFIC" or _contains_any(_normalize_ascii(question), DARWIN_TERMS)

    ranked = sorted(
        entries,
        key=lambda item: (
            _source_priority(item, is_darwin_query=is_darwin_query),
            0 if item.get("url") else 1,
            _normalize_ascii(item.get("source")),
        ),
    )
    selected = ranked[: max(1, limit)]
    if selected:
        return selected

    return [
        {
            "origin": "fallback",
            "source": "référence marché",
            "date": "non renseignée",
            "url": _reference_fallback_link(question, intent),
            "domain": _extract_domain(_reference_fallback_link(question, intent)),
        }
    ]


def _format_source_block(question: str, intent: str, sources_by_layer: Dict[str, List[Any]]) -> str:
    selected = _select_prioritized_sources(question, intent, sources_by_layer, limit=2)
    parts: List[str] = []
    for item in selected:
        source = _clean(item.get("source")) or "source inconnue"
        date = _date_or_default(item.get("date"))
        url = _clean(item.get("url")) or _reference_fallback_link(question, intent)
        parts.append(f"{source} | date: {date} | lien: {url}")
    return "Sources: " + " ; ".join(parts)


def _question_has_explicit_period(question: str) -> bool:
    qn = _normalize_ascii(question)
    if re.search(r"\b(20\d{2}|t[1-4]|q[1-4]|trimestre)\b", qn):
        return True
    return _contains_any(qn, ["annee", "année", "actuel", "actuelle", "dernier", "derniere"])


def _question_has_explicit_criteria(question: str) -> bool:
    qn = _normalize_ascii(question)
    return _contains_any(qn, EXPLICIT_SCORING_CRITERIA_KEYWORDS + TOP_CRITERIA_KEYWORDS)


def _default_criteria_for_question(question: str) -> Optional[Dict[str, str]]:
    qn = _normalize_ascii(question)
    if "top" in qn and "scpi" in qn:
        return {
            "label": "Top SCPI",
            "criterion": "Taux de distribution",
            "period": "Année en cours",
        }
    if ("meilleure" in qn or "meilleur" in qn) and "scpi" in qn:
        return {
            "label": "Meilleures SCPI",
            "criterion": "Mix rendement + capitalisation",
            "period": "3 dernières années",
        }
    if "scpi" in qn and _contains_any(qn, ["sure", "sures", "securisee", "securisees", "defensive", "défensive"]):
        return {
            "label": "SCPI sûres",
            "criterion": "Capitalisation + ancienneté",
            "period": "Actuelles",
        }
    if "rendement" in qn and "scpi" in qn:
        return {
            "label": "Rendement",
            "criterion": "Taux de distribution",
            "period": "Dernières données",
        }
    return None


def _transition_pack(question: str, intent: str) -> Dict[str, Any]:
    qn = _normalize_ascii(question)
    is_top_like = _contains_any(qn, TOP_QUERY_KEYWORDS)
    is_refinement = is_top_like or intent in {"FACTUAL_KPI", "COMPARISON", "REGULATORY"}

    if is_refinement:
        opener = TRANSITION_AFFINER[0]
        options = [
            "Par profil de risque",
            "Par ticket d'entrée",
            "Par fiscalité",
        ]
        question_line = TRANSITION_ENGAGER[1]
    else:
        opener = TRANSITION_CONSEILLER[0]
        options = [
            "Selon ton horizon et objectif",
            "Selon ton budget d'investissement",
            "Selon ta fiscalité (IR/IS)",
        ]
        question_line = TRANSITION_ENGAGER[0]

    if intent == "DARWIN_SPECIFIC":
        options = [
            "Par couple rendement/risque",
            "Par niveau de liquidité",
            "Par cohérence fiscale",
        ]
        question_line = TRANSITION_ENGAGER[2]

    return {"opener": opener, "options": options[:3], "question": question_line}


def _new_rule_output() -> RuleOutput:
    return {
        "triggered": False,
        "changes": {},
        "warnings": [],
        "flags": {},
        "debug": {},
    }


# --------- pure business rules (R1..R8) ---------
def rule_r1_clarification_minimale(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R1: clarification minimale si inputs client manquants."""
    _ = material, sources_by_layer, context, state
    out = _new_rule_output()
    qn = _normalize_ascii(question)

    if intent != "STRATEGIC_ALLOCATION":
        return out

    missing: List[str] = []
    if not re.search(r"\b(horizon|\d+\s*(ans?|an|mois))\b", qn):
        missing.append("horizon")
    if not re.search(r"\b\d{2,}\s*(k|keur|euros?|€)\b|\b\d{4,}\b", qn):
        missing.append("montant")
    if not re.search(r"\b(ir|is|fiscalite|fiscalite)\b", qn):
        missing.append("fiscalite")
    if not re.search(r"\b(objectif|revenu|croissance|capital|transmission|liquidite|liquidite)\b", qn):
        missing.append("objectif")

    if not missing:
        return out

    out["triggered"] = True
    out["warnings"].append("clarification_inputs_manquants:" + ",".join(missing))
    out["flags"].update({"needs_followup_inputs": True, "needs_clarification": True})
    out["changes"].update(
        {
            "direct_answer": "Première orientation possible avec hypothèses prudentes, à confirmer avec tes paramètres.",
            "key_points_add": [
                "Hypothèses de départ: allocation progressive et diversification.",
                "Sans montant/horizon/fiscalité, la recommandation reste indicative.",
                "Je peux te donner une version chiffrée dès que les paramètres sont fixés.",
            ],
            "clarifying_questions_add": [
                "Quel montant veux-tu investir ?",
                "Quel est ton horizon (en années) ?",
                "Tu es en fiscalité IR ou IS ?",
            ],
            "actions_add": ["Donner horizon, montant, fiscalité et objectif patrimonial."],
            "risks_add": ["Sans cadrage client, la recommandation serait trop générique."],
        }
    )
    out["debug"] = {"rule": "R1", "missing_inputs": missing}
    return out


def rule_r2_anti_hallucination_chiffres_dates(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R2: bloque les chiffres non prouvés et signale dates manquantes."""
    _ = intent, material
    out = _new_rule_output()
    qn = _normalize_ascii(question)
    constraints = context.get("constraints") if isinstance(context.get("constraints"), dict) else {}
    require_source_for_numbers = bool(constraints.get("require_source_for_numbers", True))

    has_sources = _has_any_sources(sources_by_layer)
    facts_text = "\n".join(state.get("facts", []))
    fact_numbers = _extract_numbers(facts_text)
    fact_years = _extract_year_tokens(facts_text)
    asked_years = _extract_year_tokens(question)
    numeric_demand = bool(_contains_any(qn, NUMERIC_KPI_TERMS) or asked_years or _extract_numbers(question))

    warnings: List[str] = []
    flags: Dict[str, Any] = {}
    changes: Dict[str, Any] = {}

    if require_source_for_numbers and numeric_demand and not has_sources:
        warnings.append("chiffres_sans_source_verifiable")
        flags["forbid_numbers"] = True
        changes.setdefault("risks_add", []).append("Les chiffres sont à confirmer faute de source vérifiable.")

    if asked_years and not fact_years:
        warnings.append("date_non_trouvee_dans_sources")
        changes.setdefault("risks_add", []).append("La période demandée n’est pas explicitement documentée.")

    if asked_years and fact_years and not set(asked_years).intersection(set(fact_years)):
        warnings.append("periode_a_confirmer")
        changes.setdefault("risks_add", []).append("La période dans les sources ne correspond pas exactement à la demande.")

    if warnings:
        out["triggered"] = True
        out["warnings"] = warnings
        out["flags"] = flags
        out["changes"] = changes

    out["debug"] = {
        "rule": "R2",
        "asked_years": asked_years,
        "fact_years": fact_years,
        "fact_numbers_count": len(fact_numbers),
        "has_sources": has_sources,
    }
    return out


def rule_r3_structure_cgp_obligatoire(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R3: impose une structure CGP senior fixe en 6 sections."""
    out = _new_rule_output()

    facts = state.get("facts", [])
    direct = _clean(state.get("direct_answer")) or (
        facts[0] if facts else "Je pose une première estimation prudente à partir des éléments disponibles."
    )

    key_points = [x for x in state.get("key_points", []) if _clean(x)]
    if not key_points:
        key_points = facts[1:4]
    while len(key_points) < 3:
        key_points.append("Estimation à affiner selon ton profil, ton horizon et les dernières sources datées.")

    risks = [x for x in state.get("risks", []) if _clean(x)]
    risk_line = risks[0] if risks else "Vérifier les hypothèses, la fiscalité et la liquidité avant décision."

    actions = [x for x in state.get("actions", []) if _clean(x)]
    next_action = actions[0] if actions else "Comparer deux options datées avant arbitrage final."

    flags = state.get("flags", {}) if isinstance(state.get("flags"), dict) else {}
    default_cfg = flags.get("default_criteria") if isinstance(flags.get("default_criteria"), dict) else {}
    default_criterion = _clean(str(default_cfg.get("criterion") or ""))
    default_period = _clean(str(default_cfg.get("period") or ""))

    if flags.get("forbid_numbers"):
        direct = _mask_numbers(direct)
        key_points = [_mask_numbers(k) for k in key_points]
        risk_line = _mask_numbers(risk_line)

    if flags.get("requires_estimation_mode"):
        risk_line = (
            risk_line.rstrip(".")
            + ". Mode estimation actif: pas de signal web live fiable."
        )

    analysis_parts: List[str] = [direct, key_points[0]]
    if default_criterion or default_period:
        criterion = default_criterion or "non précisé"
        period = default_period or "non précisée"
        analysis_parts.append(f"Base par défaut appliquée: critère {criterion} | période {period}.")

    projection_line = ""
    for candidate in [direct] + key_points + facts:
        candidate_clean = _single_line(candidate)
        if candidate_clean and _extract_numbers(candidate_clean):
            projection_line = candidate_clean
            break
    if not projection_line:
        if flags.get("forbid_numbers"):
            projection_line = "Données chiffrées non exploitables sans source vérifiable."
        else:
            projection_line = "Projection à compléter avec montant, horizon, fiscalité et hypothèses de rendement."

    if intent == "STRATEGIC_ALLOCATION":
        arbitrage_line = (
            "Prioriser la cohérence objectif/horizon, puis arbitrer rendement visé "
            "contre liquidité et charge fiscale."
        )
    elif intent in {"FACTUAL_KPI", "COMPARISON"}:
        arbitrage_line = (
            "Arbitrer performance affichée contre frais, qualité des actifs et niveau de liquidité."
        )
    elif intent == "REGULATORY":
        arbitrage_line = "Arbitrer sécurité de conformité contre efficacité opérationnelle."
    else:
        arbitrage_line = "Arbitrer potentiel de performance contre volatilité et contraintes de liquidité."

    conclusion_line = "Décision recommandée: valider les hypothèses puis exécuter l'action retenue."

    questions = [q for q in state.get("clarifying_questions", []) if _clean(q)]
    if flags.get("needs_clarification") and questions:
        conclusion_line = "Avant arbitrage final, préciser: " + " | ".join(questions[:3])

    lines = [
        f"Analyse: {_single_line(' '.join([part for part in analysis_parts if _clean(part)]))}",
        f"Stratégie recommandée: {_single_line(next_action)}",
        f"Projection / chiffres: {projection_line}",
        f"Arbitrages: {_single_line(arbitrage_line)}",
        f"Risques: {_single_line(risk_line)}",
        f"Conclusion: {_single_line(conclusion_line)}",
    ]

    response_mode = str(context.get("response_mode", "compact") or "compact")
    line_budget = 18 if response_mode == "audit" else 16
    structured = _truncate_non_empty_lines("\n".join(lines), max_lines=line_budget)

    out["triggered"] = True
    out["changes"] = {"structured_answer": structured}
    out["debug"] = {"rule": "R3", "line_budget": line_budget, "line_count": len(structured.splitlines())}
    return out


def rule_r4_top_classement(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R4: top/classement => clarification requise si critères/période absents."""
    _ = intent, material, sources_by_layer, context, state
    out = _new_rule_output()
    qn = _normalize_ascii(question)

    if not _contains_any(qn, TOP_QUERY_KEYWORDS):
        return out

    has_criteria = _question_has_explicit_criteria(question)
    has_period = _question_has_explicit_period(question)

    out["triggered"] = True
    out["debug"] = {"rule": "R4", "has_criteria": has_criteria, "has_period": has_period}

    if has_criteria and has_period:
        out["changes"] = {
            "key_points_add": [
                "Classement basé sur rendement net, risque locatif et qualité des baux.",
                "Pondérer frais, diversification géographique et liquidité.",
                "Conclure selon horizon, fiscalité et tolérance au risque.",
            ]
        }
        return out

    out["warnings"].append("top_classement_sans_criteres")
    out["flags"].update({"needs_clarification": True})

    questions: List[str] = []
    if not has_period:
        questions.append("Sur quelle période veux-tu le top (ex: 2024, 2025, T3 2025) ?")
    if not has_criteria:
        questions.append("Quel critère de classement veux-tu (TD, TOF, collecte, capitalisation, frais, etc.) ?")

    out["changes"] = {
        "direct_answer": "Je peux faire un top, mais il me manque le critère et/ou la période pour éviter un classement trompeur.",
        "clarifying_questions_add": questions[:3],
        "actions_add": ["Donner critère + période, puis relancer le classement."],
        "risks_add": ["Un classement sans critère/période explicites peut être hors-sujet."],
    }
    return out


def rule_r5_temps_reel(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R5: demande temps réel => signal live web requis sinon estimation explicite."""
    _ = intent, material, state
    out = _new_rule_output()
    qn = _normalize_ascii(question)
    asks_live = _contains_any(qn, FRESHNESS_KEYWORDS)

    if not asks_live:
        return out

    has_web = _has_layer_sources(sources_by_layer, "rag_market")
    live_web_signal = bool(context.get("live_web_signal", has_web))

    if has_web and live_web_signal:
        out["triggered"] = True
        out["debug"] = {"rule": "R5", "mode": "live_ok"}
        return out

    out["triggered"] = True
    out["warnings"].append("pas_de_signal_live_web")
    out["flags"].update({"needs_web": True, "requires_estimation_mode": True})
    out["changes"] = {
        "actions_add": ["Relancer la recherche web live pour confirmer les dernières données."],
        "risks_add": ["La réponse bascule en mode estimation faute de signal live."],
    }
    out["debug"] = {"rule": "R5", "mode": "estimation", "has_web_sources": has_web, "live_web_signal": live_web_signal}
    return out


def rule_r6_sources_insuffisantes(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R6: sources insuffisantes => réponse prudente et explicite."""
    _ = question, material, context
    out = _new_rule_output()

    has_sources = _has_any_sources(sources_by_layer)
    facts = state.get("facts", [])

    if has_sources and len(facts) >= 1:
        return out

    out["triggered"] = True
    out["warnings"].append("source_insuffisante_reponse_prudente")
    out["flags"]["is_material_sparse"] = True
    if intent in INTENT_NEEDS_WEB:
        out["flags"]["needs_web"] = True

    out["changes"] = {
        "direct_answer": "Je propose une estimation prudente immédiate, à valider sur sources datées.",
        "key_points_add": [
            "Les éléments disponibles ne permettent pas de conclure proprement.",
            "Les chiffres ou dates doivent être confirmés par sources datées.",
            "Je peux reformuler la demande pour relancer une recherche ciblée.",
        ],
        "actions_add": ["Préciser SCPI, indicateur exact et période pour une réponse vérifiable."],
        "risks_add": ["Risque d’erreur élevé si on conclut sans source exploitable."],
    }
    out["debug"] = {"rule": "R6", "has_sources": has_sources, "fact_count": len(facts)}
    return out


def rule_r7_comparaison_methodique(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R7: comparaison => grille de critères + conclusion exploitable."""
    _ = material, context, state
    out = _new_rule_output()
    qn = _normalize_ascii(question)

    is_comparison = intent == "COMPARISON" or _contains_any(qn, COMPARISON_TERMS)
    if not is_comparison:
        return out

    out["triggered"] = True
    out["changes"] = {
        "key_points_add": [
            "Comparer rendement net, TOF/WALT et diversification géographique.",
            "Comparer frais d’entrée, frais de gestion et liquidité.",
            "Conclure selon horizon, fiscalité et profil de risque.",
        ],
        "actions_add": ["Valider deux sources datées par SCPI avant recommandation finale."],
    }

    if not _has_layer_sources(sources_by_layer, "rag_market"):
        out["warnings"].append("comparaison_sans_sources_marche")
        out["flags"]["needs_web"] = True

    out["debug"] = {"rule": "R7", "is_comparison": is_comparison}
    return out


def rule_r8_darwin_specific_provenance(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R8: qualifier provenance Darwin interne vs web, et marques externes."""
    _ = material, context, state
    out = _new_rule_output()
    qn = _normalize_ascii(question)

    has_web = _has_layer_sources(sources_by_layer, "rag_market")
    has_darwin = _has_layer_sources(sources_by_layer, "rag_darwin")

    is_darwin_query = intent == "DARWIN_SPECIFIC" or _contains_any(qn, DARWIN_TERMS)
    is_external_brand = any(brand in qn for brand in EXTERNAL_BRANDS)

    if is_darwin_query:
        out["triggered"] = True
        if has_darwin and has_web:
            out["changes"]["key_points_add"] = ["Provenance: synthèse croisée docs Darwin + web."]
        elif has_darwin and not has_web:
            out["changes"]["key_points_add"] = ["Provenance: information issue des documents Darwin internes."]
            out["changes"]["risks_add"] = ["Validation marché externe non incluse."]
        else:
            out["warnings"].append("darwin_sources_manquantes")
            out["changes"]["risks_add"] = ["Aucune source Darwin exploitable trouvée."]

    if is_external_brand and not has_web:
        out["triggered"] = True
        out["warnings"].append("source_manquante_a_confirmer")
        out["flags"]["needs_web"] = True
        out["changes"].update(
            {
                "direct_answer": "Je n’ai pas de source web fiable pour confirmer cette information externe, donc c’est à confirmer.",
                "actions_add": ["Relancer une recherche web datée sur la société mentionnée."],
                "risks_add": ["Sans source web, la donnée externe peut être inexacte."],
            }
        )

    out["debug"] = {
        "rule": "R8",
        "is_darwin_query": is_darwin_query,
        "is_external_brand": is_external_brand,
        "has_web": has_web,
        "has_darwin": has_darwin,
    }
    return out


def rule_r11_criteres_par_defaut(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R11: applique des critères/périodes par défaut si la demande est incomplète."""
    _ = intent, material, sources_by_layer, context, state
    out = _new_rule_output()

    default_cfg = _default_criteria_for_question(question)
    if not default_cfg:
        return out

    has_criteria = _question_has_explicit_criteria(question)
    has_period = _question_has_explicit_period(question)
    if has_criteria and has_period:
        return out

    applied_criterion = default_cfg.get("criterion", "") if not has_criteria else ""
    applied_period = default_cfg.get("period", "") if not has_period else ""

    summary: List[str] = []
    if applied_criterion:
        summary.append(f"critère {applied_criterion}")
    if applied_period:
        summary.append(f"période {applied_period}")

    out["triggered"] = True
    out["warnings"].append("criteres_par_defaut_appliques")
    out["flags"]["default_criteria"] = {
        "label": default_cfg.get("label"),
        "criterion": applied_criterion,
        "period": applied_period,
    }
    out["changes"] = {
        "key_points_add": [
            "Paramètres de niveau 1 appliqués: " + (", ".join(summary) if summary else "base standard."),
        ],
    }
    out["debug"] = {
        "rule": "R11",
        "question_type": default_cfg.get("label"),
        "has_criteria": has_criteria,
        "has_period": has_period,
        "applied_criterion": applied_criterion,
        "applied_period": applied_period,
    }
    return out


def rule_r9_anti_vague_claims(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R9: bloque les phrases creuses sans démonstration chiffrée."""
    _ = question, intent, sources_by_layer, context
    out = _new_rule_output()

    business_answer = _clean(str(state.get("structured_answer") or ""))
    if not business_answer:
        return out

    answer_norm = _normalize_ascii(business_answer)
    vague_hits = [phrase for phrase in GENERIC_CLAIM_PHRASES if phrase in answer_norm]
    if not vague_hits:
        return out

    evidence_blob = _normalize_ascii((material or "") + "\n" + business_answer)
    has_calc_proof = "calc_proof" in evidence_blob
    has_calcul_section = "calcul" in evidence_blob
    has_calc_block = has_calc_proof or has_calcul_section

    if has_calc_block:
        out["debug"] = {
            "rule": "R9",
            "vague_hits": vague_hits,
            "has_calc_proof": has_calc_proof,
            "has_calcul_section": has_calcul_section,
            "blocked": False,
        }
        return out

    clarification_questions = [
        "Quel montant veux-tu investir ?",
        "Quel horizon veux-tu retenir (en années) ?",
        "Quelle fiscalité et quels frais dois-je appliquer (IR/IS + TMI si IR) ?",
    ]
    structured = (
        "Analyse: Inputs partiels, je pose une estimation prudente pour avancer.\n"
        "Stratégie recommandée: Commencer avec un scénario de base puis affiner à réception des paramètres manquants.\n"
        "Projection / chiffres: Estimation initiale possible, mais la précision dépend du montant, de l'horizon et de la fiscalité.\n"
        "Arbitrages: Sans ces paramètres, l'arbitrage rendement/risque/liquidité reste indicatif.\n"
        "Risques: Aucune solution magique, les hypothèses doivent être validées avant exécution.\n"
        "Conclusion: Pour fiabiliser l'estimation, précise: "
        + " | ".join(clarification_questions[:3])
    )

    out["triggered"] = True
    out["warnings"] = ["phrase_creuse_sans_calcul"]
    out["flags"] = {
        "forbid_generic_claims": True,
        "needs_clarification": True,
    }
    out["changes"] = {
        "direct_answer": "Je ne peux pas conclure sans démonstration chiffrée.",
        "clarifying_questions_add": clarification_questions[:3],
        "structured_answer": (
            "Je ne peux pas conclure sans démonstration chiffrée.\n\n" + structured
        ).strip(),
    }
    out["debug"] = {
        "rule": "R9",
        "vague_hits": vague_hits,
        "has_calc_proof": has_calc_proof,
        "has_calcul_section": has_calcul_section,
        "blocked": True,
    }
    return out


def rule_r10_mode_analyse_financiere(
    question: str,
    intent: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    context: Dict[str, Any],
    state: Dict[str, Any],
) -> RuleOutput:
    """R10: force POSER -> CALCULER -> CONCLURE pour toute demande de calcul."""
    _ = intent, sources_by_layer
    out = _new_rule_output()

    qn = _normalize_ascii(question)
    calc_payload = context.get("cgp_calc") if isinstance(context.get("cgp_calc"), dict) else {}
    calc_results = calc_payload.get("calc_results") if isinstance(calc_payload.get("calc_results"), dict) else {}
    calc_warnings = calc_payload.get("calc_warnings") if isinstance(calc_payload.get("calc_warnings"), list) else []
    calc_mode = bool(calc_results) or _looks_like_calc_question(question)
    if not calc_mode:
        return out

    tax = calc_results.get("TAX") if isinstance(calc_results.get("TAX"), dict) else {}
    net = calc_results.get("NET") if isinstance(calc_results.get("NET"), dict) else {}
    projection = calc_results.get("PROJECTION") if isinstance(calc_results.get("PROJECTION"), dict) else {}
    allocation = calc_results.get("ALLOCATION") if isinstance(calc_results.get("ALLOCATION"), dict) else {}

    amount = projection.get("capital_initial", allocation.get("portfolio_amount"))
    horizon = projection.get("horizon_years")
    tmi_rate = tax.get("tmi_rate")
    gross_rate = net.get("gross_yield_rate")
    operating_cost_rate = net.get("operating_cost_rate")
    prudent_haircut_rate = net.get("prudent_haircut_rate")
    social_contrib_rate = net.get("social_contrib_rate", tax.get("social_contrib_rate"))
    net_prudent_rate = net.get("net_prudent_rate")

    revenue_brut = None
    if amount is not None and gross_rate is not None:
        try:
            revenue_brut = float(amount) * float(gross_rate)
        except Exception:
            revenue_brut = None

    cash_flow_net = None
    if revenue_brut is not None and net_prudent_rate is not None:
        try:
            cash_flow_net = float(revenue_brut) * float(net_prudent_rate)
        except Exception:
            cash_flow_net = None

    tri_value = None
    tri_method = "IRR approximation sur flux simplifiés (C0, Cfinal)"
    c0 = projection.get("capital_initial")
    c_final = projection.get("capital_final")
    n_years = projection.get("horizon_years")
    try:
        if c0 is not None and c_final is not None and n_years:
            c0f = float(c0)
            cff = float(c_final)
            ny = float(n_years)
            if c0f > 0 and cff > 0 and ny > 0:
                tri_value = (cff / c0f) ** (1.0 / ny) - 1.0
    except Exception:
        tri_value = None

    sensitivity_requested = _contains_any(qn, SENSITIVITY_KEYWORDS)
    sensitivity_text = "n/d"
    if sensitivity_requested and all(v is not None for v in [gross_rate, tmi_rate, social_contrib_rate, operating_cost_rate, prudent_haircut_rate]):
        try:
            g = float(gross_rate)
            t = float(tmi_rate)
            ps = float(social_contrib_rate)
            op = float(operating_cost_rate)
            hair = float(prudent_haircut_rate)
            up = (g + 0.01) * (1.0 - op) * (1.0 - (t + ps)) * (1.0 - hair)
            down = max(0.0, g - 0.01) * (1.0 - op) * (1.0 - (t + ps)) * (1.0 - hair)
            sensitivity_text = f"si rendement brut +/-1pt => net prudent {_fmt_rate_or_nd(down)} à {_fmt_rate_or_nd(up)}"
        except Exception:
            sensitivity_text = "n/d"
    elif not sensitivity_requested:
        sensitivity_text = "n/d (non demandée)"

    missing_inputs = bool(calc_payload.get("calc_has_missing_inputs")) or any(
        "missing_inputs" in _normalize_ascii(str(w))
        or "_missing_" in _normalize_ascii(str(w))
        for w in calc_warnings
    )

    if tri_value is not None:
        headline = f"Résultat chiffré: TRI estimé {_fmt_rate_or_nd(tri_value)}."
    elif projection.get("gain") is not None:
        headline = f"Résultat chiffré: gain projeté {_fmt_money_or_nd(projection.get('gain'))}."
    elif net_prudent_rate is not None:
        headline = f"Résultat chiffré: rendement net prudent {_fmt_rate_or_nd(net_prudent_rate)}."
    else:
        headline = "Résultat chiffré: n/d."

    lines: List[str] = [
        headline,
        "1) Hypothèses",
        f"- Montant investi: {_fmt_money_or_nd(amount)}",
        f"- Horizon: {str(horizon) + ' ans' if horizon is not None else 'n/d'}",
        f"- Fiscalité: {'IR (TMI ' + _fmt_rate_or_nd(tmi_rate) + ')' if tmi_rate is not None else 'n/d'}",
        f"- Rendement brut annuel: {_fmt_rate_or_nd(gross_rate)}",
        "- Taux d’occupation (TOF): n/d",
        f"- Frais (entrée/gestion/autres): gestion/opération {_fmt_rate_or_nd(operating_cost_rate)}, autres n/d",
        "- Inflation / revalorisation: n/d",
        f"- Prudent haircut: {_fmt_rate_or_nd(prudent_haircut_rate)}",
        "2) Calcul des flux",
        "- Formules: revenu_brut = montant * rendement_brut ; cash_flow_net = revenu_brut * rendement_net_prudent",
        f"- Calcul: revenu_brut={_fmt_money_or_nd(revenue_brut)} ; cash_flow_net={_fmt_money_or_nd(cash_flow_net)}",
        "3) Projection",
        "- Formule: C_final = C0 * (1 + r)^n ; Gain = C_final - C0",
        f"- Calcul: C0={_fmt_money_or_nd(projection.get('capital_initial'))} ; r={_fmt_rate_or_nd(projection.get('taux_net_annuel'))} ; n={projection.get('horizon_years', 'n/d')}",
        f"- Résultat: C_final={_fmt_money_or_nd(projection.get('capital_final'))} ; Gain={_fmt_money_or_nd(projection.get('gain'))}",
        "4) TRI estimé",
        f"- Méthode: {tri_method}",
        f"- Flux utilisés: Flux0=-{_fmt_money_or_nd(c0)} ; Flux{n_years if n_years is not None else 'n'}=+{_fmt_money_or_nd(c_final)}",
        f"- Résultat TRI: {_fmt_rate_or_nd(tri_value) if tri_value is not None else 'TRI n/d'}",
        "5) Sensibilité (si applicable)",
        f"- Résultat: {sensitivity_text}",
        "6) Conclusion stratégique basée sur les chiffres",
    ]

    if missing_inputs:
        lines.append("- Conclusion: n/d, inputs insuffisants pour conclure proprement.")
        lines.append("- Risques / limites: l’estimation reste non exploitable sans paramètres manquants.")
    else:
        lines.append(
            "- Décision: allocation progressive avec validation du couple rendement net / horizon."
        )
        lines.append(f"- Point 1: rendement net prudent {_fmt_rate_or_nd(net_prudent_rate)}.")
        lines.append(f"- Point 2: capital projeté {_fmt_money_or_nd(projection.get('capital_final'))}.")
        lines.append(f"- Point 3: TRI estimé {_fmt_rate_or_nd(tri_value) if tri_value is not None else 'n/d'}.")
        lines.append("- Risques / limites: performance sensible au rendement brut, frais et fiscalité.")

    changes: Dict[str, Any] = {
        "direct_answer": headline,
        "structured_answer": "\n".join(lines),
    }
    flags: Dict[str, Any] = {"mode_analyse_financiere": True}
    warnings: List[str] = []

    if missing_inputs:
        flags["needs_clarification"] = True
        warnings.append("analyse_financiere_inputs_manquants")
        changes["clarifying_questions_add"] = [
            "Quel montant veux-tu investir ?",
            "Quel horizon veux-tu retenir (en années) ?",
            "Quelle fiscalité/frais dois-je appliquer (IR/IS + TMI + frais) ?",
        ]

    out["triggered"] = True
    out["flags"] = flags
    out["warnings"] = warnings
    out["changes"] = changes
    out["debug"] = {
        "rule": "R10",
        "calc_mode": calc_mode,
        "missing_inputs": missing_inputs,
        "has_calc_results": bool(calc_results),
        "headline": headline,
    }
    return out


# --------- result model ---------
@dataclass
class BusinessLayerResult:
    answer: str
    details: str
    used_facts: List[str]
    warnings: List[str]
    business_checks: List[str]
    confidence: float
    version: str = "cgp_business_layer/v1.4"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CGPBusinessLayer:
    """Deterministic CGP business wrapper around pure rules."""

    VERSION = "cgp_business_layer/v1.4"

    def __init__(self, compact_line_budget: int = 16, audit_line_budget: int = 18) -> None:
        self.compact_line_budget = compact_line_budget
        self.audit_line_budget = audit_line_budget

    # === CGP_BUSINESS_LAYER_START ===
    def apply(
        self,
        question: str,
        intent: str,
        material: str,
        sources_by_layer: dict,
        profile_scoring: dict,
        context: dict | None = None,
    ) -> dict:
        """Apply deterministic business rules and build CGP answer payload."""
        _ = profile_scoring
        context = context or {}
        sources: Dict[str, List[Any]] = sources_by_layer if isinstance(sources_by_layer, dict) else {}

        facts = _extract_candidate_facts(question, material, k=6)
        state: Dict[str, Any] = {
            "facts": facts,
            "direct_answer": facts[0] if facts else "",
            "key_points": facts[1:4],
            "risks": [],
            "actions": [],
            "clarifying_questions": [],
            "flags": {
                "needs_clarification": False,
                "needs_web": False,
                "forbid_numbers": False,
                "requires_estimation_mode": False,
                "is_material_sparse": False,
                "has_verified_sources": _has_any_sources(sources),
            },
            "warnings": [],
            "structured_answer": "",
            "rules_triggered": [],
            "rule_debug": {},
        }

        rules = [
            rule_r1_clarification_minimale,
            rule_r11_criteres_par_defaut,
            rule_r4_top_classement,
            rule_r5_temps_reel,
            rule_r6_sources_insuffisantes,
            rule_r7_comparaison_methodique,
            rule_r8_darwin_specific_provenance,
            rule_r2_anti_hallucination_chiffres_dates,
            rule_r3_structure_cgp_obligatoire,
            rule_r10_mode_analyse_financiere,
            rule_r9_anti_vague_claims,
        ]

        for rule in rules:
            out = rule(
                question=question,
                intent=intent,
                material=material,
                sources_by_layer=sources,
                context=context,
                state=state,
            )
            if not isinstance(out, dict):
                continue

            rule_name = rule.__name__
            state["rule_debug"][rule_name] = out.get("debug", {})

            if out.get("triggered"):
                state["rules_triggered"].append(rule_name)

            for warning in out.get("warnings", []):
                if _clean(str(warning)):
                    state["warnings"].append(str(warning))

            for flag_name, flag_value in (out.get("flags") or {}).items():
                if isinstance(flag_value, bool):
                    state["flags"][flag_name] = bool(state["flags"].get(flag_name)) or flag_value
                else:
                    state["flags"][flag_name] = flag_value

            changes = out.get("changes") or {}
            direct = _clean(changes.get("direct_answer"))
            if direct:
                state["direct_answer"] = direct

            structured = _clean(changes.get("structured_answer"))
            if structured:
                state["structured_answer"] = structured

            for item in changes.get("key_points_add", []):
                txt = _clean(str(item))
                if txt:
                    state["key_points"].append(txt)

            for item in changes.get("risks_add", []):
                txt = _clean(str(item))
                if txt:
                    state["risks"].append(txt)

            for item in changes.get("actions_add", []):
                txt = _clean(str(item))
                if txt:
                    state["actions"].append(txt)

            for item in changes.get("clarifying_questions_add", []):
                txt = _clean(str(item))
                if txt:
                    state["clarifying_questions"].append(txt)

        business_answer = _clean(state.get("structured_answer"))
        if not business_answer:
            fallback = _clean(state.get("direct_answer")) or "Je propose une estimation prudente avec hypothèses explicites."
            business_answer = (
                f"Analyse: {_single_line(fallback)}\n"
                "Stratégie recommandée: Clarifier les objectifs, l'horizon et les contraintes avant de trancher.\n"
                "Projection / chiffres: Chiffrage à consolider avec des sources datées et vérifiables.\n"
                "Arbitrages: Comparer au moins deux options homogènes (rendement, frais, risque, liquidité).\n"
                "Risques: Aucune solution magique, toute recommandation dépend du contexte client réel.\n"
                "Conclusion: Priorité à la collecte des données manquantes puis arbitrage chiffré."
            )

        if state["flags"].get("mode_analyse_financiere"):
            line_budget = 40
        else:
            line_budget = self.audit_line_budget if str(context.get("response_mode", "compact")) == "audit" else self.compact_line_budget
        business_answer = _truncate_non_empty_lines(business_answer, max_lines=line_budget)

        # Anti-hallucination final: nombres de la réponse doivent être supportés
        # par la matière textuelle disponible (material), les sources sérialisées,
        # et les sorties de calcul déterministe quand présentes.
        calc_payload = context.get("cgp_calc") if isinstance(context.get("cgp_calc"), dict) else {}
        calc_proof = _clean(str(calc_payload.get("calc_proof_text") or ""))
        calc_results = calc_payload.get("calc_results") if isinstance(calc_payload.get("calc_results"), dict) else {}
        calc_results_blob = json.dumps(calc_results, ensure_ascii=False, sort_keys=True) if calc_results else ""
        support_material = (material or "") + "\n" + _sources_to_text(sources)
        if calc_proof:
            support_material += "\n" + calc_proof
        if calc_results_blob:
            support_material += "\n" + calc_results_blob

        heading_numbers = re.findall(r"(?m)^\s*(\d+)\)", business_answer)
        point_numbers = re.findall(r"(?mi)\bpoint\s+(\d+)\b", business_answer)
        structural_numbers = set(heading_numbers + point_numbers)

        answer_numbers = [n for n in extract_numbers(business_answer) if n not in structural_numbers]
        support_map = numbers_supported(answer_numbers, support_material)
        unsupported_numbers = [n for n, ok in support_map.items() if not ok]
        if unsupported_numbers:
            business_answer = _tag_unsupported_numbers(business_answer, unsupported_numbers)
            state["warnings"].append("chiffres_non_supportes_a_confirmer")
            state["flags"]["forbid_numbers"] = True

        business_warnings = _dedupe([str(w) for w in state.get("warnings", [])])
        business_actions = _dedupe([str(a) for a in state.get("actions", [])])[:3]
        if not business_actions:
            business_actions = ["Valider les données avec une source datée avant décision."]

        clarifying_questions = _dedupe([str(q) for q in state.get("clarifying_questions", [])])[:3]
        if clarifying_questions:
            state["flags"]["clarifying_questions"] = clarifying_questions

        confidence = 0.9
        if state["flags"].get("is_material_sparse"):
            confidence -= 0.3
        if state["flags"].get("forbid_numbers"):
            confidence -= 0.2
        if state["flags"].get("needs_clarification"):
            confidence -= 0.12
        if state["flags"].get("needs_web"):
            confidence -= 0.08
        confidence = max(0.1, min(0.98, round(confidence, 2)))

        return {
            "business_answer": business_answer,
            "business_warnings": business_warnings,
            "business_actions": business_actions,
            "business_flags": state["flags"],
            "business_debug": {
                "version": self.VERSION,
                "rules_triggered": state["rules_triggered"],
                "rule_debug": state["rule_debug"],
                "selected_facts_count": len(state.get("facts", [])),
                "source_counts": {
                    "sql_kpi": len(sources.get("sql_kpi") or []),
                    "rag_market": len(sources.get("rag_market") or []),
                    "rag_darwin": len(sources.get("rag_darwin") or []),
                },
                "numbers_support": support_map,
                "unsupported_numbers": unsupported_numbers,
                "confidence": confidence,
            },
        }

    # === CGP_BUSINESS_LAYER_END ===


def apply_business_layer(
    question: str,
    answer: str,
    details: str = "",
    used_facts: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    intent: Optional[str] = None,
    response_mode: str = "compact",
    sources_by_layer: Optional[Dict[str, List[Any]]] = None,
    constraints: Optional[Dict[str, Any]] = None,
    must_include: Optional[List[str]] = None,
    notes_for_synthesizer: str = "",
) -> BusinessLayerResult:
    """Compatibility wrapper for existing pipeline calls."""
    _ = must_include, notes_for_synthesizer
    material = _clean(answer)
    if _clean(details):
        material = material + "\n\n" + _clean(details)

    layer = CGPBusinessLayer()
    out = layer.apply(
        question=question,
        intent=str(intent or ""),
        material=material,
        sources_by_layer=sources_by_layer or {},
        profile_scoring={},
        context={
            "response_mode": response_mode,
            "constraints": constraints or {},
            "warnings": warnings or [],
            "used_facts": used_facts or [],
        },
    )

    business_answer = _clean(str(out.get("business_answer") or ""))
    business_warnings = out.get("business_warnings") if isinstance(out.get("business_warnings"), list) else []
    business_debug = out.get("business_debug") if isinstance(out.get("business_debug"), dict) else {}
    business_checks = (
        business_debug.get("rules_triggered")
        if isinstance(business_debug.get("rules_triggered"), list)
        else []
    )
    confidence = float(business_debug.get("confidence", 0.5) or 0.5)

    out_details = _clean(details) or business_answer
    out_used_facts = _dedupe([_clean(x) for x in (used_facts or []) if _clean(x)])[:10]
    if not out_used_facts and business_answer:
        first_line = business_answer.splitlines()[0].strip()
        if first_line:
            out_used_facts = [first_line]

    return BusinessLayerResult(
        answer=business_answer,
        details=out_details,
        used_facts=out_used_facts,
        warnings=_dedupe([str(w) for w in business_warnings]),
        business_checks=[str(c) for c in business_checks],
        confidence=max(0.1, min(0.98, round(confidence, 2))),
        version=str(business_debug.get("version") or CGPBusinessLayer.VERSION),
    )


__all__ = [
    "CGPBusinessLayer",
    "BusinessLayerResult",
    "apply_business_layer",
    "extract_numbers",
    "numbers_supported",
    "rule_r1_clarification_minimale",
    "rule_r2_anti_hallucination_chiffres_dates",
    "rule_r3_structure_cgp_obligatoire",
    "rule_r4_top_classement",
    "rule_r5_temps_reel",
    "rule_r6_sources_insuffisantes",
    "rule_r7_comparaison_methodique",
    "rule_r8_darwin_specific_provenance",
    "rule_r11_criteres_par_defaut",
    "rule_r9_anti_vague_claims",
    "rule_r10_mode_analyse_financiere",
]
