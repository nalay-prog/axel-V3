# claude_fallback_patch.py - Patch pour corriger les problèmes Claude
# À appliquer sur orchestrator_v3.py

import os
import time
from typing import Any, Dict, List, Optional, Tuple

# Patch pour _call_claude avec fallback amélioré
def _call_claude_patched(system_prompt: str, user_prompt: str) -> Tuple[str, Dict[str, Any]]:
    """Version patchée de _call_claude avec diagnostics et fallbacks"""
    print("🤖 [PATCH] _call_claude_patched called")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = (
        os.getenv("CLAUDE_OPTIMIZED_MODEL")
        or os.getenv("FINALIZER_ANTHROPIC_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    )

    print(f"🤖 [PATCH] API Key configured: {not _is_placeholder_key(api_key)}")
    print(f"🤖 [PATCH] Model: {model}")

    if _is_placeholder_key(api_key):
        print("❌ [PATCH] API key is placeholder - using fallback response")
        fallback_msg = "⚠️ Clé API Anthropic non configurée. Veuillez vérifier votre configuration .env"
        return fallback_msg, {
            "provider_requested": "anthropic",
            "provider_effective": "none",
            "model": model,
            "warning": "anthropic_api_key_missing",
            "fallback_used": True
        }

    # Essai avec le modèle principal
    models_to_try = [model]

    # Ajouter les fallbacks
    fallbacks = os.getenv("ANTHROPIC_MODEL_FALLBACKS", "").split(",")
    for fb in fallbacks:
        fb = fb.strip()
        if fb and fb not in models_to_try:
            models_to_try.append(fb)

    print(f"🤖 [PATCH] Models to try: {models_to_try}")

    for attempt_model in models_to_try:
        print(f"🤖 [PATCH] Trying model: {attempt_model}")
        try:
            start_time = time.time()

            # Essai avec anthropic library
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                message = client.messages.create(
                    model=attempt_model,
                    max_tokens=int(os.getenv("CLAUDE_OPTIMIZED_MAX_TOKENS", "900")),
                    temperature=float(os.getenv("CLAUDE_OPTIMIZED_TEMPERATURE", "0.2")),
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                parts: List[str] = []
                for block in (message.content or []):
                    block_text = getattr(block, "text", None)
                    if block_text:
                        parts.append(str(block_text))

                response_text = "\n".join(parts).strip()
                elapsed = time.time() - start_time

                if response_text:
                    print(f"✅ [PATCH] Claude responded with model {attempt_model} in {elapsed:.2f}s")
                    return response_text, {
                        "provider_requested": "anthropic",
                        "provider_effective": "anthropic",
                        "model": attempt_model,
                        "response_time": elapsed
                    }
                else:
                    print(f"⚠️ [PATCH] Empty response from {attempt_model}")

            except ImportError:
                print("⚠️ [PATCH] Anthropic library not available, trying HTTP fallback")

            # Fallback HTTP
            try:
                import httpx
                payload = {
                    "model": attempt_model,
                    "max_tokens": int(os.getenv("CLAUDE_OPTIMIZED_MAX_TOKENS", "900")),
                    "temperature": float(os.getenv("CLAUDE_OPTIMIZED_TEMPERATURE", "0.2")),
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                headers = {
                    "x-api-key": str(api_key),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }

                response = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=float(os.getenv("CLAUDE_OPTIMIZED_TIMEOUT_SECONDS", "20")),
                )
                response.raise_for_status()
                data = response.json()

                parts = []
                for block in (data.get("content") or []):
                    if isinstance(block, dict) and _clean(block.get("text")):
                        parts.append(_clean(block.get("text")))

                response_text = "\n".join(parts).strip()
                elapsed = time.time() - start_time

                if response_text:
                    print(f"✅ [PATCH] HTTP Claude responded with model {attempt_model} in {elapsed:.2f}s")
                    return response_text, {
                        "provider_requested": "anthropic",
                        "provider_effective": "anthropic_http",
                        "model": attempt_model,
                        "response_time": elapsed
                    }

            except ImportError:
                print("⚠️ [PATCH] httpx not available, trying urllib")

            # Dernier fallback avec urllib
            try:
                import json
                from urllib.request import Request, urlopen

                payload = {
                    "model": attempt_model,
                    "max_tokens": int(os.getenv("CLAUDE_OPTIMIZED_MAX_TOKENS", "900")),
                    "temperature": float(os.getenv("CLAUDE_OPTIMIZED_TEMPERATURE", "0.2")),
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                headers = {
                    "x-api-key": str(api_key),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }

                request = Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=float(os.getenv("CLAUDE_OPTIMIZED_TIMEOUT_SECONDS", "20"))) as res:
                    data = json.loads(res.read().decode("utf-8", errors="replace"))

                parts = []
                for block in (data.get("content") or []):
                    if isinstance(block, dict) and _clean(block.get("text")):
                        parts.append(_clean(block.get("text")))

                response_text = "\n".join(parts).strip()
                elapsed = time.time() - start_time

                if response_text:
                    print(f"✅ [PATCH] urllib Claude responded with model {attempt_model} in {elapsed:.2f}s")
                    return response_text, {
                        "provider_requested": "anthropic",
                        "provider_effective": "anthropic_urllib",
                        "model": attempt_model,
                        "response_time": elapsed
                    }

            except Exception as http_exc:
                print(f"⚠️ [PATCH] HTTP fallback failed for {attempt_model}: {str(http_exc)}")
                continue

        except Exception as exc:
            print(f"⚠️ [PATCH] Model {attempt_model} failed: {str(exc)}")
            continue

    # Tous les modèles ont échoué
    print("❌ [PATCH] All Claude models failed - using final fallback")
    fallback_msg = "⚠️ Impossible de contacter Claude. Vérifiez votre connexion internet et votre clé API Anthropic."
    return fallback_msg, {
        "provider_requested": "anthropic",
        "provider_effective": "error",
        "model": model,
        "warning": "all_claude_models_failed",
        "fallback_used": True
    }


def _is_placeholder_key(value: Optional[str]) -> bool:
    """Fonction utilitaire pour détecter les clés placeholder"""
    token = (value or "").strip().lower()
    if not token:
        return True
    placeholders = {"...", "xxx", "your_key", "your-api-key", "changeme", "replace_me"}
    if token in placeholders:
        return True
    return token.startswith("your_") or token.startswith("sk-...")


def _clean(text: Any) -> str:
    """Fonction utilitaire pour nettoyer le texte"""
    return str(text or "").strip()


# Instructions d'application du patch
PATCH_INSTRUCTIONS = """
📋 INSTRUCTIONS POUR APPLIQUER LE PATCH:

1. Sauvegardez l'original:
   cp backend/core/orchestrator_v3.py backend/core/orchestrator_v3.py.backup

2. Ajoutez l'import en haut du fichier:
   from .claude_fallback_patch import _call_claude_patched

3. Remplacez l'appel à _call_claude par _call_claude_patched:
   # Avant:
   answer_raw, llm_meta = _call_claude(
       system_prompt=prompts["system_prompt"],
       user_prompt=prompts["user_prompt"],
   )

   # Après:
   answer_raw, llm_meta = _call_claude_patched(
       system_prompt=prompts["system_prompt"],
       user_prompt=prompts["user_prompt"],
   )

4. Redémarrez le backend pour appliquer les changements.

Le patch ajoute:
- ✅ Logs détaillés pour diagnostiquer les problèmes
- ✅ Essai de plusieurs modèles en fallback
- ✅ Support HTTP et urllib si la lib anthropic échoue
- ✅ Messages d'erreur explicites
- ✅ Métriques de performance
"""

if __name__ == "__main__":
    print("🔧 CLAUDE FALLBACK PATCH")
    print("========================")
    print(PATCH_INSTRUCTIONS)