import os
import json
from typing import Optional, List, Dict, Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from types import SimpleNamespace

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
                "max_tokens": int(os.getenv("RAPPORT_ANTHROPIC_MAX_TOKENS", "2200")),
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
            "Anthropic model indisponible pour report: "
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


def _build_report_llm():
    openai_key = _read_env_prefer_non_placeholder("OPENAI_API_KEY")
    anthropic_key = _read_env_prefer_non_placeholder("ANTHROPIC_API_KEY")

    default_provider = "anthropic" if (anthropic_key and not _is_placeholder_key(anthropic_key)) else "openai"
    requested_provider = str(
        os.getenv("RAPPORT_LLM_PROVIDER", os.getenv("LLM_PROVIDER", default_provider))
    ).strip().lower() or default_provider
    allow_fallback = _env_flag("RAPPORT_LLM_ALLOW_FALLBACK", _env_flag("LLM_ALLOW_PROVIDER_FALLBACK", True))

    openai_model = os.getenv("RAPPORT_OPENAI_MODEL", os.getenv("RAPPORT_MODEL", "gpt-4o-mini"))
    anthropic_model = os.getenv(
        "RAPPORT_ANTHROPIC_MODEL",
        os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    )
    anthropic_candidates = _anthropic_model_candidates(anthropic_model, scope="RAPPORT")

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
                return client, "anthropic_http", anthropic_candidates[0]
            except Exception:
                pass
            if ChatAnthropic is not None:
                for candidate_model in anthropic_candidates:
                    try:
                        client = ChatAnthropic(model=candidate_model, temperature=0.2, api_key=anthropic_key)
                        return client, "anthropic_langchain", candidate_model
                    except TypeError:
                        try:
                            client = ChatAnthropic(
                                model=candidate_model,
                                temperature=0.2,
                                anthropic_api_key=anthropic_key,
                            )
                            return client, "anthropic_langchain", candidate_model
                        except Exception:
                            continue
                    except Exception:
                        continue
        if provider == "openai" and not _is_placeholder_key(openai_key) and ChatOpenAI is not None:
            try:
                client = ChatOpenAI(model=openai_model, temperature=0.2, api_key=openai_key)
                return client, "openai", openai_model
            except Exception:
                pass

    return None, "none", ""


llm, RAPPORT_LLM_PROVIDER_EFFECTIVE, RAPPORT_MODEL = _build_report_llm()


def _format_history(history: List[dict], max_items: int = 8) -> str:
    if not history:
        return "Aucun historique."
    lines = []
    for msg in history[-max_items:]:
        role = str(msg.get("role", "user")).upper()
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "Aucun historique pertinent."


def _materials_to_text(materials: List[Dict[str, Any]]) -> str:
    chunks = []
    for i, out in enumerate(materials, start=1):
        agent = out.get("agent", "inconnu")
        meta = out.get("meta") or {}
        provider = meta.get("provider")
        draft = str(out.get("draft", "")).strip()
        if draft:
            tag = f"agent={agent}" + (f", provider={provider}" if provider else "")
            chunks.append(f"[SOURCE_INTERNE_{i}] {tag}\n{draft[:3000]}")
    return "\n\n".join(chunks) if chunks else "Aucune matière agent disponible."


def _normalize_sources(materials: List[Dict[str, Any]]) -> List[Any]:
    raw_sources: List[Any] = []
    for out in materials:
        raw_sources.extend(out.get("sources") or [])

    seen = set()
    normalized: List[Any] = []
    for source in raw_sources:
        if not source:
            continue
        if isinstance(source, dict):
            key = json.dumps(source, sort_keys=True, ensure_ascii=False, default=str)
        else:
            key = str(source)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(source)
    return normalized


def ask_agent(
    question: str,
    history: Optional[List[dict]] = None,
    materials: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Génère une réponse de type rapport client à partir de la matière produite
    par les autres agents (core / online).
    """
    history = history or []
    materials = materials or []

    try:
        history_text = _format_history(history)
        materials_text = _materials_to_text(materials)

        if llm is None:
            sources = _normalize_sources(materials)
            fallback = (
                "## 1. Synthèse Exécutive\n"
                "Moteur rapport indisponible: réponse générée en mode dégradé.\n\n"
                "## 2. Profil Et Objectifs (ou hypothèses explicites)\n"
                "Hypothèses à confirmer avec le client (horizon, risque, fiscalité).\n\n"
                "## 3. Analyse Et Comparaison Des Options\n"
                "| Option | Atouts | Limites | Horizon |\n"
                "| --- | --- | --- | --- |\n"
                "| Option A | Données disponibles | Analyse incomplète | A confirmer |\n\n"
                "## 4. Recommandation Argumentée\n"
                "Recommandation provisoire: valider les données clés avant décision.\n\n"
                "## 5. Risques Et Vigilances\n"
                "- Données potentiellement incomplètes\n"
                "- Vérification des sources requise\n\n"
                "## 6. Plan D'action Proposé\n"
                "- Confirmer profil investisseur\n"
                "- Vérifier KPI et dates\n"
                "- Valider allocation cible\n\n"
                "## 7. Sources Et Traçabilité\n"
                + (materials_text[:2500] if materials_text else "Aucune matière agent disponible.")
            )
            return {
                "draft": fallback,
                "sources": sources,
                "meta": {
                    "tool": "rapport",
                    "model": RAPPORT_MODEL,
                    "llm_provider_effective": RAPPORT_LLM_PROVIDER_EFFECTIVE,
                    "materials_count": len(materials),
                    "warning": "report_llm_unavailable",
                },
            }

        prompt = f"""
Tu es l'agent RAPPORT Darwin pour conseillers en gestion de patrimoine.
Ta mission: produire une note claire, structurée et actionnable à partir de la matière fournie.

QUESTION UTILISATEUR:
{question}

HISTORIQUE:
{history_text}

MATIERE AGENTS:
{materials_text}

Contraintes:
- Ne pas inventer de faits.
- Si une information est incertaine, l'indiquer explicitement.
- Produire un livrable client professionnel en markdown.
- Utiliser exactement ces sections:
  ## 1. Synthèse Exécutive
  ## 2. Profil Et Objectifs (ou hypothèses explicites)
  ## 3. Analyse Et Comparaison Des Options
  ## 4. Recommandation Argumentée
  ## 5. Risques Et Vigilances
  ## 6. Plan D'action Proposé
  ## 7. Sources Et Traçabilité
- Dans "Analyse Et Comparaison", inclure un tableau court (Option | Atouts | Limites | Horizon).
- Dans "Plan D'action", inclure 3 à 5 actions concrètes.
- Style: clair, sobre, orienté décision CGP.
"""

        draft = llm.invoke(prompt).content

        sources = _normalize_sources(materials)

        return {
            "draft": draft,
            "sources": sources,
            "meta": {
                "tool": "rapport",
                "model": RAPPORT_MODEL,
                "llm_provider_effective": RAPPORT_LLM_PROVIDER_EFFECTIVE,
                "materials_count": len(materials),
            },
        }

    except Exception as e:
        return {
            "draft": f"❌ Erreur agent rapport: {str(e)}",
            "sources": [],
            "meta": {"tool": "rapport", "error": str(e)},
        }
