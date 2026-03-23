# orchestrator_v3_debug.py - Version de debug de l'orchestrateur v3
# Ajoute des logs détaillés pour diagnostiquer les problèmes avec Claude

import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

try:
    import httpx
except Exception:
    httpx = None

try:
    import anthropic
except Exception:
    anthropic = None

from ..agents.agent_core import retrieve_raw as retrieve_core_raw
from ..agents.agent_online import web_search
from ..agents.agent_sql_kpi import ask_agent as ask_sql_kpi
from .evidence_pack import build_evidence_pack
from .output_validator import build_structured_payload, validate_output
from .prompt_builder import build_prompt
from .simple_intent import detect_intent
from .source_router import choose_sources


def _clean(text: Any) -> str:
    return str(text or "").strip()


def _is_placeholder_key(value: Optional[str]) -> bool:
    token = _clean(value).lower()
    if not token:
        return True
    placeholders = {"...", "xxx", "your_key", "your-api-key", "changeme", "replace_me"}
    if token in placeholders:
        return True
    return token.startswith("your_") or token.startswith("sk-...")


def _history_tail(history: Optional[List[dict]], max_items: int = 3) -> str:
    rows: List[str] = []
    for msg in (history or [])[-max_items:]:
        role = _clean(msg.get("role")).upper() or "USER"
        content = _clean(msg.get("content"))
        if content:
            rows.append(f"{role}: {content}")
    return "\n".join(rows)


def _retrieve_vector(question: str, history: Optional[List[dict]], k: int = 4) -> Dict[str, Any]:
    return retrieve_core_raw(question, history, k=k)


def _retrieve_web(question: str, max_results: int = 6) -> Dict[str, Any]:
    print(f"🔍 [DEBUG] _retrieve_web called with question: '{question}', max_results: {max_results}")
    results, provider = web_search(question, max_results=max_results)
    print(f"🔍 [DEBUG] web_search returned {len(results)} results from provider: {provider}")
    return {
        "results": results,
        "meta": {
            "tool": "web",
            "provider": provider,
            "rows_count": len(results),
        },
    }


def _retrieve_sql_kpi(question: str, history: Optional[List[dict]]) -> Dict[str, Any]:
    print(f"🗃️ [DEBUG] _retrieve_sql_kpi called with question: '{question}'")
    out = ask_sql_kpi(question, history=history)
    print(f"🗃️ [DEBUG] ask_sql_kpi returned: {type(out)}, has sources: {'sources' in out}")
    return {
        "results": out.get("sources") if isinstance(out.get("sources"), list) else [],
        "meta": dict(out.get("meta", {}) or {}),
    }


def _call_claude_debug(system_prompt: str, user_prompt: str) -> Tuple[str, Dict[str, Any]]:
    """Version de debug de _call_claude avec logs détaillés"""
    print("🤖 [DEBUG] _call_claude called")
    print(f"🤖 [DEBUG] System prompt length: {len(system_prompt)} chars")
    print(f"🤖 [DEBUG] User prompt length: {len(user_prompt)} chars")
    print(f"🤖 [DEBUG] User prompt preview: {user_prompt[:200]}...")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = (
        os.getenv("CLAUDE_OPTIMIZED_MODEL")
        or os.getenv("FINALIZER_ANTHROPIC_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    )

    print(f"🤖 [DEBUG] API Key configured: {not _is_placeholder_key(api_key)}")
    print(f"🤖 [DEBUG] Model: {model}")

    if _is_placeholder_key(api_key):
        print("❌ [DEBUG] API key is placeholder - returning error")
        return "", {
            "provider_requested": "anthropic",
            "provider_effective": "none",
            "model": model,
            "warning": "anthropic_api_key_missing",
        }

    meta = {
        "provider_requested": "anthropic",
        "provider_effective": "anthropic",
        "model": model,
    }

    try:
        print("🤖 [DEBUG] Attempting Claude API call...")
        start_time = time.time()

        if anthropic is not None:
            print("🤖 [DEBUG] Using anthropic library")
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=model,
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
            print(f"✅ [DEBUG] Claude responded in {elapsed:.2f}s, response length: {len(response_text)} chars")
            print(f"🤖 [DEBUG] Response preview: {response_text[:200]}...")
            return response_text, meta

        print("🤖 [DEBUG] Anthropic library not available, using HTTP fallback")
        # HTTP fallback code...
        payload = {
            "model": model,
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

        if httpx is not None:
            print("🤖 [DEBUG] Using httpx for HTTP request")
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=float(os.getenv("CLAUDE_OPTIMIZED_TIMEOUT_SECONDS", "20")),
            )
            response.raise_for_status()
            data = response.json()
        else:
            print("🤖 [DEBUG] Using urllib for HTTP request")
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
        print(f"✅ [DEBUG] Claude HTTP fallback responded in {elapsed:.2f}s, response length: {len(response_text)} chars")
        print(f"🤖 [DEBUG] Response preview: {response_text[:200]}...")
        return response_text, meta

    except Exception as exc:
        elapsed = time.time() - start_time
        print(f"❌ [DEBUG] Claude call failed after {elapsed:.2f}s with error: {str(exc)}")
        meta["provider_effective"] = "anthropic_error"
        meta["warning"] = str(exc)
        return "", meta


def orchestrate_v3_debug(
    question: str,
    history: Optional[List[dict]] = None,
    force_agent: Optional[str] = None,
    neutral_pure: Optional[bool] = None,
    audit_detail: Optional[bool] = None,
    portfolio_simulation_input: Optional[Dict[str, Any]] = None,
    scoring_version: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Version de debug de orchestrate_v3 avec logs détaillés"""
    print(f"\n🚀 [DEBUG] orchestrate_v3_debug called with question: '{question}'")
    print(f"🚀 [DEBUG] force_agent: {force_agent}, neutral_pure: {neutral_pure}")

    start_time = time.time()
    _ = audit_detail, portfolio_simulation_input, scoring_version, session_state
    history = history or []
    question = _clean(question)
    neutral_mode = bool(neutral_pure)

    print("🔍 [DEBUG] Detecting intent...")
    intent = detect_intent(question=question, history=history)
    print(f"🔍 [DEBUG] Intent detected: {intent.get('type')} (confidence: {intent.get('confidence', 0):.2f})")

    print("🗂️ [DEBUG] Choosing sources...")
    source_plan = choose_sources(
        question=question,
        intent=intent,
        force_agent=force_agent,
        neutral_pure=neutral_mode,
    )
    print(f"🗂️ [DEBUG] Sources chosen: {source_plan.get('sources', [])}")

    raw_material: Dict[str, Dict[str, Any]] = {}
    agents_called: List[str] = []
    retrieval_meta: Dict[str, Any] = {}

    print("📊 [DEBUG] Retrieving raw material from sources...")
    for source in source_plan.get("sources") or []:
        print(f"📊 [DEBUG] Retrieving from source: {source}")
        if source == "web":
            payload = _retrieve_web(question)
        elif source == "sql_kpi":
            payload = _retrieve_sql_kpi(question, history)
        elif source == "vector":
            payload = _retrieve_vector(question, history)
        else:
            print(f"⚠️ [DEBUG] Unsupported source: {source}")
            payload = {"results": [], "meta": {"tool": source, "warning": "unsupported_source"}}

        raw_material[source] = payload
        retrieval_meta[source] = dict(payload.get("meta", {}) or {})
        agents_called.append(source)

    print("📋 [DEBUG] Building evidence pack...")
    evidence_pack = build_evidence_pack(
        question=question,
        intent=intent,
        raw_material=raw_material,
        max_items=8,
    )
    print(f"📋 [DEBUG] Evidence pack built with {len(evidence_pack.get('items', []))} items")

    print("✍️ [DEBUG] Building prompts...")
    prompts = build_prompt(
        question=question,
        history=history,
        intent=intent,
        evidence_pack=evidence_pack,
    )
    print("✍️ [DEBUG] Prompts built successfully")

    print("🤖 [DEBUG] Calling Claude for final synthesis...")
    answer_raw, llm_meta = _call_claude_debug(
        system_prompt=prompts["system_prompt"],
        user_prompt=prompts["user_prompt"],
    )

    if not answer_raw:
        print("❌ [DEBUG] Claude returned empty response!")
        if "warning" in llm_meta:
            print(f"❌ [DEBUG] Warning: {llm_meta['warning']}")

    print("✅ [DEBUG] Validating output...")
    validated = validate_output(
        answer=answer_raw,
        question=question,
        intent=intent,
        evidence_pack=evidence_pack,
    )
    final_answer = validated["answer"]
    warnings = list(validated.get("warnings") or [])

    print("🏗️ [DEBUG] Building structured payload...")
    structured_v2 = build_structured_payload(
        answer=final_answer,
        intent=intent,
        evidence_pack=evidence_pack,
        status=str(validated.get("status") or "ok"),
    )

    total_time = time.time() - start_time
    print(f"🎯 [DEBUG] orchestrate_v3_debug completed in {total_time:.2f}s")
    print(f"🎯 [DEBUG] Final answer length: {len(final_answer)} chars")

    answer_json = {
        "format": "simple_v3",
        "intent": structured_v2.get("intent"),
        "answer": final_answer,
        "sources": structured_v2.get("sources_used"),
    }

    return {
        "answer": final_answer,
        "answer_text": final_answer,
        "answer_json": answer_json,
        "answer_structured": answer_json,
        "answer_structured_v2": structured_v2,
        "answer_contract": {
            "intent": str(structured_v2.get("intent") or intent.get("type") or "INFO"),
            "kpi_target": str(structured_v2.get("kpi_target") or intent.get("kpi_target") or "none"),
        },
        "details": final_answer,
        "meta": {
            "orchestrator_version": "v3_debug",
            "intent": intent,
            "sources_used": agents_called,
            "llm_meta": llm_meta,
            "warnings": warnings,
            "total_time_seconds": round(total_time, 2),
            "raw_material_keys": list(raw_material.keys()),
        },
    }


# Test rapide si appelé directement
if __name__ == "__main__":
    print("🧪 Testing orchestrate_v3_debug...")
    result = orchestrate_v3_debug("Quel est le taux de distribution de Darwin RE01 ?")
    print(f"✅ Test completed. Answer: {result['answer'][:100]}...")