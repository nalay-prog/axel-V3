# backend/core/source_router.py
"""
Source router strict pour Darwin.

Décide: DIRECT vs RAG vs WEB vs RAG+WEB.

Deux APIs:
- route_sources(question, history, ...) -> SourceRouteDecision (router strict)
- choose_sources(question, intent, history, force_agent, neutral_pure, ...) -> Dict plan compatible orchestrators

Pourquoi 2 APIs ?
- route_sources = logique métier simple et testable
- choose_sources = adaptateur de compatibilité (évite les bugs d'import et de signature)

But:
- éviter la recherche web pour du smalltalk ("hello")
- éviter que INFO => web par défaut
- permettre à l’orchestrateur d’obtenir un plan {"sources": [...], "primary_source": ..., "reasoning": [...]} stable
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class SourceRouteDecision:
    mode: str  # "DIRECT" | "RAG" | "WEB" | "RAG+WEB"
    rag_score: int
    web_score: int
    reasons: Tuple[str, ...]
    debug: Dict[str, Any]


# --------------------------------------------------------------------------------------
# Patterns / keywords
# --------------------------------------------------------------------------------------
_SMALLTALK_PATTERNS: Sequence[re.Pattern] = [
    re.compile(r"^(hi|hello|hey|yo|salut|coucou|bonjour|bonsoir)\b", re.I),
    re.compile(r"^(merci|thx|thanks)\b", re.I),
    re.compile(r"^(ok|d'accord|parfait|super|top)\b", re.I),
    re.compile(r"^(ça va|ca va|comment vas-tu|comment ça va)\b", re.I),
]

# "Darwin / métier" => RAG
_RAG_KEYWORDS: Sequence[str] = [
    # métier CGP / immobilier / SCPI
    "scpi",
    "sci",
    "assurance vie",
    "assurance-vie",
    "pea",
    "cto",
    "immobilier",
    "nue propriete",
    "nue-propriete",
    "usufruit",
    "rendement",
    "distribution",
    "tdvm",
    "dvm",
    "capitalisation",
    "fiscalite",
    "fiscalité",
    "pinel",
    "lmnp",
    "lmp",
    "ifi",
    "ir",
    "is",
    "tmi",
    "prelevements sociaux",
    "prélèvements sociaux",
    "arbitrage",
    "allocation",
    "patrimoine",
    "recommand",
    "conseil",
    "profil",
    "horizon",
    "risque",
    "ticket",
    "minimum de souscription",
    "delai de jouissance",
    "délai de jouissance",
    "frais",
    "commission",
    "prix de part",
    "revalorisation",
    "collecte",
    "taux d'occupation",
    "taux d occupation",
    "tof",
    # marque / base interne
    "darwin",
    "base darwin",
    "documentation",
    "doc",
    "produit",
    "fiche",
]

# "Besoin d'info récente / factuelle / news" => WEB
_WEB_KEYWORDS: Sequence[str] = [
    "aujourd'hui",
    "aujourdhui",
    "actuel",
    "actuelle",
    "actuels",
    "actuelles",
    "maintenant",
    "en ce moment",
    "cette semaine",
    "ce mois-ci",
    "cette annee",
    "cette année",
    "2024",
    "2025",
    "2026",
    "dernier",
    "derniere",
    "dernière",
    "derniers",
    "dernières",
    "mise a jour",
    "mise à jour",
    "maj",
    "news",
    "actualite",
    "actualité",
    "communique",
    "communiqué",
    "communiqué de presse",
    "article",
    "source",
    "lien",
    "url",
    "rapport",
    "bulletin",
    "insee",
    "bce",
    "banque centrale",
    "taux bce",
    "euribor",
    "oat",
    "obligation",
    "inflation",
    "chomage",
    "chômage",
    "decret",
    "décret",
    "loi",
    "budget",
    "plf",
    "jo",
    "journal officiel",
    "reglementation",
    "réglementation",
    "amf",
    "acpr",
    "barometre",
    "baromètre",
]

_URL_RE = re.compile(r"https?://|www\.", re.I)
_NUM_RE = re.compile(r"\d{2,}")  # multi-digit numbers can signal "facts"; used carefully


# --------------------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------------------
def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = (
        t.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("î", "i")
        .replace("ô", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ç", "c")
    )
    t = re.sub(r"\s+", " ", t)
    return t


def _is_smalltalk(norm: str) -> bool:
    if not norm:
        return True
    if len(norm) <= 3:
        return True
    for pat in _SMALLTALK_PATTERNS:
        if pat.search(norm):
            return True
    return False


def _count_keyword_hits(norm: str, keywords: Sequence[str]) -> Tuple[int, List[str]]:
    hits: List[str] = []
    score = 0
    for kw in keywords:
        if kw in norm:
            score += 1
            if len(hits) < 8:
                hits.append(kw)
    return score, hits


# --------------------------------------------------------------------------------------
# Strict router: route_sources
# --------------------------------------------------------------------------------------
def route_sources(
    question: str,
    history: Optional[List[dict]] = None,
    *,
    min_len_for_web: int = 18,
    web_threshold: int = 2,
) -> SourceRouteDecision:
    """
    Règles:
      - SMALLTALK / trivial => DIRECT
      - RAG par défaut
      - WEB seulement si signaux récents/externes suffisants
      - RAG+WEB si les deux signaux sont forts
    """
    norm = _normalize(question)
    history = history or []
    _ = history  # gardé pour extension future

    reasons: List[str] = []
    debug: Dict[str, Any] = {"normalized": norm}

    # 1) Hard guard: smalltalk / trivial
    if _is_smalltalk(norm):
        reasons.append("smalltalk_or_trivial")
        return SourceRouteDecision(
            mode="DIRECT",
            rag_score=0,
            web_score=0,
            reasons=tuple(reasons),
            debug=debug,
        )

    # 2) Score signals
    rag_score, rag_hits = _count_keyword_hits(norm, _RAG_KEYWORDS)
    web_score, web_hits = _count_keyword_hits(norm, _WEB_KEYWORDS)

    # URL => strong web hint
    if _URL_RE.search(question or ""):
        web_score += 3
        web_hits.append("url_detected")

    # Numbers can hint "facts", but do not auto-trigger web.
    # We add only a small bump if there are explicit recency words already.
    has_numbers = bool(_NUM_RE.search(norm))
    has_recency_words = any(
        w in norm for w in ("aujourd", "actuel", "dernier", "2024", "2025", "2026", "maj", "mise a jour")
    )
    if has_numbers and has_recency_words:
        web_score += 1
        web_hits.append("numbers_with_recency")

    debug.update(
        {
            "rag_hits": rag_hits,
            "web_hits": web_hits[:8],
            "len": len(norm),
            "has_numbers": has_numbers,
            "has_recency_words": has_recency_words,
        }
    )

    # 3) Web noise guard
    allow_web = len(norm) >= min_len_for_web and web_score >= web_threshold
    debug["allow_web"] = allow_web

    if not allow_web:
        reasons.append("web_guard_blocked")
        if rag_score > 0:
            reasons.append("rag_signals")
        else:
            reasons.append("default_rag")
        return SourceRouteDecision(
            mode="RAG",
            rag_score=rag_score,
            web_score=web_score,
            reasons=tuple(reasons),
            debug=debug,
        )

    # 4) Decide mode
    if rag_score > 0 and web_score > 0:
        reasons.append("rag_and_web_signals")
        mode = "RAG+WEB"
    elif web_score > 0 and rag_score == 0:
        reasons.append("web_signals_only")
        mode = "WEB"
    else:
        reasons.append("rag_default_even_if_web_allowed")
        mode = "RAG"

    return SourceRouteDecision(
        mode=mode,
        rag_score=rag_score,
        web_score=web_score,
        reasons=tuple(reasons),
        debug=debug,
    )


# --------------------------------------------------------------------------------------
# Compatibility adapter: choose_sources
# --------------------------------------------------------------------------------------
def choose_sources(
    question: str,
    intent: Optional[Dict[str, Any]] = None,
    *,
    history: Optional[List[dict]] = None,
    force_agent: Optional[str] = None,
    neutral_pure: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Adapter compatible avec les orchestrators existants.

    Signature attendue (legacy):
      choose_sources(question=..., intent=..., force_agent=..., neutral_pure=...)

    Retour:
      {
        "sources": [...],
        "primary_source": ...,
        "reasoning": [...],
        "debug": {...}
      }

    Notes:
    - intent n'est pas requis par le routeur strict: on l'accepte pour compat.
    - neutral_pure peut influencer la préférence: si True, on évite d'ajouter des sources "métier"
      qui pourraient biaiser (ici on garde un mapping simple; l’orchestrator gère ses overrides).
    """
    history = history or []
    intent = intent or {}
    neutral_mode = bool(neutral_pure)

    # 1) Force agent (prioritaire)
    if force_agent:
        src = str(force_agent).strip().lower()
        sources = [src] if src else []
        return {
            "sources": sources,
            "primary_source": sources[0] if sources else None,
            "reasoning": [f"force_agent:{force_agent}"],
            "debug": {"force_agent": force_agent, "neutral_pure": neutral_mode, "intent_type": intent.get("type")},
        }

    # 2) Strict decision
    decision = route_sources(question=question, history=history)

    # 3) Map modes -> sources plan
    # RAG policy:
    # - default RAG uses vector; sql_kpi is useful for KPI-heavy questions.
    # - To keep behavior stable with your current orchestrator tests, we include sql_kpi + vector in RAG.
    # - You can later optimize by enabling sql_kpi only when KPI keywords are present.
    if decision.mode == "DIRECT":
        sources: List[str] = []
    elif decision.mode == "WEB":
        sources = ["web"]
    elif decision.mode == "RAG":
        sources = ["sql_kpi", "vector"]
    else:  # "RAG+WEB"
        sources = ["web", "sql_kpi", "vector"]

    # If neutral mode: keep the plan but don't "force" extra sources beyond decision
    sources = list(dict.fromkeys([s for s in sources if s]))

    reasoning = list(decision.reasons)
    reasoning.append(f"adapter:choose_sources(mode={decision.mode})")

    debug = {
        "mode": decision.mode,
        "rag_score": decision.rag_score,
        "web_score": decision.web_score,
        "reasons": decision.reasons,
        "decision_debug": decision.debug,
        "neutral_pure": neutral_mode,
        "intent_type": intent.get("type"),
    }

    return {
        "sources": sources,
        "primary_source": sources[0] if sources else None,
        "reasoning": reasoning,
        "debug": debug,
    }


# --------------------------------------------------------------------------------------
# Integration snippet (doc)
# --------------------------------------------------------------------------------------
INTEGRATION_SNIPPET = r'''
# backend/core/orchestrator_v3.py (extrait)
from backend.core.source_router import choose_sources

def orchestrate_v3(question: str, history: list, **kwargs):
    plan = choose_sources(question=question, intent=kwargs.get("intent"), history=history)
    sources = plan["sources"]
'''  # kept as documentation only