# backend/core/orchestrator_v3.py
"""
Orchestrator V3 — version finale (router fiable + Claude stable + WEB seulement si nécessaire)

Architecture:
1) Router (DIRECT / RAG / WEB / RAG+WEB)
2) Retrieval (Darwin vector/sql_kpi) + Web (si nécessaire)
3) Evidence pack
4) Prompt builder
5) Claude synthesis (Anthropic) + validation + structured output
6) Fallback déterministe si Claude indisponible

Design final validé:
✅ DIRECT = sans Claude
✅ RAG/WEB/RAG+WEB = avec Claude

Objectifs clés:
- "hello" ne déclenche jamais une recherche web.
- WEB uniquement si signaux de fraîcheur / actualité.
- Chargement .env robuste: override=True (évite clés exportées obsolètes).
- Meta explicites: route_mode, route_signals, anthropic_key_tail, etc.

Règle produit validée (b):
✅ STRATEGIE = NO WEB par défaut (sauf signaux explicites: actuel/2026/source/lien/BCE/URL…)

Ajout demandé:
✅ Si aucune infos à ce sujet dans ma documentation, je fais une recherche plus approfondie ?
(phrase gardée telle quelle, sans répétition de la question)
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

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

try:
    from .synthesis_agent import synthesize_answer
except Exception:  # pragma: no cover
    synthesize_answer = None  # type: ignore

try:
    from .claude_fallback_patch import _call_claude_patched
except Exception:  # pragma: no cover
    _call_claude_patched = None  # type: ignore


# --------------------------------------------------------------------------------------
# ENV loading (robuste)
# --------------------------------------------------------------------------------------
def _boot_load_env() -> Dict[str, Any]:
    meta: Dict[str, Any] = {"dotenv_loaded": False, "dotenv_path": None}
    if load_dotenv is None:
        meta["dotenv_loaded"] = False
        meta["dotenv_error"] = "python-dotenv unavailable"
        return meta

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env_path = os.path.join(project_root, ".env")
    meta["dotenv_path"] = env_path

    try:
        loaded = bool(load_dotenv(dotenv_path=env_path, override=True))
        meta["dotenv_loaded"] = loaded
        return meta
    except Exception as exc:  # pragma: no cover
        meta["dotenv_loaded"] = False
        meta["dotenv_error"] = str(exc)
        return meta


ENV_BOOT_META = _boot_load_env()


# --------------------------------------------------------------------------------------
# Utils
# --------------------------------------------------------------------------------------
def _clean(text: Any) -> str:
    return str(text or "").strip()


def _anthropic_key_tail() -> str:
    key = os.getenv("ANTHROPIC_API_KEY") or ""
    key = key.strip()
    return key[-4:] if len(key) >= 4 else ""


def _is_placeholder_key(value: Optional[str]) -> bool:
    token = _clean(value).lower()
    if not token:
        return True
    placeholders = {"...", "xxx", "your_key", "your-api-key", "changeme", "replace_me"}
    if token in placeholders:
        return True
    return token.startswith("your_") or token.startswith("sk-...")


# --------------------------------------------------------------------------------------
# Router signals
# --------------------------------------------------------------------------------------
_SMALLTALK_PATTERNS = [
    re.compile(r"^(hi|hello|hey|yo)\b", re.IGNORECASE),
    re.compile(r"^(salut|coucou|bonjour|bonsoir)\b", re.IGNORECASE),
    re.compile(r"^(merci|thx|thanks)\b", re.IGNORECASE),
    re.compile(r"^(ok|d'accord|parfait|super|top)\b", re.IGNORECASE),
    re.compile(r"^(ca va|ça va|comment vas tu|comment ca va)\b", re.IGNORECASE),
]

_WEB_SIGNAL_TERMS = (
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
    "source",
    "lien",
    "url",
    "bce",
    "euribor",
    "oat",
    "insee",
    "amf",
    "acpr",
)


def _normalize_ascii(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.replace("é", "e").replace("è", "e").replace("ê", "e")
    t = t.replace("à", "a").replace("â", "a").replace("î", "i")
    t = t.replace("ô", "o").replace("ù", "u").replace("û", "u")
    t = t.replace("ç", "c")
    t = re.sub(r"\s+", " ", t)
    return t


def _is_smalltalk(question: str) -> bool:
    q = _normalize_ascii(question)
    if not q:
        return True
    if len(q) <= 4:
        return True
    return any(p.search(q) for p in _SMALLTALK_PATTERNS)


def _has_web_signal(question: str, intent: Dict[str, Any]) -> Tuple[bool, List[str]]:
    q = _normalize_ascii(question)
    signals: List[str] = []

    if not q:
        return False, signals

    if re.search(r"https?://|www\.", question or "", flags=re.IGNORECASE):
        signals.append("url_in_question")

    years = re.findall(r"\b(20\d{2})\b", q)
    for y in years:
        if y in {"2024", "2025", "2026"}:
            signals.append(f"year:{y}")
            break

    for term in _WEB_SIGNAL_TERMS:
        if term in q:
            signals.append(f"term:{term}")
            break

    if bool(intent.get("needs_freshness")):
        signals.append("intent.needs_freshness")

    return bool(signals), signals[:6]


def _route_mode_from_sources(sources: List[str]) -> str:
    src = list(dict.fromkeys([s for s in sources if s]))
    if not src:
        return "DIRECT"
    has_web = "web" in src
    has_rag = any(s in src for s in ("vector", "sql_kpi"))
    if has_web and has_rag:
        return "RAG+WEB"
    if has_web:
        return "WEB"
    return "RAG"


def _apply_router_overrides(
    question: str,
    intent: Dict[str, Any],
    source_plan: Dict[str, Any],
    force_agent: Optional[str],
    neutral_mode: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    debug: Dict[str, Any] = {"overrides": []}
    sources = list(source_plan.get("sources") or [])
    sources = [str(s) for s in sources if str(s).strip()]

    if str(force_agent or "").strip():
        debug["overrides"].append("force_agent_bypass_overrides")
        final_sources = list(dict.fromkeys(sources))
        return (
            {
                "sources": final_sources,
                "primary_source": final_sources[0] if final_sources else None,
                "reasoning": list(source_plan.get("reasoning") or []) + ["overrides:force_agent_no_change"],
            },
            debug,
        )

    if _is_smalltalk(question):
        debug["overrides"].append("smalltalk_direct")
        return ({"sources": [], "primary_source": None, "reasoning": ["smalltalk_direct"]}, debug)

    intent_type = str(intent.get("type") or "INFO").upper()
    is_darwin = bool(intent.get("is_darwin_specific"))
    has_web_signal, web_signals = _has_web_signal(question, intent)
    debug["web_signals"] = web_signals
    debug["has_web_signal"] = has_web_signal

    final_sources = list(dict.fromkeys(sources))

    # Règle b: STRATEGIE => pas de web par défaut
    if intent_type == "STRATEGIE" and not has_web_signal:
        if "web" in final_sources:
            final_sources = [s for s in final_sources if s != "web"]
            debug["overrides"].append("strategie_no_web_by_default")

    if intent_type in {"INFO", "DARWIN"} and not has_web_signal:
        if "web" in final_sources:
            final_sources = [s for s in final_sources if s != "web"]
            debug["overrides"].append("info_no_web_by_default")
        if (not is_darwin) and (not any(s in final_sources for s in ("vector", "sql_kpi"))):
            final_sources = []
            debug["overrides"].append("info_non_darwin_direct")

    if is_darwin and not neutral_mode:
        if "vector" not in final_sources and intent_type in {"INFO", "DARWIN", "KPI", "STRATEGIE", "RAPPORT"}:
            final_sources.insert(0, "vector")
            debug["overrides"].append("darwin_prefers_vector")

    if has_web_signal and "web" not in final_sources:
        final_sources.insert(0, "web")
        debug["overrides"].append("web_signal_adds_web")

    final_sources = list(dict.fromkeys(final_sources))
    reasoning = list(source_plan.get("reasoning") or [])
    reasoning.extend([f"override:{x}" for x in debug.get("overrides") or []])

    return (
        {
            "sources": final_sources,
            "primary_source": final_sources[0] if final_sources else None,
            "reasoning": reasoning,
        },
        debug,
    )


# --------------------------------------------------------------------------------------
# DIRECT (sans Claude) — réponse déterministe
# --------------------------------------------------------------------------------------
def _direct_answer(question: str, history: List[dict]) -> str:
    _ = history
    q = _normalize_ascii(question)

    if _is_smalltalk(question):
        return (
            "Bonjour 👋\n\n"
            "Dis-moi ce que tu veux faire (SCPI, allocation, fiscalité, comparaison, rédaction d'un message client, etc.) "
            "et je te réponds en format CGP."
        )

    if len(q) < 12 or len(q.split()) <= 2:
        return (
            "Je peux t’aider, mais il me manque le contexte.\n\n"
            "Pour que je te réponde comme un CGP, donne-moi :\n"
            "- objectif (revenus, valorisation, diversification, optimisation fiscale)\n"
            "- horizon (années)\n"
            "- montant\n"
            "- fiscalité (IR/IS) + TMI si IR\n"
        )

    return (
        "Reçu. Pour te répondre au mieux, précise si tu veux :\n"
        "- une réponse courte (KPI)\n"
        "- une analyse + recommandation\n"
        "- un classement/benchmark\n"
        "et indique l’horizon + fiscalité si c’est une reco."
    )


# --------------------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------------------
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
        "meta": {"tool": "web", "provider": provider, "rows_count": len(results)},
    }


def _retrieve_sql_kpi(question: str, history: Optional[List[dict]]) -> Dict[str, Any]:
    out = ask_sql_kpi(question, history=history)
    return {
        "results": out.get("sources") if isinstance(out.get("sources"), list) else [],
        "meta": dict(out.get("meta", {}) or {}),
    }


# --------------------------------------------------------------------------------------
# Ajout: WEB fallback si doc Darwin vide
# --------------------------------------------------------------------------------------
def _count_internal_evidence(evidence_pack: Dict[str, Any]) -> Dict[str, int]:
    items = evidence_pack.get("items") if isinstance(evidence_pack.get("items"), list) else []
    vector_count = 0
    sql_count = 0
    web_count = 0
    for it in items:
        layer = str((it or {}).get("layer") or "").strip().lower()
        if layer == "vector":
            vector_count += 1
        elif layer == "sql_kpi":
            sql_count += 1
        elif layer == "web":
            web_count += 1
    return {
        "vector": vector_count,
        "sql_kpi": sql_count,
        "web": web_count,
        "internal": vector_count + sql_count,
        "total": len(items),
    }


def _should_web_fallback_from_empty_darwin(
    intent: Dict[str, Any],
    route_mode: str,
    sources: List[str],
    evidence_pack: Dict[str, Any],
) -> bool:
    # Jamais pour smalltalk (déjà DIRECT)
    if route_mode == "DIRECT":
        return False

    # Déjà web
    if "web" in (sources or []):
        return False

    # On ne déclenche ce fallback QUE si la question est Darwin-specific
    # (ce que tu demandes: "si pas dans la doc Darwin -> web")
    if not bool(intent.get("is_darwin_specific")):
        return False

    counts = _count_internal_evidence(evidence_pack)
    return counts["internal"] == 0


def _prefix_deeper_search_notice(answer: str) -> str:
    # Phrase EXACTE demandée par toi
    prefix = "Aucune infos a ce suejt dans ma docuemtation je fais une recherche plus approfondie ?"
    cleaned = (answer or "").strip()
    if not cleaned:
        return prefix
    # Evite double insertion
    if prefix.lower() in cleaned.lower():
        return cleaned
    return f"{prefix}\n\n{cleaned}"


# --------------------------------------------------------------------------------------
# Claude synthesis
# --------------------------------------------------------------------------------------
def _call_claude_local(system_prompt: str, user_prompt: str) -> Tuple[str, Dict[str, Any]]:
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

    meta = {"provider_requested": "anthropic", "provider_effective": "anthropic", "model": model}

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

        parts: List[str] = []
        for block in (data.get("content") or []):
            if isinstance(block, dict) and _clean(block.get("text")):
                parts.append(_clean(block.get("text")))
        return "\n".join(parts).strip(), meta

    except Exception as exc:
        meta["provider_effective"] = "anthropic_error"
        meta["warning"] = str(exc)
        return "", meta


def _call_claude(system_prompt: str, user_prompt: str) -> Tuple[str, Dict[str, Any]]:
    if callable(_call_claude_patched):
        try:
            return _call_claude_patched(system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception as exc:  # pragma: no cover
            text, meta = _call_claude_local(system_prompt, user_prompt)
            meta["warning"] = f"patched_failed:{exc} | {meta.get('warning') or ''}".strip()
            return text, meta
    return _call_claude_local(system_prompt, user_prompt)


# --------------------------------------------------------------------------------------
# Orchestrate
# --------------------------------------------------------------------------------------
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
    start_time = time.time()
    _ = audit_detail, portfolio_simulation_input, scoring_version, session_state

    history = history or []
    question = _clean(question)
    neutral_mode = bool(neutral_pure)

    # 1) Intent
    intent = detect_intent(question=question, history=history)

    # 2) Sources plan (base) + router overrides
    base_plan = choose_sources(
        question=question,
        intent=intent,
        force_agent=force_agent,
        neutral_pure=neutral_mode,
    )
    source_plan, router_debug = _apply_router_overrides(
        question=question,
        intent=intent,
        source_plan=base_plan,
        force_agent=force_agent,
        neutral_mode=neutral_mode,
    )
    route_mode = _route_mode_from_sources(list(source_plan.get("sources") or []))

    # ✅ DIRECT = sans Claude (early return)
    if route_mode == "DIRECT":
        direct = _direct_answer(question, history)

        empty_evidence = {"items": [], "sources": [], "sources_by_layer": {}, "raw_count": 0}

        validated = validate_output(
            answer=direct,
            question=question,
            intent=intent,
            evidence_pack=empty_evidence,
        )
        final_answer = validated["answer"]
        warnings = list(validated.get("warnings") or [])

        structured_v2 = build_structured_payload(
            answer=final_answer,
            intent=intent,
            evidence_pack=empty_evidence,
            status=str(validated.get("status") or "ok"),
        )

        answer_json = {
            "format": "simple_v3",
            "intent": structured_v2.get("intent"),
            "answer": final_answer,
            "sources": structured_v2.get("sources_used"),
        }

        total_time = time.time() - start_time
        meta = {
            "darwin_version": "v3",
            "simple_mode": True,
            "route_mode": "DIRECT",
            "route_signals": {
                "smalltalk": _is_smalltalk(question),
                "web_signals": router_debug.get("web_signals") if isinstance(router_debug, dict) else [],
                "router_overrides": router_debug.get("overrides") if isinstance(router_debug, dict) else [],
            },
            "intent": intent.get("type"),
            "intent_raw": intent.get("type"),
            "intent_final": intent.get("type"),
            "intent_cgp": structured_v2.get("intent"),
            "kpi_target": structured_v2.get("kpi_target"),
            "agents_called": [],
            "agent_outputs_count": 0,
            "selected_agent": None,
            "selected_layer": None,
            "neutral_pure": neutral_mode,
            "live_web_signal": False,
            "response_format": "structured_contract_v3",
            "contract_format_enforced": True,
            "contract_rewrite_engine": "direct_deterministic",
            "finalizer_llm_provider_requested": "anthropic",
            "finalizer_llm_provider_effective": "none_direct",
            "finalizer_llm_model": None,
            "llm_warning": None,
            "anthropic_key_tail": _anthropic_key_tail(),
            "env_boot": ENV_BOOT_META,
            "source_plan": {"sources": [], "primary_source": None, "reasoning": ["direct_early_return"]},
            "retrieval_meta": {},
            "evidence_items_count": 0,
            "evidence_raw_count": 0,
            "followup_flow_active": False,
            "followup_phase": "idle",
            "effective_query_used": question,
            "session_state_patch": {},
            "response_time_seconds": round(total_time, 2),
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
            "used_facts": [],
            "warnings": warnings,
            "sources": [],
            "sources_by_layer": {},
            "agent_used": "darwin_v3_brain",
            "meta": meta,
        }

    # 3) Retrieval
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

    # 4) Evidence + prompts (premier passage)
    evidence_pack = build_evidence_pack(
        question=question,
        intent=intent,
        raw_material=raw_material,
        max_items=8,
    )

    # ✅ AJOUT: si doc Darwin vide -> on déclenche web_search + rebuild evidence_pack
    web_fallback_applied = False
    if _should_web_fallback_from_empty_darwin(intent=intent, route_mode=route_mode, sources=agents_called, evidence_pack=evidence_pack):
        web_payload = _retrieve_web(question, max_results=6)
        raw_material["web"] = web_payload
        retrieval_meta["web"] = dict(web_payload.get("meta", {}) or {})
        agents_called.append("web")
        web_fallback_applied = True

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

    # 5) Claude synthesis (or fallback)
    answer_raw, llm_meta = _call_claude(
        system_prompt=prompts.get("system_prompt", ""),
        user_prompt=prompts.get("user_prompt", ""),
    )

    deterministic_payload: Optional[Dict[str, Any]] = None
    if not _clean(answer_raw) and callable(synthesize_answer):
        try:
            material = prompts.get("user_prompt", "") or ""
            deterministic_payload = synthesize_answer(question=question, material=material, mode="compact")
            answer_raw = str(deterministic_payload.get("answer") or "").strip()
            llm_meta = dict(llm_meta or {})
            llm_meta["provider_effective"] = llm_meta.get("provider_effective") or "deterministic_fallback"
            llm_meta["warning"] = llm_meta.get("warning") or "empty_claude_answer_used_deterministic"
        except Exception as exc:  # pragma: no cover
            llm_meta = dict(llm_meta or {})
            llm_meta["warning"] = f"{llm_meta.get('warning') or ''} | deterministic_failed:{exc}".strip()

    # ✅ Ajout: préfixe si web fallback a été appliqué
    if web_fallback_applied:
        answer_raw = _prefix_deeper_search_notice(answer_raw)

    # 6) Validate + structured payload
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

    total_time = time.time() - start_time

    # Recalcule route_mode si fallback web a été ajouté
    final_route_mode = route_mode
    if web_fallback_applied and final_route_mode == "RAG":
        final_route_mode = "RAG+WEB"

    meta = {
        "darwin_version": "v3",
        "simple_mode": True,
        "route_mode": final_route_mode,
        "route_signals": {
            "smalltalk": _is_smalltalk(question),
            "web_signals": router_debug.get("web_signals") if isinstance(router_debug, dict) else [],
            "router_overrides": router_debug.get("overrides") if isinstance(router_debug, dict) else [],
            "web_fallback_from_empty_darwin": web_fallback_applied,
            "evidence_counts": _count_internal_evidence(evidence_pack),
        },
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
        "contract_rewrite_engine": "claude_single_brain" if _clean(answer_raw) else "deterministic_fallback",
        "finalizer_llm_provider_requested": (llm_meta or {}).get("provider_requested"),
        "finalizer_llm_provider_effective": (llm_meta or {}).get("provider_effective"),
        "finalizer_llm_model": (llm_meta or {}).get("model"),
        "llm_warning": (llm_meta or {}).get("warning"),
        "anthropic_key_tail": _anthropic_key_tail(),
        "env_boot": ENV_BOOT_META,
        "source_plan": source_plan,
        "retrieval_meta": retrieval_meta,
        "evidence_items_count": len(evidence_pack.get("items") or []),
        "evidence_raw_count": int(evidence_pack.get("raw_count") or 0),
        "followup_flow_active": False,
        "followup_phase": "idle",
        "effective_query_used": question,
        "session_state_patch": {},
        "response_time_seconds": round(total_time, 2),
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
        "details": final_answer if not deterministic_payload else str(deterministic_payload.get("details") or final_answer),
        "used_facts": [
            item.get("snippet")
            for item in (evidence_pack.get("items") or [])[:5]
            if _clean(item.get("snippet"))
        ],
        "warnings": warnings,
        "sources": evidence_pack.get("sources") or [],
        "sources_by_layer": evidence_pack.get("sources_by_layer") or {},
        "agent_used": "darwin_v3_brain",
        "meta": meta,
    }


if __name__ == "__main__":
    # IMPORTANT: lancer via module pour que les imports relatifs fonctionnent:
    #   python -m backend.core.orchestrator_v3
    tests = [
        "hello",
        "Quels sont les frais de Darwin RE01 ?",
        "taux BCE actuel 2026 ?",
        "Darwin RE01: frais de souscription ?",  # Darwin-specific
    ]
    for q in tests:
        res = orchestrate_v3(question=q, history=[])
        print("\n====================")
        print("Q:", q)
        print("route_mode:", res.get("meta", {}).get("route_mode"))
        print("agents_called:", res.get("meta", {}).get("agents_called"))
        print("web_fallback:", res.get("meta", {}).get("route_signals", {}).get("web_fallback_from_empty_darwin"))
        print("answer:", (res.get("answer") or "")[:200])