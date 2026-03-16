from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any, Dict, List, Optional

try:
    from .intent_understanding import detect_intent as shared_detect_intent
except Exception:  # pragma: no cover
    from backend.core.intent_understanding import detect_intent as shared_detect_intent


# Tu es un architecte logiciel senior spécialisé en agents IA (LLM), RAG et systèmes de décision déterministes.

INTENT_STRATEGIC_ALLOCATION = "STRATEGIC_ALLOCATION"
INTENT_FACTUAL_KPI = "FACTUAL_KPI"
INTENT_COMPARISON = "COMPARISON"
INTENT_DARWIN_SPECIFIC = "DARWIN_SPECIFIC"
INTENT_REGULATORY = "REGULATORY"
INTENT_RAPPORT = "RAPPORT"


DARWIN_KEYWORDS = [
    "darwin",
    "re01",
    "darwin invest",
    "offre darwin",
    "produit darwin",
    "frais darwin",
]

SCPI_BRANDS = [
    "darwin",
    "re01",
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

KPI_TERMS = [
    "td",
    "taux de distribution",
    "tof",
    "walt",
    "collecte",
    "capitalisation",
    "pga",
    "rendement",
    "vacance",
]

REGULATORY_TERMS = [
    "amf",
    "mifid",
    "mifid ii",
    "dici",
    "reglement",
    "regulation",
    "directive",
    "compliance",
    "conformite",
]

STRATEGIC_TERMS = [
    "allocation",
    "strategie",
    "repartition",
    "profil",
    "horizon",
    "patrimoine",
    "recommandation",
    "plan",
]

SIMULATION_TERMS = [
    "simulation",
    "simuler",
    "simulateur",
    "cash-flow",
    "cash flow",
    "rendement net",
]

CALCULATION_TERMS = [
    "calcul",
    "combien",
    "projection",
    "mensualite",
    "mensualité",
]

SYNTHESIS_TERMS = [
    "synthese",
    "synthèse",
    "resume",
    "résume",
    "resumer",
    "en bref",
    "tldr",
    "tl;dr",
    "version courte",
]

REALTIME_TERMS = [
    "temps reel",
    "temps réel",
    "aujourd",
    "maintenant",
    "dernier",
    "derniere",
    "latest",
    "live",
]


@dataclass
class StrategicDecision:
    intent_refined: str
    needs_clarification: bool
    clarifying_questions: List[str]
    routing: Dict[str, Any]
    response_mode: str
    constraints: Dict[str, Any]
    must_include: List[str]
    business_rules_triggered: List[str]
    notes_for_synthesizer: str
    version: str = "cgp_strategic_layer/v1.0"
    confidence: float = 0.7

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize(text: str) -> str:
    base = unicodedata.normalize("NFKD", text or "")
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.lower().strip()
    return re.sub(r"\s+", " ", base)


def _has_numbers(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _contains_any(text: str, words: List[str]) -> bool:
    return any(w in text for w in words)


def _token_count(text: str) -> int:
    return len(re.findall(r"[a-z0-9]+", _normalize(text)))


def _extract_scpi_entities(text: str) -> List[str]:
    q = _normalize(text)
    found = []
    for brand in SCPI_BRANDS:
        if brand in q:
            found.append(brand)
    return found


def _has_period(text: str) -> bool:
    q = _normalize(text)
    return bool(re.search(r"\b(20\d{2}|t[1-4]|q[1-4]|trimestre)\b", q))


def _has_montant(text: str) -> bool:
    q = _normalize(text)
    if re.search(r"\b\d{2,}\s*(k|ke|keur|euros?|€)\b", q):
        return True
    if re.search(r"\b\d{4,}\b", q):
        return True
    return False


def _has_horizon(text: str) -> bool:
    q = _normalize(text)
    return bool(re.search(r"\b\d+\s*(ans?|an|mois)\b", q)) or "horizon" in q


def _has_fiscalite(text: str) -> bool:
    q = _normalize(text)
    return bool(re.search(r"\b(ir|is)\b", q)) or "fiscalite" in q or "fiscalité" in q


def _has_risk_profile(text: str) -> bool:
    q = _normalize(text)
    return _contains_any(q, ["defensif", "défensif", "equilibre", "equilibré", "offensif", "prudent", "dynamique"])


def _history_has_anchor(history: Optional[List[dict]]) -> bool:
    for msg in (history or [])[-8:]:
        if str(msg.get("role", "")).lower().strip() != "user":
            continue
        content = _normalize(str(msg.get("content", "")))
        if _contains_any(content, SCPI_BRANDS + KPI_TERMS + STRATEGIC_TERMS + REGULATORY_TERMS):
            return True
    return False


def _infer_intent(question: str, raw_intent: Optional[str]) -> str:
    if raw_intent:
        return raw_intent
    return shared_detect_intent(
        question=question,
        neutral_pure=False,
        is_external_market=False,
        allow_darwin_specific=True,
        darwin_keywords=DARWIN_KEYWORDS,
    )


def _default_routing(intent: str, prefer_live_web: bool, neutral_pure: bool) -> Dict[str, Any]:
    if neutral_pure:
        if intent == INTENT_RAPPORT:
            return {
                "agents_order": ["online", "sql_kpi", "rapport"],
                "allow_parallel_core_online": False,
                "prefer_web_first": True,
                "strict_realtime_required": False,
            }
        return {
            "agents_order": ["online", "sql_kpi"],
            "allow_parallel_core_online": False,
            "prefer_web_first": True,
            "strict_realtime_required": False,
        }

    if intent == INTENT_REGULATORY:
        return {
            "agents_order": ["online", "sql_kpi", "core"],
            "allow_parallel_core_online": True,
            "prefer_web_first": True,
            "strict_realtime_required": True,
        }
    if intent == INTENT_FACTUAL_KPI:
        return {
            "agents_order": ["online", "sql_kpi", "core"],
            "allow_parallel_core_online": True,
            "prefer_web_first": True,
            "strict_realtime_required": False,
        }
    if intent == INTENT_COMPARISON:
        return {
            "agents_order": ["online", "sql_kpi", "core"],
            "allow_parallel_core_online": True,
            "prefer_web_first": True,
            "strict_realtime_required": False,
        }
    if intent == INTENT_DARWIN_SPECIFIC:
        return {
            "agents_order": ["online", "core", "sql_kpi"],
            "allow_parallel_core_online": True,
            "prefer_web_first": True,
            "strict_realtime_required": False,
        }
    if intent == INTENT_RAPPORT:
        return {
            "agents_order": ["online", "sql_kpi", "core", "rapport"],
            "allow_parallel_core_online": True,
            "prefer_web_first": True,
            "strict_realtime_required": False,
        }
    if prefer_live_web:
        return {
            "agents_order": ["online", "sql_kpi", "core"],
            "allow_parallel_core_online": True,
            "prefer_web_first": True,
            "strict_realtime_required": False,
        }
    return {
        "agents_order": ["sql_kpi", "core", "online"],
        "allow_parallel_core_online": True,
        "prefer_web_first": False,
        "strict_realtime_required": False,
    }


def _default_must_include(intent: str) -> List[str]:
    if intent == INTENT_FACTUAL_KPI:
        return ["valeur", "periode", "source", "date"]
    if intent == INTENT_COMPARISON:
        return ["comparatif", "ecarts", "risques", "sources"]
    if intent == INTENT_DARWIN_SPECIFIC:
        return ["reponse_directe", "chiffres", "date", "sources"]
    if intent == INTENT_REGULATORY:
        return ["texte_reference", "juridiction", "date", "limites"]
    if intent == INTENT_RAPPORT:
        return ["resume_client", "points_cles", "risques", "prochaines_etapes"]
    return ["objectif", "horizon", "fiscalite", "risques", "hypotheses"]


def _default_notes(intent: str) -> str:
    if intent == INTENT_FACTUAL_KPI:
        return (
            "Structure attendue: 1 reponse directe, puis 3-5 puces. "
            "Chaque chiffre doit inclure date + source. Mentionner incertitudes."
        )
    if intent == INTENT_COMPARISON:
        return (
            "Structure attendue: comparatif synthétique, critères homogènes, "
            "points forts/faiblesses, puis conclusion prudente."
        )
    if intent == INTENT_DARWIN_SPECIFIC:
        return (
            "Structure attendue: réponse prioritaire Darwin, puis confirmation marché si disponible, "
            "et note de consolidation des données."
        )
    if intent == INTENT_REGULATORY:
        return (
            "Structure attendue: réponse factuelle avec référence réglementaire, date, portée, "
            "et avertissement si besoin de validation juridique."
        )
    if intent == INTENT_RAPPORT:
        return (
            "Structure attendue: format client-ready, clair, actionnable, non technique."
        )
    return (
        "Structure attendue: recommandation actionnable, hypothèses explicites, "
        "risques, prochaines étapes."
    )


def decide(
    question: str,
    history: Optional[List[dict]] = None,
    intent: Optional[str] = None,
    flags: Optional[Dict[str, Any]] = None,
) -> StrategicDecision:
    flags = flags or {}
    q = _normalize(question)
    history = history or []
    business_rules: List[str] = []
    clarifying_questions: List[str] = []

    intent_refined = _infer_intent(question, intent)
    prefer_live_web = bool(flags.get("prefer_live_web_default", True))
    neutral_pure = bool(flags.get("neutral_pure", False))
    darwin_context = bool(flags.get("darwin_context", _contains_any(q, DARWIN_KEYWORDS)))
    scpi_context = bool(flags.get("scpi_context", "scpi" in q or _contains_any(q, SCPI_BRANDS)))
    token_count = _token_count(question)
    has_anchor = scpi_context or darwin_context or _history_has_anchor(history)

    response_mode = "compact"
    if bool(flags.get("audit_detail", False)) or _contains_any(q, ["audit", "detail", "détail"]):
        response_mode = "audit"
        business_rules.append("R12_AUDIT_MODE")
    elif _contains_any(q, ["rapport", "client ready", "note client"]):
        response_mode = "client_ready"
        business_rules.append("R12_CLIENT_READY_MODE")

    routing = _default_routing(intent_refined, prefer_live_web=prefer_live_web, neutral_pure=neutral_pure)
    constraints = {
        "no_hallucination": True,
        "require_source_for_numbers": True,
        "real_time_required": False,
        "prefer_recent_sources": True,
    }
    must_include = _default_must_include(intent_refined)
    notes_for_synthesizer = _default_notes(intent_refined)

    # R1: question trop courte et ambiguë.
    if token_count <= 2 and not has_anchor:
        clarifying_questions.append("Tu veux analyser quelle SCPI ou quel acteur précisément ?")
        clarifying_questions.append("Tu veux la donnée sur quelle période (ex: 2025, T3 2025) ?")
        business_rules.append("R1_SHORT_AMBIGUOUS")

    entities = _extract_scpi_entities(question)
    has_year_period = _has_period(question)
    has_explicit_kpi_criterion = _contains_any(q, KPI_TERMS)
    is_kpi_like = _contains_any(q, KPI_TERMS) or intent_refined == INTENT_FACTUAL_KPI
    is_comparison_like = intent_refined == INTENT_COMPARISON or _contains_any(q, ["compare", "vs", "versus"])
    is_simulation_like = _contains_any(q, SIMULATION_TERMS)
    is_top_like = _contains_any(q, ["top", "classement", "palmares"])
    is_synthesis_like = _contains_any(q, SYNTHESIS_TERMS)
    has_numbers = _has_numbers(question)
    has_entity = scpi_context or darwin_context
    is_simple_calc = _contains_any(q, CALCULATION_TERMS) and has_numbers and not has_entity and not is_top_like

    # R2: KPI factuel sans période claire -> clarification requise (évite réponses vagues / bruit).
    if is_kpi_like and not has_year_period and not _contains_any(q, REALTIME_TERMS):
        clarifying_questions.append("Sur quelle période veux-tu la donnée (ex: 2024, 2025, T3 2025) ?")
        business_rules.append("R2_KPI_PERIOD_CLARIFICATION_REQUIRED")

    # R3: comparaison sans deux entités -> première réponse méthodique puis affiner.
    if is_comparison_like and len(entities) < 2 and not is_top_like:
        business_rules.append("R3_COMPARISON_SCOPE_TO_REFINE")

    # R4: réglementaire = web requis.
    if intent_refined == INTENT_REGULATORY:
        routing["agents_order"] = ["online", "sql_kpi", "core"]
        routing["strict_realtime_required"] = True
        constraints["real_time_required"] = True
        business_rules.append("R4_REGULATORY_WEB_REQUIRED")

    # R5: marché externe (hors Darwin) = web d'abord.
    has_external_brand = any(b in entities for b in SCPI_BRANDS if b not in {"darwin", "re01"})
    if has_external_brand and not darwin_context:
        routing["agents_order"] = ["online", "sql_kpi", "core"] if not neutral_pure else ["online", "sql_kpi"]
        routing["prefer_web_first"] = True
        business_rules.append("R5_EXTERNAL_WEB_FIRST")

    # R6: Darwin spécifique = core prioritaire après web.
    if darwin_context and intent_refined in {INTENT_DARWIN_SPECIFIC, INTENT_FACTUAL_KPI} and not neutral_pure:
        routing["agents_order"] = ["online", "core", "sql_kpi"]
        business_rules.append("R6_DARWIN_CORE_PRIORITY")

    # R7: stratégie/allocation sans infos de base -> clarification requise (au moins profil de risque).
    if intent_refined == INTENT_STRATEGIC_ALLOCATION and not is_simulation_like:
        missing = []
        if not _has_horizon(question):
            missing.append("horizon")
        if not _has_risk_profile(question):
            missing.append("profil de risque")
        if not _has_montant(question):
            missing.append("montant")
        if missing:
            if "profil de risque" in missing:
                clarifying_questions.append("Quel est ton profil de risque (prudent/équilibré/dynamique) ?")
            if "horizon" in missing:
                clarifying_questions.append("Quel horizon d'investissement veux-tu retenir (en années) ?")
            if "montant" in missing:
                clarifying_questions.append("Quel montant veux-tu allouer (en €) ?")
            business_rules.append("R7_STRATEGY_MISSING_INPUTS_CLARIFICATION_REQUIRED")

    # R8: simulation portefeuille incomplète -> prioriser une première réponse prudente.
    if is_simulation_like:
        missing = []
        if not _has_montant(question):
            missing.append("montant")
        if not _has_fiscalite(question):
            missing.append("fiscalité (IR/IS)")
        if not _has_horizon(question):
            missing.append("horizon")
        if missing:
            business_rules.append("R8_SIMULATION_MISSING_INPUTS_NON_BLOCKING")
        else:
            business_rules.append("R8_SIMULATION_READY")

    # R9: chiffres => source obligatoire.
    if is_kpi_like or is_comparison_like or is_top_like:
        constraints["require_source_for_numbers"] = True
        business_rules.append("R9_REQUIRE_SOURCE_FOR_NUMBERS")

    # R10: top/classement sans scope explicite -> clarification requise (critère + période).
    if is_top_like:
        # Si un KPI explicite est déjà fourni (ex: TOF/TD), la période est déjà couverte par R2.
        if not has_year_period and not has_explicit_kpi_criterion:
            clarifying_questions.append("Sur quelle période veux-tu le top (ex: 2024, 2025, T3 2025) ?")
        if not has_explicit_kpi_criterion:
            clarifying_questions.append("Quel critère de classement veux-tu (TD, TOF, collecte, capitalisation, frais, etc.) ?")
        business_rules.append("R10_TOP_SCOPE_CLARIFICATION_REQUIRED")

    # R11: neutral pure exclut core.
    if neutral_pure:
        routing["agents_order"] = [a for a in routing["agents_order"] if a != "core"]
        if "sql_kpi" not in routing["agents_order"]:
            routing["agents_order"].append("sql_kpi")
        if "online" not in routing["agents_order"]:
            routing["agents_order"].insert(0, "online")
        business_rules.append("R11_NEUTRAL_PURE_EXCLUDES_CORE")

    # R12: realtime explicite utilisateur.
    if _contains_any(q, REALTIME_TERMS):
        routing["strict_realtime_required"] = True
        constraints["real_time_required"] = True
        business_rules.append("R12_REALTIME_EXPLICIT")

    # R13: Retriever optionnel (web/docs/sql) seulement si besoin.
    retrieval_allowed = (
        intent_refined == INTENT_FACTUAL_KPI
        or is_top_like
        or is_synthesis_like
        or intent_refined in {INTENT_COMPARISON, INTENT_REGULATORY, INTENT_DARWIN_SPECIFIC}
    )
    if is_simple_calc:
        retrieval_allowed = False

    if not retrieval_allowed:
        routing["agents_order"] = []
        routing["allow_parallel_core_online"] = False
        routing["prefer_web_first"] = False
        routing["strict_realtime_required"] = False
        constraints["real_time_required"] = False
        business_rules.append("R13_RETRIEVER_SKIPPED")

    # Limite 1-3 questions.
    dedup_questions: List[str] = []
    seen = set()
    for item in clarifying_questions:
        key = _normalize(item)
        if key in seen:
            continue
        seen.add(key)
        dedup_questions.append(item)
    dedup_questions = dedup_questions[:3]

    confidence = 0.72
    if dedup_questions:
        confidence = 0.58
    if intent_refined == INTENT_REGULATORY:
        confidence = max(confidence, 0.82)
    if intent_refined == INTENT_DARWIN_SPECIFIC and darwin_context:
        confidence = max(confidence, 0.8)

    return StrategicDecision(
        intent_refined=intent_refined,
        needs_clarification=bool(dedup_questions),
        clarifying_questions=dedup_questions,
        routing=routing,
        response_mode=response_mode,
        constraints=constraints,
        must_include=must_include,
        business_rules_triggered=business_rules,
        notes_for_synthesizer=notes_for_synthesizer,
        confidence=confidence,
    )
