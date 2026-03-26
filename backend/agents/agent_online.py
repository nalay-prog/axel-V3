# backend/agents/agent_online.py
"""
Agent online (WEB) + RAG Darwin (local) avec routing guard.

Modes:
- DIRECT: smalltalk / trivial -> aucune recherche web
- RAG: par défaut (Darwin docs via backend/agents/agent_core.py)
- WEB: seulement si signaux "actualité / données récentes / sources / liens"
- RAG+WEB: si sujet Darwin/SCPI + besoin d'actualité

Contrat de sortie:
{
  "draft": str,
  "sources": list,
  "meta": dict
}
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
import warnings
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False


try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    from ddgs import DDGS
    DDGS_PROVIDER = "ddgs"
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore
        DDGS_PROVIDER = "duckduckgo_search"
        warnings.filterwarnings(
            "ignore",
            message=r".*duckduckgo_search.*renamed to `ddgs`.*",
            category=RuntimeWarning,
        )
    except ImportError:
        DDGS = None
        DDGS_PROVIDER = ""

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None

try:
    from langchain_anthropic import ChatAnthropic
except Exception:  # pragma: no cover
    ChatAnthropic = None

# Optional: Claude optimized agent (si tu l'as)
try:
    from .claude_agent_optimized import claude_agent
except Exception:  # pragma: no cover
    try:
        from backend.agents.claude_agent_optimized import claude_agent  # type: ignore
    except Exception:
        claude_agent = None

# Question detector (si présent)
try:
    from .question_detector import detector
except Exception:  # pragma: no cover
    try:
        from backend.agents.question_detector import detector  # type: ignore
    except Exception:
        detector = None

# RAG local Darwin (ton agent_core)
try:
    from .agent_core import ask_agent as ask_agent_core
except Exception:  # pragma: no cover
    try:
        from backend.agents.agent_core import ask_agent as ask_agent_core  # type: ignore
    except Exception:
        ask_agent_core = None  # type: ignore

# Prioritized search (optionnel)
try:
    from .web_search_prioritized import web_search_prioritized
except Exception:  # pragma: no cover
    try:
        from backend.agents.web_search_prioritized import web_search_prioritized  # type: ignore
    except Exception:
        web_search_prioritized = None


# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"))

WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "auto").lower()
WEB_SEARCH_RETRY_ATTEMPTS = max(1, int(os.getenv("WEB_SEARCH_RETRY_ATTEMPTS", "2")))
WEB_FALLBACK_TO_DDGS = os.getenv("WEB_FALLBACK_TO_DDGS", "true").strip().lower() in {"1", "true", "yes", "on"}

WEB_USE_CLAUDE_OPTIMIZED = os.getenv("WEB_USE_CLAUDE_OPTIMIZED", "true").strip().lower() in {"1", "true", "yes", "on"}
WEB_USE_PRIORITIZED_SEARCH = os.getenv("WEB_USE_PRIORITIZED_SEARCH", "true").strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_PRIORITY_DOMAINS = "aspim.fr,amf-france.org,pierrepapier.fr,francescpi.com,centraledesscpi.com"
RAW_PRIORITY_DOMAINS = os.getenv("WEB_PRIORITY_DOMAINS", DEFAULT_PRIORITY_DOMAINS)

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "")

SERPAPI_AUTH_DISABLED = False
SERPER_AUTH_DISABLED = False
GOOGLE_CSE_AUTH_DISABLED = False

DDGS_AVAILABLE = DDGS is not None


def _is_placeholder_key(value: Optional[str]) -> bool:
    token = (value or "").strip().lower()
    if not token:
        return True
    placeholders = {"...", "xxx", "your_key", "your-api-key", "changeme", "replace_me"}
    if token in placeholders:
        return True
    return token.startswith("your_") or token.startswith("sk-...")


def _normalize_text_for_match(value: str) -> str:
    text = (value or "").lower().strip()
    text = text.replace("é", "e").replace("è", "e").replace("ê", "e")
    text = text.replace("à", "a").replace("â", "a").replace("î", "i")
    text = text.replace("ô", "o").replace("ù", "u").replace("û", "u")
    text = text.replace("ç", "c")
    text = re.sub(r"\s+", " ", text)
    return text


def _format_history(history: list, window: int = 8) -> str:
    if not history:
        return "Aucun historique (première question)."
    formatted = "\n".join([f"{msg['role'].upper()}: {msg['content']}" for msg in history[-window:]])
    return formatted if formatted else "Aucun historique pertinent."


# --------------------------------------------------------------------------------------
# Routing (DIRECT / RAG / WEB / RAG+WEB)
# --------------------------------------------------------------------------------------
_SMALLTALK_RE: Sequence[re.Pattern] = [
    re.compile(r"^(hi|hello|hey|yo|salut|coucou|bonjour|bonsoir)\b", re.I),
    re.compile(r"^(merci|thx|thanks)\b", re.I),
    re.compile(r"^(ok|d'accord|parfait|super|top)\b", re.I),
    re.compile(r"^(ca va|ça va|comment vas tu|comment ca va)\b", re.I),
]

_RAG_HINTS: Sequence[str] = [
    "darwin",
    "documentation",
    "doc",
    "fiche",
    "produit",
    "scpi",
    "immobilier",
    "tdvm",
    "tof",
    "walt",
    "frais",
    "prix de part",
    "delai de jouissance",
    "délai de jouissance",
    "collecte",
    "capitalisation",
    "allocation",
    "fiscalite",
    "fiscalité",
]

_WEB_SIGNALS: Sequence[str] = [
    "aujourd",
    "actuel",
    "actuelle",
    "maintenant",
    "en ce moment",
    "dernier",
    "derniere",
    "dernière",
    "mise a jour",
    "mise à jour",
    "maj",
    "actualite",
    "actualité",
    "news",
    "2025",
    "2026",
    "decret",
    "décret",
    "loi",
    "amf",
    "acpr",
    "bce",
    "euribor",
    "oat",
    "insee",
    "source",
    "lien",
    "url",
]

_URL_RE = re.compile(r"https?://|www\.", re.I)


@dataclass(frozen=True)
class RouteDecision:
    mode: str  # DIRECT|RAG|WEB|RAG+WEB
    reasons: Tuple[str, ...]
    debug: Dict[str, Any]


def _is_smalltalk_or_trivial(question: str) -> bool:
    q = _normalize_text_for_match(question or "")
    if not q or len(q) <= 3:
        return True
    return any(p.search(q) for p in _SMALLTALK_RE)


def _score_hits(q: str, keywords: Sequence[str], limit: int = 8) -> Tuple[int, List[str]]:
    hits: List[str] = []
    score = 0
    for kw in keywords:
        if kw in q:
            score += 1
            if len(hits) < limit:
                hits.append(kw)
    return score, hits


def route(question: str) -> RouteDecision:
    q = _normalize_text_for_match(question or "")
    if _is_smalltalk_or_trivial(question):
        return RouteDecision(mode="DIRECT", reasons=("smalltalk_or_trivial",), debug={"q": q})

    rag_score, rag_hits = _score_hits(q, _RAG_HINTS)
    web_score, web_hits = _score_hits(q, _WEB_SIGNALS)

    if _URL_RE.search(question or ""):
        web_score += 2
        web_hits.append("url_detected")

    # Web guard: requête trop courte -> pas de web
    allow_web = len(q) >= 18 and web_score >= 2

    if allow_web and rag_score > 0:
        mode = "RAG+WEB"
    elif allow_web:
        mode = "WEB"
    else:
        mode = "RAG"

    return RouteDecision(
        mode=mode,
        reasons=tuple(
            [
                f"rag_score={rag_score}",
                f"web_score={web_score}",
                f"allow_web={str(allow_web).lower()}",
            ]
        ),
        debug={
            "q": q,
            "rag_hits": rag_hits,
            "web_hits": web_hits[:8],
            "len": len(q),
        },
    )


# --------------------------------------------------------------------------------------
# LLM (web synthesis)
# --------------------------------------------------------------------------------------
class _ClaudeHTTPChat:
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout: float = 20.0,
        fallback_models: Optional[List[str]] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.fallback_models = [m for m in (fallback_models or []) if m]

    def invoke(self, prompt: str):
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        candidates = [self.model] + [m for m in self.fallback_models if m != self.model]

        for model_name in candidates:
            payload = {
                "model": model_name,
                "max_tokens": int(os.getenv("WEB_ANTHROPIC_MAX_TOKENS", "1800")),
                "temperature": self.temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            try:
                if httpx is not None:
                    resp = httpx.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers,
                        json=payload,
                        timeout=self.timeout,
                    )
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                else:
                    req = Request(
                        "https://api.anthropic.com/v1/messages",
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    with urlopen(req, timeout=self.timeout) as res:
                        data = json.loads(res.read().decode("utf-8", errors="replace"))

                self.model = model_name
                parts = [str(item.get("text", "")) for item in (data.get("content") or []) if isinstance(item, dict)]
                text = "\n".join([p for p in parts if p]).strip()
                return SimpleNamespace(content=text)
            except HTTPError as exc:
                if getattr(exc, "code", None) == 404:
                    continue
                raise
            except Exception:
                continue

        raise RuntimeError("Anthropic model indisponible.")


def _anthropic_model_candidates(primary_model: str) -> List[str]:
    raw = os.getenv("WEB_ANTHROPIC_MODEL_FALLBACKS", os.getenv("ANTHROPIC_MODEL_FALLBACKS", ""))
    env_candidates = [item.strip() for item in str(raw).split(",") if item.strip()]
    defaults = [
        "claude-sonnet-4-6",
        "claude-3-5-sonnet-latest",
        "claude-3-haiku-20240307",
    ]
    ordered: List[str] = []
    for item in [primary_model] + env_candidates + defaults:
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def _build_web_llm():
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    web_openai_model = os.getenv("WEB_OPENAI_MODEL", os.getenv("WEB_MODEL", "gpt-4o-mini"))
    web_anthropic_model = os.getenv("WEB_ANTHROPIC_MODEL", os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    anthro_candidates = _anthropic_model_candidates(web_anthropic_model)

    requested = str(os.getenv("WEB_LLM_PROVIDER", os.getenv("LLM_PROVIDER", ""))).strip().lower()
    if requested not in {"anthropic", "openai"}:
        requested = "anthropic" if anthropic_key and not _is_placeholder_key(anthropic_key) else "openai"

    # Try requested then fallback
    providers = [requested] + (["openai", "anthropic"] if requested == "anthropic" else ["anthropic", "openai"])

    for provider in providers:
        if provider == "anthropic" and anthropic_key and not _is_placeholder_key(anthropic_key):
            try:
                client = _ClaudeHTTPChat(
                    api_key=str(anthropic_key),
                    model=anthro_candidates[0],
                    temperature=0.0,
                    fallback_models=anthro_candidates[1:],
                )
                return client, "anthropic_http", anthro_candidates[0], requested
            except Exception:
                pass
            if ChatAnthropic is not None:
                for m in anthro_candidates:
                    try:
                        return ChatAnthropic(model=m, temperature=0, api_key=anthropic_key), "anthropic_langchain", m, requested
                    except Exception:
                        continue

        if provider == "openai" and openai_key and not _is_placeholder_key(openai_key) and ChatOpenAI is not None:
            try:
                return ChatOpenAI(model=web_openai_model, temperature=0, api_key=openai_key), "openai", web_openai_model, requested
            except Exception:
                pass

    return None, "none", "", requested


llm, WEB_LLM_PROVIDER_EFFECTIVE, WEB_MODEL, WEB_LLM_PROVIDER_REQUESTED = _build_web_llm()


def _claude_optimized_available() -> bool:
    if not WEB_USE_CLAUDE_OPTIMIZED:
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


# --------------------------------------------------------------------------------------
# Question analysis (reuse detector if available)
# --------------------------------------------------------------------------------------
def _analyze_question(question: str) -> Dict[str, Any]:
    if detector is not None:
        try:
            analysis = detector.analyze(question or "")
            return {
                "type": analysis.type,
                "confidence": float(analysis.confidence),
                "keywords_matched": list(analysis.keywords_matched),
                "numerical_values": list(analysis.numerical_values),
                "requires_context": bool(analysis.requires_context),
                "detected_labels": list(getattr(analysis, "detected_labels", [])),
            }
        except Exception:
            pass

    q = _normalize_text_for_match(question or "")
    is_calc = any(x in q for x in ["calcul", "combien", "projection", "rendement", "%", "mensualite", "mensualité"])
    is_strategy = any(x in q for x in ["allocation", "fiscalite", "fiscalité", "invest", "recommande", "que faire", "strategie", "stratégie"])
    if is_calc and is_strategy:
        q_type = "mixte_calcul_strategie"
    elif is_strategy:
        q_type = "strategie_cgp"
    elif is_calc:
        q_type = "calcul"
    else:
        q_type = "info"
    return {"type": q_type, "confidence": 0.6, "keywords_matched": [], "numerical_values": re.findall(r"\d+(?:[.,]\d+)?", question or "")}


def _format_instruction_by_type(question_type: str) -> str:
    if question_type == "calcul":
        return (
            "Format obligatoire:\n"
            "Hypothèses:\n- ...\n\n"
            "Calcul:\n- ...\n\n"
            "Résultat:\n- ...\n\n"
            "Interprétation:\n- ..."
        )
    if question_type == "strategie_cgp":
        return (
            "Format obligatoire:\n"
            "Analyse:\n- ...\n\n"
            "Stratégie recommandée:\n- ...\n\n"
            "Arbitrages:\n- ...\n\n"
            "Risques:\n- ...\n\n"
            "Conclusion:\n- ...\n\n"
            "Questions à poser (max 3):\n- ..."
        )
    if question_type == "mixte_calcul_strategie":
        return (
            "Format obligatoire:\n"
            "1) Hypothèses/Calcul/Résultat/Interprétation\n"
            "2) Puis Analyse/Stratégie recommandée/Arbitrages/Risques/Conclusion/Questions à poser"
        )
    return (
        "Format obligatoire:\n"
        "Réponse directe:\n- ...\n\n"
        "Points clés:\n- ...\n- ...\n- ...\n\n"
        "Optionnel:\n- À vérifier / nuance courte"
    )


# --------------------------------------------------------------------------------------
# DIRECT / RAG synthesis helpers
# --------------------------------------------------------------------------------------
def _direct_answer_no_web(question: str, history: Optional[list], question_type: str) -> str:
    prompt = f"""
Tu es un Conseiller en Gestion de Patrimoine (CGP) senior.
Réponds directement sans recherche web, utile et actionnable.

HISTORIQUE:
{_format_history(history or [], window=8)}

QUESTION:
{question}

RÈGLES:
- Ne refuse jamais.
- Si info incomplète: hypothèses prudentes + points à vérifier.
- Pas de liens/sources web.

{_format_instruction_by_type(question_type)}
""".strip()

    if _claude_optimized_available():
        try:
            return str(
                claude_agent.query(
                    user_query=question,
                    context="Mode DIRECT (sans web).",
                    question_type=question_type,
                )
            ).strip()
        except Exception:
            pass

    if llm is not None:
        try:
            return str(llm.invoke(prompt).content).strip()
        except Exception:
            pass

    return "Bonjour 👋\n\nDis-moi ce que tu veux faire (SCPI, allocation, fiscalité, comparaison, etc.) et je te réponds."


def _rag_fetch(question: str, history: Optional[List[dict]], k: int = 4) -> Dict[str, Any]:
    if not callable(ask_agent_core):
        return {
            "draft": "RAG Darwin indisponible (agent_core non importable).",
            "sources": [],
            "meta": {"tool": "core", "warning": "agent_core_unavailable"},
        }
    try:
        return ask_agent_core(question=question, history=history or [], k=k)
    except TypeError:
        return ask_agent_core(question, history or [], k)  # type: ignore
    except Exception as exc:
        return {
            "draft": f"RAG Darwin indisponible: {str(exc)}",
            "sources": [],
            "meta": {"tool": "core", "warning": "agent_core_error", "error": str(exc)},
        }


def _synthesize_with_llm(question: str, history: list, question_type: str, context: str, *, sources_block: str = "") -> str:
    prompt = f"""
Tu es un Conseiller en Gestion de Patrimoine (CGP) senior.
Ta mission: produire une réponse claire, structurée, actionnable.

HISTORIQUE:
{_format_history(history, window=8)}

QUESTION:
{question}

CONTEXTE:
{context}

RÈGLES:
- Factuel: n'invente pas.
- Si info incomplète: hypothèses prudentes + points à vérifier.
- Si tu cites une info issue du web: cite la source (domaine).
{sources_block}

{_format_instruction_by_type(question_type)}
""".strip()

    if _claude_optimized_available():
        try:
            return str(
                claude_agent.query(
                    user_query=question,
                    context=context,
                    question_type=question_type,
                )
            ).strip()
        except Exception:
            pass

    if llm is not None:
        return str(llm.invoke(prompt).content).strip()

    return context.strip()


# --------------------------------------------------------------------------------------
# WEB search
# --------------------------------------------------------------------------------------
def _parse_priority_domains(raw: str) -> List[str]:
    domains: List[str] = []
    for item in (raw or "").split(","):
        d = (item or "").strip().lower()
        if not d:
            continue
        d = d.replace("https://", "").replace("http://", "")
        d = d.split("/", 1)[0].lstrip("www.").strip()
        if d and d not in domains:
            domains.append(d)
    return domains


WEB_PRIORITY_DOMAINS = _parse_priority_domains(RAW_PRIORITY_DOMAINS)


def _append_result(target: List[Dict[str, str]], title: str, href: str, body: str, min_length: int) -> None:
    title = (title or "").strip()
    href = (href or "").strip()
    body = (body or "").strip() or title
    if not title or not href:
        return
    if len(body) < min_length:
        body = body[:500]
    target.append({"title": title, "href": href, "body": body[:500]})


def _dedupe_results(results: List[Dict[str, str]], max_results: int) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for r in results:
        href = r.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(r)
        if len(out) >= max_results:
            break
    return out


def _search_google_serper(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    global SERPER_AUTH_DISABLED
    if SERPER_AUTH_DISABLED or httpx is None or _is_placeholder_key(SERPER_API_KEY):
        return []
    try:
        resp = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": max_results * 2, "hl": "fr", "gl": "fr"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            SERPER_AUTH_DISABLED = True
            return []
        return []
    results: List[Dict[str, str]] = []
    for item in data.get("organic", []):
        _append_result(results, item.get("title", ""), item.get("link", ""), item.get("snippet", ""), min_length)
    return _dedupe_results(results, max_results)


def _search_google_serpapi(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    global SERPAPI_AUTH_DISABLED
    if SERPAPI_AUTH_DISABLED or httpx is None or _is_placeholder_key(SERPAPI_API_KEY):
        return []
    try:
        resp = httpx.get(
            "https://serpapi.com/search.json",
            params={"engine": "google", "q": query, "api_key": SERPAPI_API_KEY, "num": max_results * 2, "hl": "fr", "gl": "fr"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            SERPAPI_AUTH_DISABLED = True
            return []
        return []
    results: List[Dict[str, str]] = []
    for item in data.get("organic_results", []):
        _append_result(results, item.get("title", ""), item.get("link", ""), item.get("snippet", ""), min_length)
    return _dedupe_results(results, max_results)


def _search_google_cse(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    global GOOGLE_CSE_AUTH_DISABLED
    if GOOGLE_CSE_AUTH_DISABLED or httpx is None or _is_placeholder_key(GOOGLE_CSE_API_KEY) or _is_placeholder_key(GOOGLE_CSE_CX):
        return []
    try:
        resp = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_CSE_API_KEY, "cx": GOOGLE_CSE_CX, "q": query, "num": min(max_results, 10), "hl": "fr", "gl": "fr", "safe": "off"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            GOOGLE_CSE_AUTH_DISABLED = True
            return []
        return []
    results: List[Dict[str, str]] = []
    for item in data.get("items", []):
        _append_result(results, item.get("title", ""), item.get("link", ""), item.get("snippet", ""), min_length)
    return _dedupe_results(results, max_results)


def _search_ddgs(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    if not DDGS_AVAILABLE:
        return []
    results: List[Dict[str, str]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 3):
                _append_result(results, r.get("title", ""), r.get("href", ""), r.get("body", ""), min_length)
                if len(results) >= max_results:
                    break
    return _dedupe_results(results, max_results)


def _clean_html_fragment(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_duckduckgo_href(href: str) -> str:
    link = html.unescape((href or "").strip())
    if not link:
        return ""
    if link.startswith("//"):
        link = "https:" + link
    if link.startswith("/l/?") or "uddg=" in link:
        try:
            parsed = urlparse(link)
            query = parse_qs(parsed.query)
            uddg = query.get("uddg", [])
            if uddg and uddg[0]:
                return unquote(uddg[0])
        except Exception:
            return ""
    return link


def _parse_duckduckgo_html_results(page_html: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    if not page_html:
        return []

    results: List[Dict[str, str]] = []
    anchor_matches = list(
        re.finditer(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    snippet_matches = re.findall(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|'
        r'<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets = [_clean_html_fragment(a or b or "") for (a, b) in snippet_matches]

    for i, match in enumerate(anchor_matches):
        href = _normalize_duckduckgo_href(match.group(1))
        title = _clean_html_fragment(match.group(2))
        if not href.startswith(("http://", "https://")):
            continue
        body = snippets[i] if i < len(snippets) else title
        _append_result(results, title=title, href=href, body=body, min_length=min_length)
        if len(results) >= max_results:
            break

    return _dedupe_results(results, max_results)


def _search_duckduckgo_html(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    def _http_get_text(url: str, params: Dict[str, str], timeout: float = 10.0) -> Optional[str]:
        if httpx is not None:
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        querystring = urlencode(params or {})
        full_url = url + (("&" if "?" in url else "?") + querystring if querystring else "")
        req = Request(full_url, headers=headers, method="GET")
        with urlopen(req, timeout=timeout) as res:
            charset = res.headers.get_content_charset() or "utf-8"
            return res.read().decode(charset, errors="replace")

    for base in ("https://duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            html_text = _http_get_text(base, {"q": query}, timeout=10.0)
            parsed = _parse_duckduckgo_html_results(html_text or "", max_results=max_results, min_length=min_length)
            if parsed:
                return parsed
        except Exception:
            continue
    return []


def _provider_enabled(provider: str) -> bool:
    if provider == "google_serpapi":
        return (not SERPAPI_AUTH_DISABLED) and (not _is_placeholder_key(SERPAPI_API_KEY)) and (httpx is not None)
    if provider == "google_serper":
        return (not SERPER_AUTH_DISABLED) and (not _is_placeholder_key(SERPER_API_KEY)) and (httpx is not None)
    if provider == "google_cse":
        return (
            (not GOOGLE_CSE_AUTH_DISABLED)
            and (not _is_placeholder_key(GOOGLE_CSE_API_KEY))
            and (not _is_placeholder_key(GOOGLE_CSE_CX))
            and (httpx is not None)
        )
    if provider == "ddgs":
        return DDGS_AVAILABLE
    if provider == "duckduckgo_html":
        return True
    return False


def _resolve_provider_chain() -> List[str]:
    if WEB_SEARCH_PROVIDER == "auto":
        providers = ["google_serpapi", "google_serper", "google_cse"]
        if WEB_FALLBACK_TO_DDGS:
            providers.append("ddgs")
        providers.append("duckduckgo_html")
    elif WEB_SEARCH_PROVIDER in {"serpapi", "google_serpapi"}:
        providers = ["google_serpapi", "google_serper", "google_cse", "duckduckgo_html"]
    elif WEB_SEARCH_PROVIDER == "google_serper":
        providers = ["google_serper", "google_serpapi", "google_cse", "duckduckgo_html"]
    elif WEB_SEARCH_PROVIDER == "google_cse":
        providers = ["google_cse", "duckduckgo_html"]
    elif WEB_SEARCH_PROVIDER == "ddgs":
        providers = ["ddgs"]
    elif WEB_SEARCH_PROVIDER in {"duckduckgo_html", "ddg_html"}:
        providers = ["duckduckgo_html"]
    else:
        providers = ["google_serper", "google_cse", "duckduckgo_html"]

    enabled = [p for p in providers if _provider_enabled(p)]
    return list(dict.fromkeys(enabled))


def _search_provider(provider: str, query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    if provider == "google_serpapi":
        return _search_google_serpapi(query, max_results, min_length)
    if provider == "google_serper":
        return _search_google_serper(query, max_results, min_length)
    if provider == "google_cse":
        return _search_google_cse(query, max_results, min_length)
    if provider == "ddgs":
        return _search_ddgs(query, max_results, min_length)
    if provider == "duckduckgo_html":
        return _search_duckduckgo_html(query, max_results, min_length)
    return []


def _domain_matches(domain: str, expected_domain: str) -> bool:
    d = (domain or "").lower().lstrip("www.").strip()
    expected = (expected_domain or "").lower().lstrip("www.").strip()
    if not d or not expected:
        return False
    return d == expected or d.endswith(f".{expected}")


def _source_domain_from_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    try:
        return (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""


def _filter_results_for_domain(results: List[Dict[str, str]], expected_domain: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in results:
        d = _source_domain_from_url(item.get("href", ""))
        if _domain_matches(d, expected_domain):
            out.append(item)
    return out


def _run_provider_chain_for_query(query: str, providers: List[str], max_results: int, min_length: int) -> Tuple[List[Dict[str, str]], str]:
    simplified_query = query.replace("?", " ").strip()
    for provider in providers:
        for attempt in range(max(1, WEB_SEARCH_RETRY_ATTEMPTS)):
            q_try = simplified_query if attempt > 0 and simplified_query else query
            try:
                results = _search_provider(provider, q_try, max_results, min_length)
                if results:
                    return results, provider
            except Exception:
                pass
            time.sleep(0.15 * (attempt + 1))
    return [], "none"


def web_search(query: str, max_results: int = 5, min_length: int = 50) -> Tuple[List[Dict[str, str]], str]:
    # Optional prioritized search
    if WEB_USE_PRIORITIZED_SEARCH and web_search_prioritized is not None:
        try:
            prioritized_results = web_search_prioritized.search(
                query=query,
                search_type="general",
                max_results=max_results,
                min_priority=3,
            )
            if prioritized_results:
                converted: List[Dict[str, str]] = []
                for item in prioritized_results:
                    snippet = (item.snippet or item.title or "").strip()
                    if len(snippet) < min_length:
                        snippet = (item.title or snippet or "").strip()
                    converted.append(
                        {
                            "title": (item.title or "").strip(),
                            "href": (item.url or "").strip(),
                            "body": snippet[:500],
                            "source_name": (item.source_name or "").strip(),
                            "date": (item.date or "").strip(),
                        }
                    )
                converted = _dedupe_results(converted, max_results)
                if converted:
                    return converted, "prioritized"
        except Exception:
            pass

    providers = _resolve_provider_chain()
    if not providers:
        return [], "none_configured"

    combined: List[Dict[str, str]] = []
    trace: List[str] = []
    per_domain = min(3, max(1, max_results))

    # Priority domains first
    for domain in WEB_PRIORITY_DOMAINS:
        site_query = f"site:{domain} {query}".strip()
        domain_results, p = _run_provider_chain_for_query(site_query, providers, max_results=min(10, per_domain * 2), min_length=min_length)
        if p != "none":
            trace.append(f"{domain}:{p}")
        if not domain_results:
            continue
        filtered = _filter_results_for_domain(domain_results, domain) or domain_results
        combined.extend(filtered[:per_domain])

    # Fill generic
    if len(_dedupe_results(combined, max_results)) < max_results:
        generic, p = _run_provider_chain_for_query(query, providers, max_results=max_results, min_length=min_length)
        if p != "none":
            trace.append(f"general:{p}")
        combined.extend(generic)

    final = _dedupe_results(combined, max_results)
    return final, ("|" + ",".join(trace) if trace else "generic")


# --------------------------------------------------------------------------------------
# Main entrypoint
# --------------------------------------------------------------------------------------
def ask_agent(
    question: str,
    history: Optional[list] = None,
    max_results: int = 5,
    history_window: int = 8,
    strict_realtime: Optional[bool] = None,  # conservé pour compat
    skip_web_search: bool = False,
    routing_override: Optional[Dict[str, Any]] = None,  # conservé pour compat
    k_rag: int = 4,
) -> dict:
    """
    Router + execution.

    - DIRECT: smalltalk -> réponse sans web
    - RAG: Darwin local -> synthèse (si LLM dispo)
    - WEB: web_search -> synthèse (si LLM dispo)
    - RAG+WEB: combine RAG + WEB -> synthèse (si LLM dispo)
    """
    start_time = time.time()
    history = history or []

    analysis = _analyze_question(question)
    question_type = str(analysis.get("type") or "info")

    decision = route(question)

    # DIRECT
    if decision.mode == "DIRECT":
        draft = _direct_answer_no_web(question, history, question_type)
        elapsed = time.time() - start_time
        return {
            "draft": draft,
            "sources": [],
            "meta": {
                "tool": "direct",
                "knowledge_layer": "direct",
                "response_time_seconds": round(elapsed, 2),
                "route_mode": decision.mode,
                "route_reasons": list(decision.reasons),
                "route_debug": decision.debug,
                "question_type": question_type,
                "question_analysis": analysis,
            },
        }

    # RAG only
    if decision.mode == "RAG":
        rag_payload = _rag_fetch(question, history, k=k_rag)
        rag_text = str(rag_payload.get("draft") or "").strip()
        sources = rag_payload.get("sources") if isinstance(rag_payload.get("sources"), list) else []
        elapsed = time.time() - start_time

        if rag_text and llm is not None:
            draft = _synthesize_with_llm(
                question=question,
                history=history,
                question_type=question_type,
                context=rag_text,
            )
        else:
            draft = rag_text or "Aucun extrait Darwin pertinent trouvé."

        return {
            "draft": draft,
            "sources": sources,
            "meta": {
                "tool": "core",
                "knowledge_layer": "rag_darwin",
                "response_time_seconds": round(elapsed, 2),
                "route_mode": decision.mode,
                "route_reasons": list(decision.reasons),
                "route_debug": decision.debug,
                "question_type": question_type,
                "question_analysis": analysis,
                "core_meta": rag_payload.get("meta", {}),
            },
        }

    # WEB / RAG+WEB
    web_results: List[Dict[str, str]] = []
    provider_used = "skipped"
    if not skip_web_search:
        web_results, provider_used = web_search(question, max_results=max_results)

    web_context_lines: List[str] = []
    web_sources: List[str] = []
    for i, r in enumerate(web_results[: min(8, len(web_results))], start=1):
        href = (r.get("href") or "").strip()
        if href:
            web_sources.append(href)
        web_context_lines.append(
            f"[{i}] {r.get('title','')}\n"
            f"domain: {_source_domain_from_url(href)}\n"
            f"url: {href}\n"
            f"extrait: {r.get('body','')}"
        )
    web_context = "\n\n".join(web_context_lines).strip()

    # If WEB only
    if decision.mode == "WEB":
        if not web_context:
            draft = _direct_answer_no_web(question, history, question_type)
        else:
            sources_block = "SOURCES WEB: cite le domaine (ex: aspim.fr) quand tu utilises une info."
            draft = _synthesize_with_llm(question, history, question_type, context=web_context, sources_block=sources_block)

        elapsed = time.time() - start_time
        return {
            "draft": draft,
            "sources": list(dict.fromkeys(web_sources)),
            "meta": {
                "tool": "web",
                "knowledge_layer": "rag_market",
                "provider": provider_used,
                "configured_provider": WEB_SEARCH_PROVIDER,
                "max_results": max_results,
                "actual_results": len(web_results),
                "response_time_seconds": round(elapsed, 2),
                "route_mode": decision.mode,
                "route_reasons": list(decision.reasons),
                "route_debug": decision.debug,
                "question_type": question_type,
                "question_analysis": analysis,
                "history_length": len(history),
            },
        }

    # RAG+WEB
    rag_payload = _rag_fetch(question, history, k=k_rag)
    rag_text = str(rag_payload.get("draft") or "").strip()
    rag_sources = rag_payload.get("sources") if isinstance(rag_payload.get("sources"), list) else []

    combined_context = "\n\n".join(
        [
            "=== CONTEXTE DARWIN (RAG) ===",
            rag_text or "Aucun extrait Darwin pertinent trouvé.",
            "",
            "=== CONTEXTE WEB ===",
            web_context or "Aucun résultat web exploitable.",
        ]
    ).strip()

    if llm is not None:
        sources_block = "SOURCES WEB: cite le domaine (ex: aspim.fr) quand tu utilises une info web."
        draft = _synthesize_with_llm(question, history, question_type, context=combined_context, sources_block=sources_block)
    else:
        draft = combined_context

    elapsed = time.time() - start_time
    return {
        "draft": draft,
        "sources": {
            "rag": rag_sources,
            "web": list(dict.fromkeys(web_sources)),
        },
        "meta": {
            "tool": "rag+web",
            "knowledge_layer": "hybrid",
            "provider": provider_used,
            "configured_provider": WEB_SEARCH_PROVIDER,
            "max_results": max_results,
            "actual_results": len(web_results),
            "k_rag": k_rag,
            "response_time_seconds": round(elapsed, 2),
            "route_mode": decision.mode,
            "route_reasons": list(decision.reasons),
            "route_debug": decision.debug,
            "question_type": question_type,
            "question_analysis": analysis,
            "core_meta": rag_payload.get("meta", {}),
        },
    }


if __name__ == "__main__":
    print(ask_agent("hello")["draft"])
    print("\n---\n")
    print(ask_agent("Quels sont les frais de Darwin RE01 ?")["draft"][:400])
    print("\n---\n")
    print(ask_agent("taux BCE actuel 2026 ?", max_results=3)["draft"][:400])