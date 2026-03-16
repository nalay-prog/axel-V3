from typing import Any, Dict, List, Optional


FORCE_SOURCE_MAP = {
    "web": ["web"],
    "online": ["web"],
    "core": ["vector"],
    "vector": ["vector"],
    "sql_kpi": ["sql_kpi"],
    "kpi": ["sql_kpi"],
}


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def choose_sources(
    question: str,
    intent: Dict[str, Any],
    force_agent: Optional[str] = None,
    neutral_pure: bool = False,
) -> Dict[str, Any]:
    _ = question
    forced = str(force_agent or "").strip().lower()
    if forced == "rapport":
        sources = ["web", "sql_kpi"]
        if not neutral_pure:
            sources.append("vector")
        return {
            "sources": _dedupe(sources),
            "primary_source": sources[0],
            "reasoning": ["forced_rapport_bundle"],
        }

    if forced in FORCE_SOURCE_MAP:
        sources = FORCE_SOURCE_MAP[forced]
        if neutral_pure and "vector" in sources:
            sources = ["web", "sql_kpi"]
        sources = _dedupe(sources)
        return {
            "sources": sources,
            "primary_source": sources[0] if sources else None,
            "reasoning": [f"force_agent:{forced}"],
        }

    intent_type = str(intent.get("type") or "INFO").upper()
    is_darwin = bool(intent.get("is_darwin_specific"))
    needs_freshness = bool(intent.get("needs_freshness"))

    sources: List[str] = []
    reasoning: List[str] = []

    if intent_type == "TOP":
        sources.append("web")
        reasoning.append("top_queries_need_market_sources")
        if is_darwin and not neutral_pure:
            sources.append("sql_kpi")
            reasoning.append("darwin_top_adds_internal_kpi")
    elif intent_type == "KPI":
        if is_darwin and not neutral_pure:
            sources.extend(["sql_kpi", "vector"])
            reasoning.append("darwin_kpi_prefers_internal_sources")
            if needs_freshness:
                sources.insert(0, "web")
                reasoning.append("freshness_adds_web")
        else:
            sources.extend(["web", "sql_kpi"])
            reasoning.append("market_kpi_prefers_web_and_sql")
    elif intent_type == "STRATEGIE":
        sources.append("web")
        reasoning.append("strategy_keeps_market_context")
        if is_darwin and not neutral_pure:
            sources.append("vector")
            reasoning.append("darwin_strategy_adds_vector")
    elif intent_type == "RAPPORT":
        if is_darwin and not neutral_pure:
            sources.extend(["vector", "sql_kpi"])
            reasoning.append("darwin_report_prefers_internal_material")
        else:
            sources.extend(["web", "sql_kpi"])
            reasoning.append("report_uses_compact_source_bundle")
    elif intent_type == "DARWIN":
        if neutral_pure:
            sources.append("web")
            reasoning.append("neutral_mode_skips_vector")
        else:
            sources.extend(["vector", "sql_kpi"])
            reasoning.append("darwin_question_prefers_internal_sources")
    else:
        if is_darwin and not neutral_pure:
            sources.append("vector")
            reasoning.append("darwin_info_prefers_vector")
        sources.append("web")
        reasoning.append("default_info_keeps_web")

    sources = _dedupe(sources)
    return {
        "sources": sources,
        "primary_source": sources[0] if sources else None,
        "reasoning": reasoning,
    }
