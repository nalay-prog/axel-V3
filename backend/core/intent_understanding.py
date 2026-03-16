import re
import unicodedata
from typing import List, Optional


INTENT_STRATEGIC_ALLOCATION = "STRATEGIC_ALLOCATION"
INTENT_FACTUAL_KPI = "FACTUAL_KPI"
INTENT_COMPARISON = "COMPARISON"
INTENT_DARWIN_SPECIFIC = "DARWIN_SPECIFIC"
INTENT_REGULATORY = "REGULATORY"
INTENT_RAPPORT = "RAPPORT"


DEFAULT_DARWIN_KEYWORDS = [
    "darwin",
    "re01",
    "darwin invest",
    "offre darwin",
    "produit darwin",
    "frais darwin",
]

DEFAULT_EXTERNAL_SCPI_BRANDS = [
    "iroko",
    "corum",
    "sofidy",
    "perial",
    "paref",
    "praemia",
    "remake",
    "pf grand paris",
    "immorente",
    "primovie",
    "primopierre",
    "efimmo",
    "epargne pierre",
]

DEFAULT_MARKET_QUERY_KEYWORDS = [
    "classement",
    "top",
    "palmares",
    "meilleure",
    "meilleur",
    "comparatif",
    "compare",
    "comparer",
    "vs",
    "versus",
]

DEFAULT_TIE_BREAKER = [
    INTENT_STRATEGIC_ALLOCATION,
    INTENT_FACTUAL_KPI,
    INTENT_REGULATORY,
    INTENT_DARWIN_SPECIFIC,
    INTENT_COMPARISON,
]
DEFAULT_TIE_BREAKER_NEUTRAL = [
    INTENT_STRATEGIC_ALLOCATION,
    INTENT_FACTUAL_KPI,
    INTENT_REGULATORY,
    INTENT_COMPARISON,
]


def _normalize_ascii(text: str) -> str:
    base = unicodedata.normalize("NFKD", text or "")
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.lower().strip()
    return re.sub(r"\s+", " ", base)


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(k in text for k in keywords)


def has_amount_signal(question: str) -> bool:
    q = _normalize_ascii(question or "")
    if not q:
        return False
    if re.search(r"\b\d+(?:[.,]\d+)?\s*(k|ke|keur|m|meur|€|euros?)\b", q):
        return True
    if re.search(r"\b\d{5,}\b", q):
        return True
    return False


def has_horizon_signal(question: str) -> bool:
    q = _normalize_ascii(question or "")
    if not q:
        return False
    return bool(re.search(r"\b\d+\s*(ans?|an|mois)\b", q)) or "horizon" in q


def has_objective_signal(question: str) -> bool:
    q = _normalize_ascii(question or "")
    objective_terms = [
        "objectif",
        "revenu",
        "revenus",
        "rente",
        "allocation",
        "strategie",
        "stratégie",
        "repartition",
        "patrimoine",
        "optimise",
        "optimiser",
        "cashflow",
        "cash-flow",
    ]
    return _contains_any(q, objective_terms)


def has_freshness_signal(question: str) -> bool:
    qn = _normalize_ascii(question or "")
    freshness_keywords = [
        "temps reel",
        "temps réel",
        "live",
        "en direct",
        "aujourd",
        "maintenant",
        "dernier",
        "derniere",
        "recent",
        "récente",
        "mis a jour",
        "mise a jour",
    ]
    return _contains_any(qn, freshness_keywords)


def should_force_strategic_intent(question: str) -> bool:
    q = _normalize_ascii(question or "")
    if not q:
        return False
    has_amount = has_amount_signal(question)
    has_horizon = has_horizon_signal(question)
    has_objective = has_objective_signal(question)
    if has_amount and has_horizon and has_objective:
        return True
    if has_amount and has_horizon and _contains_any(q, ["tmi", "fiscalite", "fiscalité", "ir", "is"]):
        return True
    return False


def is_external_market_query(
    question: str,
    darwin_keywords: Optional[List[str]] = None,
    external_brands: Optional[List[str]] = None,
    market_terms: Optional[List[str]] = None,
) -> bool:
    q = _normalize_ascii(question or "")
    if not q:
        return False

    brands = external_brands or DEFAULT_EXTERNAL_SCPI_BRANDS
    d_keywords = darwin_keywords or DEFAULT_DARWIN_KEYWORDS
    m_terms = market_terms or DEFAULT_MARKET_QUERY_KEYWORDS

    has_scpi_context = "scpi" in q or _contains_any(q, brands)
    if not has_scpi_context:
        return False

    has_external_brand = _contains_any(q, brands)
    has_market_signal = _contains_any(q, m_terms)
    has_darwin = _contains_any(q, d_keywords)

    if has_external_brand and not has_darwin:
        return True
    if ("top" in q or "classement" in q or "palmares" in q) and "scpi" in q and not has_darwin:
        return True
    if has_market_signal and "scpi" in q and not has_darwin:
        return True
    return False


def detect_intent(
    question: str,
    neutral_pure: bool = False,
    history: Optional[List[dict]] = None,
    is_external_market: Optional[bool] = None,
    allow_darwin_specific: bool = True,
    darwin_keywords: Optional[List[str]] = None,
    tie_breaker: Optional[List[str]] = None,
    tie_breaker_neutral: Optional[List[str]] = None,
) -> str:
    _ = history  # reserved for future scoring based on dialogue history
    q = _normalize_ascii(question or "")

    if should_force_strategic_intent(question):
        return INTENT_STRATEGIC_ALLOCATION

    external_market = (
        is_external_market
        if isinstance(is_external_market, bool)
        else is_external_market_query(question, darwin_keywords=darwin_keywords)
    )
    if external_market:
        return INTENT_FACTUAL_KPI

    rapport_keywords = [
        "rapport",
        "adequation",
        "note client",
        "document client",
        "compte rendu",
        "synthese client",
        "pdf",
    ]
    strategic_keywords = [
        "allocation",
        "strategie",
        "stratégie",
        "repartition",
        "diversification",
        "horizon",
        "profil de risque",
        "patrimoine",
        "optimiser",
        "plan d action",
        "objectif",
        "revenu",
        "revenus",
        "tmi",
        "fiscalite",
        "fiscalité",
        "ir",
        "is",
    ]
    kpi_keywords = [
        "kpi",
        "scpi",
        "encours",
        "aum",
        "collecte",
        "collecte nette",
        "performance",
        "marge",
        "conversion",
        "pipeline",
        "ca ",
        "chiffre d affaires",
        "taux",
        "rentabilite",
        "rendement",
        "concentration locative",
        "granularite geographique",
        "walt",
        "duree moyenne baux",
        "part europe hors france",
        "dependance 3 premiers locataires",
        "top 3 locataires",
        "locataire",
        "taux de distribution",
        "pga",
        "classement",
        "top",
    ]
    comparison_keywords = [
        "compare",
        "comparer",
        "vs",
        "versus",
        "difference",
        "avantage",
        "inconvenient",
        "meilleur",
        "mieux",
        "top",
        "classement",
        "palmares",
    ]
    regulatory_keywords = [
        "reglement",
        "regulation",
        "amf",
        "mifid",
        "mifid ii",
        "dici",
        "conformite",
        "compliance",
        "directive",
        "loi",
        "article l.",
    ]
    d_keywords = darwin_keywords or DEFAULT_DARWIN_KEYWORDS

    if _contains_any(q, rapport_keywords):
        return INTENT_RAPPORT

    scores = {
        INTENT_STRATEGIC_ALLOCATION: 0,
        INTENT_FACTUAL_KPI: 0,
        INTENT_COMPARISON: 0,
        INTENT_DARWIN_SPECIFIC: 0,
        INTENT_REGULATORY: 0,
    }

    if _contains_any(q, strategic_keywords):
        scores[INTENT_STRATEGIC_ALLOCATION] += 6
    if _contains_any(q, kpi_keywords):
        scores[INTENT_FACTUAL_KPI] += 5
    if _contains_any(q, comparison_keywords):
        scores[INTENT_COMPARISON] += 5
    if allow_darwin_specific and (not neutral_pure) and _contains_any(q, d_keywords):
        scores[INTENT_DARWIN_SPECIFIC] += 6
    if _contains_any(q, regulatory_keywords):
        scores[INTENT_REGULATORY] += 7

    if has_amount_signal(question):
        scores[INTENT_STRATEGIC_ALLOCATION] += 4
    if has_horizon_signal(question):
        scores[INTENT_STRATEGIC_ALLOCATION] += 3
    if has_objective_signal(question):
        scores[INTENT_STRATEGIC_ALLOCATION] += 3
    if _contains_any(q, ["tmi", "ir", "is", "fiscalite", "fiscalité"]):
        scores[INTENT_STRATEGIC_ALLOCATION] += 2

    if has_freshness_signal(q):
        scores[INTENT_REGULATORY] += 2
        scores[INTENT_FACTUAL_KPI] += 1

    if allow_darwin_specific and (not neutral_pure) and _contains_any(q, comparison_keywords) and _contains_any(q, d_keywords):
        scores[INTENT_COMPARISON] += 2
        scores[INTENT_DARWIN_SPECIFIC] += 1

    if _contains_any(q, kpi_keywords) and _contains_any(q, regulatory_keywords):
        scores[INTENT_REGULATORY] += 2

    if neutral_pure or (not allow_darwin_specific):
        scores[INTENT_DARWIN_SPECIFIC] = -999

    best_score = max(scores.values())
    if best_score <= 0:
        return INTENT_STRATEGIC_ALLOCATION

    tied = [intent for intent, score in scores.items() if score == best_score]
    tie = tie_breaker or DEFAULT_TIE_BREAKER
    tie_neutral = tie_breaker_neutral or DEFAULT_TIE_BREAKER_NEUTRAL
    active_tie_breaker = tie_neutral if neutral_pure else tie
    for candidate in active_tie_breaker:
        if candidate in tied:
            return candidate
    return INTENT_STRATEGIC_ALLOCATION
