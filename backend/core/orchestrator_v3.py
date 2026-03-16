import json
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    import anthropic
except Exception:  # pragma: no cover
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
    retrieval = retrieve_core_raw(question=question, history=history, k=k)
    return {
        "results": retrieval.get("results") if isinstance(retrieval.get("results"), list) else [],
        "meta": dict(retrieval.get("meta", {}) or {}),
    }


def _retrieve_web(question: str, max_results: int = 6) -> Dict[str, Any]:
    results, provider = web_search(question, max_results=max_results)
    return {
        "results": results,
        "meta": {
            "tool": "web",
            "provider": provider,
            "rows_count": len(results),
        },
    }


def _retrieve_sql_kpi(question: str, history: Optional[List[dict]]) -> Dict[str, Any]:
    out = ask_sql_kpi(question, history=history)
    return {
        "results": out.get("sources") if isinstance(out.get("sources"), list) else [],
        "meta": dict(out.get("meta", {}) or {}),
    }


def _call_claude(system_prompt: str, user_prompt: str) -> Tuple[str, Dict[str, Any]]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = (
        os.getenv("CLAUDE_OPTIMIZED_MODEL")
        or os.getenv("FINALIZER_ANTHROPIC_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    )
    if _is_placeholder_key(api_key):
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
        if anthropic is not None:
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
            return "\n".join(parts).strip(), meta

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
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=float(os.getenv("CLAUDE_OPTIMIZED_TIMEOUT_SECONDS", "20")),
            )
            response.raise_for_status()
            data = response.json()
        else:
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
        return "\n".join(parts).strip(), meta
    except Exception as exc:
        meta["provider_effective"] = "anthropic_error"
        meta["warning"] = str(exc)
        return "", meta


def orchestrate_v3(
    question: str,
    history: Optional[List[dict]] = None,
    force_agent: Optional[str] = None,
    neutral_pure: Optional[bool] = None,
    audit_detail: Optional[bool] = None,
    portfolio_simulation_input: Optional[Dict[str, Any]] = None,
    scoring_version: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _ = audit_detail, portfolio_simulation_input, scoring_version, session_state
    history = history or []
    question = _clean(question)
    neutral_mode = bool(neutral_pure)

    intent = detect_intent(question=question, history=history)
    source_plan = choose_sources(
        question=question,
        intent=intent,
        force_agent=force_agent,
        neutral_pure=neutral_mode,
    )

    raw_material: Dict[str, Dict[str, Any]] = {}
    agents_called: List[str] = []
    retrieval_meta: Dict[str, Any] = {}

    for source in source_plan.get("sources") or []:
        if source == "web":
            payload = _retrieve_web(question)
        elif source == "sql_kpi":
            payload = _retrieve_sql_kpi(question, history)
        elif source == "vector":
            payload = _retrieve_vector(question, history)
        else:
            payload = {"results": [], "meta": {"tool": source, "warning": "unsupported_source"}}

        raw_material[source] = payload
        retrieval_meta[source] = dict(payload.get("meta", {}) or {})
        agents_called.append(source)

    evidence_pack = build_evidence_pack(
        question=question,
        intent=intent,
        raw_material=raw_material,
        max_items=8,
    )
    prompts = build_prompt(
        question=question,
        history=history,
        intent=intent,
        evidence_pack=evidence_pack,
    )

    answer_raw, llm_meta = _call_claude(
        system_prompt=prompts["system_prompt"],
        user_prompt=prompts["user_prompt"],
    )
    validated = validate_output(
        answer=answer_raw,
        question=question,
        intent=intent,
        evidence_pack=evidence_pack,
    )
    final_answer = validated["answer"]
    warnings = list(validated.get("warnings") or [])

    structured_v2 = build_structured_payload(
        answer=final_answer,
        intent=intent,
        evidence_pack=evidence_pack,
        status=str(validated.get("status") or "ok"),
    )
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
        "used_facts": [item.get("snippet") for item in (evidence_pack.get("items") or [])[:5] if _clean(item.get("snippet"))],
        "warnings": warnings,
        "sources": evidence_pack.get("sources") or [],
        "sources_by_layer": evidence_pack.get("sources_by_layer") or {},
        "agent_used": "darwin_v3_brain",
        "meta": {
            "darwin_version": "v3",
            "simple_mode": True,
            "intent": intent.get("type"),
            "intent_raw": intent.get("type"),
            "intent_final": intent.get("type"),
            "intent_cgp": structured_v2.get("intent"),
            "kpi_target": structured_v2.get("kpi_target"),
            "agents_called": agents_called,
            "agent_outputs_count": len(raw_material),
            "selected_agent": source_plan.get("primary_source"),
            "selected_layer": source_plan.get("primary_source"),
            "neutral_pure": neutral_mode,
            "live_web_signal": bool((raw_material.get("web") or {}).get("results")),
            "strict_realtime_required": False,
            "strict_realtime_blocked": False,
            "clarification_requested": bool(intent.get("needs_clarification")),
            "clarification_reason": "light_intent_missing_context" if intent.get("needs_clarification") else None,
            "clarification_missing_fields": list(intent.get("clarification_questions") or []),
            "response_format": "structured_contract_v3",
            "contract_format_enforced": True,
            "contract_rewrite_engine": "claude_single_brain" if answer_raw else "deterministic_fallback",
            "finalizer_llm_provider_requested": llm_meta.get("provider_requested"),
            "finalizer_llm_provider_effective": llm_meta.get("provider_effective"),
            "finalizer_llm_model": llm_meta.get("model"),
            "source_plan": source_plan,
            "retrieval_meta": retrieval_meta,
            "evidence_items_count": len(evidence_pack.get("items") or []),
            "evidence_raw_count": int(evidence_pack.get("raw_count") or 0),
            "llm_warning": llm_meta.get("warning"),
            "followup_flow_active": False,
            "followup_phase": "idle",
            "effective_query_used": question,
            "session_state_patch": {},
        },
    }
