import re
from typing import Any, Dict, List


def _clean(text: Any) -> str:
    return str(text or "").strip()


def _format_sources(sources: List[Dict[str, Any]], max_items: int = 4) -> str:
    rows: List[str] = []
    for source in sources[:max_items]:
        domain = _clean(source.get("domain")) or _clean(source.get("source")) or "source"
        date = _clean(source.get("date"))
        if date:
            rows.append(f"- {domain} ({date})")
        else:
            rows.append(f"- {domain}")
    return "\n".join(rows)


def _fallback_answer(question: str, intent: Dict[str, Any], evidence_pack: Dict[str, Any]) -> str:
    _ = question
    items = evidence_pack.get("items") if isinstance(evidence_pack.get("items"), list) else []
    if not items:
        questions = intent.get("clarification_questions") if isinstance(intent.get("clarification_questions"), list) else []
        if questions:
            return "J'ai besoin d'une précision avant de répondre proprement:\n" + "\n".join(
                f"- {item}" for item in questions[:2]
            )
        return "Je n'ai pas trouvé assez de matière fiable pour répondre proprement. Donne-moi une période ou un critère plus précis."

    lines = ["Réponse provisoire basée sur les sources récupérées :"]
    for item in items[:3]:
        title = _clean(item.get("title"))
        snippet = _clean(item.get("snippet"))
        domain = _clean(item.get("domain"))
        if snippet:
            lines.append(f"- {title}: {snippet}")
        else:
            lines.append(f"- {title}")
        if domain:
            lines.append(f"  Source: {domain}")
    return "\n".join(lines)


def validate_output(
    answer: str,
    question: str,
    intent: Dict[str, Any],
    evidence_pack: Dict[str, Any],
) -> Dict[str, Any]:
    text = _clean(answer)
    warnings: List[str] = []

    if not text:
        text = _fallback_answer(question, intent, evidence_pack)
        warnings.append("empty_llm_response")

    sources = evidence_pack.get("sources") if isinstance(evidence_pack.get("sources"), list) else []
    if re.search(r"\d", text) and sources and "Source" not in text and "source" not in text:
        text = text.rstrip() + "\n\nSources:\n" + _format_sources(sources)
        warnings.append("source_block_appended")

    questions = intent.get("clarification_questions") if isinstance(intent.get("clarification_questions"), list) else []
    if intent.get("needs_clarification") and questions:
        lowered = text.lower()
        if "?" not in text and "précis" not in lowered and "precis" not in lowered:
            text = text.rstrip() + "\n\nPour affiner:\n" + "\n".join(f"- {item}" for item in questions[:2])
            warnings.append("clarification_appended")

    return {
        "answer": text.strip(),
        "warnings": warnings,
        "status": "partial" if warnings else "ok",
    }


def build_structured_payload(
    answer: str,
    intent: Dict[str, Any],
    evidence_pack: Dict[str, Any],
    status: str = "ok",
) -> Dict[str, Any]:
    items_payload: List[Dict[str, str]] = []
    for item in (evidence_pack.get("items") or [])[:5]:
        items_payload.append(
            {
                "name": _clean(item.get("title")) or "Source",
                "value": _clean(item.get("value")) or _clean(item.get("snippet"))[:140] or "Non trouvé",
                "metric": _clean(item.get("metric")) or _clean(intent.get("kpi_target")) or "none",
                "period": _clean(item.get("date")) or _clean(intent.get("year")) or "Non trouvé",
                "source_domain": _clean(item.get("domain")) or "n/a",
            }
        )

    sources_used = evidence_pack.get("sources") if isinstance(evidence_pack.get("sources"), list) else []
    intent_type = _clean(intent.get("type")).upper() or "INFO"
    kpi_target = _clean(intent.get("kpi_target")) or "none"

    return {
        "intent": intent_type,
        "kpi_target": kpi_target,
        "status": status,
        "items": items_payload,
        "kpi_response": items_payload[0] if items_payload else {
            "kpi": kpi_target,
            "value": "Non trouvé",
            "period": _clean(intent.get("year")) or "Non trouvé",
            "source_domain": "n/a",
        },
        "strategy_blocks": {
            "analyse": [],
            "recommandation": [],
            "risques": [],
            "questions_manquantes": list(intent.get("clarification_questions") or []),
        },
        "clarification_questions": list(intent.get("clarification_questions") or []),
        "sources_used": sources_used[:5],
        "rendered_text": _clean(answer),
    }
