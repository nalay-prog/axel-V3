# agent_online.py - Version Optimisée
import os
import time
import logging
import warnings
import re
import html
import json
from urllib.parse import parse_qs, unquote, urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from types import SimpleNamespace
from typing import Optional, Dict, List, Tuple, Any
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
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
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    from ddgs import DDGS
    DDGS_PROVIDER = "ddgs"
except ImportError:
    try:
        from duckduckgo_search import DDGS
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
try:
    from .claude_agent_optimized import claude_agent
except Exception:  # pragma: no cover
    try:
        from backend.agents.claude_agent_optimized import claude_agent
    except Exception:
        claude_agent = None
try:
    from .question_detector import detector
except Exception:  # pragma: no cover
    try:
        from backend.agents.question_detector import detector  # type: ignore
    except Exception:
        detector = None
try:
    from .router_finalizer_checker import (
        answer_with_router_finalizer_checker,
        answer_with_cabinet_pipeline,
        is_router_pipeline_available,
        render_router_output_text,
    )
except Exception:  # pragma: no cover
    try:
        from backend.agents.router_finalizer_checker import (  # type: ignore
            answer_with_router_finalizer_checker,
            answer_with_cabinet_pipeline,
            is_router_pipeline_available,
            render_router_output_text,
        )
    except Exception:
        answer_with_router_finalizer_checker = None
        answer_with_cabinet_pipeline = None
        render_router_output_text = None
        is_router_pipeline_available = None
try:
    from .web_search_prioritized import web_search_prioritized
except Exception:  # pragma: no cover
    try:
        from backend.agents.web_search_prioritized import web_search_prioritized  # type: ignore
    except Exception:
        web_search_prioritized = None

# Configuration logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
        tried: List[str] = []
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
                text = "\n".join([p for p in parts if p]).strip()
                return SimpleNamespace(content=text)
            except HTTPError as exc:
                if getattr(exc, "code", None) == 404:
                    tried.append(model_name)
                    continue
                raise
            except Exception as exc:
                msg = str(exc).lower()
                if "not_found_error" in msg or "model:" in msg and "404" in msg:
                    tried.append(model_name)
                    continue
                raise
        raise RuntimeError(
            "Anthropic model indisponible pour tous les candidats: "
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


def _build_web_llm():
    openai_key = _read_env_prefer_non_placeholder("OPENAI_API_KEY")
    anthropic_key = _read_env_prefer_non_placeholder("ANTHROPIC_API_KEY")

    default_provider = "anthropic" if (anthropic_key and not _is_placeholder_key(anthropic_key)) else "openai"
    requested_provider = str(
        os.getenv("WEB_LLM_PROVIDER", os.getenv("LLM_PROVIDER", default_provider))
    ).strip().lower() or default_provider
    allow_fallback = _env_flag("WEB_LLM_ALLOW_FALLBACK", _env_flag("LLM_ALLOW_PROVIDER_FALLBACK", True))

    web_openai_model = os.getenv("WEB_OPENAI_MODEL", os.getenv("WEB_MODEL", "gpt-4o-mini"))
    web_anthropic_model = os.getenv(
        "WEB_ANTHROPIC_MODEL",
        os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    )
    web_anthropic_candidates = _anthropic_model_candidates(web_anthropic_model, scope="WEB")

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
                    model=web_anthropic_candidates[0],
                    temperature=0.0,
                    fallback_models=web_anthropic_candidates[1:],
                )
                return client, "anthropic_http", web_anthropic_candidates[0], requested_provider
            except Exception:
                pass
            if ChatAnthropic is not None:
                for candidate_model in web_anthropic_candidates:
                    try:
                        client = ChatAnthropic(model=candidate_model, temperature=0, api_key=anthropic_key)
                        return client, "anthropic_langchain", candidate_model, requested_provider
                    except TypeError:
                        try:
                            client = ChatAnthropic(
                                model=candidate_model,
                                temperature=0,
                                anthropic_api_key=anthropic_key,
                            )
                            return client, "anthropic_langchain", candidate_model, requested_provider
                        except Exception:
                            continue
                    except Exception:
                        continue

        if provider == "openai" and not _is_placeholder_key(openai_key) and ChatOpenAI is not None:
            try:
                client = ChatOpenAI(model=web_openai_model, temperature=0, api_key=openai_key)
                return client, "openai", web_openai_model, requested_provider
            except Exception:
                pass

    return None, "none", "", requested_provider


llm, WEB_LLM_PROVIDER_EFFECTIVE, WEB_MODEL, WEB_LLM_PROVIDER_REQUESTED = _build_web_llm()

# "auto" = essaie Google SerpAPI -> Serper -> CSE (puis DDGS si activé)
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "auto").lower()
SERPAPI_API_KEY = _read_env_prefer_non_placeholder("SERPAPI_API_KEY")
SERPER_API_KEY = _read_env_prefer_non_placeholder("SERPER_API_KEY")
GOOGLE_CSE_API_KEY = _read_env_prefer_non_placeholder("GOOGLE_CSE_API_KEY")
GOOGLE_CSE_CX = _read_env_prefer_non_placeholder("GOOGLE_CSE_CX")
WEB_FALLBACK_TO_DDGS = _env_flag("WEB_FALLBACK_TO_DDGS", True)
WEB_STRICT_REALTIME = _env_flag("WEB_STRICT_REALTIME", True)
WEB_SEARCH_RETRY_ATTEMPTS = max(1, int(os.getenv("WEB_SEARCH_RETRY_ATTEMPTS", "2")))
WEB_USE_CLAUDE_OPTIMIZED = _env_flag("WEB_USE_CLAUDE_OPTIMIZED", True)
WEB_USE_PRIORITIZED_SEARCH = _env_flag("WEB_USE_PRIORITIZED_SEARCH", True)
WEB_USE_ROUTER_FINALIZER_CHECKER = _env_flag("WEB_USE_ROUTER_FINALIZER_CHECKER", True)
WEB_USE_CABINET_PIPELINE = _env_flag("WEB_USE_CABINET_PIPELINE", True)
WEB_ROUTER_FINALIZER_MAX_RETRIES = max(0, int(os.getenv("WEB_ROUTER_FINALIZER_MAX_RETRIES", "2")))
DDGS_AVAILABLE = DDGS is not None

SERPAPI_AUTH_DISABLED = False
SERPER_AUTH_DISABLED = False
GOOGLE_CSE_AUTH_DISABLED = False

_DEFAULT_PRIORITY_DOMAINS = "aspim.fr,amf-france.org,pierrepapier.fr,francescpi.com,centraledesscpi.com"
_RAW_PRIORITY_DOMAINS = os.getenv("WEB_PRIORITY_DOMAINS", _DEFAULT_PRIORITY_DOMAINS)


def _parse_priority_domains(raw: str) -> List[str]:
    domains: List[str] = []
    for item in (raw or "").split(","):
        domain = (item or "").strip().lower()
        if not domain:
            continue
        domain = domain.replace("https://", "").replace("http://", "")
        domain = domain.split("/", 1)[0].lstrip("www.").strip()
        if domain and domain not in domains:
            domains.append(domain)
    return domains


WEB_PRIORITY_DOMAINS = _parse_priority_domains(_RAW_PRIORITY_DOMAINS)

PRIORITY_DOMAIN_LABELS = {
    "pierrepapier.fr": "Pierre-Papier",
    "francescpi.com": "France SCPI",
    "france-scpi.fr": "France SCPI",
    "aspim.fr": "ASPIM",
    "amf-france.org": "AMF",
    "centraledesscpi.com": "La Centrale des SCPI",
    "meilleurescpi.com": "MeilleureSCPI.com",
    "francetransactions.com": "France Transactions",
    "primaliance.com": "Primaliance",
    "louveinvest.com": "Louve Invest",
    "epargnoo.com": "Epargnoo",
    "homunity.com": "Homunity",
    "avenuedesinvestisseurs.fr": "Avenue des Investisseurs",
    "finance-heros.fr": "Finance Heros",
    "capital.fr": "Capital",
    "reddit.com": "Reddit",
    "lesechos.fr": "Les Echos",
}

RELEVANCE_STOPWORDS = {
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
    "par",
    "sur",
    "dans",
    "avec",
    "sans",
    "est",
    "sont",
    "que",
    "qui",
    "quoi",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "donne",
    "moi",
}

RECOMMENDATION_AXES_BY_TYPE = {
    "calcul": [
        "Valider les hypothèses de calcul (frais, fiscalité, horizon).",
        "Comparer un scénario prudent et un scénario central.",
        "Arbitrer sur le rendement net plutôt que brut.",
    ],
    "info": [
        "Prioriser les sources avec données chiffrées récentes.",
        "Vérifier la cohérence entre rendement, frais et liquidité.",
        "Comparer au moins 2 sources avant décision.",
    ],
    "strategie_cgp": [
        "Aligner la recommandation avec l'objectif et l'horizon client.",
        "Construire une allocation progressive plutôt qu'un pari unique.",
        "Documenter les risques principaux et les points de contrôle.",
    ],
    "mixte_calcul_strategie": [
        "Démarrer par un calcul base-case puis tester la sensibilité.",
        "Transformer le résultat en allocation opérationnelle.",
        "Valider les KPI critiques avant arbitrage final.",
    ],
}

KPI_FILTER_KEYWORDS: Dict[str, List[str]] = {
    "td": ["td", "tdvm", "taux de distribution", "rendement"],
    "tof": ["tof", "taux d'occupation financier", "occupation financier"],
    "walt": ["walt", "duree moyenne des baux", "durée moyenne des baux", "baux"],
    "frais": ["frais", "commission", "frais d'entree", "frais entrée"],
    "capitalisation": ["capitalisation", "encours"],
    "collecte": ["collecte", "collecte nette"],
    "prix_part": ["prix de part", "valeur de part", "souscription", "part"],
}
STYLE_INFO = "INFO"
STYLE_CALCUL = "CALCUL"
STYLE_TOP = "TOP"
STYLE_STRATEGIE = "STRATEGIE"
PIPELINE_INTENT_TOP = "TOP"
PIPELINE_INTENT_KPI = "KPI"
PIPELINE_INTENT_STRATEGIE = "STRATEGIE"
PIPELINE_RESPONSE_FORMAT_BY_INTENT = {
    PIPELINE_INTENT_TOP: "TOP → liste classée + chiffres",
    PIPELINE_INTENT_KPI: "KPI → réponse courte + valeur",
    PIPELINE_INTENT_STRATEGIE: "STRATEGIE → analyse + recommandation",
}
PIPELINE_KPI_TO_FILTER_TARGETS = {
    "td": ["td"],
    "rendement": ["td"],
    "prix": ["prix_part"],
    "tof": ["tof"],
    "walt": ["walt"],
    "frais": ["frais"],
    "none": [],
}
PIPELINE_MARKETING_TOKENS = {
    "sponsorise",
    "sponsorisé",
    "sponsorisee",
    "sponsorisé",
    "sponsorisée",
    "publicite",
    "publicité",
    "promotion",
    "promotions",
    "offre partenaire",
    "partenariat",
    "communique",
    "communiqué",
    "contenu sponsorise",
    "contenu sponsorisé",
    "native ad",
}


def _strict_realtime_unavailable_draft(question: str) -> str:
    q = (question or "").strip()
    prefix = f"pour ta question \"{q}\", " if q else ""
    return (
        "Je passe en mode estimation prudente: "
        + prefix
        + "les sources web live ne sont pas assez fiables pour trancher en temps réel.\n"
        "Je te donne une première orientation exploitable avec hypothèses explicites, "
        "puis je précise les points à vérifier dès actualisation des sources."
    )


def _analyze_question(question: str) -> Dict[str, object]:
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

    query = (question or "").lower()
    is_calc = any(x in query for x in ["calcul", "combien", "projection", "rendement", "%", "mensualite", "mensualité"])
    is_strategy = any(x in query for x in ["allocation", "fiscalite", "fiscalité", "invest", "recommande", "que faire", "strategie", "stratégie"])
    if is_calc and is_strategy:
        q_type = "mixte_calcul_strategie"
    elif is_strategy:
        q_type = "strategie_cgp"
    elif is_calc:
        q_type = "calcul"
    else:
        q_type = "info"
    return {
        "type": q_type,
        "confidence": 0.6,
        "keywords_matched": [],
        "numerical_values": re.findall(r"\d+(?:[.,]\d+)?", question or ""),
        "requires_context": bool(is_strategy or "top" in query or "classement" in query),
        "detected_labels": [],
    }


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
        "Optionnel:\n- À vérifier / nuance courte\n\n"
        "Si la question demande un top/classement: donner un classement indicatif basé sur tendances connues, "
        "puis préciser 'à valider selon TD, TOF, WALT, frais et liquidité'."
    )


def _detect_style(question: str, question_type: str) -> str:
    qn = _normalize_text_for_match(question or "")
    if any(token in qn for token in ("top", "classement", "palmares", "meilleure", "meilleur")):
        return STYLE_TOP
    if question_type == "calcul":
        return STYLE_CALCUL
    if question_type in {"strategie_cgp", "mixte_calcul_strategie"}:
        return STYLE_STRATEGIE
    return STYLE_INFO


def _extract_year(question: str) -> str:
    qn = _normalize_text_for_match(question or "")
    m = re.search(r"\b(20\d{2})\b", qn)
    return m.group(1) if m else ""


def _detect_pipeline_intent(question: str, question_type: str) -> str:
    qn = _normalize_text_for_match(question or "")
    if any(token in qn for token in ("top", "classement", "palmares", "meilleure", "meilleur")):
        return PIPELINE_INTENT_TOP
    if question_type in {"strategie_cgp", "mixte_calcul_strategie"}:
        return PIPELINE_INTENT_STRATEGIE
    if any(
        token in qn
        for token in (
            "allocation",
            "strategie",
            "stratégie",
            "arbitrage",
            "fiscalite",
            "fiscalité",
            "que faire",
            "recommande",
        )
    ):
        return PIPELINE_INTENT_STRATEGIE
    return PIPELINE_INTENT_KPI


def _extract_primary_kpi(question: str) -> str:
    qn = _normalize_text_for_match(question or "")
    if not qn:
        return "none"
    if "tof" in qn or "taux d'occupation financier" in qn or "occupation financier" in qn:
        return "tof"
    if "walt" in qn or "duree moyenne des baux" in qn or "durée moyenne des baux" in qn:
        return "walt"
    if "frais" in qn or "commission" in qn:
        return "frais"
    if "prix de part" in qn or "valeur de part" in qn or "souscription" in qn or "prix" in qn:
        return "prix"
    if re.search(r"\btd\b", qn) or "tdvm" in qn or "taux de distribution" in qn:
        return "td"
    if "rendement" in qn:
        return "rendement"
    return "none"


def _extract_missing_inputs_for_pipeline(
    question: str,
    intent: str,
    primary_kpi: str,
) -> List[str]:
    qn = _normalize_text_for_match(question or "")
    missing: List[str] = []

    if intent == PIPELINE_INTENT_TOP:
        if primary_kpi == "none":
            missing.append("kpi_principal")
    elif intent == PIPELINE_INTENT_KPI:
        if primary_kpi == "none":
            missing.append("kpi_principal")
    elif intent == PIPELINE_INTENT_STRATEGIE:
        if not re.search(r"\b\d+\s*(ans?|mois)\b", qn):
            missing.append("horizon")
        if not re.search(r"\b\d+(?:[.,]\d+)?\s*(k|ke|keur|m|meur|€|euros?)\b", qn):
            missing.append("montant")
        if not any(token in qn for token in ("prudent", "equilibre", "équilibré", "dynamique", "risque")):
            missing.append("profil_risque")

    if len(missing) > 3:
        return missing[:3]
    return missing


def _build_selection_rules_for_pipeline(intent: str, primary_kpi: str) -> Dict[str, Any]:
    filter_targets = PIPELINE_KPI_TO_FILTER_TARGETS.get(primary_kpi, [])
    keep_keywords: List[str] = []
    for target in filter_targets:
        keep_keywords.extend(KPI_FILTER_KEYWORDS.get(target, [target]))

    if intent == PIPELINE_INTENT_TOP:
        keep_keywords.extend(["top", "classement", "comparatif"])
    elif intent == PIPELINE_INTENT_STRATEGIE:
        keep_keywords.extend(["allocation", "strategie", "fiscalite", "risque", "horizon", "liquidite"])

    ordered_keep: List[str] = []
    for keyword in keep_keywords:
        key = _normalize_text_for_match(keyword)
        if key and key not in ordered_keep:
            ordered_keep.append(key)

    return {
        "keep_keywords": ordered_keep[:16],
        "exclude_tokens": [
            "hors_sujet",
            "marketing",
            "publicite",
            "publicité",
            "sponsorise",
            "sponsorisé",
            "promotion",
            "snippets",
            "titres",
        ],
        "remove_marketing": True,
        "drop_offtopic": True,
    }


def _build_pre_response_pipeline(question: str, question_type: str) -> Dict[str, Any]:
    intent = _detect_pipeline_intent(question, question_type)
    primary_kpi = _extract_primary_kpi(question)
    missing_inputs = _extract_missing_inputs_for_pipeline(
        question=question,
        intent=intent,
        primary_kpi=primary_kpi,
    )
    selection_rules = _build_selection_rules_for_pipeline(intent=intent, primary_kpi=primary_kpi)
    return {
        "intent": intent,
        "primary_kpi": primary_kpi,
        "needs_clarification": bool(missing_inputs),
        "missing_inputs": missing_inputs,
        "selection_rules": selection_rules,
        "response_format": PIPELINE_RESPONSE_FORMAT_BY_INTENT.get(intent, PIPELINE_RESPONSE_FORMAT_BY_INTENT[PIPELINE_INTENT_KPI]),
        "kpi_targets_for_filter": PIPELINE_KPI_TO_FILTER_TARGETS.get(primary_kpi, []),
    }


def _format_instruction_by_pipeline(pipeline: Dict[str, Any], fallback_question_type: str) -> str:
    intent = str((pipeline or {}).get("intent") or PIPELINE_INTENT_KPI).upper()
    primary_kpi = str((pipeline or {}).get("primary_kpi") or "none").lower()

    if intent == PIPELINE_INTENT_TOP:
        metric = primary_kpi if primary_kpi != "none" else "kpi demandé"
        return (
            "Format obligatoire:\n"
            f"TOP:\n- Liste classée (1..N) basée sur {metric}.\n"
            "- Chaque ligne: nom + chiffre + période courte.\n"
            "- Exclure tout contenu marketing ou hors sujet."
        )
    if intent == PIPELINE_INTENT_STRATEGIE:
        return (
            "Format obligatoire:\n"
            "STRATEGIE:\n"
            "- Analyse synthétique.\n"
            "- Recommandation opérationnelle.\n"
            "- Risques/points de contrôle.\n"
            "- Si clarification nécessaire: section finale 'Questions à préciser' (max 2)."
        )
    if intent == PIPELINE_INTENT_KPI:
        metric = primary_kpi if primary_kpi != "none" else "kpi demandé"
        return (
            "Format obligatoire:\n"
            "KPI:\n"
            f"- Réponse courte (2-4 lignes) centrée sur {metric}.\n"
            "- Donner la valeur et la période si disponible.\n"
            "- Aucun contexte hors cible."
        )
    return _format_instruction_by_type(fallback_question_type)


def _pipeline_summary_block(pipeline: Dict[str, Any]) -> str:
    intent = str((pipeline or {}).get("intent") or PIPELINE_INTENT_KPI)
    primary_kpi = str((pipeline or {}).get("primary_kpi") or "none")
    needs_clarification = bool((pipeline or {}).get("needs_clarification"))
    missing_inputs = (pipeline or {}).get("missing_inputs") if isinstance((pipeline or {}).get("missing_inputs"), list) else []
    selection_rules = (pipeline or {}).get("selection_rules") if isinstance((pipeline or {}).get("selection_rules"), dict) else {}
    keep_keywords = selection_rules.get("keep_keywords") if isinstance(selection_rules.get("keep_keywords"), list) else []
    keep_short = ", ".join([str(x) for x in keep_keywords[:8]]) if keep_keywords else "none"
    missing_text = ", ".join([str(x) for x in missing_inputs]) if missing_inputs else "none"
    response_format = str((pipeline or {}).get("response_format") or "")
    return (
        f"- intention: {intent}\n"
        f"- kpi_principal: {primary_kpi}\n"
        f"- needs_clarification: {'true' if needs_clarification else 'false'}\n"
        f"- missing_inputs: {missing_text}\n"
        f"- donnees_a_garder: {keep_short}\n"
        f"- format_reponse: {response_format}"
    ).strip()


def _looks_like_marketing_noise(result: Dict[str, str]) -> bool:
    blob = _normalize_text_for_match(
        f"{result.get('title', '')} {result.get('body', '')} {result.get('href', '')}"
    )
    if not blob:
        return False
    return any(token in blob for token in PIPELINE_MARKETING_TOKENS)


def _filter_results_for_pipeline(
    results: List[Dict[str, str]],
    pipeline: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], bool, int]:
    if not results:
        return results, False, 0
    if not isinstance(pipeline, dict):
        return results, False, 0

    filtered = list(results)
    marketing_removed = 0
    selection_rules = pipeline.get("selection_rules") if isinstance(pipeline.get("selection_rules"), dict) else {}
    remove_marketing = bool(selection_rules.get("remove_marketing", True))
    kpi_targets_for_filter = (
        pipeline.get("kpi_targets_for_filter")
        if isinstance(pipeline.get("kpi_targets_for_filter"), list)
        else []
    )

    if remove_marketing:
        non_marketing = [item for item in filtered if not _looks_like_marketing_noise(item)]
        marketing_removed = max(0, len(filtered) - len(non_marketing))
        if non_marketing:
            filtered = non_marketing

    if kpi_targets_for_filter:
        focused = [
            item
            for item in filtered
            if any(_result_matches_kpi(item, str(target)) for target in kpi_targets_for_filter)
        ]
        if len(focused) >= 2 or (len(filtered) <= 1 and focused):
            filtered = focused

    if not filtered:
        return results, False, marketing_removed
    return filtered, filtered != results, marketing_removed


def _build_routing_override_for_style(
    question: str,
    question_type: str,
    pipeline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pipeline_analysis = pipeline if isinstance(pipeline, dict) and pipeline else _build_pre_response_pipeline(question, question_type)
    intent = str(pipeline_analysis.get("intent") or PIPELINE_INTENT_KPI).upper()
    primary_kpi = str(pipeline_analysis.get("primary_kpi") or "none").lower()
    if primary_kpi not in {"td", "rendement", "prix", "tof", "walt", "frais", "none"}:
        primary_kpi = "none"
    needs_clarification = bool(pipeline_analysis.get("needs_clarification"))
    missing_inputs = [str(x) for x in (pipeline_analysis.get("missing_inputs") or []) if str(x).strip()]
    selection_rules = (
        pipeline_analysis.get("selection_rules")
        if isinstance(pipeline_analysis.get("selection_rules"), dict)
        else {}
    )
    base_exclusions = [
        "titres_bruts",
        "snippets_bruts",
        "hors_sujet",
        "non_renseigne",
        "http://",
        "https://",
        "marketing",
        "publicite",
        "publicité",
    ]
    extra_exclusions = [
        str(item)
        for item in (selection_rules.get("exclude_tokens") or [])
        if isinstance(item, str) and item.strip()
    ]
    exclusions = list(dict.fromkeys(base_exclusions + extra_exclusions))
    kpi_targets = [primary_kpi] if primary_kpi != "none" else []
    year = _extract_year(question)
    include = [*kpi_targets, year] if year else kpi_targets

    if intent == PIPELINE_INTENT_TOP:
        return {
            "intent": PIPELINE_INTENT_TOP,
            "intents": [PIPELINE_INTENT_TOP],
            "confidence": 0.88,
            "needs_clarification": needs_clarification,
            "missing_inputs": missing_inputs,
            "answer_target": {
                "primary": "top_list",
                "required_fields": ["items"],
                "optional_fields": ["criteria", "period", "disclaimer"],
                "exclusions": exclusions,
                "kpi_targets": kpi_targets,
                "answer_span": {
                    "focus": "ranking",
                    "include": include,
                    "exclude": exclusions,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "criteria": {"type": "string"},
                        "period": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "metric": {"type": "string"},
                                    "value": {"type": "string"},
                                    "period": {"type": "string"},
                                },
                                "required": ["name", "value"],
                            },
                        },
                        "disclaimer": {"type": "string"},
                    },
                    "required": ["items"],
                },
            },
        }

    if intent == PIPELINE_INTENT_STRATEGIE:
        return {
            "intent": PIPELINE_INTENT_STRATEGIE,
            "intents": [PIPELINE_INTENT_STRATEGIE],
            "confidence": 0.82,
            "needs_clarification": needs_clarification,
            "missing_inputs": missing_inputs,
            "answer_target": {
                "primary": "strategie_cgp",
                "required_fields": ["analyse", "recommandation"],
                "optional_fields": ["risques", "plan_action", "questions"],
                "exclusions": exclusions,
                "kpi_targets": kpi_targets,
                "answer_span": {
                    "focus": "strategie",
                    "include": include,
                    "exclude": exclusions,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "analyse": {"type": "string"},
                        "recommandation": {"type": "string"},
                        "risques": {"type": "string"},
                        "plan_action": {"type": "string"},
                        "questions": {"type": "array"},
                    },
                    "required": ["analyse", "recommandation"],
                },
            },
        }

    return {
        "intent": PIPELINE_INTENT_KPI,
        "intents": [PIPELINE_INTENT_KPI],
        "confidence": 0.8,
        "needs_clarification": needs_clarification,
        "missing_inputs": missing_inputs,
        "answer_target": {
            "primary": "kpi_value",
            "required_fields": ["kpi", "value"],
            "optional_fields": ["period", "disclaimer"],
            "exclusions": exclusions,
            "kpi_targets": kpi_targets,
            "answer_span": {
                "focus": "kpi" if primary_kpi == "none" else f"kpi:{primary_kpi}",
                "include": include,
                "exclude": exclusions,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "kpi": {"type": "string"},
                    "value": {"type": "string"},
                    "period": {"type": "string"},
                    "disclaimer": {"type": "string"},
                },
                "required": ["kpi", "value"],
            },
        },
    }


def _build_brain_facts_pack(
    ranked_results: List[Dict[str, Any]],
    limit: int = 8,
) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    for row in ranked_results[: max(1, limit)]:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        title = (result.get("title") or "").strip()
        body = (result.get("body") or "").strip()
        href = (result.get("href") or "").strip()
        domain = row.get("domain") or _source_domain_from_url(href)
        signal = row.get("signal") or _extract_first_signal(f"{title} {body}")
        year = _extract_year(f"{title} {body}")
        facts.append(
            {
                "source_domain": domain,
                "title": title,
                "signal": signal,
                "year": year,
                "data_type": row.get("data_type"),
                "url": href,
                "score": row.get("score"),
            }
        )
    return facts


def _draft_from_results_without_llm(question: str, results: List[Dict[str, str]]) -> str:
    q = (question or "").strip()
    if not results:
        analysis = _analyze_question(question)
        qtype = str(analysis.get("type") or "info")
        qn = (question or "").lower()
        if qtype == "calcul":
            return (
                "Hypothèses :\n"
                "- Estimation prudente à partir d'hypothèses standards (rendement brut, frais, fiscalité).\n"
                "- Les chiffres exacts sont à confirmer avec les dernières sources datées.\n\n"
                "Calcul :\n"
                "- Appliquer la formule de base avec vos paramètres actuels.\n\n"
                "Résultat :\n"
                "- Première estimation exploitable immédiate, à ajuster après vérification des données live.\n\n"
                "Interprétation :\n"
                "- Décision possible dès maintenant en mode prudent, puis recalage des chiffres dès actualisation."
            )
        if qtype == "mixte_calcul_strategie":
            return (
                "Hypothèses :\n"
                "- Estimation prudente sur rendement, frais et fiscalité.\n\n"
                "Calcul :\n"
                "- Projection initiale avec vos paramètres disponibles.\n\n"
                "Résultat :\n"
                "- Ordre de grandeur utile pour décider sans attendre.\n\n"
                "Interprétation :\n"
                "- Le chiffrage sert de base d'arbitrage.\n\n"
                "Analyse :\n"
                "- Objectif patrimonial à aligner avec horizon et risque.\n\n"
                "Stratégie recommandée :\n"
                "- Allocation progressive, puis ajustement une fois les données live consolidées.\n\n"
                "Arbitrages :\n"
                "- Rendement visé vs liquidité et frais réels.\n\n"
                "Risques :\n"
                "- Hypothèses à vérifier sur TD, TOF, WALT et frais.\n\n"
                "Conclusion :\n"
                "- On avance avec cette base, puis on valide les chiffres clés."
            )
        if "top" in qn or "classement" in qn or "liste" in qn:
            return (
                "Réponse directe :\n"
                "- Classement indicatif disponible immédiatement selon tendances connues du marché.\n\n"
                "Points clés :\n"
                "- Prioriser les SCPI avec historique de distribution stable.\n"
                "- Contrôler qualité locative et diversification géographique.\n"
                "- Vérifier la liquidité et les frais avant décision.\n\n"
                "Optionnel :\n"
                "- À valider selon TD, TOF, WALT, frais et liquidité."
            )
        if qtype == "strategie_cgp":
            return (
                "Analyse :\n"
                "- Objectif, horizon et fiscalité sont les axes de décision prioritaires.\n\n"
                "Stratégie recommandée :\n"
                "- Démarrer par une allocation prudente et diversifiée, puis ajuster par paliers.\n\n"
                "Arbitrages :\n"
                "- Performance attendue vs liquidité et fiscalité.\n\n"
                "Risques :\n"
                "- Sensibilité aux hypothèses de rendement et au niveau de frais.\n\n"
                "Conclusion :\n"
                "- Décision possible dès maintenant en estimation prudente.\n\n"
                "Questions à poser (max 3) :\n"
                "- Montant investi ?\n"
                "- Horizon cible ?\n"
                "- Régime fiscal à appliquer ?"
            )
        return (
            "Réponse directe :\n"
            "- Je te donne une première réponse exploitable en mode estimation prudente.\n\n"
            "Points clés :\n"
            "- Utiliser des hypothèses explicites et conservatrices.\n"
            "- Prioriser les décisions réversibles.\n"
            "- Confirmer les chiffres clés à la prochaine actualisation.\n\n"
            "Optionnel :\n"
            "- Nuance: les ordres de grandeur restent à valider sur sources datées."
        )

    intelligence = _build_information_intelligence(
        question=question,
        question_type=str(_analyze_question(question).get("type") or "info"),
        results=results,
    )
    selected = intelligence.get("selected_results") if isinstance(intelligence.get("selected_results"), list) else results
    consensus_lines = intelligence.get("consensus_lines") if isinstance(intelligence.get("consensus_lines"), list) else []
    recommendation_axes = intelligence.get("recommendation_axes") if isinstance(intelligence.get("recommendation_axes"), list) else []

    lines: List[str] = ["Réponse directe :"]
    if q:
        lines.append(f"- Synthèse provisoire basée sur les sources les plus pertinentes pour : {q}")
    else:
        lines.append("- Synthèse provisoire basée sur les sources les plus pertinentes.")
    lines.append("")

    lines.append("Points clés :")
    for idx, item in enumerate(selected[:3], start=1):
        domain = _source_domain_from_url(item.get("href", ""))
        label = _source_label_from_domain(domain)
        title = (item.get("title") or "").strip() or f"Source {idx}"
        body = (item.get("body") or "").strip()
        excerpt = body[:180] + ("..." if len(body) > 180 else "")
        lines.append(f"- {title} ({label}) : {excerpt}")

    if consensus_lines:
        lines.append("")
        lines.append("Analyse inter-sources :")
        for line in consensus_lines[:2]:
            lines.append(f"- {line}")

    if recommendation_axes:
        lines.append("")
        lines.append("Recommandation ciblée :")
        for axis in recommendation_axes[:3]:
            lines.append(f"- {axis}")

    draft = "\n".join(lines).strip()
    draft = _apply_web_quality_guardrails(draft, results)
    return draft


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


def _append_result(
    target: List[Dict[str, str]],
    title: str,
    href: str,
    body: str,
    min_length: int
) -> None:
    title = (title or "").strip()
    href = (href or "").strip()
    body = (body or "").strip()

    if not title or not href:
        return

    if not body:
        body = title

    if len(body) < min_length:
        target.append({"title": title, "href": href, "body": body[:500]})
    else:
        target.append({"title": title, "href": href, "body": body[:500]})


def _dedupe_results(results: List[Dict[str, str]], max_results: int) -> List[Dict[str, str]]:
    seen = set()
    out = []
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
    if SERPER_AUTH_DISABLED:
        return []
    if _is_placeholder_key(SERPER_API_KEY) or httpx is None:
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
            logger.warning("⚠️ SERPER_API_KEY invalide (401/403), provider google_serper désactivé.")
            return []
        raise

    results: List[Dict[str, str]] = []
    for item in data.get("organic", []):
        _append_result(
            target=results,
            title=item.get("title", ""),
            href=item.get("link", ""),
            body=item.get("snippet", ""),
            min_length=min_length,
        )
    return _dedupe_results(results, max_results)


def _search_google_serpapi(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    global SERPAPI_AUTH_DISABLED
    if SERPAPI_AUTH_DISABLED:
        return []
    if _is_placeholder_key(SERPAPI_API_KEY) or httpx is None:
        return []

    try:
        resp = httpx.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "api_key": SERPAPI_API_KEY,
                "num": max_results * 2,
                "hl": "fr",
                "gl": "fr",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            SERPAPI_AUTH_DISABLED = True
            logger.warning("⚠️ SERPAPI_API_KEY invalide (401/403), provider google_serpapi désactivé.")
            return []
        raise

    results: List[Dict[str, str]] = []
    for item in data.get("organic_results", []):
        _append_result(
            target=results,
            title=item.get("title", ""),
            href=item.get("link", ""),
            body=item.get("snippet", ""),
            min_length=min_length,
        )
    return _dedupe_results(results, max_results)


def _search_google_cse(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    global GOOGLE_CSE_AUTH_DISABLED
    if GOOGLE_CSE_AUTH_DISABLED:
        return []
    if _is_placeholder_key(GOOGLE_CSE_API_KEY) or _is_placeholder_key(GOOGLE_CSE_CX) or httpx is None:
        return []

    try:
        resp = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_CSE_API_KEY,
                "cx": GOOGLE_CSE_CX,
                "q": query,
                "num": min(max_results, 10),
                "hl": "fr",
                "gl": "fr",
                "safe": "off",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            GOOGLE_CSE_AUTH_DISABLED = True
            logger.warning("⚠️ GOOGLE_CSE_API_KEY/CX invalide (401/403), provider google_cse désactivé.")
            return []
        raise

    results: List[Dict[str, str]] = []
    for item in data.get("items", []):
        _append_result(
            target=results,
            title=item.get("title", ""),
            href=item.get("link", ""),
            body=item.get("snippet", ""),
            min_length=min_length,
        )
    return _dedupe_results(results, max_results)


def _search_ddgs(query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    if not DDGS_AVAILABLE:
        logger.warning("⚠️ Provider DDGS indisponible: installe `ddgs` ou `duckduckgo_search`.")
        return []

    results = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 3):
                _append_result(
                    target=results,
                    title=r.get("title", ""),
                    href=r.get("href", ""),
                    body=r.get("body", ""),
                    min_length=min_length,
                )
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
    snippets = [
        _clean_html_fragment(a or b or "")
        for (a, b) in snippet_matches
    ]

    for i, match in enumerate(anchor_matches):
        raw_href = match.group(1)
        raw_title = match.group(2)
        href = _normalize_duckduckgo_href(raw_href)
        title = _clean_html_fragment(raw_title)
        if not href.startswith(("http://", "https://")):
            continue
        body = snippets[i] if i < len(snippets) else title
        _append_result(results, title=title, href=href, body=body, min_length=min_length)
        if len(results) >= max_results:
            return _dedupe_results(results, max_results)

    if results:
        return _dedupe_results(results, max_results)

    # Fallback parsing for lite pages where CSS classes differ.
    generic_links = re.findall(
        r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for raw_href, raw_title in generic_links:
        href = _normalize_duckduckgo_href(raw_href)
        title = _clean_html_fragment(raw_title)
        if not href.startswith(("http://", "https://")):
            continue
        if "duckduckgo.com" in href:
            continue
        if not title or len(title) < 3:
            continue
        _append_result(results, title=title, href=href, body=title, min_length=min_length)
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
    candidates = [
        ("https://duckduckgo.com/html/", {"q": query}),
        ("https://lite.duckduckgo.com/lite/", {"q": query}),
    ]

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

    for url, params in candidates:
        try:
            html_text = _http_get_text(url, params=params, timeout=10.0)
            parsed = _parse_duckduckgo_html_results(html_text or "", max_results=max_results, min_length=min_length)
            if parsed:
                return parsed
        except Exception:
            continue
    return []


def _search_provider(provider: str, query: str, max_results: int, min_length: int) -> List[Dict[str, str]]:
    if provider in {"google_serpapi", "serpapi"}:
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


def _provider_enabled(provider: str) -> bool:
    if provider in {"google_serpapi", "serpapi"}:
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


def _domain_matches(domain: str, expected_domain: str) -> bool:
    d = (domain or "").lower().lstrip("www.").strip()
    expected = (expected_domain or "").lower().lstrip("www.").strip()
    if not d or not expected:
        return False
    return d == expected or d.endswith(f".{expected}")


def _resolve_provider_chain() -> List[str]:
    if WEB_SEARCH_PROVIDER == "auto":
        providers = ["google_serpapi", "google_serper", "google_cse"]
        if WEB_FALLBACK_TO_DDGS:
            providers.append("ddgs")
        providers.append("duckduckgo_html")
    elif WEB_SEARCH_PROVIDER in {"google", "google_serpapi", "serpapi"}:
        providers = ["google_serpapi", "google_serper", "google_cse"]
        if WEB_FALLBACK_TO_DDGS:
            providers.append("ddgs")
        providers.append("duckduckgo_html")
    elif WEB_SEARCH_PROVIDER == "google_serper":
        providers = ["google_serper", "google_serpapi", "google_cse"]
        if WEB_FALLBACK_TO_DDGS:
            providers.append("ddgs")
        providers.append("duckduckgo_html")
    elif WEB_SEARCH_PROVIDER == "google_cse":
        providers = ["google_cse"]
        if WEB_FALLBACK_TO_DDGS:
            providers.append("ddgs")
        providers.append("duckduckgo_html")
    elif WEB_SEARCH_PROVIDER == "ddgs":
        providers = ["ddgs"]
    elif WEB_SEARCH_PROVIDER in {"duckduckgo_html", "ddg_html"}:
        providers = ["duckduckgo_html"]
    else:
        providers = ["google_serper", "google_cse"]
        if WEB_FALLBACK_TO_DDGS:
            providers.append("ddgs")
        providers.append("duckduckgo_html")

    enabled = [p for p in providers if _provider_enabled(p)]
    # Dedup en conservant l'ordre
    return list(dict.fromkeys(enabled))


def _run_provider_chain_for_query(
    query: str,
    providers: List[str],
    max_results: int,
    min_length: int,
) -> Tuple[List[Dict[str, str]], str]:
    simplified_query = query.replace("?", " ").strip()
    for provider in providers:
        for attempt in range(max(1, WEB_SEARCH_RETRY_ATTEMPTS)):
            q_try = query
            if attempt > 0 and simplified_query and simplified_query != query:
                q_try = simplified_query
            try:
                results = _search_provider(provider, q_try, max_results, min_length)
                if results:
                    logger.info(
                        f"✅ {len(results)} résultats obtenus via {provider} (attempt={attempt + 1}, query='{q_try[:80]}')"
                    )
                    return results, provider
            except Exception as e:
                logger.warning(
                    f"⚠️ Provider {provider} indisponible (attempt={attempt + 1}): {str(e)}"
                )
            if attempt + 1 < max(1, WEB_SEARCH_RETRY_ATTEMPTS):
                time.sleep(0.2 * (attempt + 1))
    return [], "none"


def _filter_results_for_domain(results: List[Dict[str, str]], expected_domain: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in results:
        domain = _source_domain_from_url(item.get("href", ""))
        if _domain_matches(domain, expected_domain):
            out.append(item)
    return out


# ----------------------------
# Recherche Web avec Filtrage Qualité
# ----------------------------
def web_search(query: str, max_results: int = 5, min_length: int = 50) -> Tuple[List[Dict[str, str]], str]:
    """
    Recherche web avec filtrage qualité.
    
    Args:
        query: Requête de recherche
        max_results: Nombre max de résultats souhaités
        min_length: Longueur minimale du body
        
    Returns:
        Liste de dict {title, href, body}
    """
    try:
        logger.info(f"🔍 Recherche: '{query}' (max={max_results})")

        # 0) Prioritized SCPI search (Pierre-Papier / France SCPI / ASPIM first).
        if WEB_USE_PRIORITIZED_SEARCH and web_search_prioritized is not None:
            try:
                prioritized_type = _infer_prioritized_search_type(query)
                prioritized_results = web_search_prioritized.search(
                    query=query,
                    search_type=prioritized_type,
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
                                "priority": str(item.priority),
                                "source_name": (item.source_name or "").strip(),
                                "date": (item.date or "").strip(),
                            }
                        )
                    converted = _dedupe_results(converted, max_results)
                    if converted:
                        logger.info(
                            "✅ %s résultats via prioritized_web_search (type=%s)",
                            len(converted),
                            prioritized_type,
                        )
                        return converted, f"prioritized_serper:{prioritized_type}"
            except Exception as exc:
                logger.warning(f"⚠️ prioritized_web_search indisponible: {str(exc)}")

        providers = _resolve_provider_chain()
        if not providers:
            logger.warning(
                "⚠️ Aucun provider web actif (clé absente/invalide ou dépendance manquante)."
            )
            return [], "none_configured"

        combined_results: List[Dict[str, str]] = []
        provider_trace: List[str] = []
        max_priority_per_domain = min(3, max(1, max_results))

        # 1) Priorité aux sources métier ciblées.
        for domain in WEB_PRIORITY_DOMAINS:
            site_query = f"site:{domain} {query}".strip()
            domain_results, provider_used = _run_provider_chain_for_query(
                query=site_query,
                providers=providers,
                max_results=min(10, max_priority_per_domain * 2),
                min_length=min_length,
            )
            if provider_used and provider_used != "none":
                provider_trace.append(f"{domain}:{provider_used}")
            if not domain_results:
                continue

            filtered = _filter_results_for_domain(domain_results, domain)
            if not filtered:
                filtered = domain_results

            combined_results.extend(filtered[:max_priority_per_domain])

        # 2) Compléter avec d'autres sources si nécessaire.
        if len(_dedupe_results(combined_results, max_results)) < max_results:
            generic_results, generic_provider = _run_provider_chain_for_query(
                query=query,
                providers=providers,
                max_results=max_results,
                min_length=min_length,
            )
            if generic_provider and generic_provider != "none":
                provider_trace.append(f"general:{generic_provider}")
            combined_results.extend(generic_results)

        final_results = _dedupe_results(combined_results, max_results)
        if final_results:
            provider_summary = "priority_chain|" + ",".join(provider_trace) if provider_trace else "priority_chain"
            return final_results, provider_summary

        logger.warning("⚠️ Aucun résultat obtenu via les providers actifs")
        return [], "none"

    except Exception as e:
        logger.error(f"⚠️ Erreur web_search: {str(e)}")
        return [], "error"

# ----------------------------
# Formatage Historique
# ----------------------------
def _format_history(history: list, window: int = 8) -> str:
    """Formate l'historique pour le prompt."""
    if not history:
        return "Aucun historique (première question)."

    formatted = "\n".join(
        [f"{msg['role'].upper()}: {msg['content']}" 
         for msg in history[-window:]]
    )
    return formatted if formatted else "Aucun historique pertinent."


def _normalize_text_for_match(value: str) -> str:
    text = (value or "").lower().strip()
    text = text.replace("é", "e").replace("è", "e").replace("ê", "e")
    text = text.replace("à", "a").replace("â", "a").replace("î", "i")
    text = text.replace("ô", "o").replace("ù", "u").replace("û", "u")
    text = text.replace("ç", "c")
    text = re.sub(r"\s+", " ", text)
    return text


def _requested_top_results(question: str, default_top: int = 10, cap: int = 20) -> int:
    qn = _normalize_text_for_match(question)
    if not qn:
        return 0
    if not any(token in qn for token in ("top", "classement", "palmares", "meilleure", "meilleur")):
        return 0

    m = re.search(r"\btop\s*(\d{1,2})\b", qn)
    if not m:
        m = re.search(r"\bclassement\s*(?:des|de)?\s*(\d{1,2})\b", qn)
    if m:
        try:
            value = int(m.group(1))
            return max(1, min(cap, value))
        except Exception:
            return min(cap, default_top)
    return min(cap, default_top)


def _infer_prioritized_search_type(question: str) -> str:
    qn = _normalize_text_for_match(question or "")
    if not qn:
        return "general"
    if any(token in qn for token in ["td", "tdvm", "taux de distribution", "rendement"]):
        return "tdvm"
    if any(token in qn for token in ["prix", "valeur de part", "part", "souscription"]):
        return "price"
    if any(token in qn for token in ["compar", "vs", "entre", "classement", "top"]):
        return "comparison"
    if any(token in qn for token in ["actualite", "news", "nouvelle", "nouveaute"]):
        return "news"
    return "general"


def _extract_kpi_targets(question: str) -> List[str]:
    qn = _normalize_text_for_match(question or "")
    found: List[str] = []
    for target, keywords in KPI_FILTER_KEYWORDS.items():
        if any(keyword in qn for keyword in keywords):
            found.append(target)
    return found


def _result_matches_kpi(result: Dict[str, str], kpi_target: str) -> bool:
    keywords = KPI_FILTER_KEYWORDS.get(kpi_target, [kpi_target])
    blob = _normalize_text_for_match(
        f"{result.get('title', '')} {result.get('body', '')} {result.get('href', '')}"
    )
    return any(keyword in blob for keyword in keywords)


def _filter_results_by_kpi(
    results: List[Dict[str, str]],
    question: str,
) -> Tuple[List[Dict[str, str]], List[str], bool]:
    kpi_targets = _extract_kpi_targets(question)
    if not results or not kpi_targets:
        return results, kpi_targets, False

    filtered = [
        item
        for item in results
        if any(_result_matches_kpi(item, target) for target in kpi_targets)
    ]

    # Keep original set if KPI match is too strict.
    if len(filtered) < 2 and len(results) >= 2:
        return results, kpi_targets, False
    if not filtered:
        return results, kpi_targets, False
    return filtered, kpi_targets, True


def _effective_max_results(question: str, max_results: int) -> int:
    requested_top = _requested_top_results(question)
    base = max(1, int(max_results or 1))
    if requested_top <= 0:
        return base
    return min(20, max(base, requested_top))


def _source_domain_from_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    try:
        domain = (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""
    return domain


def _tokenize_for_relevance(text: str) -> List[str]:
    normalized = _normalize_text_for_match(text or "")
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return [tok for tok in tokens if len(tok) > 2 and tok not in RELEVANCE_STOPWORDS]


def _extract_first_signal(text: str) -> str:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return ""
    percent = re.search(r"\d+(?:[.,]\d+)?\s*%", compact)
    if percent:
        return percent.group(0)
    year = re.search(r"\b20\d{2}\b", compact)
    if year:
        return year.group(0)
    return compact[:90] + ("..." if len(compact) > 90 else "")


def _detect_result_data_type(title: str, body: str, href: str) -> str:
    blob = _normalize_text_for_match(f"{title} {body} {href}")
    if any(tok in blob for tok in ["tdvm", "td ", "taux de distribution", "rendement"]):
        return "tdvm"
    if any(tok in blob for tok in ["prix", "valeur de part", "souscription", "part"]):
        return "price"
    if any(tok in blob for tok in ["classement", "top", "comparatif", "comparaison"]):
        return "comparison"
    if any(tok in blob for tok in ["reglement", "reglementation", "aspim", "amf"]):
        return "regulation"
    if any(tok in blob for tok in ["actualite", "news", "breaking", "communique"]):
        return "news"
    return "analysis"


def _priority_domain_bonus(domain: str) -> float:
    d = (domain or "").lower().lstrip("www.").strip()
    if not d:
        return 0.0
    for idx, priority_domain in enumerate(WEB_PRIORITY_DOMAINS):
        if _domain_matches(d, priority_domain):
            return max(8.0, 18.0 - idx * 3.0)
    if "aspim.fr" in d:
        return 10.0
    return 2.0


def _score_result_relevance(
    question: str,
    question_type: str,
    result: Dict[str, str],
) -> Dict[str, Any]:
    title = (result.get("title") or "").strip()
    body = (result.get("body") or "").strip()
    href = (result.get("href") or "").strip()
    domain = _source_domain_from_url(href)
    data_type = _detect_result_data_type(title, body, href)

    q_tokens = _tokenize_for_relevance(question)
    doc_tokens = set(_tokenize_for_relevance(f"{title} {body}"))
    overlap = [tok for tok in q_tokens if tok in doc_tokens]

    score = 12.0 + min(30.0, float(len(overlap)) * 5.0)
    reasons: List[str] = []
    if overlap:
        reasons.append(f"chevauchement_mots_cles={len(overlap)}")

    priority_bonus = _priority_domain_bonus(domain)
    score += priority_bonus
    if priority_bonus >= 8:
        reasons.append("source_prioritaire")

    has_percent = bool(re.search(r"\d+(?:[.,]\d+)?\s*%", f"{title} {body}"))
    has_year = bool(re.search(r"\b20\d{2}\b", f"{title} {body}"))
    if has_percent:
        score += 8.0
        reasons.append("signal_chiffre")
    if has_year:
        score += 6.0
        reasons.append("signal_date")

    if question_type in {"calcul", "mixte_calcul_strategie"} and has_percent:
        score += 8.0
    if question_type == "info" and data_type in {"comparison", "tdvm"}:
        score += 6.0
    if question_type == "strategie_cgp" and data_type in {"analysis", "comparison"}:
        score += 6.0

    if len(body) < 40:
        score -= 4.0
    if not domain:
        score -= 3.0

    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 2),
        "reasons": reasons,
        "domain": domain,
        "data_type": data_type,
        "signal": _extract_first_signal(f"{title} {body}"),
    }


def _build_information_intelligence(
    question: str,
    question_type: str,
    results: List[Dict[str, str]],
    max_selected: int = 6,
) -> Dict[str, Any]:
    if not results:
        return {
            "ranked_results": [],
            "selected_results": [],
            "consensus_lines": [],
            "recommendation_axes": RECOMMENDATION_AXES_BY_TYPE.get(question_type, RECOMMENDATION_AXES_BY_TYPE["info"]),
            "context_block": "ANALYSE AUTOMATIQUE: aucun résultat exploitable.",
        }

    enriched: List[Dict[str, Any]] = []
    for item in results:
        score_info = _score_result_relevance(question=question, question_type=question_type, result=item)
        enriched.append(
            {
                "result": item,
                "score": score_info["score"],
                "domain": score_info["domain"],
                "data_type": score_info["data_type"],
                "signal": score_info["signal"],
                "reasons": score_info["reasons"],
            }
        )
    enriched.sort(key=lambda x: x["score"], reverse=True)

    # Keep diversity by domain first.
    selected: List[Dict[str, Any]] = []
    seen_domains = set()
    for row in enriched:
        domain = (row.get("domain") or "").strip()
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        selected.append(row)
        if len(selected) >= max_selected:
            break
    for row in enriched:
        if len(selected) >= max_selected:
            break
        if row in selected:
            continue
        selected.append(row)

    # Consensus / divergence detection from percentage signals.
    numeric_points: List[Tuple[str, float, str]] = []
    for row in selected:
        snippet = f"{row['result'].get('title', '')} {row['result'].get('body', '')}"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", snippet)
        if not m:
            continue
        try:
            value = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        numeric_points.append((row.get("domain") or "source", value, m.group(0)))

    consensus_lines: List[str] = []
    if len(numeric_points) >= 2:
        vals = [p[1] for p in numeric_points]
        min_v, max_v = min(vals), max(vals)
        spread = round(max_v - min_v, 2)
        if spread <= 0.2:
            consensus_lines.append(
                f"Convergence chiffrée: dispersion faible ({min_v:.2f}% à {max_v:.2f}%)."
            )
        else:
            consensus_lines.append(
                f"Écart chiffré: {min_v:.2f}% à {max_v:.2f}% (écart {spread:.2f} points, vérifier période/méthode)."
            )
    elif selected:
        consensus_lines.append("Consensus chiffré limité: recouper au moins 2 sources avant arbitrage.")

    recommendation_axes = RECOMMENDATION_AXES_BY_TYPE.get(
        question_type,
        RECOMMENDATION_AXES_BY_TYPE["info"],
    )

    top_lines: List[str] = []
    for idx, row in enumerate(selected[:4], start=1):
        r = row["result"]
        domain = row.get("domain") or _source_domain_from_url(r.get("href", ""))
        top_lines.append(
            f"{idx}) score={row['score']} | {domain} | type={row['data_type']} | signal={row['signal'] or 'n/a'}"
        )

    context_lines: List[str] = [
        "ANALYSE AUTOMATIQUE DE PERTINENCE:",
        "- Top signaux retenus:",
    ]
    if top_lines:
        context_lines.extend([f"  - {line}" for line in top_lines])
    else:
        context_lines.append("  - aucun")
    context_lines.append("- Lecture inter-sources:")
    if consensus_lines:
        context_lines.extend([f"  - {line}" for line in consensus_lines])
    else:
        context_lines.append("  - n/a")
    context_lines.append("- Axes de recommandation:")
    context_lines.extend([f"  - {axis}" for axis in recommendation_axes[:3]])
    context_block = "\n".join(context_lines).strip()

    return {
        "ranked_results": enriched,
        "selected_results": [row["result"] for row in selected],
        "consensus_lines": consensus_lines,
        "recommendation_axes": recommendation_axes[:3],
        "context_block": context_block,
    }


def _structured_search_sources(results: List[Dict[str, str]]) -> List[Dict[str, str]]:
    structured: List[Dict[str, str]] = []
    for item in results:
        href = (item.get("href") or "").strip()
        if not href:
            continue
        title = (item.get("title") or "").strip()
        snippet = (item.get("body") or "").strip()
        source_label = (item.get("source_name") or "").strip() or _source_domain_from_url(href) or "web"
        source_domain = _source_domain_from_url(href) or "web"
        entry: Dict[str, str] = {
            "url": href,
            "href": href,
            "title": title or source_label,
            "snippet": snippet[:500],
            "source": source_label,
            "source_domain": source_domain,
            "date": (item.get("date") or "non_renseigne").strip() if isinstance(item.get("date"), str) else "non_renseigne",
        }
        priority = (item.get("priority") or "").strip() if isinstance(item.get("priority"), str) else ""
        if priority:
            entry["priority"] = priority
        structured.append(entry)
    return structured


def _source_label_from_domain(domain: str) -> str:
    d = (domain or "").lower().lstrip("www.").strip()
    if not d:
        return "Source web"
    return PRIORITY_DOMAIN_LABELS.get(d, d)


def _source_citations_block(results: List[Dict[str, str]], max_items: int = 8) -> str:
    if not results:
        return ""
    seen = set()
    lines: List[str] = ["Sources utilisées :"]
    for item in results:
        href = (item.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        domain = _source_domain_from_url(href)
        label = (item.get("source_name") or "").strip() or _source_label_from_domain(domain)
        lines.append(f"- {label} ({domain}) : {href}")
        if len(lines) - 1 >= max_items:
            break
    return "\n".join(lines).strip()


def _extract_number_signal(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    m = re.search(r"\d+(?:[.,]\d+)?\s*%", value)
    if m:
        return m.group(0)
    m = re.search(r"\d+(?:[.,]\d+)?", value)
    return m.group(0) if m else ""


def _source_comparison_block(results: List[Dict[str, str]], max_sources: int = 3) -> str:
    if not results:
        return ""

    by_domain: Dict[str, Dict[str, str]] = {}
    for item in results:
        href = (item.get("href") or "").strip()
        if not href:
            continue
        domain = _source_domain_from_url(href)
        if not domain or domain in by_domain:
            continue
        by_domain[domain] = {
            "domain": domain,
            "title": (item.get("title") or "").strip(),
            "snippet": (item.get("body") or "").strip(),
            "href": href,
        }
        if len(by_domain) >= max_sources:
            break

    domains = list(by_domain.keys())
    if len(domains) < 2:
        return ""

    lines: List[str] = ["Comparaison rapide des sources :"]
    numeric_signals: List[Tuple[str, str]] = []

    for domain in domains:
        entry = by_domain[domain]
        title = entry.get("title", "") or _source_label_from_domain(domain)
        snippet = re.sub(r"\s+", " ", entry.get("snippet", "")).strip()
        if len(snippet) > 180:
            snippet = snippet[:177].rstrip() + "..."
        lines.append(f"- {title} ({domain}) : {snippet or 'Résumé non disponible.'}")
        num = _extract_number_signal(snippet)
        if num:
            numeric_signals.append((domain, num))

    if len(numeric_signals) >= 2:
        d1, v1 = numeric_signals[0]
        d2, v2 = numeric_signals[1]
        if v1 != v2:
            lines.append(
                f"- Écart repéré : {d1} mentionne {v1} et {d2} mentionne {v2} (vérifier période et métrique)."
            )
        else:
            lines.append(
                f"- Convergence repérée : {d1} et {d2} rapportent {v1} (à confirmer sur la même période)."
            )
    else:
        lines.append(
            "- Convergence qualitative : les sources couvrent le même thème, valider date de mise à jour et métrique exacte."
        )

    return "\n".join(lines).strip()


def _ensure_sources_cited(draft: str, results: List[Dict[str, str]]) -> str:
    if not results:
        return draft
    citations_block = _source_citations_block(results)
    if not citations_block:
        return draft
    normalized_draft = _normalize_text_for_match(draft)
    if "sources utilisees" in normalized_draft or "sources utilisées" in normalized_draft:
        return draft
    return (draft.rstrip() + "\n\n" + citations_block).strip()


def _ensure_comparison_present(draft: str, results: List[Dict[str, str]]) -> str:
    comparison_block = _source_comparison_block(results)
    if not comparison_block:
        return draft
    normalized_draft = _normalize_text_for_match(draft)
    if "comparaison" in normalized_draft or "convergence" in normalized_draft or "ecart" in normalized_draft:
        return draft
    return (draft.rstrip() + "\n\n" + comparison_block).strip()


def _apply_web_quality_guardrails(draft: str, results: List[Dict[str, str]]) -> str:
    out = _ensure_comparison_present(draft, results)
    out = _ensure_sources_cited(out, results)
    return out.strip()


def _ensure_recommendation_present(
    draft: str,
    question_type: str,
    recommendation_axes: Optional[List[str]] = None,
) -> str:
    text = (draft or "").strip()
    if not text:
        return text
    normalized = _normalize_text_for_match(text)
    if "recommandation" in normalized or "strategie recommandee" in normalized or "stratégie recommandée" in normalized:
        return text

    axes = recommendation_axes or RECOMMENDATION_AXES_BY_TYPE.get(
        question_type,
        RECOMMENDATION_AXES_BY_TYPE["info"],
    )
    if not axes:
        return text

    block = "\n".join(
        [
            "Recommandation ciblée :",
            *[f"- {axis}" for axis in axes[:3]],
        ]
    ).strip()
    return (text.rstrip() + "\n\n" + block).strip()


def _priority_domains_used(results: List[Dict[str, str]]) -> List[str]:
    used: List[str] = []
    for item in results:
        domain = _source_domain_from_url(item.get("href", ""))
        for priority_domain in WEB_PRIORITY_DOMAINS:
            if _domain_matches(domain, priority_domain) and priority_domain not in used:
                used.append(priority_domain)
    return used

# ----------------------------
# Agent Principal avec Métriques
# ----------------------------
def ask_agent(
    question: str, 
    history: Optional[list] = None, 
    max_results: int = 5,
    history_window: int = 8,
    strict_realtime: Optional[bool] = None,
    skip_web_search: bool = False,
    routing_override: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Agent WEB avec recherche internet + synthèse LLM.
    
    Returns:
        {
            "draft": str,
            "sources": list,
            "meta": dict (avec response_time_seconds)
        }
    """
    start_time = time.time()
    history = history or []
    strict_mode = WEB_STRICT_REALTIME if strict_realtime is None else bool(strict_realtime)
    effective_max_results = _effective_max_results(question, max_results)
    requested_top = _requested_top_results(question)
    question_analysis = _analyze_question(question)
    question_type = str(question_analysis.get("type") or "info")
    pre_response_pipeline = _build_pre_response_pipeline(question, question_type)
    style = _detect_style(question, question_type)
    pipeline_intent = str(pre_response_pipeline.get("intent") or PIPELINE_INTENT_KPI).upper()
    if pipeline_intent == PIPELINE_INTENT_TOP:
        style = STYLE_TOP
    elif pipeline_intent == PIPELINE_INTENT_STRATEGIE:
        style = STYLE_STRATEGIE
    routing_override_style = _build_routing_override_for_style(
        question=question,
        question_type=question_type,
        pipeline=pre_response_pipeline,
    )
    effective_routing_override = (
        routing_override
        if isinstance(routing_override, dict) and routing_override
        else routing_override_style
    )

    try:
        # 1. Recherche web
        if skip_web_search:
            results, provider_used = [], "skipped"
        else:
            results, provider_used = web_search(question, max_results=effective_max_results)
        raw_results_count = len(results)
        results, kpi_targets, kpi_filter_applied = _filter_results_by_kpi(results, question)
        results_after_kpi_filter = len(results)
        results, pipeline_filter_applied, pipeline_marketing_removed = _filter_results_for_pipeline(
            results=results,
            pipeline=pre_response_pipeline,
        )

        if not results:
            strict_realtime_failed = bool(strict_mode)
            strict_warning = "no_results_strict_realtime" if strict_realtime_failed else None

            # Fallback utile: réponse directe, prudente et actionnable même sans résultats web.
            format_instruction = _format_instruction_by_pipeline(pre_response_pipeline, question_type)
            fallback_prompt = f"""
Tu es un Conseiller en Gestion de Patrimoine (CGP) senior.
Les sources web live sont insuffisantes pour confirmer les derniers chiffres en temps réel.

QUESTION:
{question}

Consignes:
- Ne refuse jamais de répondre.
- Fais une estimation prudente avec hypothèses raisonnables.
- Précise ce qu'il faut vérifier ensuite.
- Évite les phrases "données non disponibles" et "je ne peux pas répondre".
- Réponse directe et utile, sans blabla.

{format_instruction}
"""
            fallback_draft: Optional[str] = None
            fallback_warning = "no_results_fallback_no_llm"
            synthesis_engine = "rule_based"

            if _claude_optimized_available():
                try:
                    fallback_draft = claude_agent.query(
                        user_query=question,
                        context="Aucune source web live exploitable.",
                        question_type=question_type,
                    )
                    fallback_warning = "no_results_fallback_claude_optimized"
                    synthesis_engine = "claude_agent_optimized"
                except Exception as claude_exc:
                    logger.warning(f"⚠️ Claude optimized indisponible en fallback web: {str(claude_exc)}")

            if not fallback_draft and llm is not None:
                try:
                    fallback_draft = llm.invoke(fallback_prompt).content
                    fallback_warning = "no_results_fallback_llm"
                    synthesis_engine = "web_llm"
                except Exception as llm_exc:
                    logger.warning(f"⚠️ LLM indisponible en fallback web: {str(llm_exc)}")
                    fallback_draft = _draft_from_results_without_llm(question, [])
                    fallback_warning = "no_results_fallback_llm_error"
                    synthesis_engine = "rule_based"

            if not fallback_draft:
                fallback_draft = (
                    _strict_realtime_unavailable_draft(question)
                )
                fallback_warning = "no_results_fallback_no_llm"
                synthesis_engine = "rule_based"
            elapsed = time.time() - start_time
            combined_warning = fallback_warning
            if strict_warning:
                combined_warning = f"{strict_warning}|{fallback_warning}"
            return {
                "draft": fallback_draft,
                "sources": [],
                "meta": {
                    "tool": "web",
                    "knowledge_layer": "rag_market",
                    "provider": provider_used,
                    "configured_provider": WEB_SEARCH_PROVIDER,
                    "max_results": effective_max_results,
                    "requested_top_results": requested_top or None,
                    "actual_results": 0,
                    "search_results": [],
                    "results_before_kpi_filter": raw_results_count,
                    "results_after_kpi_filter": results_after_kpi_filter,
                    "kpi_targets": kpi_targets,
                    "kpi_filter_applied": kpi_filter_applied,
                    "pipeline_filter_applied": pipeline_filter_applied,
                    "pipeline_marketing_removed": pipeline_marketing_removed,
                    "priority_domains": WEB_PRIORITY_DOMAINS,
                    "priority_domains_used": [],
                    "priority_domains_missing": WEB_PRIORITY_DOMAINS,
                    "priority_results_count": 0,
                    "response_time_seconds": round(elapsed, 2),
                    "warning": combined_warning,
                    "strict_realtime_failed": strict_realtime_failed,
                    "strict_realtime_enabled": strict_mode,
                    "synthesis_engine": synthesis_engine,
                    "question_type": question_type,
                    "question_analysis": question_analysis,
                    "pre_response_pipeline": pre_response_pipeline,
                    "intelligence_enabled": True,
                    "intelligence_selected_results": 0,
                    "intelligence_consensus": [],
                    "intelligence_recommendation_axes": RECOMMENDATION_AXES_BY_TYPE.get(
                        question_type,
                        RECOMMENDATION_AXES_BY_TYPE["info"],
                    ),
                    "router_pipeline_used": False,
                    "router_pipeline_ok": False,
                    "router_pipeline_errors": [],
                    "router_pipeline_intents": [],
                    "router_pipeline_answer_target": None,
                    "router_pipeline_style": style,
                    "llm_available": llm is not None,
                    "llm_provider_requested": WEB_LLM_PROVIDER_REQUESTED,
                    "llm_provider_effective": WEB_LLM_PROVIDER_EFFECTIVE,
                    "llm_model": WEB_MODEL,
                },
            }

        intelligence = _build_information_intelligence(
            question=question,
            question_type=question_type,
            results=results,
            max_selected=min(8, max(3, effective_max_results)),
        )
        ranked_results = intelligence.get("ranked_results") if isinstance(intelligence.get("ranked_results"), list) else []
        facts_pack = _build_brain_facts_pack(ranked_results)
        selected_results = (
            intelligence.get("selected_results")
            if isinstance(intelligence.get("selected_results"), list)
            else []
        )
        context_results = selected_results if selected_results else results

        score_by_href: Dict[str, Dict[str, Any]] = {}
        if isinstance(ranked_results, list):
            for row in ranked_results:
                if not isinstance(row, dict):
                    continue
                result = row.get("result") if isinstance(row.get("result"), dict) else {}
                href = (result.get("href") or "").strip()
                if not href:
                    continue
                score_by_href[href] = {
                    "score": row.get("score"),
                    "data_type": row.get("data_type"),
                    "signal": row.get("signal"),
                    "reasons": row.get("reasons"),
                }

        # 2. Construction contexte
        context = "\n\n".join([
            (
                f"[{i+1}] {r['title']}\n"
                f"    source: {_source_domain_from_url(r['href']) or 'web'}\n"
                f"    url: {r['href']}\n"
                f"    pertinence: {score_by_href.get(r.get('href', ''), {}).get('score', 'n/a')}\n"
                f"    type: {score_by_href.get(r.get('href', ''), {}).get('data_type', 'n/a')}\n"
                f"    signal: {score_by_href.get(r.get('href', ''), {}).get('signal', 'n/a')}\n"
                f"    extrait: {r['body']}"
            )
            for i, r in enumerate(context_results)
        ])

        priority_used = _priority_domains_used(context_results)
        priority_targets = [
            f"{PRIORITY_DOMAIN_LABELS.get(domain, domain)} ({domain})"
            for domain in WEB_PRIORITY_DOMAINS
        ]
        priority_used_labels = [
            f"{PRIORITY_DOMAIN_LABELS.get(domain, domain)} ({domain})"
            for domain in priority_used
        ]
        pipeline_primary_kpi = str(pre_response_pipeline.get("primary_kpi") or "none")
        if kpi_targets:
            kpi_target_labels = ", ".join(kpi_targets)
        elif pipeline_primary_kpi != "none":
            kpi_target_labels = pipeline_primary_kpi
        else:
            kpi_target_labels = "none"

        history_text = _format_history(history, history_window)
        facts_block = json.dumps(facts_pack, ensure_ascii=False, indent=2) if facts_pack else "[]"
        pipeline_block = _pipeline_summary_block(pre_response_pipeline)
        pipeline_format_instruction = _format_instruction_by_pipeline(
            pre_response_pipeline,
            question_type,
        )

        # 3. Prompt
        prompt = f"""
Tu es un Conseiller en Gestion de Patrimoine (CGP) senior. Produis une matière utile et exploitable.

HISTORIQUE:
{history_text}

QUESTION:
{question}

FACTS PACK (trié, dédupliqué, déterministe):
{facts_block}

RÉSULTATS:
{context}

ANALYSE DE PERTINENCE (AUTO):
{intelligence.get("context_block", "")}

PIPELINE AVANT RÉPONSE (OBLIGATOIRE):
{pipeline_block}

FORMAT:
{pipeline_format_instruction}

RÈGLES:
- Factuel, pas d'invention.
- Ne refuse jamais de répondre: si données incomplètes, estimation prudente + points à vérifier.
- Évite "données non disponibles" et "je ne peux pas répondre".
- Priorise d'abord ces sources: {", ".join(priority_targets) if priority_targets else "sources métier prioritaires"}.
- Si les sources prioritaires sont insuffisantes, complète avec d'autres sources.
- Compare explicitement les informations entre au moins 2 sources (convergences et écarts).
- Chaque information importante doit citer sa source (domaine ou URL).
- Termine toujours par une section "Sources utilisées:" avec liens cliquables.
- Base d'abord ton raisonnement sur les sources avec meilleure pertinence.
- Respect strict du format de sortie imposé par l'intention (TOP/KPI/STRATEGIE).
- Si needs_clarification=true: répondre quand même et poser au plus 2 questions uniquement dans une section finale "Questions à préciser" (jamais dans Analyse/KPI/Résultat).
- KPI cible demandé: {kpi_target_labels}
- Exclure toute information qui ne répond pas directement au KPI cible.

SOURCES PRIORITAIRES TROUVÉES DANS CETTE RECHERCHE:
{", ".join(priority_used_labels) if priority_used_labels else "Aucune - complété par autres sources."}
"""

        # 4. LLM
        draft: Optional[str] = None
        synthesis_engine = "rule_based"
        router_pipeline_used = False
        router_pipeline_ok = False
        router_pipeline_errors: List[str] = []
        router_pipeline_routing: Dict[str, Any] = {}
        router_pipeline_output: Dict[str, Any] = {}

        if (
            WEB_USE_ROUTER_FINALIZER_CHECKER
            and callable(answer_with_router_finalizer_checker)
            and callable(render_router_output_text)
            and callable(is_router_pipeline_available)
            and bool(is_router_pipeline_available())
        ):
            try:
                router_pipeline_used = True
                router_model = (
                    os.getenv("WEB_ROUTER_FINALIZER_MODEL")
                    or os.getenv("CLAUDE_OPTIMIZED_MODEL")
                    or WEB_MODEL
                    or "claude-sonnet-4-6"
                )
                router_context_lines = [str(intelligence.get("context_block") or "").strip(), ""]
                router_context_lines.append("FACTS_PACK:")
                router_context_lines.append(facts_block)
                router_context_lines.append("")
                for idx, item in enumerate(context_results[:8], start=1):
                    href = (item.get("href") or "").strip()
                    router_context_lines.append(
                        f"[{idx}] title={item.get('title', '')} | domain={_source_domain_from_url(href)} | url={href}"
                    )
                    router_context_lines.append(f"snippet={item.get('body', '')}")
                router_context = "\n".join([line for line in router_context_lines if line]).strip()

                router_payload = answer_with_router_finalizer_checker(
                    user_query=question,
                    context=router_context,
                    model=router_model,
                    max_retries=WEB_ROUTER_FINALIZER_MAX_RETRIES,
                    routing_override=effective_routing_override,
                )
                router_pipeline_ok = bool(router_payload.get("ok"))
                router_pipeline_errors = (
                    router_payload.get("errors")
                    if isinstance(router_payload.get("errors"), list)
                    else []
                )
                router_pipeline_routing = (
                    router_payload.get("routing")
                    if isinstance(router_payload.get("routing"), dict)
                    else {}
                )
                router_pipeline_output = (
                    router_payload.get("output")
                    if isinstance(router_payload.get("output"), dict)
                    else {}
                )
                if router_pipeline_ok and router_pipeline_output:
                    rendered = render_router_output_text(router_pipeline_output, router_pipeline_routing)
                    if rendered:
                        draft = rendered
                        synthesis_engine = "router_finalizer_checker"
            except Exception as router_exc:
                logger.warning(f"⚠️ Router/Finalizer/Checker indisponible: {str(router_exc)}")

        if (not draft) and _claude_optimized_available():
            try:
                draft = claude_agent.query(
                    user_query=question,
                    context=context,
                    question_type=question_type,
                )
                synthesis_engine = "claude_agent_optimized"
            except Exception as claude_exc:
                logger.warning(f"⚠️ Claude optimized indisponible pour synthèse web: {str(claude_exc)}")

        if not draft and llm is not None:
            try:
                draft = llm.invoke(prompt).content
                synthesis_engine = "web_llm"
            except Exception as llm_exc:
                logger.warning(f"⚠️ LLM indisponible pour synthèse web: {str(llm_exc)}")
                draft = _draft_from_results_without_llm(question, context_results)
                synthesis_engine = "rule_based"
        elif not draft:
            draft = _draft_from_results_without_llm(question, context_results)
            synthesis_engine = "rule_based"

        if pipeline_intent == PIPELINE_INTENT_KPI:
            draft = (draft or "").strip()
        else:
            draft = _apply_web_quality_guardrails(draft or "", context_results)
        if pipeline_intent == PIPELINE_INTENT_STRATEGIE:
            draft = _ensure_recommendation_present(
                draft=draft,
                question_type=question_type,
                recommendation_axes=intelligence.get("recommendation_axes") if isinstance(intelligence.get("recommendation_axes"), list) else None,
            )

        # 5. Sources
        sources = list(dict.fromkeys([r["href"] for r in context_results]))
        search_results = _structured_search_sources(context_results)

        if (not draft) and WEB_USE_CABINET_PIPELINE and callable(answer_with_cabinet_pipeline):
            try:
                cabinet_payload = answer_with_cabinet_pipeline(
                    user_query=question,
                    items=context_results,
                    model=os.getenv("WEB_ROUTER_FINALIZER_MODEL") or os.getenv("CLAUDE_OPTIMIZED_MODEL") or WEB_MODEL,
                    max_items=max(5, min(10, requested_top or 10)),
                )
                cabinet_answer = str(cabinet_payload.get("answer") or "").strip()
                if cabinet_answer:
                    draft = cabinet_answer
                    synthesis_engine = "cabinet_pipeline"
            except Exception as cabinet_exc:
                logger.warning(f"⚠️ Cabinet pipeline indisponible: {str(cabinet_exc)}")

        # 6. Retour avec métriques
        elapsed = time.time() - start_time
        
        return {
            "draft": draft,
            "sources": sources,
            "meta": {
                "tool": "web",
                "knowledge_layer": "rag_market",
                "provider": provider_used,
                "configured_provider": WEB_SEARCH_PROVIDER,
                "max_results": effective_max_results,
                "requested_top_results": requested_top or None,
                "actual_results": len(results),
                "selected_results": len(context_results),
                "search_results": search_results,
                "results_before_kpi_filter": raw_results_count,
                "results_after_kpi_filter": results_after_kpi_filter,
                "kpi_targets": kpi_targets,
                "kpi_filter_applied": kpi_filter_applied,
                "pipeline_filter_applied": pipeline_filter_applied,
                "pipeline_marketing_removed": pipeline_marketing_removed,
                "priority_domains": WEB_PRIORITY_DOMAINS,
                "priority_domains_used": priority_used,
                "priority_domains_missing": [d for d in WEB_PRIORITY_DOMAINS if d not in priority_used],
                "priority_results_count": sum(
                    1 for r in context_results if any(_domain_matches(_source_domain_from_url(r.get("href", "")), d) for d in WEB_PRIORITY_DOMAINS)
                ),
                "model": WEB_MODEL,
                "history_length": len(history),
                "response_time_seconds": round(elapsed, 2),
                "query": question,
                "strict_realtime_failed": False,
                "strict_realtime_enabled": strict_mode,
                "synthesis_engine": synthesis_engine,
                "question_type": question_type,
                "question_analysis": question_analysis,
                "pre_response_pipeline": pre_response_pipeline,
                "intelligence_enabled": True,
                "intelligence_selected_results": len(context_results),
                "intelligence_consensus": intelligence.get("consensus_lines") if isinstance(intelligence.get("consensus_lines"), list) else [],
                "intelligence_recommendation_axes": intelligence.get("recommendation_axes") if isinstance(intelligence.get("recommendation_axes"), list) else [],
                "intelligence_top_scores": [
                    row.get("score")
                    for row in (ranked_results[:5] if isinstance(ranked_results, list) else [])
                    if isinstance(row, dict)
                ],
                "router_pipeline_used": router_pipeline_used,
                "router_pipeline_ok": router_pipeline_ok,
                "router_pipeline_errors": router_pipeline_errors[:5],
                "router_pipeline_intents": (
                    router_pipeline_routing.get("intents")
                    if isinstance(router_pipeline_routing.get("intents"), list)
                    else []
                ),
                "router_pipeline_answer_target": (
                    (router_pipeline_routing.get("answer_target") or {}).get("primary")
                    if isinstance(router_pipeline_routing.get("answer_target"), dict)
                    else None
                ),
                "router_pipeline_style": style,
                "brain_facts_pack": facts_pack,
                "llm_available": llm is not None,
                "llm_provider_requested": WEB_LLM_PROVIDER_REQUESTED,
                "llm_provider_effective": WEB_LLM_PROVIDER_EFFECTIVE,
                "llm_model": WEB_MODEL,
            }
        }

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ Erreur agent_online: {str(e)}")
        
        return {
            "draft": f"❌ Erreur : {str(e)}",
            "sources": [],
            "meta": {
                "tool": "web",
                "knowledge_layer": "rag_market",
                "error": str(e),
                "response_time_seconds": round(elapsed, 2),
                "strict_realtime_failed": strict_mode,
                "strict_realtime_enabled": strict_mode,
                "question_type": question_type,
                "question_analysis": question_analysis,
                "pre_response_pipeline": pre_response_pipeline,
                "llm_provider_requested": WEB_LLM_PROVIDER_REQUESTED,
                "llm_provider_effective": WEB_LLM_PROVIDER_EFFECTIVE,
                "llm_model": WEB_MODEL,
            }
        }

# ----------------------------
# Test
# ----------------------------
if __name__ == "__main__":
    result = ask_agent(
        "Taux de rendement SCPI 2024 ?",
        max_results=3
    )
    print(f"\n📄 Draft ({len(result['draft'])} chars):", result["draft"][:200])
    print(f"\n🌐 Sources ({len(result['sources'])}):", result["sources"])
    print(f"\n⏱️ Temps: {result['meta']['response_time_seconds']}s")
    print(f"\n🔍 Meta:", result["meta"])
