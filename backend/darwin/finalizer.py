import json
import os
import re
import sys
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from types import SimpleNamespace

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        dotenv_path = kwargs.get("dotenv_path")
        if not dotenv_path and args:
            dotenv_path = args[0]
        path = str(dotenv_path or ".env")
        override = bool(kwargs.get("override", False))
        if not os.path.exists(path):
            return False
        loaded = False
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'").strip('"')
                    if not key:
                        continue
                    if (not override) and key in os.environ:
                        continue
                    os.environ[key] = value
                    loaded = True
        except Exception:
            return False
        return loaded
try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None
try:
    from langchain_anthropic import ChatAnthropic
except Exception:  # pragma: no cover
    ChatAnthropic = None
try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    from ..agents.agent_core import ask_agent as ask_core
    from ..agents.agent_online import ask_agent as ask_online
    from ..agents.agent_report import ask_agent as ask_rapport
    from ..agents.agent_sql_kpi import ask_agent as ask_sql_kpi
    from ..core.cgp_business_layer import CGPBusinessLayer
    from ..core.intent_understanding import (
        detect_intent as shared_detect_intent,
        has_amount_signal as shared_has_amount_signal,
        has_horizon_signal as shared_has_horizon_signal,
        has_objective_signal as shared_has_objective_signal,
        should_force_strategic_intent as shared_should_force_strategic_intent,
    )
    from ..core.cgp_strategic_layer import decide as strategic_decide
    from ..core.synthesis_agent import synthesize_answer
    from .persona import DARWIN_PERSONA_PROMPT
except ImportError:
    # Support exécution directe: python backend/darwin/finalizer.py
    PROJECT_ROOT_FALLBACK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if PROJECT_ROOT_FALLBACK not in sys.path:
        sys.path.insert(0, PROJECT_ROOT_FALLBACK)
    from backend.agents.agent_core import ask_agent as ask_core
    from backend.agents.agent_online import ask_agent as ask_online
    from backend.agents.agent_report import ask_agent as ask_rapport
    from backend.agents.agent_sql_kpi import ask_agent as ask_sql_kpi
    from backend.core.cgp_business_layer import CGPBusinessLayer
    from backend.core.intent_understanding import (
        detect_intent as shared_detect_intent,
        has_amount_signal as shared_has_amount_signal,
        has_horizon_signal as shared_has_horizon_signal,
        has_objective_signal as shared_has_objective_signal,
        should_force_strategic_intent as shared_should_force_strategic_intent,
    )
    from backend.core.cgp_strategic_layer import decide as strategic_decide
    from backend.core.synthesis_agent import synthesize_answer
    from backend.darwin.persona import DARWIN_PERSONA_PROMPT

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"))

def _is_placeholder_key(value: Optional[str]) -> bool:
    token = (value or "").strip().lower()
    if not token:
        return True
    placeholders = {"...", "xxx", "your_key", "your-api-key", "changeme", "replace_me"}
    if token in placeholders:
        return True
    return token.startswith("your_") or token.startswith("sk-...")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _first_non_placeholder_from_dotenv(key: str) -> Optional[str]:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return None
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() != key:
                    continue
                candidate = value.strip().strip("'").strip('"')
                if not _is_placeholder_key(candidate):
                    return candidate
    except Exception:
        return None
    return None


def _read_env_prefer_non_placeholder(key: str) -> Optional[str]:
    direct = os.getenv(key)
    if not _is_placeholder_key(direct):
        return direct
    return _first_non_placeholder_from_dotenv(key) or direct


class _ClaudeHTTPChat:
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.2,
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
        tried: List[str] = []

        for model_name in candidates:
            payload = {
                "model": model_name,
                "max_tokens": int(os.getenv("FINALIZER_ANTHROPIC_MAX_TOKENS", "2200")),
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
                        tried.append(model_name)
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
                return SimpleNamespace(content="\n".join([p for p in parts if p]).strip())
            except HTTPError as exc:
                if getattr(exc, "code", None) == 404:
                    tried.append(model_name)
                    continue
                raise
            except Exception as exc:
                msg = str(exc).lower()
                if "not_found_error" in msg or ("model:" in msg and "404" in msg):
                    tried.append(model_name)
                    continue
                raise

        raise RuntimeError(
            "Anthropic model indisponible pour finalizer: "
            + ", ".join(candidates)
            + (f" | tried: {', '.join(tried)}" if tried else "")
        )


def _anthropic_model_candidates(primary_model: str, scope: str) -> List[str]:
    raw = os.getenv(f"{scope}_ANTHROPIC_MODEL_FALLBACKS", os.getenv("ANTHROPIC_MODEL_FALLBACKS", ""))
    env_candidates = [item.strip() for item in str(raw).split(",") if item.strip()]
    defaults = [
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-latest",
        "claude-3-7-sonnet-latest",
        "claude-3-haiku-20240307",
    ]
    ordered: List[str] = []
    for item in [primary_model] + env_candidates + defaults:
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def _build_finalizer_llm() -> Tuple[Optional[Any], str, str, str]:
    openai_key = _read_env_prefer_non_placeholder("OPENAI_API_KEY")
    anthropic_key = _read_env_prefer_non_placeholder("ANTHROPIC_API_KEY")

    default_provider = "anthropic" if (anthropic_key and not _is_placeholder_key(anthropic_key)) else "openai"
    requested_provider = str(
        os.getenv("FINALIZER_LLM_PROVIDER", os.getenv("LLM_PROVIDER", default_provider))
    ).strip().lower() or default_provider
    allow_fallback = _env_flag("FINALIZER_LLM_ALLOW_FALLBACK", _env_flag("LLM_ALLOW_PROVIDER_FALLBACK", True))

    openai_model = os.getenv("FINALIZER_OPENAI_MODEL", os.getenv("FINALIZER_MODEL", "gpt-4o-mini"))
    anthropic_model = os.getenv(
        "FINALIZER_ANTHROPIC_MODEL",
        os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    )
    anthropic_candidates = _anthropic_model_candidates(anthropic_model, scope="FINALIZER")

    if requested_provider in {"anthropic", "claude"}:
        candidates = ["anthropic"] + (["openai"] if allow_fallback else [])
    elif requested_provider == "openai":
        candidates = ["openai"] + (["anthropic"] if allow_fallback else [])
    else:
        candidates = ["anthropic", "openai"]

    for provider in candidates:
        if provider == "anthropic" and not _is_placeholder_key(anthropic_key):
            try:
                client = _ClaudeHTTPChat(
                    api_key=str(anthropic_key),
                    model=anthropic_candidates[0],
                    temperature=0.2,
                    fallback_models=anthropic_candidates[1:],
                )
                return client, "anthropic_http", anthropic_candidates[0], requested_provider
            except Exception:
                pass
            if ChatAnthropic is not None:
                for candidate_model in anthropic_candidates:
                    try:
                        client = ChatAnthropic(model=candidate_model, temperature=0.2, api_key=anthropic_key)
                        return client, "anthropic_langchain", candidate_model, requested_provider
                    except TypeError:
                        try:
                            client = ChatAnthropic(
                                model=candidate_model,
                                temperature=0.2,
                                anthropic_api_key=anthropic_key,
                            )
                            return client, "anthropic_langchain", candidate_model, requested_provider
                        except Exception:
                            continue
                    except Exception:
                        continue
        if provider == "openai" and not _is_placeholder_key(openai_key) and ChatOpenAI is not None:
            try:
                client = ChatOpenAI(model=openai_model, temperature=0.2, api_key=openai_key)
                return client, "openai", openai_model, requested_provider
            except Exception:
                pass

    return None, "none", "", requested_provider


_finalizer_llm, FINALIZER_LLM_PROVIDER_EFFECTIVE, FINALIZER_MODEL, FINALIZER_LLM_PROVIDER_REQUESTED = (
    _build_finalizer_llm()
)
PARALLEL_CORE_ONLINE = os.getenv("PARALLEL_CORE_ONLINE", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PREFER_LIVE_WEB_BY_DEFAULT = os.getenv("PREFER_LIVE_WEB_BY_DEFAULT", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STRICT_REALTIME_GUARD = os.getenv("STRICT_REALTIME_GUARD", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STRICT_REALTIME_AUTO_ESTIMATION_FALLBACK = os.getenv(
    "STRICT_REALTIME_AUTO_ESTIMATION_FALLBACK",
    "true",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
NEUTRAL_PURE_DEFAULT = os.getenv("NEUTRAL_PURE_DEFAULT", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SCPI_FRESHNESS_THRESHOLD_DAYS = int(os.getenv("SCPI_FRESHNESS_THRESHOLD_DAYS", "210"))
SCPI_CONSOLIDATED_MESSAGE_FALLBACK = os.getenv(
    "SCPI_CONSOLIDATED_MESSAGE_FALLBACK",
    "Données consolidées au T3 2025",
)
FINALIZER_STRUCTURED_REWRITE_ENABLED = _env_flag("FINALIZER_STRUCTURED_REWRITE_ENABLED", True)
FINALIZER_STRUCTURED_MAX_SOURCES = max(
    2, int(os.getenv("FINALIZER_STRUCTURED_MAX_SOURCES", "8"))
)

# Singleton léger pour éviter de ré-instancier la couche métier à chaque requête.
_cgp_business_layer = CGPBusinessLayer()

INTENT_STRATEGIC_ALLOCATION = "STRATEGIC_ALLOCATION"
INTENT_FACTUAL_KPI = "FACTUAL_KPI"
INTENT_COMPARISON = "COMPARISON"
INTENT_DARWIN_SPECIFIC = "DARWIN_SPECIFIC"
INTENT_REGULATORY = "REGULATORY"
INTENT_RAPPORT = "RAPPORT"

CGP_INTENT_TOP = "TOP"
CGP_INTENT_KPI = "KPI"
CGP_INTENT_STRATEGIE = "STRATEGIE"
CGP_ALLOWED_KPI_TARGETS = {"td", "tof", "walt", "frais", "prix_part", "capitalisation", "collecte", "none"}

INTENT_TIE_BREAKER = [
    INTENT_STRATEGIC_ALLOCATION,
    INTENT_FACTUAL_KPI,
    INTENT_REGULATORY,
    INTENT_DARWIN_SPECIFIC,
    INTENT_COMPARISON,
]
INTENT_TIE_BREAKER_NEUTRAL = [
    INTENT_STRATEGIC_ALLOCATION,
    INTENT_FACTUAL_KPI,
    INTENT_REGULATORY,
    INTENT_COMPARISON,
]
DARWIN_QUERY_KEYWORDS = [
    "darwin",
    "re01",
    "offre darwin",
    "produit darwin",
    "frais darwin",
    "conditions darwin",
    "simulateur",
    "scpi darwin",
]
EXTERNAL_SCPI_BRAND_KEYWORDS = [
    "iroko",
    "corum",
    "primopierre",
    "epargne pierre",
    "novapierre",
    "pfo2",
    "immorente",
    "sofidy",
    "la francaise",
    "remake",
    "coeur de regions",
    "paref",
    "aestiam",
    "perial",
    "amundi",
    "edmond de rothschild",
    "praemia",
]
MARKET_COMPARISON_QUERY_KEYWORDS = [
    "top",
    "classement",
    "meilleure",
    "meilleur",
    "compar",
    "palmares",
    "ranking",
    "pga",
    "politique de gestion",
    "taux de distribution",
    "td",
    "rendement",
]
CONCRETE_DATA_DEMAND_KEYWORDS = [
    "top",
    "classement",
    "palmares",
    "meilleure",
    "meilleur",
    "rendement",
    "taux de distribution",
    "td",
    "tof",
    "walt",
    "kpi",
    "chiffre",
    "chiffres",
    "donnees",
    "donnees concretes",
    "données",
    "données concrètes",
    "compare",
    "compar",
    "liste",
]
VAGUE_RESPONSE_PHRASES = [
    "des classements sont disponibles",
    "les sources fournissent des informations",
    "vous pouvez consulter",
    "tu peux consulter",
    "consulte les sources",
    "informations disponibles",
]
CONCRETE_RESPONSE_NO_DATA_PREFIX = "Estimation prudente (sources live incomplètes)"
FRESHNESS_LINE_PREFIX = "📅 Dernière mise à jour :"
SOURCES_LINE_PREFIX = "📊 Sources :"
AVAILABILITY_BLOCK_HEADER = "💡 Je reste à votre disposition si vous avez besoin de précisions sur :"
def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _normalize_ascii(text: Optional[str]) -> str:
    normalized = (text or "").lower().strip()
    normalized = normalized.replace("é", "e").replace("è", "e").replace("ê", "e")
    normalized = normalized.replace("à", "a").replace("â", "a").replace("î", "i")
    normalized = normalized.replace("ô", "o").replace("û", "u").replace("ù", "u")
    normalized = normalized.replace("ç", "c")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _looks_like_data_demand(question: str, intent: str) -> bool:
    qn = _normalize_ascii(question or "")
    if intent in {INTENT_FACTUAL_KPI, INTENT_COMPARISON, INTENT_REGULATORY}:
        return True
    if "scpi" in qn and _contains_any(qn, CONCRETE_DATA_DEMAND_KEYWORDS):
        return True
    return _contains_any(qn, CONCRETE_DATA_DEMAND_KEYWORDS) and _contains_any(
        qn,
        ["scpi", "immobilier", "patrimoine", "darwin", "re01"],
    )


def _contains_vague_phrase(answer: str) -> bool:
    an = _normalize_ascii(answer or "")
    return any(phrase in an for phrase in VAGUE_RESPONSE_PHRASES)


def _line_has_numeric_signal(line: str) -> bool:
    return bool(re.search(r"\d", line or ""))


def _line_has_percent_signal(line: str) -> bool:
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*%", line or ""))


def _line_has_metric_signal(line: str) -> bool:
    ln = _normalize_ascii(line or "")
    metric_terms = [
        "td",
        "taux de distribution",
        "tof",
        "walt",
        "capitalisation",
        "collecte",
        "pga",
        "rendement",
    ]
    return any(term in ln for term in metric_terms)


def _is_top_like_question(question: str) -> bool:
    qn = _normalize_ascii(question or "")
    return _contains_any(qn, ["top", "classement", "palmares", "meilleure", "meilleur"])


def _requested_top_size(question: str, default_for_top: int = 10, cap: int = 20) -> int:
    qn = _normalize_ascii(question or "")
    if not _is_top_like_question(question):
        return 0

    m = re.search(r"\btop\s*(\d{1,2})\b", qn)
    if not m:
        m = re.search(r"\bclassement\s*(?:des|de)?\s*(\d{1,2})\b", qn)
    if m:
        try:
            value = int(m.group(1))
            return max(1, min(cap, value))
        except Exception:
            return min(cap, default_for_top)
    return min(cap, default_for_top)


def _is_noisy_concrete_line(line: str) -> bool:
    ln = _normalize_ascii(line or "")
    noisy_tokens = [
        "reponse courte",
        "réponse courte",
        "reponse directe",
        "réponse directe",
        "classement scpi",
        "meilleures scpi",
        "retrouvez notre classement",
        "awards",
        "sources:",
        "source:",
        "http://",
        "https://",
    ]
    if any(token in ln for token in noisy_tokens):
        return True
    if len(line) > 160 and ":" in line:
        return True
    return False


def _is_actionable_data_line(line: str, question: str) -> bool:
    if not line:
        return False
    if _is_noisy_concrete_line(line):
        return False
    top_like = _is_top_like_question(question)
    has_percent = _line_has_percent_signal(line)
    if top_like and not has_percent:
        return False
    if has_percent:
        return True
    return _line_has_numeric_signal(line) and _line_has_metric_signal(line)


def _clean_candidate_line(line: str) -> str:
    txt = _clean(line)
    txt = re.sub(r"^\s*[-*•]+\s*", "", txt)
    txt = re.sub(r"^\s*\d+\s*[.)]\s*", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _extract_concrete_lines_from_text(text: str, question: str, limit: int = 10) -> List[str]:
    lines: List[str] = []
    seen = set()
    skip_prefixes = (
        "sources",
        "source:",
        "dates",
        "donnees utilisees",
        "mode audit",
        "score breakdown",
        "ponderations",
        "agent retenu",
        "intent:",
        "moteur:",
        "profil retenu:",
        "scores:",
        "ecart de confiance:",
        "note version:",
        "changelog:",
        "release version:",
        "contribution ",
    )
    for raw in (text or "").splitlines():
        line = _clean_candidate_line(raw)
        if not line:
            continue
        ln = _normalize_ascii(line)
        if ln.startswith(skip_prefixes):
            continue
        if len(line) < 8:
            continue
        if "deterministic_profile_scoring" in ln:
            continue
        if not _is_actionable_data_line(line, question=question):
            continue
        if ln in seen:
            continue
        seen.add(ln)
        lines.append(line[:140])
        if len(lines) >= max(1, limit):
            break
    return lines


def _extract_concrete_lines_from_sources(
    question: str,
    sources_by_layer: Dict[str, List[Any]],
    limit: int = 10,
) -> List[str]:
    lines: List[str] = []
    seen = set()
    for item in (sources_by_layer.get("sql_kpi") or []):
        if not isinstance(item, dict):
            continue
        metric = _clean(str(item.get("metric") or item.get("context") or "KPI"))
        value = _clean(str(item.get("value") or ""))
        unit = _clean(str(item.get("unit") or ""))
        if not value or not _line_has_numeric_signal(value):
            continue
        date_txt = _clean(str(item.get("date") or "non renseignée"))
        source_txt = _clean(str(item.get("source") or "source inconnue"))
        line = f"{metric} - {value}{(' ' + unit) if unit else ''} - {source_txt} - {date_txt}".strip()
        key = _normalize_ascii(line)
        if not _is_actionable_data_line(line, question=question):
            continue
        if key in seen:
            continue
        seen.add(key)
        lines.append(line[:140])
        if len(lines) >= max(1, limit):
            break

    if len(lines) >= max(1, limit):
        return lines

    for item in (sources_by_layer.get("rag_market") or []):
        if not isinstance(item, dict):
            continue
        title = _clean(str(item.get("title") or ""))
        snippet = _clean(
            str(item.get("snippet") or item.get("body") or item.get("content") or "")
        )
        if not title and not snippet:
            continue
        raw_url = _to_url(item.get("url") or item.get("href") or item.get("source"))
        source_txt = _clean(
            str(item.get("source") or _source_label_from_url(raw_url) or "source web")
        )
        date_txt = _clean(
            str(item.get("date") or item.get("updated_at") or item.get("published_at") or "non renseignée")
        )
        percent_chunks: List[str] = []
        for block in [snippet, title]:
            block_txt = _clean(block)
            if not block_txt:
                continue
            extracted = re.findall(
                r"[^.;\n]{0,110}\d+(?:[.,]\d+)?\s*%[^.;\n]{0,110}",
                block_txt,
            )
            if extracted:
                percent_chunks.extend(extracted)
            elif _line_has_percent_signal(block_txt):
                percent_chunks.append(block_txt)
        if not percent_chunks:
            continue

        for chunk in percent_chunks:
            core = _clean_candidate_line(chunk)
            if not core:
                continue
            title_is_usable = bool(title) and (not _is_noisy_concrete_line(title))
            if title_is_usable and _normalize_ascii(title) not in _normalize_ascii(core):
                candidate = f"{title} - {core} - {source_txt} - {date_txt}"
            else:
                candidate = f"{core} - {source_txt} - {date_txt}"
            key = _normalize_ascii(candidate)
            if not _is_actionable_data_line(candidate, question=question):
                continue
            if key in seen:
                continue
            seen.add(key)
            lines.append(candidate[:140])
            if len(lines) >= max(1, limit):
                return lines
    return lines


def _collect_concrete_data_lines(
    question: str,
    answer: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    limit: int = 10,
) -> List[str]:
    merged: List[str] = []
    seen = set()
    for bucket in (
        _extract_concrete_lines_from_sources(question=question, sources_by_layer=sources_by_layer, limit=limit),
        _extract_concrete_lines_from_text(answer, question=question, limit=limit),
        _extract_concrete_lines_from_text(material, question=question, limit=limit),
    ):
        for line in bucket:
            key = _normalize_ascii(line)
            if key in seen:
                continue
            seen.add(key)
            merged.append(line)
            if len(merged) >= max(1, limit):
                return merged
    return merged


def _latest_data_label(
    sources_by_layer: Dict[str, List[Any]],
    latest_consolidated_date: Optional[date],
) -> str:
    if latest_consolidated_date is not None:
        return _format_date_french(latest_consolidated_date)
    latest = _extract_latest_consolidated_date(sources_by_layer)
    if latest is not None:
        return _format_date_french(latest)
    return "non disponible"


def _is_non_available_label(text: str) -> bool:
    token = _normalize_ascii(text or "")
    return token in {
        "",
        "non disponible",
        "non precisee",
        "non precisees",
        "non precise",
        "non precisees",
        "non renseigne",
        "non renseignee",
        "n/a",
        "na",
    }


def _format_date_french(value: Optional[date]) -> str:
    if value is None:
        return "non disponible"
    months = [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ]
    month_txt = months[value.month - 1] if 1 <= value.month <= 12 else str(value.month)
    return f"{value.day} {month_txt} {value.year}"


def _to_url(raw: Any) -> str:
    text = _clean(str(raw or ""))
    if not text:
        return ""
    if re.match(r"^https?://", text, flags=re.IGNORECASE):
        return text
    if re.match(r"^www\.[a-z0-9.-]+\.[a-z]{2,}([/?].*)?$", text, flags=re.IGNORECASE):
        return f"https://{text}"
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}([/?].*)?$", text, flags=re.IGNORECASE):
        return f"https://{text}"
    return ""


def _source_label_from_url(url: str) -> str:
    clean_url = _clean(url)
    if not clean_url:
        return ""
    match = re.match(r"^https?://([^/\s]+)", clean_url, flags=re.IGNORECASE)
    if not match:
        return clean_url
    return match.group(1).lower().lstrip("www.")


def _humanize_source_label(source_label: str, url: str) -> str:
    src = _clean(source_label)
    if not src and url:
        src = _source_label_from_url(url)
    lower = _normalize_ascii(src)
    if "data/docs/" in lower or lower.endswith(".pdf") or lower.endswith(".txt"):
        if "darwin" in lower:
            return "Documentation Darwin"
        return "Documentation interne"
    if src.startswith("http://") or src.startswith("https://"):
        return _source_label_from_url(src) or src
    return src or "source inconnue"


def _collect_source_entries_for_freshness(
    sources_by_layer: Dict[str, List[Any]],
    max_items: int = 4,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    for item in (sources_by_layer.get("rag_darwin") or []):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        source_label = _clean(
            str(
                metadata.get("source")
                or metadata.get("title")
                or metadata.get("file_name")
                or "Darwin docs"
            )
        )
        date_obj = (
            _parse_any_date(metadata.get("date"))
            or _parse_any_date(metadata.get("as_of"))
            or _parse_any_date(metadata.get("updated_at"))
            or _parse_any_date(metadata.get("published_at"))
        )
        url = _to_url(metadata.get("url") or metadata.get("source_url") or source_label)
        if not url and "darwin" in _normalize_ascii(source_label):
            url = "https://darwin.fr/documentation"
        if not source_label and url:
            source_label = _source_label_from_url(url)
        entries.append(
            {
                "priority": 0,
                "source": source_label or "Darwin docs",
                "date_obj": date_obj,
                "url": url,
            }
        )

    for item in (sources_by_layer.get("rag_market") or []):
        if isinstance(item, str):
            url = _to_url(item)
            source_label = _source_label_from_url(url) or _clean(item) or "web"
            entries.append(
                {
                    "priority": 1,
                    "source": source_label,
                    "date_obj": _parse_any_date(None),
                    "url": url,
                }
            )
            continue
        if isinstance(item, dict):
            raw_url = item.get("url") or item.get("href") or item.get("source")
            url = _to_url(raw_url)
            source_label = _clean(str(item.get("title") or item.get("source") or "")) or _source_label_from_url(url) or "web"
            date_obj = (
                _parse_any_date(item.get("date"))
                or _parse_any_date(item.get("updated_at"))
                or _parse_any_date(item.get("published_at"))
            )
            entries.append(
                {
                    "priority": 1,
                    "source": source_label,
                    "date_obj": date_obj,
                    "url": url,
                }
            )

    for item in (sources_by_layer.get("sql_kpi") or []):
        if not isinstance(item, dict):
            continue
        source_label = _clean(str(item.get("source") or "sql_kpi"))
        date_obj = (
            _parse_any_date(item.get("date"))
            or _parse_any_date(item.get("as_of"))
            or _parse_any_date((item.get("provenance") or {}).get("date") if isinstance(item.get("provenance"), dict) else None)
        )
        url = _to_url(source_label)
        if not source_label and url:
            source_label = _source_label_from_url(url)
        entries.append(
            {
                "priority": 2,
                "source": source_label or "sql_kpi",
                "date_obj": date_obj,
                "url": url,
            }
        )

    dedup: List[Dict[str, Any]] = []
    seen = set()
    for item in entries:
        key = (
            int(item.get("priority", 9)),
            _normalize_ascii(str(item.get("source") or "")),
            _normalize_ascii(str(item.get("url") or "")),
            _normalize_ascii(str(item.get("date_obj") or "")),
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)

    dedup.sort(
        key=lambda x: (
            0 if isinstance(x.get("date_obj"), date) else 1,
            int(x.get("priority", 9)),
            -(x.get("date_obj").toordinal() if isinstance(x.get("date_obj"), date) else 0),
            _normalize_ascii(str(x.get("source") or "")),
        )
    )
    return dedup[: max(1, max_items)]


def _sources_with_dates_summary(
    sources_by_layer: Dict[str, List[Any]],
    max_items: int = 3,
) -> str:
    entries = _collect_source_entries_for_freshness(sources_by_layer, max_items=max_items)
    if not entries:
        return ""

    parts: List[str] = []
    seen = set()
    informative_present = False
    for item in entries:
        source_label = _humanize_source_label(
            source_label=str(item.get("source") or ""),
            url=str(item.get("url") or ""),
        )
        date_obj = item.get("date_obj") if isinstance(item.get("date_obj"), date) else None
        date_label = _format_date_french(date_obj)
        url = _clean(str(item.get("url") or ""))
        key = (
            _normalize_ascii(source_label),
            _normalize_ascii(url),
            _normalize_ascii(date_label),
        )
        if key in seen:
            continue
        seen.add(key)

        if date_obj is not None:
            informative_present = True
        if source_label not in {"Documentation Darwin", "Documentation interne", "source inconnue"}:
            informative_present = True
        if url and "darwin.fr/documentation" not in _normalize_ascii(url):
            informative_present = True

        if url and (not _is_non_available_label(date_label)):
            parts.append(f"{source_label} ({date_label}) - {url}")
        elif url:
            parts.append(f"{source_label} - {url}")
        elif not _is_non_available_label(date_label):
            parts.append(f"{source_label} ({date_label})")
        else:
            parts.append(source_label)

    if not parts or not informative_present:
        return ""
    return f"{SOURCES_LINE_PREFIX} " + " ; ".join(parts)


def _source_urls_summary(sources_by_layer: Dict[str, List[Any]], max_items: int = 3) -> str:
    entries = _collect_source_entries_for_freshness(sources_by_layer, max_items=max_items * 2)
    urls: List[str] = []
    seen = set()
    for item in entries:
        url = _clean(str(item.get("url") or ""))
        if not url:
            continue
        key = _normalize_ascii(url)
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= max_items:
            break
    if not urls:
        return ""
    return "Sources: " + ", ".join(urls)


def _build_no_data_concrete_answer(
    latest_label: str,
    sources_by_layer: Dict[str, List[Any]],
) -> str:
    source_line = _source_urls_summary(sources_by_layer, max_items=2)
    lines = [
        f"{CONCRETE_RESPONSE_NO_DATA_PREFIX} — dernière consolidation repérée : {latest_label}.",
        "Réponse utile immédiate : on propose une première orientation basée sur tendances connues, à confirmer.",
        "Alternatives concrètes :",
        "1. Donner le dernier classement vérifiable sur la période disponible.",
        "2. Comparer 2 SCPI précises avec TD, TOF et WALT si disponibles.",
        "3. Produire une grille KPI datée (source + date + valeur).",
        "Point de vigilance : à valider selon TD, TOF, WALT, frais et liquidité.",
    ]
    if source_line:
        lines.append(source_line)
    return "\n".join(lines).strip()


def _build_concrete_data_answer(
    question: str,
    concrete_lines: List[str],
    sources_by_layer: Dict[str, List[Any]],
    requested_top: int = 0,
) -> str:
    qn = _normalize_ascii(question or "")
    is_top = _contains_any(qn, ["top", "classement", "palmares"])
    if is_top:
        target_items = requested_top or _requested_top_size(question, default_for_top=10, cap=20) or 10
        max_items = min(max(1, target_items), max(1, len(concrete_lines)))
        if max_items < target_items:
            header = (
                f"Voici le top {max_items} le plus fiable avec les chiffres "
                f"(sur {target_items} demandés, selon les données disponibles) :"
            )
        else:
            header = f"Voici le top {max_items} le plus fiable avec les chiffres :"
    else:
        max_items = min(10, max(1, len(concrete_lines)))
        header = "Voici les données concrètes et vérifiables :"

    lines = [header]
    for idx, item in enumerate(concrete_lines[:max_items], start=1):
        lines.append(f"{idx}. {item}")
    return "\n".join(lines).strip()


def _availability_suggestions(question: str, intent: str) -> List[str]:
    qn = _normalize_ascii(question or "")
    if intent in {INTENT_FACTUAL_KPI, INTENT_COMPARISON} or _contains_any(
        qn,
        ["top", "classement", "palmares", "rendement", "taux de distribution", "scpi"],
    ):
        return [
            "Le classement par profil de risque (défensif, équilibré, dynamique).",
            "Le filtre par ticket d'entrée, frais et niveau de liquidité.",
            "La comparaison détaillée TD, TOF et WALT de 2 SCPI ciblées.",
        ]

    if intent == INTENT_REGULATORY or _contains_any(qn, ["amf", "aspim", "reglement", "réglement"]):
        return [
            "Les impacts concrets de la règle pour votre situation client.",
            "Les obligations à respecter et leurs échéances opérationnelles.",
            "Les références officielles à prioriser (AMF/ASPIM).",
        ]

    if intent == INTENT_DARWIN_SPECIFIC or _contains_any(qn, ["darwin", "re01"]):
        return [
            "Le positionnement de RE01 selon votre objectif patrimonial.",
            "Les frais, la liquidité et les limites à surveiller.",
            "La comparaison chiffrée avec 2 alternatives du marché.",
        ]

    if _contains_any(qn, ["allocation", "strategie", "stratégie"]):
        return [
            "L'allocation cible selon votre horizon et votre tolérance au risque.",
            "L'impact fiscal IR/IS sur le rendement net projeté.",
            "Un scénario prudent versus dynamique sur 3, 5 ou 10 ans.",
        ]

    return [
        "La période exacte à analyser (année ou trimestre).",
        "Les sources prioritaires à retenir pour votre cas.",
        "Une comparaison chiffrée entre 2 options concrètes.",
    ]


def _strip_existing_availability_block(answer: str) -> str:
    lines = (answer or "").splitlines()
    out: List[str] = []
    skipping = False
    for raw in lines:
        line = _clean(raw)
        ln = _normalize_ascii(line)
        if ln.startswith(_normalize_ascii(AVAILABILITY_BLOCK_HEADER)):
            skipping = True
            continue
        if skipping:
            if line.startswith("•") or line.startswith("-"):
                continue
            if not line:
                continue
            skipping = False
        out.append(raw)
    return "\n".join(out).strip()


def _strip_existing_freshness_lines(answer: str) -> str:
    out: List[str] = []
    for raw in (answer or "").splitlines():
        line = _clean(raw)
        if line.startswith(FRESHNESS_LINE_PREFIX):
            continue
        if line.startswith(SOURCES_LINE_PREFIX):
            continue
        ln = _normalize_ascii(line)
        if ln.startswith("sources:") or ln.startswith("source:"):
            continue
        if re.match(r"^https?://\S+$", line, flags=re.IGNORECASE):
            continue
        out.append(raw)
    return "\n".join(out).strip()


def _strip_noise_answer_lines(answer: str) -> str:
    out: List[str] = []
    for raw in (answer or "").splitlines():
        line = _clean(raw)
        ln = _normalize_ascii(line)
        if not line:
            out.append(raw)
            continue
        if ln.startswith("reponse courte:") or ln.startswith("réponse courte:"):
            continue
        if ln.startswith("reponse directe:") or ln.startswith("réponse directe:"):
            continue
        if ln.startswith("options de precision:") or ln.startswith("options de précision:"):
            continue
        if ln.startswith("sur quel critere souhaitez-vous approfondir ?") or ln.startswith("quelle information serait la plus utile pour vous ?"):
            continue
        if re.match(r"^https?://\S+$", line, flags=re.IGNORECASE):
            continue
        out.append(raw)
    compact = "\n".join(out)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()
    return compact


def _enforce_freshness_and_availability(
    question: str,
    intent: str,
    answer: str,
    sources_by_layer: Dict[str, List[Any]],
    latest_consolidated_date: Optional[date] = None,
) -> Tuple[str, List[str]]:
    base_answer = _clean(answer or "")
    if not base_answer:
        return base_answer, []

    base_answer = _strip_existing_availability_block(base_answer)
    base_answer = _strip_existing_freshness_lines(base_answer)
    base_answer = _strip_noise_answer_lines(base_answer)

    latest_label = _latest_data_label(
        sources_by_layer=sources_by_layer,
        latest_consolidated_date=latest_consolidated_date,
    )
    freshness_line = ""
    if not _is_non_available_label(latest_label):
        freshness_line = f"{FRESHNESS_LINE_PREFIX} {latest_label}"
    source_line = _sources_with_dates_summary(sources_by_layer=sources_by_layer, max_items=2)

    suggestions = _availability_suggestions(question=question, intent=intent)[:3]
    availability_lines = [AVAILABILITY_BLOCK_HEADER] + [f"• {item}" for item in suggestions]

    rebuilt_lines: List[str] = []
    if freshness_line:
        rebuilt_lines.append(freshness_line)
    if source_line:
        rebuilt_lines.append(source_line)
    if rebuilt_lines:
        rebuilt_lines.append("")
    rebuilt_lines.append(base_answer)
    rebuilt_lines.append("")
    rebuilt_lines.extend(availability_lines)
    rebuilt = "\n".join(rebuilt_lines).strip()

    warnings = ["availability_footer_enforced"]
    if freshness_line:
        warnings.insert(0, "freshness_header_enforced")
    if source_line:
        warnings.insert(0, "sources_header_enforced")
    return rebuilt, warnings


def _enforce_concrete_data_answer(
    question: str,
    intent: str,
    answer: str,
    material: str,
    sources_by_layer: Dict[str, List[Any]],
    latest_consolidated_date: Optional[date] = None,
) -> Tuple[str, List[str]]:
    if not _looks_like_data_demand(question, intent):
        return answer, []

    requested_top = _requested_top_size(question, default_for_top=10, cap=20)
    line_limit = max(10, requested_top) if requested_top > 0 else 10
    concrete_lines = _collect_concrete_data_lines(
        question=question,
        answer=answer,
        material=material,
        sources_by_layer=sources_by_layer,
        limit=line_limit,
    )
    if concrete_lines:
        enforced = _build_concrete_data_answer(
            question=question,
            concrete_lines=concrete_lines,
            sources_by_layer=sources_by_layer,
            requested_top=requested_top,
        )
        warning = "concrete_data_structured"
        if _contains_vague_phrase(answer):
            warning = "concrete_data_enforced"
        warnings = [warning]
        if requested_top > 0 and len(concrete_lines) < requested_top:
            warnings.append("top_data_partial")
        return enforced, warnings

    latest_label = _latest_data_label(
        sources_by_layer=sources_by_layer,
        latest_consolidated_date=latest_consolidated_date,
    )
    no_data = _build_no_data_concrete_answer(
        latest_label=latest_label,
        sources_by_layer=sources_by_layer,
    )
    return no_data, ["concrete_data_unavailable"]


def _normalize_bold_only_markdown(text: str) -> str:
    """
    Nettoie les lignes de style '# **Titre**' / '- **Titre**' pour garder uniquement '**Titre**'.
    """
    if not text:
        return ""

    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        line = re.sub(r"^\s*#{1,6}\s+(\*\*.+?\*\*)\s*$", r"\1", line)
        line = re.sub(r"^\s*[-*+]\s+(\*\*.+?\*\*)\s*$", r"\1", line)
        line = re.sub(r"^\s*\d+\.\s+(\*\*.+?\*\*)\s*$", r"\1", line)
        line = re.sub(r"^\s*#{1,6}\s+(?=\*\*)", "", line)
        line = re.sub(r"^\s*[-*+]\s+(?=\*\*)", "", line)
        line = re.sub(r"^\s*#{1,6}\s+(.+?)\s*$", r"**\1**", line)
        cleaned_lines.append(line)

    normalized = "\n".join(cleaned_lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _draft_from(agent_outputs: List[Dict[str, Any]], agent_name: str) -> str:
    for out in agent_outputs:
        if out.get("agent") == agent_name:
            return _clean(out.get("draft"))
    return ""


def _is_insufficient(draft: str) -> bool:
    if not draft:
        return True
    d = draft.lower()
    triggers = [
        "aucun résultat",
        "je ne sais pas",
        "je suis désolé",
        "je n'ai pas accès",
        "je ne trouve pas",
        "pas d'information",
        "aucune donnée kpi sql",
        "je préfère être transparente",
        "je n'ai pas de signal web fiable",
        "je n'ai pas pu récupérer de sources web fiables",
        "insuffisant",
        "non déterminable",
        "erreur",
    ]
    return any(t in d for t in triggers)


def _contains_any(q: str, keywords: List[str]) -> bool:
    return any(k in q for k in keywords)


def _has_freshness_signal(q: str) -> bool:
    qn = _normalize_ascii(q or "")
    freshness_keywords = [
        "temps réel",
        "temps reel",
        "live",
        "en direct",
        "aujourd",
        "maintenant",
        "dernier",
        "derniere",
        "recent",
        "mis à jour",
        "mis a jour",
        "mise à jour",
        "mise a jour",
        "updated",
    ]
    return _contains_any(qn, freshness_keywords)


def _has_synthesis_signal(question: str) -> bool:
    q = _normalize_ascii(question or "")
    synth_keywords = [
        "synthese",
        "synthetise",
        "synthetiser",
        "resume",
        "resumer",
        "en bref",
        "tl;dr",
        "tldr",
        "version courte",
        "court",
    ]
    return _contains_any(q, synth_keywords)


def _question_token_count(question: str) -> int:
    q = _normalize_ascii(question or "")
    return len(re.findall(r"[a-z0-9]+", q))


def _history_has_context_anchor(history: Optional[List[dict]]) -> bool:
    for msg in (history or [])[-8:]:
        if str(msg.get("role", "")).lower().strip() != "user":
            continue
        content = _clean(str(msg.get("content", "")))
        if not content:
            continue
        if _is_scpi_context(content) or _is_darwin_query(content):
            return True
    return False


def _build_clarification_message(
    reason: str,
    intent: str,
    question: str,
    missing_fields: Optional[List[str]] = None,
) -> str:
    missing_fields = missing_fields or []

    if reason in {"ambiguous_short", "missing_scope"} and intent in {
        INTENT_FACTUAL_KPI,
        INTENT_COMPARISON,
        INTENT_REGULATORY,
    }:
        return (
            "Je peux te repondre precisement, mais j'ai besoin d'un cadrage rapide:\n"
            + "- quelle SCPI / societe veux-tu analyser ?\n"
            + "- quel indicateur exact (TD, TOF, WALT, collecte, PGA...) ?\n"
            + "- quelle periode (2025, 2026, T3...)?\n"
            + "Ensuite je lance la recherche internet en priorite."
        )

    if reason == "strategic_missing":
        return (
            "Je peux te proposer une reco exploitable, mais il me manque 3 infos:\n"
            + "- horizon d'investissement\n"
            + "- profil de risque\n"
            + "- montant (et idealement fiscalite IR/IS)\n"
            + "Dès que tu me les donnes, je priorise les sources web et je te fais une proposition nette."
        )

    q = _clean(question)
    return (
        "Je veux etre precis sur ta demande"
        + (f" \"{q}\"" if q else "")
        + ".\nPeux-tu me donner un peu plus de contexte pour que je lance une recherche web ciblee ?"
    )


def _clarification_needed(
    question: str,
    intent: str,
    history: Optional[List[dict]] = None,
) -> Optional[Dict[str, Any]]:
    q = _normalize_ascii(question or "")
    if not q:
        return {
            "needed": True,
            "reason": "empty_question",
            "message": _build_clarification_message("empty_question", intent, question),
        }

    token_count = _question_token_count(question)
    has_anchor = _is_scpi_context(question) or _is_darwin_query(question) or _history_has_context_anchor(history)
    ambiguous_short_terms = {"ok", "oui", "non", "et", "du coup", "?", "encore", "plus", "details", "detail"}
    if q in ambiguous_short_terms:
        return {
            "needed": True,
            "reason": "ambiguous_short",
            "message": _build_clarification_message("ambiguous_short", intent, question),
        }

    if token_count <= 2 and not has_anchor:
        return {
            "needed": True,
            "reason": "ambiguous_short",
            "message": _build_clarification_message("ambiguous_short", intent, question),
        }

    if intent in {INTENT_FACTUAL_KPI, INTENT_COMPARISON, INTENT_REGULATORY}:
        if token_count <= 3 and not has_anchor:
            return {
                "needed": True,
                "reason": "missing_scope",
                "message": _build_clarification_message("missing_scope", intent, question),
            }

    if intent == INTENT_STRATEGIC_ALLOCATION and token_count <= 3:
        return {
            "needed": True,
            "reason": "strategic_missing",
            "message": _build_clarification_message("strategic_missing", intent, question),
        }

    return None


def _is_darwin_query(question: str) -> bool:
    q = _normalize_ascii(question or "")
    return _contains_any(q, DARWIN_QUERY_KEYWORDS)


def _is_external_market_query(question: str) -> bool:
    q = _normalize_ascii(question or "")
    if not q:
        return False

    has_scpi_context = "scpi" in q or _contains_any(q, EXTERNAL_SCPI_BRAND_KEYWORDS)
    if not has_scpi_context:
        return False

    has_external_brand = _contains_any(q, EXTERNAL_SCPI_BRAND_KEYWORDS)
    has_market_signal = _contains_any(q, MARKET_COMPARISON_QUERY_KEYWORDS)
    has_darwin = _contains_any(q, DARWIN_QUERY_KEYWORDS)

    if has_external_brand and not has_darwin:
        return True
    if ("top" in q or "classement" in q or "palmares" in q) and "scpi" in q and not has_darwin:
        return True
    if has_market_signal and "scpi" in q and not has_darwin:
        return True
    return False


def _is_scpi_context(question: str) -> bool:
    q = _normalize_ascii(question or "")
    scpi_keywords = [
        "scpi",
        "re01",
        "pierre papier",
        "rendement scpi",
        "capitalisation scpi",
        "collecte scpi",
        "darwin invest",
        "concentration locative",
        "granularite geographique",
        "walt",
        "part europe hors france",
        "top 3 locataires",
    ]
    return _contains_any(q, scpi_keywords) or _contains_any(q, EXTERNAL_SCPI_BRAND_KEYWORDS)


def _has_amount_signal(question: str) -> bool:
    return shared_has_amount_signal(question)


def _has_horizon_signal(question: str) -> bool:
    return shared_has_horizon_signal(question)


def _has_objective_signal(question: str) -> bool:
    return shared_has_objective_signal(question)


def _should_force_strategic_intent(question: str) -> bool:
    return shared_should_force_strategic_intent(question)


def _parse_any_date(raw_value: Any) -> Optional[date]:
    value = _clean(str(raw_value or ""))
    if not value or value.lower() == "non_renseigne":
        return None
    normalized = _normalize_ascii(value)

    quarter_patterns = [
        r"(?:^|[\s_-])(t|q)\s*([1-4])[\s_-]*(20\d{2})(?:$|[\s_-])",
        r"(?:^|[\s_-])(20\d{2})[\s_-]*(t|q)\s*([1-4])(?:$|[\s_-])",
    ]
    for pattern in quarter_patterns:
        m = re.search(pattern, normalized)
        if not m:
            continue
        if m.group(1) in {"t", "q"}:
            quarter = int(m.group(2))
            year = int(m.group(3))
        else:
            year = int(m.group(1))
            quarter = int(m.group(3))
        month = quarter * 3
        day = 31 if month in {3, 12} else 30
        return date(year, month, day)

    date_formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%Y-%m",
        "%Y/%m",
    ]
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(value[:10], fmt)
            if fmt in {"%Y-%m", "%Y/%m"}:
                month = parsed.month
                day = 31 if month in {1, 3, 5, 7, 8, 10, 12} else 30
                return date(parsed.year, month, day)
            return parsed.date()
        except Exception:
            continue
    return None


def _extract_latest_consolidated_date(sources_by_layer: Dict[str, List[Any]]) -> Optional[date]:
    candidates: List[date] = []
    for layer in ("sql_kpi", "rag_market", "rag_darwin"):
        for item in sources_by_layer.get(layer, []) or []:
            if not isinstance(item, dict):
                continue
            for key in ("date", "as_of", "updated_at", "published_at"):
                parsed = _parse_any_date(item.get(key))
                if parsed:
                    candidates.append(parsed)
            provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
            parsed_prov = _parse_any_date(provenance.get("date"))
            if parsed_prov:
                candidates.append(parsed_prov)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            for key in ("date", "as_of", "updated_at", "published_at"):
                parsed_meta = _parse_any_date(metadata.get(key))
                if parsed_meta:
                    candidates.append(parsed_meta)
    return max(candidates) if candidates else None


def _is_within_freshness_threshold(latest: Optional[date], threshold_days: int) -> bool:
    if latest is None:
        return False
    age_days = (date.today() - latest).days
    return age_days <= max(0, threshold_days)


def _quarter_label(dt: date) -> str:
    quarter = ((dt.month - 1) // 3) + 1
    return f"T{quarter} {dt.year}"


def _build_scpi_consolidated_message(latest: Optional[date]) -> str:
    if latest is not None:
        return f"Données consolidées au {_quarter_label(latest)}"
    return SCPI_CONSOLIDATED_MESSAGE_FALLBACK


def _extract_first_number(raw_value: Any) -> Optional[float]:
    text = _clean(str(raw_value or ""))
    if not text:
        return None
    compact = text.replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    normalized = compact.replace(",", ".")
    normalized = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", "", normalized)
    m = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _is_percent_like(value: Any, unit: Any) -> bool:
    blob = _normalize_ascii(f"{value or ''} {unit or ''}")
    return "%" in blob or "pct" in blob or "pourcent" in blob


def _detect_intent(
    question: str,
    neutral_pure: bool = False,
    history: Optional[List[dict]] = None,
) -> str:
    return shared_detect_intent(
        question=question,
        neutral_pure=neutral_pure,
        history=history,
        is_external_market=_is_external_market_query(question),
        allow_darwin_specific=not neutral_pure,
        darwin_keywords=DARWIN_QUERY_KEYWORDS,
        tie_breaker=INTENT_TIE_BREAKER,
        tie_breaker_neutral=INTENT_TIE_BREAKER_NEUTRAL,
    )


def _source_compact_key(source: Any) -> str:
    if isinstance(source, str):
        src = _normalize_ascii(source)
        if src.startswith("http://") or src.startswith("https://"):
            return f"web|{src}"
        if src.endswith(".pdf") or src.endswith(".txt") or src.startswith("data/docs/"):
            return f"doc|{src}"
        return src

    if not isinstance(source, dict):
        return _normalize_ascii(str(source))

    source_type = _normalize_ascii(str(source.get("type") or source.get("tool") or ""))
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    provenance = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}

    # KPI SQL: on garde la granularité métrique.
    if source_type == "sql_kpi":
        metric = _normalize_ascii(str(source.get("metric") or ""))
        date_v = _normalize_ascii(str(source.get("date") or provenance.get("date") or ""))
        src = _normalize_ascii(str(source.get("source") or provenance.get("source") or ""))
        return f"sql_kpi|{metric}|{date_v}|{src}"

    url = _normalize_ascii(str(source.get("url") or source.get("href") or ""))
    if url.startswith("http://") or url.startswith("https://"):
        return f"web|{url}"

    doc_source = _normalize_ascii(
        str(
            source.get("source")
            or metadata.get("source")
            or metadata.get("file_name")
            or metadata.get("title")
            or ""
        )
    )
    if doc_source:
        return f"doc|{doc_source}"

    return _normalize_ascii(json.dumps(source, sort_keys=True, ensure_ascii=False, default=str))


def _normalize_sources(agent_outputs: List[Dict[str, Any]]) -> List[Any]:
    raw_sources: List[Any] = []
    for out in agent_outputs:
        raw_sources += (out.get("sources") or [])

    seen = set()
    normalized: List[Any] = []
    for source in raw_sources:
        if not source:
            continue
        key = _source_compact_key(source)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(source)
    return normalized


def _agent_to_layer(agent: str) -> Optional[str]:
    mapping = {
        "sql_kpi": "sql_kpi",
        "web": "rag_market",
        "core": "rag_darwin",
    }
    return mapping.get(agent)


def _normalize_sources_by_layer(agent_outputs: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
    by_layer: Dict[str, List[Any]] = {
        "sql_kpi": [],
        "rag_market": [],
        "rag_darwin": [],
    }
    seen: Dict[str, set] = {k: set() for k in by_layer}

    for out in agent_outputs:
        layer = _agent_to_layer(str(out.get("agent", "")).strip())
        if not layer:
            continue
        layered_sources: List[Any] = []
        if layer == "rag_market":
            meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
            web_results = meta.get("search_results")
            if isinstance(web_results, list):
                layered_sources.extend(web_results)
        layered_sources.extend(out.get("sources") or [])

        for source in layered_sources:
            if not source:
                continue
            key = _source_compact_key(source)
            if key in seen[layer]:
                continue
            seen[layer].add(key)
            by_layer[layer].append(source)
    return by_layer


def _agent_sequence_for_intent(
    intent: str,
    neutral_pure: bool = False,
    question: str = "",
) -> List[str]:
    external_market = _is_external_market_query(question)
    darwin_query = _is_darwin_query(question)

    if neutral_pure:
        if intent == INTENT_RAPPORT:
            return ["online", "sql_kpi", "rapport"]
        if intent in {INTENT_REGULATORY, INTENT_FACTUAL_KPI, INTENT_COMPARISON, INTENT_DARWIN_SPECIFIC}:
            return ["online", "sql_kpi"]
        return ["online", "sql_kpi"]

    if intent == INTENT_RAPPORT:
        return ["online", "sql_kpi", "core", "rapport"]
    if intent == INTENT_REGULATORY:
        return ["online", "sql_kpi", "core"]
    if intent == INTENT_FACTUAL_KPI:
        return ["online", "sql_kpi"] if external_market else ["online", "sql_kpi", "core"]
    if intent == INTENT_COMPARISON:
        if external_market and not darwin_query:
            return ["online", "sql_kpi"]
        return ["online", "core", "sql_kpi"] if darwin_query else ["online", "sql_kpi", "core"]
    if intent == INTENT_DARWIN_SPECIFIC:
        return ["online", "core", "sql_kpi"]
    # STRATEGIC_ALLOCATION: web prioritaire hors contexte Darwin explicite.
    if darwin_query:
        return ["online", "core", "sql_kpi"]
    if PREFER_LIVE_WEB_BY_DEFAULT:
        return ["online", "sql_kpi", "core"]
    return ["sql_kpi", "core", "online"]


def _run_agent(
    agent_name: str,
    question: str,
    history: List[dict],
    routing_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if agent_name == "sql_kpi":
        return {"agent": "sql_kpi", **ask_sql_kpi(question, history=history)}
    if agent_name == "core":
        return {"agent": "core", **ask_core(question, history=history)}
    if agent_name == "online":
        return {
            "agent": "web",
            **ask_online(
                question,
                history=history,
                routing_override=routing_override if isinstance(routing_override, dict) else None,
            ),
        }
    if agent_name == "rapport":
        raise ValueError("L'agent 'rapport' dépend des matériaux core/web et doit être exécuté séparément.")
    raise ValueError(f"Agent inconnu: {agent_name}")


def _run_core_online_parallel(
    question: str,
    history: List[dict],
    sequence: List[str],
    online_routing_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Exécute core + online en parallèle et renvoie les sorties dans l'ordre voulu.
    """
    ordered = [name for name in sequence if name in {"core", "online"}]
    if len(ordered) < 2:
        if not ordered:
            return []
        single = ordered[0]
        return [
            _run_agent(
                single,
                question,
                history,
                routing_override=online_routing_override if single == "online" else None,
            )
        ]

    outputs_by_name: Dict[str, Dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        for name in ordered:
            override = online_routing_override if name == "online" else None
            futures[executor.submit(_run_agent, name, question, history, override)] = name
        for future, name in futures.items():
            try:
                outputs_by_name[name] = future.result()
            except Exception as exc:
                outputs_by_name[name] = {
                    "agent": "web" if name == "online" else "core",
                    "draft": f"❌ Erreur agent {name}: {str(exc)}",
                    "sources": [],
                    "meta": {"tool": name, "error": str(exc)},
                }

    return [outputs_by_name[name] for name in ordered if name in outputs_by_name]


def _history_to_text(history: Optional[List[dict]], max_items: int = 8) -> str:
    history = history or []
    if not history:
        return "Aucun historique."
    lines = []
    for msg in history[-max_items:]:
        role = str(msg.get("role", "user")).upper()
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "Aucun historique pertinent."


def _source_count(out: Dict[str, Any]) -> int:
    sources = out.get("sources") or []
    return len(sources)


def _web_has_live_signal(out: Dict[str, Any]) -> bool:
    if out.get("agent") != "web":
        return False
    meta = out.get("meta") or {}
    provider = str(meta.get("provider", "")).lower()
    actual_results = int(meta.get("actual_results", 0) or 0)
    has_sources = _source_count(out) > 0
    return actual_results > 0 or (has_sources and provider not in {"none", "error", ""})


def _quality_score(out: Dict[str, Any], intent: str, neutral_pure: bool = False) -> float:
    draft = _clean(out.get("draft"))
    if not draft:
        return -100.0

    score = 0.0
    if not _is_insufficient(draft):
        score += 20.0
    else:
        score -= 25.0

    # Favorise les réponses substantielles sans sur-pondérer la longueur.
    score += min(len(draft) / 80.0, 15.0)
    score += min(_source_count(out) * 2.5, 15.0)

    agent = out.get("agent")
    meta = out.get("meta") or {}
    provider = str(meta.get("provider", "")).lower()

    if neutral_pure and agent == "core":
        score -= 40.0

    if intent == INTENT_REGULATORY:
        if agent == "web":
            score += 14.0
            if provider and provider not in {"none", "error"}:
                score += 6.0
            if int(meta.get("actual_results", 0) or 0) > 0:
                score += 10.0
    elif intent == INTENT_FACTUAL_KPI:
        if agent == "sql_kpi":
            score += 14.0
        if agent == "web" and int(meta.get("actual_results", 0) or 0) > 0:
            score += 5.0
        if agent == "core":
            score += 2.0
    elif intent == INTENT_DARWIN_SPECIFIC:
        if agent == "core":
            score += 12.0
        if agent == "sql_kpi":
            score += 4.0
    elif intent == INTENT_COMPARISON:
        if agent == "core":
            score += 7.0
        if agent == "web":
            score += 7.0
        if agent == "sql_kpi":
            score += 4.0
    else:
        if agent == "core":
            score += 8.0
        if agent == "sql_kpi":
            score += 6.0
        if agent == "web" and int(meta.get("actual_results", 0) or 0) > 0:
            score += 4.0

    if "erreur" in draft.lower():
        score -= 15.0
    return score


def _best_material(
    agent_outputs: List[Dict[str, Any]],
    intent: str,
    neutral_pure: bool = False,
    question: str = "",
) -> Dict[str, Any]:
    if not agent_outputs:
        return {}

    candidates = list(agent_outputs)
    if _is_external_market_query(question):
        no_core_market = [out for out in candidates if out.get("agent") != "core"]
        if no_core_market:
            candidates = no_core_market

    if neutral_pure:
        no_core = [out for out in candidates if out.get("agent") != "core"]
        if no_core:
            candidates = no_core

    sql_candidates = [out for out in candidates if out.get("agent") == "sql_kpi"]
    web_candidates = [out for out in candidates if out.get("agent") == "web"]
    core_candidates = [out for out in candidates if out.get("agent") == "core"]

    prefer_web = (
        _is_external_market_query(question)
        or intent in {INTENT_FACTUAL_KPI, INTENT_COMPARISON, INTENT_REGULATORY, INTENT_DARWIN_SPECIFIC}
        or (
            intent == INTENT_STRATEGIC_ALLOCATION
            and PREFER_LIVE_WEB_BY_DEFAULT
            and not _is_darwin_query(question)
        )
    )

    usable_web = [
        out for out in web_candidates
        if not _is_insufficient(_clean(out.get("draft")))
    ]
    live_web_candidates = [
        out for out in usable_web if _web_has_live_signal(out)
    ]
    if prefer_web:
        if live_web_candidates:
            ranked_web = sorted(
                live_web_candidates,
                key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
                reverse=True,
            )
            return ranked_web[0]
        if usable_web:
            ranked_web = sorted(
                usable_web,
                key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
                reverse=True,
            )
            return ranked_web[0]

    if intent == INTENT_DARWIN_SPECIFIC and not neutral_pure:
        usable_core = [out for out in core_candidates if not _is_insufficient(_clean(out.get("draft")))]
        if usable_core:
            ranked_core = sorted(
                usable_core,
                key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
                reverse=True,
            )
            return ranked_core[0]

    if intent == INTENT_FACTUAL_KPI:
        usable_sql = [out for out in sql_candidates if not _is_insufficient(_clean(out.get("draft")))]
        if usable_sql:
            ranked_sql = sorted(
                usable_sql,
                key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
                reverse=True,
            )
            return ranked_sql[0]

    if live_web_candidates:
        ranked_web = sorted(
            live_web_candidates,
            key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
            reverse=True,
        )
        return ranked_web[0]

    if intent == INTENT_REGULATORY:
        if usable_web:
            ranked_web = sorted(
                usable_web,
                key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
                reverse=True,
            )
            return ranked_web[0]

    ranked = sorted(
        candidates,
        key=lambda out: _quality_score(out, intent, neutral_pure=neutral_pure),
        reverse=True,
    )
    return ranked[0]


def _requires_live_web(
    intent: str,
    force_agent: Optional[str],
    sequence: List[str],
    question: str,
) -> bool:
    if not STRICT_REALTIME_GUARD:
        return False
    forced = (force_agent or "").strip().lower()
    if forced == "core":
        return False
    if forced in {"sql_kpi", "kpi"}:
        return False
    if forced in {"web", "online"}:
        return True
    if _is_external_market_query(question):
        return True
    if intent == INTENT_REGULATORY:
        return True
    if forced == "rapport" and _has_freshness_signal((question or "").lower()):
        return True
    # Evite les refus strict-realtime abusifs sur les intents non réglementaires.
    if intent in {
        INTENT_STRATEGIC_ALLOCATION,
        INTENT_FACTUAL_KPI,
        INTENT_COMPARISON,
        INTENT_DARWIN_SPECIFIC,
    }:
        return False
    return False


def _strict_realtime_block_message(question: str, web_out: Dict[str, Any]) -> str:
    meta = web_out.get("meta") or {}
    provider = str(meta.get("provider", "")).strip()
    warning = str(meta.get("warning", "")).strip()
    diag: List[str] = []
    if provider and provider.lower() not in {"none", "error"}:
        diag.append(f"provider: {provider}")
    if warning:
        diag.append(f"raison: {warning}")
    suffix = f" ({', '.join(diag)})" if diag else ""
    q = (question or "").strip()
    q_prefix = f"pour ta question \"{q}\", " if q else ""
    return (
        "Je préfère être transparente: "
        + q_prefix
        + "je n'ai pas de signal web fiable en temps réel"
        + suffix
        + ".\n"
        + "Relance dans quelques secondes. Si tu veux, je peux aussi répondre en mode estimation "
        + "(non temps réel), clairement indiqué."
    )


def _out_by_agent(agent_outputs: List[Dict[str, Any]], agent_name: str) -> Dict[str, Any]:
    for out in agent_outputs:
        if out.get("agent") == agent_name:
            return out
    return {}


def _extract_web_sources(agent_outputs: List[Dict[str, Any]], max_items: int = 8) -> List[str]:
    urls: List[str] = []
    for out in agent_outputs:
        if out.get("agent") != "web":
            continue
        for source in out.get("sources") or []:
            if isinstance(source, dict):
                src = _clean(
                    str(source.get("url") or source.get("href") or source.get("source") or "")
                )
            else:
                src = _clean(str(source))
            if not src.startswith(("http://", "https://")):
                continue
            if src in urls:
                continue
            urls.append(src)
            if len(urls) >= max_items:
                return urls
    return urls


def _build_persona_material(
    agent_outputs: List[Dict[str, Any]],
    intent: str,
    neutral_pure: bool = False,
    question: str = "",
) -> str:
    best = _best_material(
        agent_outputs,
        intent=intent,
        neutral_pure=neutral_pure,
        question=question,
    )
    best_draft = _clean(best.get("draft"))

    sql_out = _out_by_agent(agent_outputs, "sql_kpi")
    core_out = _out_by_agent(agent_outputs, "core")
    web_out = _out_by_agent(agent_outputs, "web")
    sql_draft = _clean(sql_out.get("draft"))
    core_draft = _clean(core_out.get("draft"))
    web_draft = _clean(web_out.get("draft"))

    has_sql_material = bool(sql_draft) and not _is_insufficient(sql_draft)
    allow_core_material = not _is_external_market_query(question)
    has_core_material = (
        allow_core_material
        and (not neutral_pure)
        and bool(core_draft)
        and not _is_insufficient(core_draft)
    )
    has_web_material = bool(web_draft) and not _is_insufficient(web_draft)
    has_web_live_signal = has_web_material and _web_has_live_signal(web_out)
    prefer_web_first = (
        _is_external_market_query(question)
        or intent in {INTENT_FACTUAL_KPI, INTENT_COMPARISON, INTENT_REGULATORY}
        or (intent == INTENT_STRATEGIC_ALLOCATION and not _is_darwin_query(question))
    )

    parts: List[str] = []
    if (not agent_outputs) and _clean(question):
        parts.append("QUESTION CLIENT:\n" + _clean(question))

    if intent == INTENT_FACTUAL_KPI:
        if has_web_material:
            label = "RAG MARCHE (temps reel):" if has_web_live_signal else "RAG MARCHE:"
            parts.append(label + "\n" + web_draft)
        if has_sql_material:
            parts.append("BASE SQL KPI:\n" + sql_draft)
        if has_core_material:
            parts.append("RAG DARWIN:\n" + core_draft)
    elif intent == INTENT_DARWIN_SPECIFIC:
        if has_web_material:
            label = "RAG MARCHE (temps reel):" if has_web_live_signal else "RAG MARCHE:"
            parts.append(label + "\n" + web_draft)
        if has_core_material:
            parts.append("RAG DARWIN:\n" + core_draft)
        if has_sql_material:
            parts.append("COMPLEMENT SQL KPI:\n" + sql_draft)
    # Priorité explicite au web quand le signal live est exploitable.
    elif intent == INTENT_REGULATORY and has_web_live_signal:
        parts.append("RAG MARCHE (temps reel):\n" + web_draft)
        if has_sql_material:
            parts.append("BASE SQL KPI:\n" + sql_draft)
        if has_core_material:
            parts.append("RAG DARWIN:\n" + core_draft)
    elif intent in {INTENT_REGULATORY, INTENT_COMPARISON} and has_web_material:
        parts.append("RAG MARCHE (recente):\n" + web_draft)
        if has_sql_material:
            parts.append("BASE SQL KPI:\n" + sql_draft)
        if has_core_material:
            parts.append("RAG DARWIN:\n" + core_draft)
    elif intent == INTENT_STRATEGIC_ALLOCATION and has_core_material:
        if _is_darwin_query(question):
            parts.append("RAG DARWIN:\n" + core_draft)
            if has_sql_material:
                parts.append("COMPLEMENT SQL KPI:\n" + sql_draft)
            if has_web_material:
                parts.append("COMPLEMENT RAG MARCHE (non confirme temps reel):\n" + web_draft)
        else:
            if has_web_material:
                label = "RAG MARCHE (temps reel):" if has_web_live_signal else "RAG MARCHE:"
                parts.append(label + "\n" + web_draft)
            if has_sql_material:
                parts.append("COMPLEMENT SQL KPI:\n" + sql_draft)
            parts.append("COMPLEMENT RAG DARWIN:\n" + core_draft)
    else:
        if prefer_web_first and has_web_material:
            label = "RAG MARCHE (temps reel):" if has_web_live_signal else "RAG MARCHE:"
            parts.append(label + "\n" + web_draft)
        if has_sql_material:
            parts.append("BASE SQL KPI:\n" + sql_draft)
        if (not prefer_web_first) and has_web_material:
            label = "RAG MARCHE (temps reel):" if has_web_live_signal else "RAG MARCHE:"
            parts.append(label + "\n" + web_draft)
        if has_core_material:
            parts.append("RAG DARWIN:\n" + core_draft)

    if not parts and best_draft:
        parts.append("MATIERE PRINCIPALE:\n" + best_draft)

    web_sources = _extract_web_sources(agent_outputs)
    if web_sources:
        src_block = "\n".join([f"- {url}" for url in web_sources])
        parts.append("SOURCES WEB DISPONIBLES:\n" + src_block)

    return "\n\n".join(parts).strip()


def _rewrite_with_persona(question: str, history: List[dict], draft: str) -> str:
    if not draft:
        return ""
    if _finalizer_llm is None:
        return draft

    prompt = DARWIN_PERSONA_PROMPT.format(
        question=question,
        draft=draft,
        history=_history_to_text(history),
    )
    try:
        return _clean(_finalizer_llm.invoke(prompt).content)
    except Exception:
        return draft


def _synthesize_fallback_text(text: str, max_points: int = 5) -> str:
    if not text:
        return ""

    raw_lines = [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines()]
    kept: List[str] = []
    seen = set()
    skip_prefixes = (
        "scoring deterministe",
        "score breakdown",
        "ponderations",
        "donnees utilisees",
        "dates",
        "mode audit / detail",
        "sources web disponibles",
        "base sql kpi:",
        "rag marche",
        "rag darwin",
        "- moteur:",
        "- profil retenu:",
        "- scores:",
        "- ecart de confiance:",
        "- note version:",
        "- changelog:",
    )

    for line in raw_lines:
        if not line:
            continue
        lowered = _normalize_ascii(line)
        if lowered.startswith(skip_prefixes):
            continue
        if lowered.startswith("- moteur:") or lowered.startswith("- version"):
            continue
        if lowered.startswith("http://") or lowered.startswith("https://"):
            continue
        normalized = re.sub(r"^[\-\*\d\.\)\s]+", "", line).strip()
        if not normalized:
            continue
        if len(normalized) > 220:
            normalized = normalized[:217].rstrip() + "..."
        key = _normalize_ascii(normalized)
        if key in seen:
            continue
        seen.add(key)
        kept.append(normalized)
        if len(kept) >= max_points:
            break

    if not kept:
        return "Je peux te faire une synthèse dès que j'ai plus de matière exploitable."

    lines = ["**Synthese rapide**"]
    for item in kept:
        lines.append(f"- {item}")
    return "\n".join(lines).strip()


def _synthesize_with_llm(question: str, material: str) -> str:
    if not material:
        return ""
    if _finalizer_llm is None:
        return _synthesize_fallback_text(material)

    prompt = f"""
Tu es un conseiller patrimonial expert.
Tu dois produire une synthèse courte et claire en français.

QUESTION:
{question}

MATIERE:
{material}

Consignes:
- Réponse en 4 à 6 points maximum.
- Priorise les faits, chiffres et dates utiles.
- Style conversationnel, direct, naturel.
- Pas de ton e-mail.
- Pas de markdown complexe: juste du texte + puces.
- Si une donnée est incertaine, indique-le en 1 phrase.
"""
    try:
        out = _clean(_finalizer_llm.invoke(prompt).content)
        return out or _synthesize_fallback_text(material)
    except Exception:
        return _synthesize_fallback_text(material)


def _safe_json_dict(raw: str) -> Dict[str, Any]:
    text = _clean(raw or "")
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _contract_intent_from_internal(question: str, intent: str) -> str:
    if _is_top_like_question(question):
        return CGP_INTENT_TOP
    if _should_force_strategic_intent(question):
        return CGP_INTENT_STRATEGIE
    if intent in {INTENT_STRATEGIC_ALLOCATION, INTENT_DARWIN_SPECIFIC}:
        return CGP_INTENT_STRATEGIE
    q = _normalize_ascii(question or "")
    if _contains_any(q, ["allocation", "strategie", "stratégie", "repartition", "objectif"]) and (
        _has_horizon_signal(question) or _has_amount_signal(question)
    ):
        return CGP_INTENT_STRATEGIE
    return CGP_INTENT_KPI


def _extract_contract_kpi_target(question: str) -> str:
    qn = _normalize_ascii(question or "")
    if not qn:
        return "none"
    if "tof" in qn or "taux d'occupation financier" in qn or "occupation financier" in qn:
        return "tof"
    if "walt" in qn or "duree moyenne des baux" in qn or "durée moyenne des baux" in qn:
        return "walt"
    if "frais" in qn or "commission" in qn:
        return "frais"
    if "prix de part" in qn or "valeur de part" in qn or "souscription" in qn or re.search(r"\bprix\b", qn):
        return "prix_part"
    if "capitalisation" in qn or "encours" in qn:
        return "capitalisation"
    if "collecte" in qn:
        return "collecte"
    if "rendement" in qn or "tdvm" in qn or "taux de distribution" in qn or re.search(r"\btd\b", qn):
        return "td"
    return "none"


def _extract_contract_period_from_text(text: str) -> str:
    qn = _normalize_ascii(text or "")
    if not qn:
        return ""
    year_match = re.search(r"\b(20\d{2})\b", qn)
    if year_match:
        return year_match.group(1)
    quarter_match = re.search(r"\b([tq][1-4])\s*(20\d{2})?\b", qn)
    if quarter_match:
        period = quarter_match.group(1).upper().replace("Q", "T")
        year = quarter_match.group(2) or ""
        return f"{period} {year}".strip()
    if "trimestre" in qn:
        m = re.search(r"\b([1-4])\s*e?\s*trimestre\b", qn)
        if m:
            return f"T{m.group(1)}"
    return ""


def _question_has_entity_for_kpi(question: str) -> bool:
    qn = _normalize_ascii(question or "")
    if not qn:
        return False
    for brand in DARWIN_QUERY_KEYWORDS + EXTERNAL_SCPI_BRAND_KEYWORDS:
        if brand and brand in qn:
            return True
    m = re.search(r"\bscpi\s+([a-z0-9][a-z0-9\-]{2,})\b", qn)
    if m:
        token = _clean(m.group(1))
        if token and not re.fullmatch(r"\d{2,4}", token):
            return True
    return False


def _collect_contract_clarification_questions(
    question: str,
    contract_intent: str,
    kpi_target: str,
) -> List[str]:
    qn = _normalize_ascii(question or "")
    period = _extract_contract_period_from_text(question)
    questions: List[str] = []

    if contract_intent == CGP_INTENT_TOP:
        has_criteria = kpi_target != "none" or _contains_any(qn, ["critere", "critère", "metrique", "métrique"])
        if not has_criteria:
            questions.append("Quel KPI principal veux-tu pour ce top (td, tof, walt, frais, prix_part, capitalisation, collecte) ?")
        if not period:
            questions.append("Sur quelle période veux-tu le classement (ex: 2024, 2025, T3 2025) ?")
        return questions[:2]

    if contract_intent == CGP_INTENT_STRATEGIE:
        if not re.search(r"\b\d+\s*(ans?|an|mois)\b", qn):
            questions.append("Quel est ton horizon d'investissement (en années) ?")
        if not re.search(r"\b(ir|is|fiscalite|fiscalité)\b", qn):
            questions.append("Quel est ton régime fiscal (IR ou IS) ?")
        if not _contains_any(qn, ["prudent", "equilibre", "équilibré", "dynamique", "risque"]):
            questions.append("Quel est ton profil de risque (prudent, équilibré, dynamique) ?")
        if len(questions) < 3 and not _question_has_entity_for_kpi(question):
            questions.append("Souhaites-tu une sélection nominative de 3 à 5 SCPI ?")
        return questions[:3]

    if contract_intent == CGP_INTENT_KPI and not _question_has_entity_for_kpi(question):
        return ["Quelle SCPI ou quelle entité veux-tu analyser précisément ?"]

    return []


def _extract_first_numeric_signal(text: str) -> str:
    raw = _clean(text or "")
    if not raw:
        return ""
    m = re.search(r"\d+(?:[.,]\d+)?\s*%", raw)
    if m:
        return m.group(0)
    m = re.search(r"\d+(?:[.,]\d+)?\s*(?:€|euros?)", raw, flags=re.IGNORECASE)
    if m:
        return m.group(0)
    m = re.search(r"\d+(?:[.,]\d+)?\s*(?:k|m)\b", raw, flags=re.IGNORECASE)
    if m:
        return m.group(0)
    m = re.search(r"\d+(?:[.,]\d+)?", raw)
    return m.group(0) if m else ""


def _looks_like_year_token(value: str) -> bool:
    token = _clean(value or "")
    if not token:
        return False
    compact = re.sub(r"\s+", "", token)
    return bool(re.fullmatch(r"20\d{2}", compact))


def _extract_metric_numeric_signal(text: str, kpi_target: str) -> str:
    raw = _clean(text or "")
    if not raw:
        return ""
    metric = _metric_label_from_target(kpi_target)

    if metric in {"td", "tof", "frais"}:
        m = re.search(r"\d+(?:[.,]\d+)?\s*%", raw)
        return m.group(0) if m else ""

    if metric == "walt":
        m = re.search(r"\d+(?:[.,]\d+)?\s*(?:ans?|annees?)", raw, flags=re.IGNORECASE)
        if m:
            return m.group(0)
        m = re.search(r"\d+(?:[.,]\d+)?\s*%", raw)
        return m.group(0) if m else ""

    if metric in {"prix_part", "capitalisation", "collecte"}:
        m = re.search(r"\d+(?:[.,]\d+)?\s*(?:€|euros?)", raw, flags=re.IGNORECASE)
        if m:
            return m.group(0)
        m = re.search(r"\d+(?:[.,]\d+)?\s*(?:k|m|md)\b", raw, flags=re.IGNORECASE)
        if m:
            return m.group(0)
        return ""

    return _extract_first_numeric_signal(raw)


def _is_unreliable_metric_value(value: str, kpi_target: str) -> bool:
    token = _clean(value or "")
    if not token:
        return True
    if _looks_like_year_token(token):
        return True

    metric = _metric_label_from_target(kpi_target)
    norm = _normalize_ascii(token)

    if metric in {"td", "tof", "frais"}:
        return "%" not in token
    if metric == "walt":
        return (not re.search(r"\bans?\b", norm)) and ("%" not in token)
    if metric in {"prix_part", "capitalisation", "collecte"}:
        has_currency = bool(re.search(r"(€|euros?)", token, flags=re.IGNORECASE))
        has_scale = bool(re.search(r"\b(k|m|md)\b", norm))
        return not (has_currency or has_scale)
    return False


def _source_domain_from_values(url: str, source: str) -> str:
    domain = _source_label_from_url(url)
    if domain:
        return domain
    candidate = _clean(source).lower().lstrip("www.")
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", candidate):
        return candidate
    return candidate or "source"


def _collect_sources_for_contract(
    sources_by_layer: Dict[str, List[Any]],
    max_items: int = FINALIZER_STRUCTURED_MAX_SOURCES,
) -> List[Dict[str, str]]:
    packed: List[Dict[str, str]] = []
    seen = set()

    def _push(source: str, url: str, date_value: str, snippet: str, title: str = "") -> None:
        if len(packed) >= max_items:
            return
        source_label = _clean(source) or "source"
        source_url = _to_url(url)
        domain = _source_domain_from_values(source_url, source_label)
        date_txt = _clean(date_value) or "n/d"
        snip = _clean(snippet)
        title_txt = _clean(title)
        if len(snip) > 240:
            snip = snip[:237].rstrip() + "..."
        if len(title_txt) > 180:
            title_txt = title_txt[:177].rstrip() + "..."
        key = _normalize_ascii(f"{source_label}|{domain}|{date_txt}|{title_txt[:60]}|{snip[:60]}")
        if key in seen:
            return
        seen.add(key)
        packed.append(
            {
                "source": source_label,
                "domain": domain,
                "date": date_txt,
                "title": title_txt,
                "snippet": snip,
                "url": source_url,
            }
        )

    for layer in ("rag_market", "sql_kpi", "rag_darwin"):
        for item in (sources_by_layer.get(layer) or []):
            if len(packed) >= max_items:
                break
            if isinstance(item, str):
                _push(source=item, url=item, date_value="", snippet="", title="")
                continue
            if not isinstance(item, dict):
                continue
            source_name = (
                item.get("source")
                or item.get("source_domain")
                or item.get("title")
                or item.get("metric")
                or layer
            )
            _push(
                source=str(source_name or layer),
                url=str(item.get("url") or item.get("href") or ""),
                date_value=str(item.get("date") or item.get("updated_at") or item.get("published_at") or ""),
                snippet=str(item.get("snippet") or item.get("body") or item.get("value") or ""),
                title=str(item.get("title") or ""),
            )
    return packed


def _metric_label_from_target(kpi_target: str) -> str:
    labels = {
        "td": "td",
        "tof": "tof",
        "walt": "walt",
        "frais": "frais",
        "prix_part": "prix_part",
        "capitalisation": "capitalisation",
        "collecte": "collecte",
        "none": "kpi",
    }
    return labels.get(kpi_target, "kpi")


def _dedupe_keep_order(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        txt = _clean(value)
        if not txt:
            continue
        key = _normalize_ascii(txt)
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
    return out


def _sanitize_line_no_url(line: str) -> str:
    raw = _clean(line or "")
    if not raw:
        return ""

    def _replace_url(match: re.Match[str]) -> str:
        url = _clean(match.group(0))
        return _source_label_from_url(url) or "source"

    raw = re.sub(r"https?://[^\s\)\]]+", _replace_url, raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _coerce_questions_by_intent(contract_intent: str, questions: List[str]) -> List[str]:
    deduped = _dedupe_keep_order([_sanitize_line_no_url(q) for q in questions])
    if contract_intent == CGP_INTENT_STRATEGIE:
        return deduped[:3]
    if contract_intent == CGP_INTENT_TOP:
        return deduped[:2]
    return deduped[:1]


def _looks_like_site_label(name: str) -> bool:
    txt = _clean(name)
    if not txt:
        return True
    norm = _normalize_ascii(txt)
    if re.search(r"\b[a-z0-9.-]+\.(?:fr|com|org|net)\b", norm):
        return True
    if norm in {
        "france-scpi",
        "francescpi",
        "france scpi",
        "la-centrale-scpi",
        "la centrale des scpi",
        "centrale-scpi",
        "centraledesscpi",
        "centrale des scpi",
        "meilleurescpi",
        "meilleure scpi",
        "pierre-papier",
        "pierrepapier",
        "aspim",
        "amf",
        "capital",
        "reddit",
        "les-echos",
        "les echos",
        "finance-heros",
        "finance heros",
        "primaliance",
        "louve invest",
        "epargnoo",
        "homunity",
        "avenue des investisseurs",
        "france transactions",
    }:
        return True
    return False


def _is_valid_scpi_candidate_name(name: str) -> bool:
    candidate = _clean(name)
    if not candidate:
        return False
    norm = _normalize_ascii(candidate)
    if re.fullmatch(r"\d{1,4}", norm):
        return False
    if re.fullmatch(r"20\d{2}", norm):
        return False
    if re.match(r"^20\d{2}\b", norm):
        return False
    if len(re.sub(r"[^a-z0-9]", "", norm)) < 3:
        return False
    if _looks_like_site_label(candidate):
        return False
    banned = {
        "scpi",
        "top",
        "question",
        "questions",
        "faq",
        "classement",
        "comparatif",
        "comparaison",
        "rendement",
        "performance",
        "prix",
        "part",
        "kpi",
        "td",
        "tof",
        "walt",
        "actualite",
        "analyse",
        "guide",
        "investir",
        "investissement",
    }
    if norm in banned:
        return False
    words = [w for w in re.split(r"[\s\-]+", norm) if w]
    if any(re.fullmatch(r"20\d{2}", w) for w in words):
        return False
    if words and words[0] in {"guide", "question", "questions", "classement", "top"}:
        return False
    generic_words = {
        "scpi",
        "question",
        "questions",
        "faq",
        "guide",
        "guides",
        "comparatif",
        "classement",
        "top",
        "actualite",
        "actualites",
        "investir",
        "guide",
        "general",
        "generale",
        "g",
    }
    if words and all(w in generic_words for w in words):
        return False
    generic_count = sum(1 for w in words if w in generic_words)
    if generic_count >= max(1, len(words) - 1):
        return False
    if len(words) <= 2 and any(w in {"question", "questions", "faq", "guide", "guides"} for w in words):
        return False
    if len(words) <= 4 and "scpi" in words and any(w in {"question", "questions", "faq", "guide", "guides", "general", "g"} for w in words):
        return False
    if len(words) <= 2 and "scpi" in words:
        return False
    return True


def _extract_scpi_name_from_source_record(src: Dict[str, str]) -> str:
    title = _clean(src.get("title") or "")
    snippet = _clean(src.get("snippet") or "")
    source = _clean(src.get("source") or "")
    blob = _clean(f"{title} {snippet} {source}")
    if not blob:
        return ""

    blob_norm = _normalize_ascii(blob)

    known_brands = list(dict.fromkeys([*EXTERNAL_SCPI_BRAND_KEYWORDS, "re01"]))
    known_brands_sorted = sorted(known_brands, key=lambda x: len(str(x or "")), reverse=True)
    for brand in known_brands_sorted:
        bn = _normalize_ascii(brand)
        if not bn or bn not in blob_norm:
            continue
        if bn == "re01":
            return "RE01"
        return " ".join([part.capitalize() for part in _clean(brand).split()])

    patterns = [
        r"\bscpi\s+([a-z0-9][a-z0-9'’\-\s]{1,40})",
        r"\b([a-z0-9][a-z0-9'’\-\s]{1,32})\s*\(\s*scpi\s*\)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, blob, flags=re.IGNORECASE):
            candidate = _clean(match.group(1))
            if not candidate:
                continue
            candidate = re.split(r"\s+(?:de|du|des|en|avec|sur|pour|et)\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
            candidate = re.sub(r"[^a-zA-Z0-9'’\-\s]", "", candidate).strip(" -")
            if not candidate:
                continue
            if len(candidate.split()) > 4:
                continue
            formatted = " ".join([part.capitalize() for part in candidate.split()])
            if _is_valid_scpi_candidate_name(formatted):
                return formatted

    raw_url = _clean(src.get("url") or "")
    if raw_url:
        url = _normalize_ascii(raw_url)
        slug_hits = re.findall(r"(?:/scpi/|scpi-)([a-z0-9][a-z0-9\-]{2,40})", url)
        for slug in slug_hits:
            candidate = " ".join([part.capitalize() for part in _clean(slug).split("-") if part])
            if _is_valid_scpi_candidate_name(candidate):
                return candidate

    return ""


def _extract_top_items_from_sources(
    question: str,
    kpi_target: str,
    source_pack: List[Dict[str, str]],
    limit: int = 10,
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    metric = _metric_label_from_target(kpi_target)
    fallback_scpi_names = _extract_scpi_candidates_from_sources(source_pack, limit=max(6, limit * 2))
    fallback_idx = 0
    for src in source_pack:
        if len(items) >= max(1, min(20, limit)):
            break

        scpi_name = _extract_scpi_name_from_source_record(src)
        if (not scpi_name) and fallback_idx < len(fallback_scpi_names):
            scpi_name = fallback_scpi_names[fallback_idx]
            fallback_idx += 1
        if not scpi_name:
            continue

        blob = f"{src.get('title', '')} {src.get('snippet', '')} {src.get('source', '')}"
        value = _extract_metric_numeric_signal(blob, kpi_target=kpi_target)
        period = _extract_contract_period_from_text(blob) or _clean(src.get("date") or "n/d")
        if _is_unreliable_metric_value(value, kpi_target=kpi_target):
            continue
        items.append(
            {
                "name": _sanitize_line_no_url(scpi_name),
                "value": value,
                "metric": metric,
                "period": period or "n/d",
                "source_domain": _clean(src.get("domain") or "source"),
            }
        )

    if not items:
        return [
            {
                "name": "Non trouvé dans les sources fournies",
                "value": "",
                "metric": metric,
                "period": "n/d",
                "source_domain": "sources fournies",
            }
        ]
    return items


def _build_kpi_response_from_sources(
    question: str,
    kpi_target: str,
    answer_draft: str,
    source_pack: List[Dict[str, str]],
) -> Tuple[Dict[str, str], str]:
    metric = _metric_label_from_target(kpi_target)
    search_space = [answer_draft] + [str(src.get("snippet") or "") for src in source_pack]
    value = ""
    period = ""
    source_domain = "sources fournies"

    for idx, text in enumerate(search_space):
        candidate_value = _extract_metric_numeric_signal(text, kpi_target=kpi_target)
        if candidate_value and not _is_unreliable_metric_value(candidate_value, kpi_target=kpi_target):
            value = candidate_value
            period = _extract_contract_period_from_text(text) or ""
            if idx == 0 and source_pack:
                source_domain = _clean(source_pack[0].get("domain") or source_domain)
            elif idx > 0 and (idx - 1) < len(source_pack):
                source_domain = _clean(source_pack[idx - 1].get("domain") or source_domain)
            break

    if not period and source_pack:
        period = _extract_contract_period_from_text(source_pack[0].get("snippet") or "") or _clean(
            source_pack[0].get("date") or ""
        )

    if not value:
        return {
            "kpi": metric,
            "value": "Non trouvé dans les sources fournies",
            "period": period or "n/d",
            "source_domain": source_domain,
        }, "not_found"

    return {
        "kpi": metric,
        "value": value,
        "period": period or "n/d",
        "source_domain": source_domain,
    }, "ok"


def _extract_answer_lines(answer_draft: str, max_items: int = 6) -> List[str]:
    lines: List[str] = []
    for raw in (answer_draft or "").splitlines():
        txt = _sanitize_line_no_url(raw)
        if not txt:
            continue
        low = _normalize_ascii(txt)
        if low.startswith("analyse:") or low.startswith("strategie recommandee:") or low.startswith("stratégie recommandée:"):
            txt = _clean(txt.split(":", 1)[1]) if ":" in txt else txt
        if low.startswith("projection") or low.startswith("arbitrages:") or low.startswith("conclusion:"):
            continue
        if len(txt) < 8:
            continue
        lines.append(txt)
        if len(lines) >= max_items:
            break
    return _dedupe_keep_order(lines)


def _extract_scpi_candidates_from_sources(
    source_pack: List[Dict[str, str]],
    limit: int = 4,
) -> List[str]:
    candidates: List[str] = []
    known_brands = list(dict.fromkeys([*EXTERNAL_SCPI_BRAND_KEYWORDS, "re01"]))

    def _format_brand(name: str) -> str:
        raw = _clean(name)
        if not raw:
            return ""
        if _normalize_ascii(raw) == "re01":
            return "RE01"
        return " ".join([part.capitalize() for part in raw.split()])

    for src in source_pack:
        blob = _clean(f"{src.get('title', '')} {src.get('source', '')} {src.get('snippet', '')}")
        if not blob:
            continue
        blob_norm = _normalize_ascii(blob)

        for brand in known_brands:
            b_norm = _normalize_ascii(brand)
            if b_norm and b_norm in blob_norm:
                display = _format_brand(brand)
                if display:
                    candidates.append(display)

        for match in re.finditer(r"\bscpi\s+([a-z0-9][a-z0-9'’\-\s]{1,36})", blob, flags=re.IGNORECASE):
            raw_name = _clean(match.group(1))
            if not raw_name:
                continue
            raw_name = re.split(r"\s+(?:de|du|des|en|avec|sur|pour|et)\b", raw_name, maxsplit=1, flags=re.IGNORECASE)[0]
            raw_name = re.sub(r"[^a-zA-Z0-9'’\-\s]", "", raw_name).strip(" -")
            if not raw_name:
                continue
            if len(raw_name.split()) > 4:
                continue
            formatted = " ".join([part.capitalize() for part in raw_name.split()])
            if _is_valid_scpi_candidate_name(formatted):
                candidates.append(formatted)

    return _dedupe_keep_order(candidates)[: max(1, limit)]


def _build_contract_payload_deterministic(
    question: str,
    contract_intent: str,
    kpi_target: str,
    answer_draft: str,
    sources_by_layer: Dict[str, List[Any]],
    seed_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    source_pack = _collect_sources_for_contract(sources_by_layer=sources_by_layer)
    extracted_questions = _collect_contract_clarification_questions(
        question=question,
        contract_intent=contract_intent,
        kpi_target=kpi_target,
    )
    clarifications = _coerce_questions_by_intent(
        contract_intent,
        list(seed_questions or []) + extracted_questions,
    )
    status = "ok"
    payload: Dict[str, Any] = {
        "intent": contract_intent,
        "kpi_target": kpi_target if kpi_target in CGP_ALLOWED_KPI_TARGETS else "none",
        "status": "ok",
        "items": [],
        "kpi_response": {
            "kpi": _metric_label_from_target(kpi_target),
            "value": "Non trouvé dans les sources fournies",
            "period": "n/d",
            "source_domain": "sources fournies",
        },
        "strategy_blocks": {
            "analyse": [],
            "recommandation": [],
            "risques": [],
            "questions_manquantes": [],
        },
        "clarification_questions": clarifications,
        "sources_used": [
            {
                "source": _clean(src.get("source") or "source"),
                "domain": _clean(src.get("domain") or "source"),
                "date": _clean(src.get("date") or "n/d") or "n/d",
                "title": _clean(src.get("title") or ""),
                "snippet": _clean(src.get("snippet") or ""),
                "url": _to_url(src.get("url") or ""),
            }
            for src in source_pack[:6]
        ],
        "rendered_text": "",
    }

    if contract_intent == CGP_INTENT_TOP:
        if kpi_target == "none":
            payload["items"] = [
                {
                    "name": "Non trouvé dans les sources fournies",
                    "value": "",
                    "metric": "kpi",
                    "period": "n/d",
                    "source_domain": "sources fournies",
                }
            ]
            payload["status"] = "partial" if clarifications else "not_found"
            return payload
        requested_top = _requested_top_size(question, default_for_top=10, cap=20)
        payload["items"] = _extract_top_items_from_sources(
            question=question,
            kpi_target=kpi_target,
            source_pack=source_pack,
            limit=requested_top or 10,
        )
        if payload["items"] and str(payload["items"][0].get("name", "")).lower().startswith("non trouvé"):
            status = "not_found"
        if clarifications and status == "ok":
            status = "partial"
        payload["status"] = status
        return payload

    if contract_intent == CGP_INTENT_KPI:
        kpi_response, status = _build_kpi_response_from_sources(
            question=question,
            kpi_target=kpi_target,
            answer_draft=answer_draft,
            source_pack=source_pack,
        )
        payload["kpi_response"] = kpi_response
        if clarifications and status == "ok":
            status = "partial"
        payload["status"] = status
        return payload

    answer_lines = _extract_answer_lines(answer_draft, max_items=8)
    scpi_candidates = _extract_scpi_candidates_from_sources(source_pack, limit=4)
    analyse = answer_lines[:3] or ["Non trouvé dans les sources fournies"]
    if scpi_candidates:
        analyse = [f"SCPI citées dans les sources: {', '.join(scpi_candidates)}."] + analyse
    recommandation = [
        "Prioriser uniquement les éléments confirmés par les sources fournies.",
        "Aligner la décision avec l'horizon, le profil de risque et la fiscalité.",
    ]
    if scpi_candidates:
        recommandation.insert(0, f"Construire l'allocation autour de: {', '.join(scpi_candidates[:3])}.")
    risques = [
        "Risque d'arbitrage inexact si les données client sont incomplètes.",
        "Vérifier la période exacte des chiffres avant décision finale.",
    ]
    payload["strategy_blocks"] = {
        "analyse": analyse[:3],
        "recommandation": recommandation[:3],
        "risques": risques[:3],
        "questions_manquantes": clarifications[:3],
    }
    status = "ok" if analyse and analyse[0] != "Non trouvé dans les sources fournies" else "not_found"
    if clarifications and status == "ok":
        status = "partial"
    payload["status"] = status
    return payload


def _coerce_contract_payload(
    raw_payload: Dict[str, Any],
    fallback_payload: Dict[str, Any],
    contract_intent: str,
    kpi_target: str,
    seed_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload = dict(fallback_payload)
    payload["intent"] = contract_intent
    payload["kpi_target"] = kpi_target if kpi_target in CGP_ALLOWED_KPI_TARGETS else "none"

    raw_status = _clean(str(raw_payload.get("status") or "")).lower()
    if raw_status in {"ok", "not_found", "partial"}:
        payload["status"] = raw_status

    raw_sources = raw_payload.get("sources_used") if isinstance(raw_payload.get("sources_used"), list) else []
    parsed_sources: List[Dict[str, str]] = []
    for src in raw_sources:
        if not isinstance(src, dict):
            continue
        source = _sanitize_line_no_url(str(src.get("source") or "source"))
        domain = _sanitize_line_no_url(str(src.get("domain") or src.get("source_domain") or ""))
        url = _to_url(src.get("url") or "")
        if not domain:
            domain = _source_domain_from_values(url, source)
        date_txt = _sanitize_line_no_url(str(src.get("date") or "n/d")) or "n/d"
        title_txt = _sanitize_line_no_url(str(src.get("title") or ""))
        snippet_txt = _sanitize_line_no_url(str(src.get("snippet") or ""))
        parsed_sources.append(
            {
                "source": source or "source",
                "domain": domain or "source",
                "date": date_txt,
                "title": title_txt,
                "snippet": snippet_txt,
                "url": url,
            }
        )
        if len(parsed_sources) >= 12:
            break
    if parsed_sources:
        payload["sources_used"] = parsed_sources

    source_pool: List[Dict[str, str]] = []
    source_seen = set()
    for src in list(parsed_sources) + list(payload.get("sources_used") or []) + list(fallback_payload.get("sources_used") or []):
        if not isinstance(src, dict):
            continue
        source = _sanitize_line_no_url(str(src.get("source") or "source"))
        domain = _sanitize_line_no_url(str(src.get("domain") or src.get("source_domain") or ""))
        url = _to_url(src.get("url") or "")
        if not domain:
            domain = _source_domain_from_values(url, source)
        record = {
            "source": source or "source",
            "domain": domain or "source",
            "date": _sanitize_line_no_url(str(src.get("date") or "n/d")) or "n/d",
            "title": _sanitize_line_no_url(str(src.get("title") or "")),
            "snippet": _sanitize_line_no_url(str(src.get("snippet") or "")),
            "url": url,
        }
        key = _normalize_ascii(
            f"{record.get('domain', '')}|{record.get('url', '')}|{record.get('title', '')}|{record.get('snippet', '')[:80]}"
        )
        if key in source_seen:
            continue
        source_seen.add(key)
        source_pool.append(record)
        if len(source_pool) >= 20:
            break

    if contract_intent == CGP_INTENT_TOP:
        raw_items = raw_payload.get("items") if isinstance(raw_payload.get("items"), list) else []
        parsed_items: List[Dict[str, str]] = []
        seen_names = set()
        received_count = 0
        rejected_count = 0
        resolution_mode = "llm_raw"
        resolved_from_source = False
        target_metric = _metric_label_from_target(kpi_target)

        def _domain_key(value: str) -> str:
            key = _normalize_ascii(value or "")
            key = key.replace("http://", "").replace("https://", "").lstrip("www.")
            key = key.split("/", 1)[0]
            return key

        def _candidate_sources(item_idx: int, source_hint: str) -> List[Dict[str, str]]:
            matches: List[Dict[str, str]] = []
            hint_key = _domain_key(source_hint)
            if hint_key:
                for src in source_pool:
                    domain_key = _domain_key(str(src.get("domain") or ""))
                    source_key = _domain_key(str(src.get("source") or ""))
                    if hint_key == domain_key or hint_key == source_key:
                        matches.append(src)
            if item_idx < len(source_pool):
                matches.append(source_pool[item_idx])
            if not matches:
                matches = list(source_pool)
            deduped: List[Dict[str, str]] = []
            local_seen = set()
            for src in matches:
                src_key = _normalize_ascii(f"{src.get('domain', '')}|{src.get('url', '')}|{src.get('title', '')}")
                if src_key in local_seen:
                    continue
                local_seen.add(src_key)
                deduped.append(src)
            return deduped

        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            received_count += 1
            name = _sanitize_line_no_url(str(item.get("name") or ""))
            value = _sanitize_line_no_url(str(item.get("value") or ""))
            metric = _sanitize_line_no_url(str(item.get("metric") or target_metric))
            period = _sanitize_line_no_url(str(item.get("period") or "n/d"))
            source_domain = _sanitize_line_no_url(str(item.get("source_domain") or item.get("source") or "source"))
            candidate_name = name
            candidate_sources = _candidate_sources(idx, source_domain)

            if not _is_valid_scpi_candidate_name(candidate_name):
                candidate_name = ""
                for src in candidate_sources:
                    reconstructed = _extract_scpi_name_from_source_record(src)
                    if _is_valid_scpi_candidate_name(reconstructed):
                        candidate_name = reconstructed
                        resolved_from_source = True
                        break

            if not candidate_name or not _is_valid_scpi_candidate_name(candidate_name):
                rejected_count += 1
                continue

            name_key = _normalize_ascii(candidate_name)
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            if not value:
                for src in candidate_sources:
                    signal = _extract_metric_numeric_signal(
                        f"{src.get('title', '')} {src.get('snippet', '')}",
                        kpi_target=kpi_target,
                    )
                    if signal:
                        value = signal
                        break
            if _is_unreliable_metric_value(value, kpi_target=kpi_target):
                for src in candidate_sources:
                    repaired_signal = _extract_metric_numeric_signal(
                        f"{src.get('title', '')} {src.get('snippet', '')}",
                        kpi_target=kpi_target,
                    )
                    if repaired_signal and not _is_unreliable_metric_value(
                        repaired_signal,
                        kpi_target=kpi_target,
                    ):
                        value = repaired_signal
                        resolved_from_source = True
                        break
            if _is_unreliable_metric_value(value, kpi_target=kpi_target):
                rejected_count += 1
                continue
            if not period or period == "n/d":
                for src in candidate_sources:
                    candidate_period = _extract_contract_period_from_text(
                        f"{src.get('title', '')} {src.get('snippet', '')} {src.get('date', '')}"
                    ) or _sanitize_line_no_url(str(src.get("date") or ""))
                    if candidate_period:
                        period = candidate_period
                        break
            if not source_domain:
                for src in candidate_sources:
                    domain = _sanitize_line_no_url(str(src.get("domain") or src.get("source") or ""))
                    if domain:
                        source_domain = domain
                        break

            parsed_items.append(
                {
                    "name": candidate_name,
                    "value": value or "Non trouvé dans les sources fournies",
                    "metric": target_metric if target_metric != "kpi" else (metric or target_metric),
                    "period": period or "n/d",
                    "source_domain": source_domain or "source",
                }
            )
            if len(parsed_items) >= 20:
                break

        if kpi_target == "none":
            parsed_items = []

        if parsed_items:
            payload["items"] = parsed_items
            resolution_mode = "resolved_from_source" if resolved_from_source else "llm_raw"
        else:
            fallback_items = fallback_payload.get("items") if isinstance(fallback_payload.get("items"), list) else []
            strict_fallback: List[Dict[str, str]] = []
            for item in fallback_items:
                if not isinstance(item, dict):
                    continue
                fallback_name = _sanitize_line_no_url(str(item.get("name") or ""))
                fallback_value = _sanitize_line_no_url(str(item.get("value") or ""))
                fallback_metric = _sanitize_line_no_url(str(item.get("metric") or target_metric))
                fallback_period = _sanitize_line_no_url(str(item.get("period") or "n/d"))
                fallback_source = _sanitize_line_no_url(str(item.get("source_domain") or item.get("source") or "sources fournies"))
                if "non trouve" in _normalize_ascii(fallback_name):
                    strict_fallback = [
                        {
                            "name": "Non trouvé dans les sources fournies",
                            "value": "",
                            "metric": fallback_metric or _metric_label_from_target(kpi_target),
                            "period": fallback_period or "n/d",
                            "source_domain": fallback_source or "sources fournies",
                        }
                    ]
                    break
                if not _is_valid_scpi_candidate_name(fallback_name):
                    continue
                if _is_unreliable_metric_value(fallback_value, kpi_target=kpi_target):
                    continue
                strict_fallback.append(
                    {
                        "name": fallback_name,
                        "value": fallback_value or "Non trouvé dans les sources fournies",
                        "metric": target_metric if target_metric != "kpi" else (fallback_metric or target_metric),
                        "period": fallback_period or "n/d",
                        "source_domain": fallback_source or "source",
                    }
                )
                if len(strict_fallback) >= 20:
                    break
            if not strict_fallback:
                strict_fallback = [
                    {
                        "name": "Non trouvé dans les sources fournies",
                        "value": "",
                        "metric": _metric_label_from_target(kpi_target),
                        "period": "n/d",
                        "source_domain": "sources fournies",
                    }
                ]
            payload["items"] = strict_fallback
            resolution_mode = "deterministic_fallback"
            if payload.get("status") == "ok":
                payload["status"] = "not_found"

        valid_count = sum(
            1
            for row in (payload.get("items") if isinstance(payload.get("items"), list) else [])
            if isinstance(row, dict) and _is_valid_scpi_candidate_name(str(row.get("name") or ""))
        )
        payload["top_diagnostics"] = {
            "received_count": int(received_count),
            "valid_count": int(valid_count),
            "rejected_count": int(max(0, rejected_count)),
            "sanitizer_applied": bool(received_count),
            "resolution_mode": resolution_mode,
        }

    elif contract_intent == CGP_INTENT_KPI:
        raw_kpi = raw_payload.get("kpi_response") if isinstance(raw_payload.get("kpi_response"), dict) else {}
        if raw_kpi:
            raw_value = _sanitize_line_no_url(str(raw_kpi.get("value") or "Non trouvé dans les sources fournies"))
            if _is_unreliable_metric_value(raw_value, kpi_target=kpi_target):
                raw_value = "Non trouvé dans les sources fournies"
                if payload.get("status") == "ok":
                    payload["status"] = "not_found"
            payload["kpi_response"] = {
                "kpi": _sanitize_line_no_url(str(raw_kpi.get("kpi") or _metric_label_from_target(kpi_target))),
                "value": raw_value,
                "period": _sanitize_line_no_url(str(raw_kpi.get("period") or "n/d")),
                "source_domain": _sanitize_line_no_url(str(raw_kpi.get("source_domain") or raw_kpi.get("source") or "source")),
            }

    else:
        raw_blocks = raw_payload.get("strategy_blocks") if isinstance(raw_payload.get("strategy_blocks"), dict) else {}
        if raw_blocks:
            def _list_block(name: str) -> List[str]:
                values = raw_blocks.get(name) if isinstance(raw_blocks.get(name), list) else []
                return [_sanitize_line_no_url(str(v)) for v in values if _sanitize_line_no_url(str(v))][:3]

            merged_questions = _coerce_questions_by_intent(
                CGP_INTENT_STRATEGIE,
                _list_block("questions_manquantes") + list(seed_questions or []),
            )
            payload["strategy_blocks"] = {
                "analyse": _list_block("analyse") or payload.get("strategy_blocks", {}).get("analyse", []),
                "recommandation": _list_block("recommandation") or payload.get("strategy_blocks", {}).get("recommandation", []),
                "risques": _list_block("risques") or payload.get("strategy_blocks", {}).get("risques", []),
                "questions_manquantes": merged_questions,
            }

    all_questions: List[str] = []
    raw_questions = raw_payload.get("clarification_questions")
    if isinstance(raw_questions, list):
        all_questions.extend([_sanitize_line_no_url(str(q)) for q in raw_questions])
    all_questions.extend(list(seed_questions or []))
    payload["clarification_questions"] = _coerce_questions_by_intent(contract_intent, all_questions)
    if contract_intent == CGP_INTENT_STRATEGIE:
        blocks = payload.get("strategy_blocks") if isinstance(payload.get("strategy_blocks"), dict) else {}
        blocks["questions_manquantes"] = _coerce_questions_by_intent(
            CGP_INTENT_STRATEGIE,
            list(blocks.get("questions_manquantes") or []) + payload.get("clarification_questions", []),
        )
        payload["strategy_blocks"] = blocks
    return payload


def _render_contract_payload_text(payload: Dict[str, Any]) -> str:
    contract_intent = str(payload.get("intent") or CGP_INTENT_KPI).upper()
    clarifications = _coerce_questions_by_intent(
        contract_intent,
        [str(q) for q in (payload.get("clarification_questions") or [])],
    )
    lines: List[str] = []

    if contract_intent == CGP_INTENT_KPI:
        kpi = payload.get("kpi_response") if isinstance(payload.get("kpi_response"), dict) else {}
        value = _sanitize_line_no_url(str(kpi.get("value") or "Non trouvé dans les sources fournies"))
        period = _sanitize_line_no_url(str(kpi.get("period") or "n/d"))
        source_domain = _sanitize_line_no_url(str(kpi.get("source_domain") or "sources fournies"))
        label = _sanitize_line_no_url(str(kpi.get("kpi") or payload.get("kpi_target") or "kpi"))
        if "non trouve" in _normalize_ascii(value):
            lines.append("Non trouvé dans les sources fournies")
        else:
            lines.append(f"{label}: {value} | période: {period} | source: {source_domain}")
        for question in clarifications:
            if len(lines) >= 3:
                break
            lines.append(f"Question: {question}")
        if not lines:
            lines = ["Non trouvé dans les sources fournies"]
        return "\n".join(lines[:3]).strip()

    if contract_intent == CGP_INTENT_TOP:
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        ranked: List[str] = []
        idx = 1
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _sanitize_line_no_url(str(item.get("name") or ""))
            value = _sanitize_line_no_url(str(item.get("value") or "Non trouvé dans les sources fournies"))
            metric = _sanitize_line_no_url(str(item.get("metric") or "kpi"))
            period = _sanitize_line_no_url(str(item.get("period") or "n/d"))
            source_domain = _sanitize_line_no_url(str(item.get("source_domain") or "sources fournies"))
            if not name:
                continue
            if "non trouve" in _normalize_ascii(name):
                ranked.append(f"{idx}. Non trouvé dans les sources fournies")
            else:
                ranked.append(f"{idx}. {name} | {value} | {metric} | période: {period} | source: {source_domain}")
            idx += 1
            if len(ranked) >= 20:
                break
        if not ranked:
            ranked = ["1. Non trouvé dans les sources fournies"]
        for question in clarifications[:2]:
            ranked.append(f"{idx}. Question: {question}")
            idx += 1
        return "\n".join(ranked).strip()

    blocks = payload.get("strategy_blocks") if isinstance(payload.get("strategy_blocks"), dict) else {}
    analyse = _dedupe_keep_order([_sanitize_line_no_url(str(v)) for v in (blocks.get("analyse") or [])])[:3]
    recommandation = _dedupe_keep_order([_sanitize_line_no_url(str(v)) for v in (blocks.get("recommandation") or [])])[:3]
    risques = _dedupe_keep_order([_sanitize_line_no_url(str(v)) for v in (blocks.get("risques") or [])])[:3]
    questions = _coerce_questions_by_intent(
        CGP_INTENT_STRATEGIE,
        list(blocks.get("questions_manquantes") or []) + clarifications,
    )

    if not analyse:
        analyse = ["Non trouvé dans les sources fournies"]
    if not recommandation:
        recommandation = ["Valider les paramètres client avant arbitrage."]
    if not risques:
        risques = ["Risque d'erreur si les hypothèses ne sont pas confirmées."]

    lines.extend(["Analyse:"] + [f"- {item}" for item in analyse[:3]])
    lines.extend(["Recommandation:"] + [f"- {item}" for item in recommandation[:3]])
    lines.extend(["Risques:"] + [f"- {item}" for item in risques[:3]])
    lines.extend(["Questions manquantes:"] + [f"- {item}" for item in questions[:3]])
    return "\n".join(lines).strip()


def _validate_rendered_contract_text(contract_intent: str, text: str) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    clean = _clean(text or "")
    clean = re.sub(
        r"https?://[^\s\)\]]+",
        lambda m: _source_label_from_url(m.group(0)) or "source",
        clean,
        flags=re.IGNORECASE,
    )
    lines = [_clean(line) for line in clean.splitlines() if _clean(line)]
    if not lines:
        return "Non trouvé dans les sources fournies", ["contract_render_empty"]

    if contract_intent == CGP_INTENT_KPI:
        forbidden = ("analyse:", "analyse", "strategie:", "stratégie:", "strategie", "stratégie", "projection", "arbitrages:", "arbitrages", "risques:", "risques", "conclusion:", "conclusion")
        filtered = [line for line in lines if not _normalize_ascii(line).startswith(forbidden)]
        if not filtered:
            filtered = ["Non trouvé dans les sources fournies"]
        lines = filtered[:3]
    elif contract_intent == CGP_INTENT_TOP:
        forbidden = ("analyse:", "analyse", "strategie:", "stratégie:", "strategie", "stratégie", "projection", "arbitrages:", "arbitrages", "risques:", "risques", "conclusion:", "conclusion")
        filtered = [line for line in lines if not _normalize_ascii(line).startswith(forbidden)]
        if not filtered:
            filtered = ["Non trouvé dans les sources fournies"]
        normalized_body: List[str] = []
        for line in filtered:
            raw_body = _clean(re.sub(r"^\d+\.\s*", "", line))
            if not raw_body:
                continue
            body = _sanitize_line_no_url(raw_body)
            body_norm = _normalize_ascii(body)
            if body_norm.startswith("question:"):
                normalized_body.append(body)
                continue
            if "non trouve" in body_norm:
                normalized_body.append("Non trouvé dans les sources fournies")
                continue

            name = _clean(body.split("|", 1)[0]) if "|" in body else _clean(body)
            if not _is_valid_scpi_candidate_name(name):
                warnings.append("contract_top_site_name_sanitized")
                normalized_body.append("Non trouvé dans les sources fournies")
                continue
            normalized_body.append(body)

        if not normalized_body:
            normalized_body = ["Non trouvé dans les sources fournies"]

        dedup_body: List[str] = []
        dedup_seen = set()
        for body in normalized_body:
            key = _normalize_ascii(body)
            if key in dedup_seen and key == "non trouve dans les sources fournies":
                continue
            dedup_seen.add(key)
            dedup_body.append(body)

        lines = [f"{idx}. {body}" for idx, body in enumerate(dedup_body, start=1)]
    else:
        required_headers = {
            "analyse": "Analyse:",
            "recommandation": "Recommandation:",
            "risques": "Risques:",
            "questions manquantes": "Questions manquantes:",
        }
        forbidden_headers = {"projection", "arbitrages", "conclusion", "strategie", "stratégie"}

        buckets: Dict[str, List[str]] = {
            "analyse": [],
            "recommandation": [],
            "risques": [],
            "questions manquantes": [],
        }
        current_section: Optional[str] = None

        for raw_line in lines:
            line = _clean(raw_line)
            if not line:
                continue
            normalized = _normalize_ascii(line)
            header_candidate = normalized.rstrip(":").strip()

            if header_candidate in required_headers:
                current_section = header_candidate
                continue
            if header_candidate in forbidden_headers:
                current_section = None
                warnings.append("contract_strategie_forbidden_section_removed")
                continue

            item = _clean(re.sub(r"^\-\s*", "", line))
            if not item:
                continue
            item_norm = _normalize_ascii(item)
            if item_norm.rstrip(":").strip() in forbidden_headers:
                warnings.append("contract_strategie_forbidden_section_removed")
                current_section = None
                continue

            if current_section in buckets:
                buckets[current_section].append(item)

        if not buckets["analyse"]:
            buckets["analyse"] = ["Non trouvé dans les sources fournies"]
            warnings.append("contract_strategie_header_enforced")
        if not buckets["recommandation"]:
            buckets["recommandation"] = ["Valider les paramètres client avant arbitrage."]
            warnings.append("contract_strategie_header_enforced")
        if not buckets["risques"]:
            buckets["risques"] = ["Risque d'erreur si les hypothèses ne sont pas confirmées."]
            warnings.append("contract_strategie_header_enforced")
        if not buckets["questions manquantes"]:
            buckets["questions manquantes"] = ["Souhaites-tu une sélection nominative de 3 à 5 SCPI ?"]
            warnings.append("contract_strategie_header_enforced")

        rebuilt: List[str] = []
        for key in ["analyse", "recommandation", "risques", "questions manquantes"]:
            rebuilt.append(required_headers[key])
            for item in _dedupe_keep_order(buckets[key])[:3]:
                rebuilt.append(f"- {item}")
        lines = rebuilt

    return "\n".join(lines).strip(), _dedupe_keep_order(warnings)


def _legacy_structured_from_contract(payload: Dict[str, Any]) -> Dict[str, Any]:
    contract_intent = str(payload.get("intent") or CGP_INTENT_KPI).upper()
    legacy_sections: List[Dict[str, str]] = []
    if contract_intent == CGP_INTENT_TOP:
        content = _render_contract_payload_text(payload)
        legacy_sections.append({"title": "CLASSEMENT", "content": content})
    elif contract_intent == CGP_INTENT_STRATEGIE:
        blocks = payload.get("strategy_blocks") if isinstance(payload.get("strategy_blocks"), dict) else {}
        mapping = {
            "ANALYSE": blocks.get("analyse") or [],
            "RECOMMANDATION": blocks.get("recommandation") or [],
            "RISQUES": blocks.get("risques") or [],
            "QUESTIONS_MANQUANTES": blocks.get("questions_manquantes") or [],
        }
        for title, values in mapping.items():
            text = "\n".join([f"- {_sanitize_line_no_url(str(v))}" for v in values[:3]]) if values else "n/d"
            legacy_sections.append({"title": title, "content": text})
    else:
        kpi = payload.get("kpi_response") if isinstance(payload.get("kpi_response"), dict) else {}
        line = _render_contract_payload_text(payload)
        legacy_sections.append({"title": "REPONSE_DIRECTE", "content": line})
        legacy_sections.append({"title": "KPI", "content": _sanitize_line_no_url(str(kpi.get("kpi") or "kpi"))})

    legacy_sources: List[Dict[str, str]] = []
    for src in (payload.get("sources_used") or []):
        if not isinstance(src, dict):
            continue
        legacy_sources.append(
            {
                "source": _clean(str(src.get("source") or "source")),
                "url": _to_url(src.get("url") or ""),
                "date": _clean(str(src.get("date") or "n/d")) or "n/d",
            }
        )
        if len(legacy_sources) >= 6:
            break

    return {
        "format": contract_intent,
        "sections": legacy_sections,
        "questions_a_preciser": _coerce_questions_by_intent(
            contract_intent,
            [str(q) for q in (payload.get("clarification_questions") or [])],
        ),
        "sources_utilisees": legacy_sources,
    }


def _rewrite_role_context(
    question: str,
    internal_intent: str,
    contract_intent: str,
    selected_agent: str,
    strategy_notes: Optional[List[str]] = None,
) -> Dict[str, str]:
    agent_key = _normalize_ascii(selected_agent or "")
    role_by_agent = {
        "online": "Analyste Marche SCPI",
        "sql_kpi": "Analyste KPI SCPI",
        "core": "Conseiller en Gestion de Patrimoine",
        "rapport": "Redacteur de Rapport Patrimonial",
    }
    role_editorial = role_by_agent.get(agent_key, "Finalizer CGP")

    if contract_intent == CGP_INTENT_TOP:
        mission = "Produire un classement clair, nominatif et comparable."
        style = "Liste numerotee uniquement, chaque ligne avec metrique/periode/source."
    elif contract_intent == CGP_INTENT_STRATEGIE:
        mission = "Produire une recommandation actionnable adaptee au profil client."
        style = "4 blocs stricts: Analyse, Recommandation, Risques, Questions manquantes."
    else:
        mission = "Donner une reponse KPI concise et verifiable."
        style = "1 a 3 lignes max, valeur + periode + source."

    qn = _normalize_ascii(question or "")
    focus_tags: List[str] = []
    if "tmi" in qn or "ir" in qn or "is" in qn or "fiscal" in qn:
        focus_tags.append("fiscalite")
    if "horizon" in qn or re.search(r"\b\d+\s*(ans?|mois)\b", qn):
        focus_tags.append("horizon")
    if "revenu" in qn or "cashflow" in qn:
        focus_tags.append("revenus")
    if "risque" in qn or "prudent" in qn or "dynamique" in qn:
        focus_tags.append("risque")
    if not focus_tags:
        focus_tags.append("objectif_general")

    notes = [str(n).strip() for n in (strategy_notes or []) if str(n).strip()]
    notes_txt = " | ".join(notes[:3]) if notes else "none"

    return {
        "agent_source": selected_agent or "unknown",
        "role_editorial": role_editorial,
        "mission": mission,
        "style": style,
        "internal_intent": internal_intent or "",
        "question_focus": ", ".join(focus_tags),
        "strategy_notes": notes_txt,
    }


def _rewrite_with_contract_llm(
    question: str,
    intent: str,
    history: Optional[List[dict]],
    answer_draft: str,
    sources_by_layer: Dict[str, List[Any]],
    seed_questions: Optional[List[str]] = None,
    selected_agent: str = "",
    strategy_notes: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], str, str, List[str]]:
    contract_intent = _contract_intent_from_internal(question=question, intent=intent)
    kpi_target = _extract_contract_kpi_target(question)
    role_context = _rewrite_role_context(
        question=question,
        internal_intent=intent,
        contract_intent=contract_intent,
        selected_agent=selected_agent,
        strategy_notes=strategy_notes,
    )
    fallback_payload = _build_contract_payload_deterministic(
        question=question,
        contract_intent=contract_intent,
        kpi_target=kpi_target,
        answer_draft=answer_draft,
        sources_by_layer=sources_by_layer,
        seed_questions=seed_questions,
    )
    fallback_text = _render_contract_payload_text(fallback_payload)
    fallback_text, fallback_warnings = _validate_rendered_contract_text(contract_intent, fallback_text)
    fallback_payload["rendered_text"] = fallback_text

    if (not FINALIZER_STRUCTURED_REWRITE_ENABLED) or _finalizer_llm is None:
        return fallback_payload, fallback_text, "deterministic_fallback", fallback_warnings

    source_pack = _collect_sources_for_contract(sources_by_layer=sources_by_layer)
    history_text = _history_to_text(history, max_items=6)
    prompt = f"""
Tu es Claude, finalizer CGP STRICT.
Tu dois respecter ce contrat:
- intent={contract_intent}
- kpi_target={kpi_target}
- répondre uniquement à la question.
- ne jamais inventer de chiffres, dates, classements, sources.
- si absent: "Non trouvé dans les sources fournies".
- pas de blabla marketing.
- pas de lien brut dans rendered_text (domaines uniquement).

ROLE AGENT A APPLIQUER:
- agent_source={role_context.get("agent_source")}
- role_editorial={role_context.get("role_editorial")}
- mission={role_context.get("mission")}
- style_attendu={role_context.get("style")}
- internal_intent={role_context.get("internal_intent")}
- question_focus={role_context.get("question_focus")}
- strategy_notes={role_context.get("strategy_notes")}

QUESTION:
{question}

HISTORIQUE (court):
{history_text}

BROUILLON:
{answer_draft}

SOURCES:
{json.dumps(source_pack, ensure_ascii=False, indent=2)}

Retourne UNIQUEMENT un JSON valide exactement avec les clés:
{{
  "intent": "{contract_intent}",
  "kpi_target": "{kpi_target}",
  "status": "ok|not_found|partial",
  "items": [{{"name":"STRING","value":"STRING","metric":"STRING","period":"STRING","source_domain":"STRING"}}],
  "kpi_response": {{"kpi":"STRING","value":"STRING","period":"STRING","source_domain":"STRING"}},
  "strategy_blocks": {{
    "analyse": ["STRING"],
    "recommandation": ["STRING"],
    "risques": ["STRING"],
    "questions_manquantes": ["STRING"]
  }},
  "clarification_questions": ["STRING"],
  "sources_used": [{{"source":"STRING","domain":"STRING","date":"STRING","url":"STRING"}}]
}}
"""
    try:
        raw = _clean(_finalizer_llm.invoke(prompt).content)
        parsed = _safe_json_dict(raw)
        if not parsed:
            warnings = _dedupe_keep_order(["contract_llm_invalid_json"] + fallback_warnings)
            return fallback_payload, fallback_text, "deterministic_fallback", warnings
        merged = _coerce_contract_payload(
            raw_payload=parsed,
            fallback_payload=fallback_payload,
            contract_intent=contract_intent,
            kpi_target=kpi_target,
            seed_questions=seed_questions,
        )
        rendered_text = _render_contract_payload_text(merged)
        rendered_text, render_warnings = _validate_rendered_contract_text(contract_intent, rendered_text)
        merged["rendered_text"] = rendered_text
        return merged, rendered_text, "llm", render_warnings
    except Exception:
        warnings = _dedupe_keep_order(["contract_llm_error"] + fallback_warnings)
        return fallback_payload, fallback_text, "deterministic_fallback", warnings


def finalize(
    response: Optional[str] = None,
    question: Optional[str] = None,
    history: Optional[List[dict]] = None,
    agent_outputs: Optional[List[Dict[str, Any]]] = None,
    neutral_pure: bool = False,
) -> Dict[str, Any]:
    """
    Fusion textuelle des sorties agents.

    Compatibilité:
    - Ancien usage: finalize(response="...")
    - Usage actuel: finalize(question=..., history=..., agent_outputs=[...])
    """
    if agent_outputs is None:
        fallback_answer = _normalize_bold_only_markdown(_clean(response)) or "Darwin n'a pas pu générer de réponse."
        return {
            "answer": fallback_answer,
            "details": "",
            "used_facts": [],
            "warnings": [],
            "business_warnings": [],
            "business_flags": {},
            "business_debug": {},
        }

    rapport_draft = _draft_from(agent_outputs, "rapport")
    if rapport_draft:
        answer = _normalize_bold_only_markdown(rapport_draft)
        return {
            "answer": answer,
            "details": answer,
            "used_facts": [answer] if answer else [],
            "warnings": [],
            "business_warnings": [],
            "business_flags": {},
            "business_debug": {},
        }

    intent = _detect_intent(question or "", neutral_pure=neutral_pure, history=history)
    material = _build_persona_material(
        agent_outputs,
        intent=intent,
        neutral_pure=neutral_pure,
        question=question or "",
    )
    if not material:
        missing_message = (
            "Je passe en estimation prudente: j'avance avec des hypothèses explicites "
            "et je te précise les points à vérifier."
        )
        return {
            "answer": missing_message,
            "details": "",
            "used_facts": [],
            "warnings": ["estimation_mode_missing_material"],
            "business_warnings": ["estimation_mode_missing_material"],
            "business_flags": {},
            "business_debug": {},
        }

    full_material = material

    # === CGP_BUSINESS_LAYER_START ===
    sources_by_layer = _normalize_sources_by_layer(agent_outputs)
    must_include_by_intent = {
        INTENT_STRATEGIC_ALLOCATION: ["objectif", "horizon", "fiscalite", "risques", "hypotheses"],
        INTENT_FACTUAL_KPI: ["source", "date"],
        INTENT_COMPARISON: ["source", "date", "risques"],
        INTENT_DARWIN_SPECIFIC: ["source", "date"],
        INTENT_REGULATORY: ["source", "date", "limites"],
    }
    business_output = _cgp_business_layer.apply(
        question=question or "",
        intent=intent,
        material=full_material,
        sources_by_layer=sources_by_layer,
        profile_scoring={},
        context={
            "response_mode": "compact",
            "constraints": {
                "require_source_for_numbers": True,
                "real_time_required": _has_freshness_signal(question or ""),
            },
            "live_web_signal": bool(sources_by_layer.get("rag_market")),
            "must_include": must_include_by_intent.get(intent, []),
            "warnings": [],
            "used_facts": [],
        },
    )
    business_answer = _clean(str(business_output.get("business_answer") or ""))
    business_warnings = (
        business_output.get("business_warnings")
        if isinstance(business_output.get("business_warnings"), list)
        else []
    )
    business_actions = (
        business_output.get("business_actions")
        if isinstance(business_output.get("business_actions"), list)
        else []
    )
    business_flags = (
        business_output.get("business_flags")
        if isinstance(business_output.get("business_flags"), dict)
        else {}
    )
    business_debug = (
        business_output.get("business_debug")
        if isinstance(business_output.get("business_debug"), dict)
        else {}
    )
    if business_flags.get("needs_clarification"):
        clarification_answer = _normalize_bold_only_markdown(
            business_answer
            or "Pour une recommandation fiable, précise horizon, montant, fiscalité et objectif."
        )
        clarification_details = "Actions proposées:\n" + "\n".join(
            [f"- {a}" for a in business_actions[:3]]
        ) if business_actions else clarification_answer
        return {
            "answer": clarification_answer,
            "details": clarification_details,
            "used_facts": [],
            "warnings": business_warnings or ["business_needs_clarification"],
            "business_warnings": business_warnings or ["business_needs_clarification"],
            "business_actions": business_actions[:3],
            "business_flags": business_flags,
            "business_debug": business_debug,
        }

    if business_flags.get("mode_analyse_financiere"):
        answer_direct = _normalize_bold_only_markdown(business_answer or full_material)
        details_direct = _normalize_bold_only_markdown(full_material)
        used_facts_direct = [line.strip() for line in answer_direct.splitlines() if line.strip()][:10]
        return {
            "answer": answer_direct,
            "details": details_direct,
            "used_facts": used_facts_direct,
            "warnings": business_warnings,
            "business_warnings": business_warnings,
            "business_actions": business_actions[:3],
            "business_flags": business_flags,
            "business_debug": business_debug,
        }
    material_for_synthesis = business_answer or full_material
    details_for_view_more = full_material
    # === CGP_BUSINESS_LAYER_END ===

    # === SYNTHESIS_AGENT_START ===
    synthesis_mode = "compact"
    if _has_synthesis_signal(question or ""):
        synthesis_mode = "compact"
    synthesis_payload = synthesize_answer(
        question=question or "",
        material=material_for_synthesis,
        mode=synthesis_mode,
    )
    final_answer = _normalize_bold_only_markdown(_clean(str(synthesis_payload.get("answer") or "")))
    final_details = _normalize_bold_only_markdown(_clean(details_for_view_more))
    used_facts = synthesis_payload.get("used_facts") if isinstance(synthesis_payload.get("used_facts"), list) else []
    warnings = synthesis_payload.get("warnings") if isinstance(synthesis_payload.get("warnings"), list) else []
    # === SYNTHESIS_AGENT_END ===

    if not final_answer:
        final_answer = _normalize_bold_only_markdown(material_for_synthesis)
    if not final_details:
        final_details = _normalize_bold_only_markdown(material_for_synthesis)

    if business_actions:
        actions_block = "Actions proposées:\n" + "\n".join([f"- {a}" for a in business_actions[:3]])
        if actions_block.lower() not in final_details.lower():
            final_details = _normalize_bold_only_markdown(final_details + "\n\n" + actions_block)

    return {
        "answer": final_answer,
        "details": final_details,
        "used_facts": used_facts[:10],
        "warnings": list(dict.fromkeys([str(w) for w in (warnings + business_warnings)])),
        "business_warnings": business_warnings,
        "business_actions": business_actions[:3],
        "business_flags": business_flags,
        "business_debug": business_debug,
    }


def orchestrate(
    question: str,
    history: Optional[List[dict]] = None,
    force_agent: Optional[str] = None,
    neutral_pure: Optional[bool] = None,
    audit_detail: Optional[bool] = None,
    portfolio_simulation_input: Optional[Dict[str, Any]] = None,
    scoring_version: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Orchestrateur principal Darwin:
    - détecte l'intention,
    - choisit les agents,
    - exécute les outils,
    - synthétise la réponse finale.
    """
    _ = audit_detail, portfolio_simulation_input, scoring_version, session_state
    history = history or []
    neutral_mode = NEUTRAL_PURE_DEFAULT if neutral_pure is None else bool(neutral_pure)
    question = _clean(question or "")

    agent_outputs: List[Dict[str, Any]] = []
    agents_called: List[str] = []

    force_key = (force_agent or "").strip().lower()
    intent_raw = _detect_intent(question, neutral_pure=neutral_mode, history=history)

    force_map = {
        "sql_kpi": "sql_kpi",
        "kpi": "sql_kpi",
        "core": "core",
        "web": "online",
        "online": "online",
        "rapport": "rapport",
    }
    force_intent_map = {
        "strategic_allocation": INTENT_STRATEGIC_ALLOCATION,
        "factual_kpi": INTENT_FACTUAL_KPI,
        "comparison": INTENT_COMPARISON,
        "darwin_specific": INTENT_DARWIN_SPECIFIC,
        "regulatory": INTENT_REGULATORY,
    }

    if neutral_mode and force_key == "core":
        force_key = "sql_kpi"
    if neutral_mode and force_key == "darwin_specific":
        force_key = "comparison"

    forced_agent_name: Optional[str] = None
    if force_key in force_intent_map:
        intent = force_intent_map[force_key]
    elif force_key in force_map:
        forced_agent_name = force_map[force_key]
        if forced_agent_name == "rapport":
            intent = INTENT_RAPPORT
        elif forced_agent_name == "online":
            intent = INTENT_REGULATORY
        elif forced_agent_name == "sql_kpi":
            intent = INTENT_FACTUAL_KPI
        else:
            intent = INTENT_DARWIN_SPECIFIC if not neutral_mode else INTENT_COMPARISON
    else:
        intent = intent_raw

    # === STRATEGIC_LAYER_START ===
    strategic_decision = strategic_decide(
        question=question,
        history=history,
        intent=intent,
        flags={
            "neutral_pure": neutral_mode,
            "darwin_context": _is_darwin_query(question),
            "scpi_context": _is_scpi_context(question),
            "prefer_live_web_default": PREFER_LIVE_WEB_BY_DEFAULT,
        },
    )
    strategic_payload = strategic_decision.to_dict()
    strategic_clarification_questions: List[str] = []

    intent = strategic_decision.intent_refined or intent
    routing_agents = strategic_decision.routing.get("agents_order")
    if isinstance(routing_agents, list):
        sequence = routing_agents
    else:
        sequence = _agent_sequence_for_intent(
            intent,
            neutral_pure=neutral_mode,
            question=question,
        )

    # Conserve le comportement force_agent existant.
    if forced_agent_name == "rapport":
        sequence = _agent_sequence_for_intent(INTENT_RAPPORT, neutral_pure=neutral_mode, question=question)
    elif forced_agent_name == "online":
        sequence = ["online"]
    elif forced_agent_name == "sql_kpi":
        sequence = ["sql_kpi"]
    elif forced_agent_name == "core":
        sequence = ["core"] if not neutral_mode else ["online", "sql_kpi"]

    if strategic_decision.needs_clarification:
        dedup_questions: List[str] = []
        seen_questions = set()
        for item in strategic_decision.clarifying_questions:
            question_text = _clean(str(item))
            if not question_text:
                continue
            question_key = _normalize_ascii(question_text)
            if question_key in seen_questions:
                continue
            seen_questions.add(question_key)
            dedup_questions.append(question_text)
        strategic_clarification_questions = dedup_questions[:2]
    # === STRATEGIC_LAYER_END ===

    clarification = _clarification_needed(
        question=question,
        intent=intent,
        history=history,
    )
    clarification_preflight_reason: Optional[str] = None
    if clarification and clarification.get("needed"):
        clarification_preflight_reason = _clean(str(clarification.get("reason") or "clarification_required"))
        message = _clean(str(clarification.get("message") or ""))
        parsed_questions: List[str] = []
        for raw in message.splitlines():
            line = _clean(re.sub(r"^[\-\*\u2022]\s*", "", raw))
            if not line:
                continue
            if line.endswith("?"):
                parsed_questions.append(line)
        if not parsed_questions and message:
            parsed_questions = [f"Peux-tu préciser ta demande: {message[:140]}?"]
        strategic_clarification_questions = _coerce_questions_by_intent(
            _contract_intent_from_internal(question=question, intent=intent),
            strategic_clarification_questions + parsed_questions,
        )

    print(f"[FINALIZER] question='{question}' -> intent='{intent}' -> sequence={sequence}")

    allow_parallel_core_online = bool(
        (strategic_decision.routing or {}).get("allow_parallel_core_online", True)
    )
    ran_parallel = False
    if PARALLEL_CORE_ONLINE and allow_parallel_core_online and "core" in sequence and "online" in sequence:
        core_online_outputs = _run_core_online_parallel(
            question,
            history,
            sequence,
        )
        for out in core_online_outputs:
            agent_outputs.append(out)
            agents_called.append(out.get("agent", "unknown"))
        ran_parallel = len(core_online_outputs) >= 2

    for agent_name in sequence:
        if ran_parallel and agent_name in {"core", "online"}:
            continue

        if agent_name == "core":
            out = _run_agent("core", question, history)
            agent_outputs.append(out)
            agents_called.append("core")
            continue

        if agent_name == "sql_kpi":
            out = _run_agent("sql_kpi", question, history)
            agent_outputs.append(out)
            agents_called.append("sql_kpi")
            continue

        if agent_name == "online":
            out = _run_agent(
                "online",
                question,
                history,
            )
            agent_outputs.append(out)
            agents_called.append("web")
            continue

        if agent_name == "rapport":
            # Le rapport exploite la matière déjà produite par core/web.
            out = ask_rapport(question, history=history, materials=agent_outputs)
            agent_outputs.append({"agent": "rapport", **out})
            agents_called.append("rapport")
            continue

    # Fallback online si on a uniquement core et que la matière est insuffisante.
    if agents_called == ["core"]:
        core_draft = _draft_from(agent_outputs, "core")
        if _is_insufficient(core_draft):
            web_out = ask_online(
                question,
                history=history,
            )
            agent_outputs.append({"agent": "web", **web_out})
            agents_called.append("web")

    web_out = _out_by_agent(agent_outputs, "web")
    has_live_web = _web_has_live_signal(web_out) if web_out else False
    strict_realtime_required = _requires_live_web(
        intent=intent,
        force_agent=force_key,
        sequence=sequence,
        question=question,
    )
    strategic_requires_realtime = bool(
        (strategic_decision.constraints or {}).get("real_time_required")
        or (strategic_decision.routing or {}).get("strict_realtime_required")
    )
    if strategic_requires_realtime:
        strict_realtime_required = True
    strict_realtime_blocked_initial = strict_realtime_required and not has_live_web
    estimation_mode_auto = False
    estimation_reason = _clean(str(((web_out.get("meta") or {}).get("warning")) if web_out else ""))

    if strict_realtime_blocked_initial and STRICT_REALTIME_AUTO_ESTIMATION_FALLBACK:
        try:
            estimation_raw = ask_online(
                question,
                history=history,
                strict_realtime=False,
                skip_web_search=True,
            )
            estimation_out = {"agent": "web", **estimation_raw}
            estimation_draft = _clean(estimation_out.get("draft"))
            if estimation_draft and not _is_insufficient(estimation_draft):
                # Remplace la sortie web "strict realtime impossible" par une estimation explicite.
                agent_outputs = [out for out in agent_outputs if out.get("agent") != "web"]
                agent_outputs.append(estimation_out)
                if "web" not in agents_called:
                    agents_called.append("web")
                web_out = estimation_out
                has_live_web = _web_has_live_signal(web_out)
                estimation_mode_auto = True
                estimation_warning = _clean(
                    str(((estimation_out.get("meta") or {}).get("warning")) or "")
                )
                if estimation_warning and not estimation_reason:
                    estimation_reason = estimation_warning
        except Exception as fallback_exc:
            if not estimation_reason:
                estimation_reason = _clean(str(fallback_exc))

    sources = _normalize_sources(agent_outputs)
    sources_by_layer = _normalize_sources_by_layer(agent_outputs)
    darwin_context = _is_darwin_query(question)
    scpi_context = _is_scpi_context(question)
    latest_consolidated_date = _extract_latest_consolidated_date(sources_by_layer)
    consolidated_fresh = _is_within_freshness_threshold(
        latest_consolidated_date,
        SCPI_FRESHNESS_THRESHOLD_DAYS,
    )
    consolidated_override = (
        darwin_context
        and not has_live_web
        and bool(sources_by_layer.get("sql_kpi") or sources_by_layer.get("rag_darwin"))
        and (consolidated_fresh or latest_consolidated_date is None)
    )
    strict_realtime_blocked = (
        strict_realtime_required
        and not has_live_web
        and not consolidated_override
        and not estimation_mode_auto
    )
    strict_realtime_soft_downgraded = False
    if (
        strict_realtime_blocked
        and force_key not in {"web", "online"}
        and intent in {INTENT_FACTUAL_KPI, INTENT_COMPARISON, INTENT_STRATEGIC_ALLOCATION}
    ):
        strict_realtime_blocked = False
        strict_realtime_soft_downgraded = True
        estimation_mode_auto = True
        if not estimation_reason:
            estimation_reason = "no_results_strict_realtime"
    consolidated_message = _build_scpi_consolidated_message(latest_consolidated_date) if darwin_context else ""
    selected = _best_material(
        agent_outputs,
        intent=intent,
        neutral_pure=neutral_mode,
        question=question,
    )

    # === SYNTHESIS_AGENT_START ===
    if strict_realtime_blocked:
        blocked_answer = _normalize_bold_only_markdown(
            _strict_realtime_block_message(question, web_out)
        )
        final_payload = {
            "answer": blocked_answer,
            "details": "",
            "used_facts": [],
            "warnings": ["no_results_strict_realtime"],
            "business_warnings": [],
            "business_flags": {},
            "business_debug": {},
        }
    else:
        final_payload = finalize(
            question=question,
            history=history,
            agent_outputs=agent_outputs,
            neutral_pure=neutral_mode,
        )
    final_answer = _normalize_bold_only_markdown(
        _clean(str(final_payload.get("answer") or ""))
    )
    final_details = _normalize_bold_only_markdown(
        _clean(str(final_payload.get("details") or ""))
    )
    final_used_facts = final_payload.get("used_facts") if isinstance(final_payload.get("used_facts"), list) else []
    final_warnings = final_payload.get("warnings") if isinstance(final_payload.get("warnings"), list) else []
    final_business_warnings = (
        final_payload.get("business_warnings")
        if isinstance(final_payload.get("business_warnings"), list)
        else []
    )
    # === SYNTHESIS_AGENT_END ===

    if estimation_mode_auto:
        estimation_note = "Mode estimation active (non temps réel): pas de signal web live fiable."
        if estimation_reason:
            estimation_note += f" Raison initiale: {estimation_reason}."
        if estimation_note.lower() not in final_answer.lower():
            final_answer = _normalize_bold_only_markdown(final_answer + "\n\n" + estimation_note)

    if darwin_context and not strict_realtime_blocked and consolidated_message:
        if consolidated_message.lower() not in final_answer.lower():
            final_answer = _normalize_bold_only_markdown(final_answer + "\n\n" + consolidated_message)

    enforced_answer, concrete_warnings = _enforce_concrete_data_answer(
        question=question,
        intent=intent,
        answer=final_answer,
        material=final_details or "",
        sources_by_layer=sources_by_layer,
        latest_consolidated_date=latest_consolidated_date,
    )
    final_answer = _normalize_bold_only_markdown(_clean(enforced_answer))
    if concrete_warnings:
        final_warnings = list(dict.fromkeys([str(w) for w in (final_warnings + concrete_warnings)]))

    freshness_answer, freshness_warnings = _enforce_freshness_and_availability(
        question=question,
        intent=intent,
        answer=final_answer,
        sources_by_layer=sources_by_layer,
        latest_consolidated_date=latest_consolidated_date,
    )
    final_answer = _normalize_bold_only_markdown(_clean(freshness_answer))
    if freshness_warnings:
        final_warnings = list(dict.fromkeys([str(w) for w in (final_warnings + freshness_warnings)]))

    if strategic_clarification_questions:
        final_warnings = list(
            dict.fromkeys([str(w) for w in (final_warnings + ["strategic_clarification_required_non_blocking"])])
        )

    response_format = "text_markdown_v1"
    answer_json: Optional[Dict[str, Any]] = None
    final_answer_structured: Optional[Dict[str, Any]] = None
    answer_structured_v2: Optional[Dict[str, Any]] = None
    answer_contract: Optional[Dict[str, str]] = None

    rewritten_v2, rewritten_text, rewrite_engine, rewrite_warnings = _rewrite_with_contract_llm(
        question=question,
        intent=intent,
        history=history,
        answer_draft=final_answer,
        sources_by_layer=sources_by_layer,
        seed_questions=strategic_clarification_questions,
        selected_agent=str(selected.get("agent") or ""),
        strategy_notes=strategic_decision.notes_for_synthesizer,
    )
    if rewritten_v2 and rewritten_text:
        answer_structured_v2 = rewritten_v2
        answer_contract = {
            "intent": str(rewritten_v2.get("intent") or CGP_INTENT_KPI),
            "kpi_target": str(rewritten_v2.get("kpi_target") or "none"),
        }
        answer_json = _legacy_structured_from_contract(rewritten_v2)
        final_answer_structured = answer_json
        final_answer = rewritten_text
        response_format = "structured_contract_v2"
    if rewrite_warnings:
        final_warnings = list(dict.fromkeys([str(w) for w in (final_warnings + rewrite_warnings)]))

    final_business_actions = (
        final_payload.get("business_actions")
        if isinstance(final_payload.get("business_actions"), list)
        else []
    )
    final_business_flags = (
        final_payload.get("business_flags")
        if isinstance(final_payload.get("business_flags"), dict)
        else {}
    )
    final_business_debug = (
        final_payload.get("business_debug")
        if isinstance(final_payload.get("business_debug"), dict)
        else {}
    )

    answer_text = final_answer
    contract_intent_effective = (
        (answer_contract or {}).get("intent")
        or _contract_intent_from_internal(question=question, intent=intent)
    )
    rewrite_role_context = _rewrite_role_context(
        question=question,
        internal_intent=intent,
        contract_intent=str(contract_intent_effective),
        selected_agent=str(selected.get("agent") or ""),
        strategy_notes=strategic_decision.notes_for_synthesizer,
    )
    top_diag = (
        answer_structured_v2.get("top_diagnostics")
        if isinstance(answer_structured_v2, dict) and isinstance(answer_structured_v2.get("top_diagnostics"), dict)
        else {}
    )
    top_received_count_raw = top_diag.get("received_count", 0)
    top_valid_count_raw = top_diag.get("valid_count", 0)
    top_rejected_count_raw = top_diag.get("rejected_count", 0)
    top_received_count = int(top_received_count_raw) if str(top_received_count_raw).isdigit() else 0
    top_valid_count = int(top_valid_count_raw) if str(top_valid_count_raw).isdigit() else 0
    top_rejected_count = int(top_rejected_count_raw) if str(top_rejected_count_raw).isdigit() else 0
    top_name_sanitizer_applied = bool(top_diag.get("sanitizer_applied", False))
    top_name_resolution_mode = str(top_diag.get("resolution_mode") or "deterministic_fallback")
    if top_name_resolution_mode not in {"llm_raw", "resolved_from_source", "deterministic_fallback"}:
        top_name_resolution_mode = "deterministic_fallback"

    return {
        "answer": final_answer,
        "answer_text": answer_text,
        "answer_json": answer_json,
        "answer_structured": final_answer_structured,
        "answer_structured_v2": answer_structured_v2,
        "answer_contract": answer_contract,
        "details": final_details,
        "used_facts": final_used_facts,
        "warnings": final_warnings,
        "sources": sources,
        "sources_by_layer": sources_by_layer,
        "agent_used": "darwin_finalizer",
        "meta": {
            "intent": intent,
            "agents_called": agents_called,
            "agent_outputs_count": len(agent_outputs),
            "selected_agent": selected.get("agent"),
            "selected_layer": _agent_to_layer(str(selected.get("agent", ""))),
            "parallel_core_online": ran_parallel,
            "prefer_live_web_default": PREFER_LIVE_WEB_BY_DEFAULT,
            "live_web_signal": has_live_web,
            "strict_realtime_guard": STRICT_REALTIME_GUARD,
            "strict_realtime_auto_estimation_fallback_enabled": STRICT_REALTIME_AUTO_ESTIMATION_FALLBACK,
            "strict_realtime_required": strict_realtime_required,
            "strict_realtime_blocked_initial": strict_realtime_blocked_initial,
            "strict_realtime_blocked": strict_realtime_blocked,
            "strict_realtime_soft_downgraded": strict_realtime_soft_downgraded,
            "strict_realtime_overridden_by_scpi_consolidated": consolidated_override,
            "strict_realtime_auto_estimation_used": estimation_mode_auto,
            "strict_realtime_auto_estimation_reason": estimation_reason or None,
            "darwin_context": darwin_context,
            "scpi_context": scpi_context,
            "freshness_threshold_days": SCPI_FRESHNESS_THRESHOLD_DAYS,
            "latest_consolidated_date": latest_consolidated_date.isoformat() if latest_consolidated_date else None,
            "consolidated_data_is_fresh": consolidated_fresh,
            "consolidated_message": consolidated_message if darwin_context else None,
            "neutral_pure": neutral_mode,
            "neutral_exclusions": [
                "rag_darwin_index",
                "darwin_intent_tie_break_bias",
            ] if neutral_mode else [],
            "concrete_data_rule_applied": bool(concrete_warnings),
            "concrete_data_rule_warnings": concrete_warnings,
            "freshness_rule_applied": bool(freshness_warnings),
            "freshness_rule_warnings": freshness_warnings,
            "response_format": response_format,
            "cgp_json_enforced": bool(answer_json),
            "finalizer_structured_rewrite_enabled": FINALIZER_STRUCTURED_REWRITE_ENABLED,
            "finalizer_structured_rewrite_applied": bool(answer_json),
            "finalizer_structured_format": (
                (answer_json or {}).get("format")
                if isinstance(answer_json, dict)
                else None
            ),
            "intent_cgp": (
                contract_intent_effective
            ),
            "kpi_target": (
                (answer_contract or {}).get("kpi_target")
                or _extract_contract_kpi_target(question)
            ),
            "contract_format_enforced": bool(answer_structured_v2),
            "contract_rewrite_engine": rewrite_engine,
            "contract_rewrite_agent_source": rewrite_role_context.get("agent_source"),
            "contract_rewrite_role": rewrite_role_context.get("role_editorial"),
            "top_items_received_count": top_received_count,
            "top_items_valid_count": top_valid_count,
            "top_items_rejected_count": top_rejected_count,
            "top_name_sanitizer_applied": top_name_sanitizer_applied,
            "top_name_resolution_mode": top_name_resolution_mode,
            "finalizer_llm_provider_requested": FINALIZER_LLM_PROVIDER_REQUESTED,
            "finalizer_llm_provider_effective": FINALIZER_LLM_PROVIDER_EFFECTIVE,
            "finalizer_llm_model": FINALIZER_MODEL,
            "response_mode": strategic_decision.response_mode,
            "must_include": strategic_decision.must_include,
            "notes_for_synthesizer": strategic_decision.notes_for_synthesizer,
            "strategic_decision": strategic_payload,
            "business_layer_version": final_business_debug.get("version", CGPBusinessLayer.VERSION),
            "business_layer_confidence": final_business_debug.get("confidence"),
            "business_checks": final_business_debug.get("rules_triggered", []),
            "business_actions": final_business_actions,
            "business_warnings": final_business_warnings,
            "business_flags": final_business_flags,
            "business_debug": final_business_debug,
            "clarification_requested": bool(strategic_clarification_questions),
            "clarification_reason": (
                clarification_preflight_reason
                or ("strategic_layer_non_blocking" if strategic_clarification_questions else None)
            ),
            "clarification_missing_fields": strategic_clarification_questions,
            "intent_raw": intent_raw,
            "intent_inherited": None,
            "intent_final": intent,
            "followup_flow_active": False,
            "followup_phase": "idle",
            "effective_query_used": question,
            "session_state_patch": {},
        },
    }


def orchestrate_v2(
    question: str,
    history: Optional[List[dict]] = None,
    force_agent: Optional[str] = None,
    neutral_pure: Optional[bool] = None,
    audit_detail: Optional[bool] = None,
    portfolio_simulation_input: Optional[Dict[str, Any]] = None,
    scoring_version: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Darwin v2 (simple mode):
    - collecte web/docs/KPI,
    - synthèse courte,
    - finalisation stricte TOP/KPI/STRATEGIE via contrat v2.
    """
    _ = audit_detail, portfolio_simulation_input, scoring_version, session_state
    history = history or []
    neutral_mode = NEUTRAL_PURE_DEFAULT if neutral_pure is None else bool(neutral_pure)
    question = _clean(question or "")

    agent_outputs: List[Dict[str, Any]] = []
    agents_called: List[str] = []

    force_key = (force_agent or "").strip().lower()
    intent_raw = _detect_intent(question, neutral_pure=neutral_mode, history=history)
    intent = intent_raw
    if _should_force_strategic_intent(question):
        intent = INTENT_STRATEGIC_ALLOCATION

    force_map = {
        "sql_kpi": "sql_kpi",
        "kpi": "sql_kpi",
        "core": "core",
        "web": "online",
        "online": "online",
        "rapport": "rapport",
    }
    force_intent_map = {
        "strategic_allocation": INTENT_STRATEGIC_ALLOCATION,
        "factual_kpi": INTENT_FACTUAL_KPI,
        "comparison": INTENT_COMPARISON,
        "darwin_specific": INTENT_DARWIN_SPECIFIC,
        "regulatory": INTENT_REGULATORY,
    }

    if force_key in force_intent_map:
        intent = force_intent_map[force_key]
    forced_agent_name = force_map.get(force_key)

    if forced_agent_name:
        if forced_agent_name == "rapport":
            sequence = ["online", "core", "sql_kpi", "rapport"]
        else:
            sequence = [forced_agent_name]
    else:
        if neutral_mode:
            sequence = ["online", "sql_kpi"]
        elif intent == INTENT_REGULATORY:
            sequence = ["online", "sql_kpi"]
        elif intent == INTENT_FACTUAL_KPI:
            sequence = ["online", "sql_kpi"]
        elif intent == INTENT_COMPARISON:
            sequence = ["online", "sql_kpi", "core"]
        elif intent == INTENT_DARWIN_SPECIFIC:
            sequence = ["online", "core", "sql_kpi"]
        else:
            sequence = ["online", "core", "sql_kpi"]
    sequence = list(dict.fromkeys(sequence))

    for agent_name in sequence:
        try:
            if agent_name == "online":
                out = {
                    "agent": "web",
                    **ask_online(
                        question,
                        history=history,
                        strict_realtime=False,
                    ),
                }
                agent_outputs.append(out)
                agents_called.append("web")
                continue

            if agent_name == "core":
                out = {"agent": "core", **ask_core(question, history=history)}
                agent_outputs.append(out)
                agents_called.append("core")
                continue

            if agent_name == "sql_kpi":
                out = {"agent": "sql_kpi", **ask_sql_kpi(question, history=history)}
                agent_outputs.append(out)
                agents_called.append("sql_kpi")
                continue

            if agent_name == "rapport":
                out = ask_rapport(question, history=history, materials=agent_outputs)
                agent_outputs.append({"agent": "rapport", **out})
                agents_called.append("rapport")
                continue
        except Exception as exc:
            fallback_agent = "web" if agent_name == "online" else agent_name
            fallback_tool = "online" if agent_name == "online" else agent_name
            agent_outputs.append(
                {
                    "agent": fallback_agent,
                    "draft": f"❌ Erreur agent {agent_name}: {str(exc)}",
                    "sources": [],
                    "meta": {"tool": fallback_tool, "error": str(exc)},
                }
            )
            agents_called.append(fallback_agent)

    if not agent_outputs:
        agent_outputs = [
            {
                "agent": "web",
                "draft": "Non trouvé dans les sources fournies",
                "sources": [],
                "meta": {"tool": "online", "warning": "v2_no_agent_output"},
            }
        ]
        agents_called = ["web"]

    sources = _normalize_sources(agent_outputs)
    sources_by_layer = _normalize_sources_by_layer(agent_outputs)
    selected = _best_material(
        agent_outputs=agent_outputs,
        intent=intent,
        neutral_pure=neutral_mode,
        question=question,
    )

    final_payload = finalize(
        question=question,
        history=history,
        agent_outputs=agent_outputs,
        neutral_pure=neutral_mode,
    )
    final_answer = _normalize_bold_only_markdown(_clean(str(final_payload.get("answer") or "")))
    final_details = _normalize_bold_only_markdown(_clean(str(final_payload.get("details") or "")))
    final_used_facts = final_payload.get("used_facts") if isinstance(final_payload.get("used_facts"), list) else []
    final_warnings = final_payload.get("warnings") if isinstance(final_payload.get("warnings"), list) else []
    final_business_warnings = (
        final_payload.get("business_warnings")
        if isinstance(final_payload.get("business_warnings"), list)
        else []
    )
    final_business_actions = (
        final_payload.get("business_actions")
        if isinstance(final_payload.get("business_actions"), list)
        else []
    )
    final_business_flags = (
        final_payload.get("business_flags")
        if isinstance(final_payload.get("business_flags"), dict)
        else {}
    )
    final_business_debug = (
        final_payload.get("business_debug")
        if isinstance(final_payload.get("business_debug"), dict)
        else {}
    )

    if not final_answer:
        final_answer = _clean(str(selected.get("draft") or "Non trouvé dans les sources fournies"))

    contract_intent_seed = _contract_intent_from_internal(question=question, intent=intent)
    if _should_force_strategic_intent(question):
        contract_intent_seed = CGP_INTENT_STRATEGIE
    kpi_target_seed = _extract_contract_kpi_target(question)
    strategic_clarification_questions = _coerce_questions_by_intent(
        contract_intent_seed,
        _collect_contract_clarification_questions(
            question=question,
            contract_intent=contract_intent_seed,
            kpi_target=kpi_target_seed,
        ),
    )

    rewritten_v2, rewritten_text, rewrite_engine, rewrite_warnings = _rewrite_with_contract_llm(
        question=question,
        intent=intent,
        history=history,
        answer_draft=final_answer,
        sources_by_layer=sources_by_layer,
        seed_questions=strategic_clarification_questions,
        selected_agent=str(selected.get("agent") or ""),
        strategy_notes=[],
    )
    if rewrite_warnings:
        final_warnings = list(dict.fromkeys([str(w) for w in (final_warnings + rewrite_warnings)]))

    answer_structured_v2: Optional[Dict[str, Any]] = None
    if isinstance(rewritten_v2, dict) and rewritten_v2:
        answer_structured_v2 = rewritten_v2
    else:
        fallback_payload = _build_contract_payload_deterministic(
            question=question,
            contract_intent=contract_intent_seed,
            kpi_target=kpi_target_seed,
            answer_draft=final_answer,
            sources_by_layer=sources_by_layer,
            seed_questions=strategic_clarification_questions,
        )
        fallback_text = _render_contract_payload_text(fallback_payload)
        fallback_text, fallback_warn = _validate_rendered_contract_text(contract_intent_seed, fallback_text)
        fallback_payload["rendered_text"] = fallback_text
        answer_structured_v2 = fallback_payload
        rewrite_engine = "deterministic_fallback"
        if fallback_warn:
            final_warnings = list(dict.fromkeys([str(w) for w in (final_warnings + fallback_warn)]))

    rendered_v2 = _clean(str((answer_structured_v2 or {}).get("rendered_text") or ""))
    if rewritten_text:
        rendered_v2 = rewritten_text
    if not rendered_v2:
        rendered_v2 = _render_contract_payload_text(answer_structured_v2 or {})
        rendered_v2, rendered_warn = _validate_rendered_contract_text(
            str((answer_structured_v2 or {}).get("intent") or contract_intent_seed),
            rendered_v2,
        )
        if rendered_warn:
            final_warnings = list(dict.fromkeys([str(w) for w in (final_warnings + rendered_warn)]))
    if isinstance(answer_structured_v2, dict):
        answer_structured_v2["rendered_text"] = rendered_v2

    answer_contract = {
        "intent": str((answer_structured_v2 or {}).get("intent") or contract_intent_seed or CGP_INTENT_KPI),
        "kpi_target": str((answer_structured_v2 or {}).get("kpi_target") or kpi_target_seed or "none"),
    }
    answer_json = _legacy_structured_from_contract(answer_structured_v2 or {})
    final_answer_structured = answer_json
    final_answer = rendered_v2
    answer_text = rendered_v2
    response_format = "structured_contract_v2"

    rewrite_role_context = _rewrite_role_context(
        question=question,
        internal_intent=intent,
        contract_intent=answer_contract["intent"],
        selected_agent=str(selected.get("agent") or ""),
        strategy_notes=[],
    )
    top_diag = (
        answer_structured_v2.get("top_diagnostics")
        if isinstance(answer_structured_v2, dict) and isinstance(answer_structured_v2.get("top_diagnostics"), dict)
        else {}
    )
    top_received_count_raw = top_diag.get("received_count", 0)
    top_valid_count_raw = top_diag.get("valid_count", 0)
    top_rejected_count_raw = top_diag.get("rejected_count", 0)
    top_received_count = int(top_received_count_raw) if str(top_received_count_raw).isdigit() else 0
    top_valid_count = int(top_valid_count_raw) if str(top_valid_count_raw).isdigit() else 0
    top_rejected_count = int(top_rejected_count_raw) if str(top_rejected_count_raw).isdigit() else 0
    top_name_sanitizer_applied = bool(top_diag.get("sanitizer_applied", False))
    top_name_resolution_mode = str(top_diag.get("resolution_mode") or "deterministic_fallback")
    if top_name_resolution_mode not in {"llm_raw", "resolved_from_source", "deterministic_fallback"}:
        top_name_resolution_mode = "deterministic_fallback"

    web_out = _out_by_agent(agent_outputs, "web")
    has_live_web = _web_has_live_signal(web_out) if web_out else False

    return {
        "answer": final_answer,
        "answer_text": answer_text,
        "answer_json": answer_json,
        "answer_structured": final_answer_structured,
        "answer_structured_v2": answer_structured_v2,
        "answer_contract": answer_contract,
        "details": final_details,
        "used_facts": final_used_facts,
        "warnings": final_warnings,
        "sources": sources,
        "sources_by_layer": sources_by_layer,
        "agent_used": "darwin_v2_finalizer",
        "meta": {
            "darwin_version": "v2",
            "simple_mode": True,
            "intent": intent,
            "intent_raw": intent_raw,
            "intent_inherited": None,
            "intent_final": intent,
            "agents_called": agents_called,
            "agent_outputs_count": len(agent_outputs),
            "selected_agent": selected.get("agent"),
            "selected_layer": _agent_to_layer(str(selected.get("agent", ""))),
            "neutral_pure": neutral_mode,
            "live_web_signal": has_live_web,
            "strict_realtime_required": False,
            "strict_realtime_blocked": False,
            "response_format": response_format,
            "cgp_json_enforced": bool(answer_json),
            "finalizer_structured_rewrite_enabled": FINALIZER_STRUCTURED_REWRITE_ENABLED,
            "finalizer_structured_rewrite_applied": bool(answer_structured_v2),
            "finalizer_structured_format": (
                (answer_json or {}).get("format")
                if isinstance(answer_json, dict)
                else None
            ),
            "intent_cgp": answer_contract["intent"],
            "kpi_target": answer_contract["kpi_target"],
            "contract_format_enforced": bool(answer_structured_v2),
            "contract_rewrite_engine": rewrite_engine,
            "contract_rewrite_agent_source": rewrite_role_context.get("agent_source"),
            "contract_rewrite_role": rewrite_role_context.get("role_editorial"),
            "top_items_received_count": top_received_count,
            "top_items_valid_count": top_valid_count,
            "top_items_rejected_count": top_rejected_count,
            "top_name_sanitizer_applied": top_name_sanitizer_applied,
            "top_name_resolution_mode": top_name_resolution_mode,
            "finalizer_llm_provider_requested": FINALIZER_LLM_PROVIDER_REQUESTED,
            "finalizer_llm_provider_effective": FINALIZER_LLM_PROVIDER_EFFECTIVE,
            "finalizer_llm_model": FINALIZER_MODEL,
            "darwin_context": _is_darwin_query(question),
            "scpi_context": _is_scpi_context(question),
            "clarification_requested": bool(strategic_clarification_questions),
            "clarification_reason": (
                "strategic_layer_non_blocking" if strategic_clarification_questions else None
            ),
            "clarification_missing_fields": strategic_clarification_questions,
            "business_layer_version": final_business_debug.get("version", CGPBusinessLayer.VERSION),
            "business_layer_confidence": final_business_debug.get("confidence"),
            "business_checks": final_business_debug.get("rules_triggered", []),
            "business_actions": final_business_actions,
            "business_warnings": final_business_warnings,
            "business_flags": final_business_flags,
            "business_debug": final_business_debug,
            "followup_flow_active": False,
            "followup_phase": "idle",
            "effective_query_used": question,
            "session_state_patch": {},
        },
    }
