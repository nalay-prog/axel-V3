import re
import unicodedata
from typing import Any, Dict, List, Optional


DARWIN_KEYWORDS = [
    "darwin",
    "re01",
    "darwin invest",
    "offre darwin",
    "produit darwin",
    "frais darwin",
]

TOP_TERMS = ["top", "classement", "palmares", "meilleure", "meilleur"]
KPI_TERMS = [
    "td",
    "taux de distribution",
    "tof",
    "collecte",
    "capitalisation",
    "walt",
    "frais",
    "rendement",
    "vacance",
]
STRATEGY_TERMS = [
    "allocation",
    "strategie",
    "stratégie",
    "repartition",
    "répartition",
    "profil",
    "horizon",
    "patrimoine",
    "objectif",
]
RAPPORT_TERMS = ["rapport", "note client", "synthese client", "synthèse client", "compte rendu"]
FRESHNESS_TERMS = ["aujourd", "maintenant", "dernier", "derniere", "live", "temps reel", "temps réel"]

STOPWORDS = {
    "le",
    "la",
    "les",
    "de",
    "du",
    "des",
    "et",
    "ou",
    "un",
    "une",
    "sur",
    "pour",
    "avec",
    "dans",
    "que",
    "quel",
    "quelle",
    "quels",
    "quelles",
}


def _normalize(text: str) -> str:
    base = unicodedata.normalize("NFKD", text or "")
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.lower().strip()
    return re.sub(r"\s+", " ", base)


def _contains_any(text: str, terms: List[str]) -> bool:
    return any(term in text for term in terms)


def _extract_keywords(question: str, max_items: int = 6) -> List[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", _normalize(question))
    out: List[str] = []
    for token in tokens:
        if token in STOPWORDS:
            continue
        if token not in out:
            out.append(token)
        if len(out) >= max_items:
            break
    return out


def _extract_year(question: str) -> Optional[str]:
    match = re.search(r"\b(20\d{2})\b", _normalize(question))
    if match:
        return match.group(1)
    return None


def _extract_kpi_target(question: str) -> str:
    text = _normalize(question)
    mapping = [
        ("taux de distribution", "td"),
        ("td", "td"),
        ("tof", "tof"),
        ("collecte", "collecte"),
        ("capitalisation", "capitalisation"),
        ("walt", "walt"),
        ("frais", "frais"),
        ("vacance", "vacance"),
        ("rendement", "rendement"),
    ]
    for label, target in mapping:
        if label in text:
            return target
    return "none"


def detect_intent(question: str, history: Optional[List[dict]] = None) -> Dict[str, Any]:
    _ = history
    text = _normalize(question)
    is_darwin_specific = _contains_any(text, DARWIN_KEYWORDS)
    category = "INFO"

    if _contains_any(text, RAPPORT_TERMS):
        category = "RAPPORT"
    elif _contains_any(text, TOP_TERMS):
        category = "TOP"
    elif _contains_any(text, STRATEGY_TERMS):
        category = "STRATEGIE"
    elif _contains_any(text, KPI_TERMS):
        category = "KPI"
    elif is_darwin_specific:
        category = "DARWIN"

    year = _extract_year(question)
    kpi_target = _extract_kpi_target(question)
    needs_freshness = _contains_any(text, FRESHNESS_TERMS)
    clarification_questions: List[str] = []

    if category in {"TOP", "KPI"} and not year and not needs_freshness:
        clarification_questions.append("Sur quelle période veux-tu la donnée (ex: 2025, 2026, T1 2026) ?")
    if category == "TOP" and kpi_target == "none":
        clarification_questions.append("Quel critère de classement veux-tu (TD, TOF, collecte, frais) ?")

    return {
        "type": category,
        "keywords": _extract_keywords(question),
        "year": year,
        "kpi_target": kpi_target,
        "is_darwin_specific": is_darwin_specific,
        "needs_freshness": needs_freshness,
        "needs_clarification": bool(clarification_questions),
        "clarification_questions": clarification_questions[:2],
    }
