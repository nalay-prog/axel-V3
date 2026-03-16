import json
import logging
import os
import re
from typing import Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None
try:
    from .question_detector import detector
except Exception:  # pragma: no cover
    try:
        from backend.agents.question_detector import detector  # type: ignore
    except Exception:
        detector = None


logger = logging.getLogger(__name__)
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


class ClaudeAgentOptimized:
    """Agent Claude optimisé pour réponses directes et synthétiques."""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.model = os.getenv("CLAUDE_OPTIMIZED_MODEL", "claude-sonnet-4-6")
        self.timeout = float(os.getenv("CLAUDE_OPTIMIZED_TIMEOUT_SECONDS", "20"))
        self.max_tokens = int(os.getenv("CLAUDE_OPTIMIZED_MAX_TOKENS", "800"))
        self.temperature = float(os.getenv("CLAUDE_OPTIMIZED_TEMPERATURE", "0.3"))
        self.context_limit = int(os.getenv("CLAUDE_OPTIMIZED_CONTEXT_CHARS", "3000"))
        self.client = None

        if anthropic is not None and not _is_placeholder_key(self.api_key):
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                self.client = None

        self.cgp_guardrail = """RÔLE PRIORITAIRE:
Tu es un Conseiller en Gestion de Patrimoine (CGP) senior.
Tu conseilles, tu tranches, tu assumes.

RÈGLES OBLIGATOIRES:
1) Détecter l'intention: CALCUL, INFO, STRATEGIE_CGP (ou mixte).
2) Ne jamais refuser de répondre.
3) Si des données manquent, faire des hypothèses raisonnables et expliciter ce qu'il faut vérifier.
4) Prendre position, rester concret, relier les chiffres à l'objectif client.
5) Éviter les réponses vagues.

INTERDIT:
- "données non disponibles"
- "je ne peux pas répondre"
- Réponse purement théorique sans proposition exploitable
"""

        self.system_prompts: Dict[str, str] = {
            "calcul": self.cgp_guardrail + """
Format obligatoire:
Hypothèses :
- ...

Calcul :
- ...

Résultat :
- ...

Interprétation :
- ...
""",
            "info": self.cgp_guardrail + """
Format obligatoire:
Réponse directe :
- ...

Points clés :
- ...
- ...
- ...

Optionnel :
- À vérifier / nuance courte

Cas "top/classement":
- Donner un classement indicatif basé sur tendances connues.
- Ajouter: "à valider selon TD, TOF, WALT, frais et liquidité".
""",
            "strategie_cgp": self.cgp_guardrail + """
Format obligatoire:
Analyse :
- ...

Stratégie recommandée :
- ...

Arbitrages :
- ...

Risques :
- ...

Conclusion :
- ...

Questions à poser (max 3) :
- ...
""",
            "mixte_calcul_strategie": self.cgp_guardrail + """
Format obligatoire:
1) Commencer par le calcul:
Hypothèses :
- ...
Calcul :
- ...
Résultat :
- ...
Interprétation :
- ...

2) Puis recommandation patrimoniale:
Analyse :
- ...
Stratégie recommandée :
- ...
Arbitrages :
- ...
Risques :
- ...
Conclusion :
- ...
Questions à poser (max 3) :
- ...
""",
            # aliases legacy
            "synthese_web": self.cgp_guardrail + "\nFormat: Réponse directe + points clés + estimation explicite si manque de données.",
            "conseil": self.cgp_guardrail + "\nFormat: Analyse + stratégie + arbitrages + risques + conclusion.",
            "comparaison": self.cgp_guardrail + "\nFormat: Réponse directe + 3 points clés comparatifs.",
            "definition": self.cgp_guardrail + "\nFormat: Réponse directe + 3 points clés.",
        }

    def is_available(self) -> bool:
        return not _is_placeholder_key(self.api_key)

    def detect_question_type(self, query: str) -> str:
        if detector is not None:
            try:
                analysis = detector.analyze(query or "")
                return analysis.type or "info"
            except Exception:
                pass

        query_lower = (query or "").lower()
        if any(word in query_lower for word in ["calcul", "combien", "projection", "rendement", "mensualite", "mensualité", "%"]):
            if any(word in query_lower for word in ["allocation", "fiscalite", "fiscalité", "invest", "recommande", "que faire", "strategie", "stratégie"]):
                return "mixte_calcul_strategie"
            return "calcul"
        if any(word in query_lower for word in ["allocation", "fiscalite", "fiscalité", "invest", "recommande", "que faire", "strategie", "stratégie"]):
            return "strategie_cgp"
        return "info"

    def _resolve_question_type(self, question_type: Optional[str], user_query: str) -> str:
        aliases = {
            "question_directe": "info",
            "definition": "info",
            "comparaison": "info",
            "conseil": "strategie_cgp",
            "strategie": "strategie_cgp",
            "synthese_web": "info",
            "mixte": "mixte_calcul_strategie",
        }
        requested = (question_type or "").strip().lower()
        if requested:
            return aliases.get(requested, requested)
        detected = self.detect_question_type(user_query)
        return aliases.get(detected, detected)

    def _http_messages_create(self, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "x-api-key": str(self.api_key),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": int(self.max_tokens),
            "temperature": float(self.temperature),
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }

        if httpx is not None:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
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

        content = data.get("content") or []
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                txt = str(block.get("text") or "").strip()
                if txt:
                    parts.append(txt)
        return "\n".join(parts).strip()

    def query(
        self,
        user_query: str,
        context: Optional[str] = None,
        question_type: Optional[str] = None,
    ) -> str:
        if not self.is_available():
            raise RuntimeError("ANTHROPIC_API_KEY indisponible pour ClaudeAgentOptimized.")

        qtype = self._resolve_question_type(question_type=question_type, user_query=user_query)
        logger.info(f"🎯 Type de question détecté: {qtype}")
        system_prompt = self.system_prompts.get(qtype, self.system_prompts["info"])

        if context:
            bounded_context = (context or "")[: max(400, self.context_limit)]
            user_prompt = (
                "Contexte disponible:\n"
                + bounded_context
                + "\n\nQuestion: "
                + (user_query or "")
                + "\n\n⚠️ IMPORTANT: Réponds UNIQUEMENT à la question, rien d'autre."
            )
        else:
            user_prompt = (
                "Question: "
                + (user_query or "")
                + "\n\n⚠️ IMPORTANT: Réponds de manière directe, structurée, sans refuser."
            )

        try:
            if self.client is not None:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=int(self.max_tokens),
                    temperature=float(self.temperature),
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text_parts: List[str] = []
                for block in (message.content or []):
                    block_text = getattr(block, "text", None)
                    if block_text:
                        text_parts.append(str(block_text))
                response = "\n".join(text_parts).strip()
            else:
                response = self._http_messages_create(system_prompt=system_prompt, user_prompt=user_prompt)

            response = self._clean_response(response)
            logger.info(f"✅ Réponse ({len(response)} caractères, type: {qtype})")
            return response
        except HTTPError as exc:
            logger.warning(f"⚠️ Erreur Claude HTTP: {exc}")
            raise
        except Exception as exc:
            logger.warning(f"⚠️ Erreur Claude: {exc}")
            raise

    def _clean_response(self, response: str) -> str:
        forbidden_starts = [
            "Pour répondre à votre question",
            "Je vais vous expliquer",
            "Voici une analyse",
            "Permettez-moi de",
            "Il est important de noter que",
            "En résumé",
            "En conclusion",
            "D'après les informations",
        ]

        lines = (response or "").split("\n")
        cleaned_lines = []
        for raw in lines:
            line = raw.strip()
            if not line:
                cleaned_lines.append(raw)
                continue
            if any(line.startswith(phrase) for phrase in forbidden_starts):
                continue
            cleaned_lines.append(raw)

        cleaned = "\n".join(cleaned_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned


claude_agent = ClaudeAgentOptimized()
