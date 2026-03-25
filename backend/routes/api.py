# backend/routes/api.py
"""
API Flask pour le système multi-agent Darwin
Point d'entrée principal pour toutes les requêtes du frontend
"""

import sys
import os
import traceback
import inspect
from typing import List, Dict, Optional

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION DU PATH
# ════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, request, jsonify
from flask import send_from_directory
from flask_cors import CORS

# Import du routeur qui gère la logique multi-agent
from backend.routes.router import ask_router
from backend.memory.memory_store import (
    get_history,
    append_turn,
    append_ask_log,
    get_ask_log,
    list_ask_logs,
    get_session_state,
    set_session_state,
    clear_session_state,
)

# ✅ DEBUG BOOT: prouve quel module est réellement importé par Flask
try:
    from backend.core.source_router import choose_sources, route_sources

    print("[BOOT] python =", sys.executable)
    print("[BOOT] cwd    =", os.getcwd())
    print("[BOOT] source_router file =", inspect.getsourcefile(choose_sources))
    print("[BOOT] choose_sources is route_sources =", choose_sources is route_sources)
    print("[BOOT] choose_sources signature =", inspect.signature(choose_sources))
    print("[BOOT] route_sources signature  =", inspect.signature(route_sources))
except Exception:
    print("[BOOT] unable to introspect source_router")
    print(traceback.format_exc())

# ════════════════════════════════════════════════════════════════════════════
# INITIALISATION DE L'APPLICATION
# ════════════════════════════════════════════════════════════════════════════
app = Flask(__name__, static_folder=None)
CORS(app)

MAX_MEMORY_MESSAGES = int(os.getenv("MAX_MEMORY_MESSAGES", "20"))
MAX_REPLAY_LOGS_PER_SESSION = int(os.getenv("MAX_REPLAY_LOGS_PER_SESSION", "500"))
FRONTEND_BUILD_DIR = os.path.join(PROJECT_ROOT, "frontend", "build")
DARWIN_DEFAULT_VERSION = str(os.getenv("DARWIN_DEFAULT_VERSION", "v3")).strip().lower() or "v3"
SUPPORTED_DARWIN_VERSIONS = {"v1", "v2", "v3"}


def _normalize_history(history: List[dict]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for msg in history or []:
        role = str(msg.get("role", "")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if role in {"user", "assistant", "system"} and content:
            normalized.append({"role": role, "content": content})
    return normalized


def _merge_history(
    persisted: List[Dict[str, str]],
    incoming: List[Dict[str, str]],
    max_messages: int,
) -> List[Dict[str, str]]:
    if not incoming:
        return (persisted or [])[-max_messages:]

    merged = (persisted or []) + incoming
    deduped: List[Dict[str, str]] = []
    for msg in merged:
        if deduped and deduped[-1] == msg:
            continue
        deduped.append(msg)
    return deduped[-max_messages:]


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "oui"}:
        return True
    if text in {"0", "false", "no", "off", "non"}:
        return False
    return default


def _extract_portfolio_simulation(data: Dict[str, object]) -> Optional[Dict[str, object]]:
    if not isinstance(data, dict):
        return None

    payload = data.get("portfolio_simulation")
    if isinstance(payload, dict):
        return payload

    alt = data.get("simulation")
    if isinstance(alt, dict):
        return alt

    keys = [
        "amount",
        "montant",
        "amount_eur",
        "montant_eur",
        "fiscality",
        "fiscalite",
        "tax_regime",
        "horizon",
        "horizon_years",
    ]
    compact = {k: data.get(k) for k in keys if data.get(k) not in (None, "")}
    return compact or None


def _merge_session_state(current: Dict[str, object], patch: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(current, dict):
        current = {}
    if not isinstance(patch, dict):
        return dict(current)

    out: Dict[str, object] = dict(current)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_session_state(out.get(key, {}), value)  # type: ignore[arg-type]
            continue
        out[key] = value
    return out


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}

    question = str(data.get("question", "") or "").strip()
    force_agent = data.get("force_agent")
    scoring_version = str(data.get("scoring_version", "")).strip() or None
    darwin_version_raw = str(data.get("darwin_version", DARWIN_DEFAULT_VERSION)).strip().lower()
    darwin_version = darwin_version_raw if darwin_version_raw in SUPPORTED_DARWIN_VERSIONS else DARWIN_DEFAULT_VERSION

    neutral_pure = _as_bool(data.get("neutral_pure"), default=False)
    audit_detail = _as_bool(data.get("audit_detail"), default=False)
    portfolio_simulation = _extract_portfolio_simulation(data)
    session_id = str(data.get("session_id", "default") or "default")

    incoming_history = _normalize_history(data.get("history") or [])
    persisted_history = get_history(session_id, max_messages=MAX_MEMORY_MESSAGES)
    history = _merge_history(persisted_history, incoming_history, MAX_MEMORY_MESSAGES)
    session_state = get_session_state(session_id)

    if not question:
        return jsonify({"error": "❌ Question manquante ou vide"}), 400

    try:
        response = ask_router(
            question=question,
            history=history,
            force_agent=force_agent,
            neutral_pure=neutral_pure,
            audit_detail=audit_detail,
            portfolio_simulation=portfolio_simulation,
            scoring_version=scoring_version,
            session_state=session_state,
            darwin_version=darwin_version,
        )

        response_meta = dict(response.get("meta", {}) or {})
        session_state_patch = response_meta.get("session_state_patch")
        if isinstance(session_state_patch, dict) and session_state_patch:
            if bool(session_state_patch.get("_clear_state")):
                clear_session_state(session_id)
                session_state = {}
            else:
                merged_state = _merge_session_state(session_state, session_state_patch)
                set_session_state(session_id, merged_state)
                session_state = merged_state

        replay_id = None
        replay_created_at = None
        replay_log_error = None
        try:
            replay_entry = append_ask_log(
                session_id=session_id,
                question=question,
                intent=str(response_meta.get("intent") or "").strip() or None,
                sources=response.get("sources", []),
                sources_by_layer=response.get("sources_by_layer", {}),
                scoring=response_meta.get("deterministic_scoring", {}),
                answer=response.get("answer", ""),
                meta=response_meta,
                max_logs=MAX_REPLAY_LOGS_PER_SESSION,
            )
            replay_id = replay_entry.get("replay_id")
            replay_created_at = replay_entry.get("created_at")
        except Exception as replay_exc:
            replay_log_error = str(replay_exc)
            print(f"⚠️ Replay log error: {replay_log_error}")

        append_turn(
            session_id=session_id,
            user_content=question,
            assistant_content=response.get("answer", ""),
            max_messages=MAX_MEMORY_MESSAGES,
        )

        structured_answer = response.get("answer_json")
        if not isinstance(structured_answer, dict):
            structured_answer = response.get("answer_structured")
        structured_answer_v2 = response.get("answer_structured_v2")
        if not isinstance(structured_answer_v2, dict):
            structured_answer_v2 = None

        result_contract = response.get("answer_contract")
        if not isinstance(result_contract, dict):
            result_contract = {}
        result_contract = {
            "intent": str(result_contract.get("intent") or response_meta.get("intent_cgp") or "KPI"),
            "kpi_target": str(result_contract.get("kpi_target") or response_meta.get("kpi_target") or "none"),
        }

        result_text = response.get("answer_text") or response.get("answer", "")
        if not isinstance(result_text, str):
            result_text = str(result_text or "")

        if isinstance(structured_answer_v2, dict):
            rendered_v2 = str(structured_answer_v2.get("rendered_text") or "").strip()
            if rendered_v2:
                result_text = rendered_v2

        response_meta["intent_cgp"] = str(response_meta.get("intent_cgp") or result_contract.get("intent") or "KPI")
        response_meta["kpi_target"] = str(response_meta.get("kpi_target") or result_contract.get("kpi_target") or "none")
        response_meta["contract_format_enforced"] = bool(structured_answer_v2)
        response_meta["darwin_version"] = str(response_meta.get("darwin_version") or darwin_version)

        def _safe_int(value, default: int = 0) -> int:
            try:
                return int(str(value).strip())
            except Exception:
                return int(default)

        response_meta["top_items_received_count"] = _safe_int(response_meta.get("top_items_received_count"), 0)
        response_meta["top_items_valid_count"] = _safe_int(response_meta.get("top_items_valid_count"), 0)
        response_meta["top_items_rejected_count"] = _safe_int(response_meta.get("top_items_rejected_count"), 0)
        response_meta["top_name_sanitizer_applied"] = bool(response_meta.get("top_name_sanitizer_applied", False))
        top_mode = str(response_meta.get("top_name_resolution_mode") or "deterministic_fallback")
        if top_mode not in {"llm_raw", "resolved_from_source", "deterministic_fallback"}:
            top_mode = "deterministic_fallback"
        response_meta["top_name_resolution_mode"] = top_mode

        return jsonify(
            {
                "result": result_text,
                "result_structured": structured_answer if isinstance(structured_answer, dict) else None,
                "result_structured_v2": structured_answer_v2,
                "result_contract": result_contract,
                "result_text": result_text,
                "sources": response.get("sources", []),
                "sources_by_layer": response.get("sources_by_layer", {}),
                "agent_used": response.get("agent_used"),
                "replay_id": replay_id,
                "replay": {"id": replay_id, "created_at": replay_created_at},
                "meta": {
                    **response_meta,
                    "followup_flow_active": bool(response_meta.get("followup_flow_active")),
                    "followup_phase": response_meta.get("followup_phase"),
                    "effective_query_used": response_meta.get("effective_query_used"),
                    "request_flags": {
                        "darwin_version": darwin_version,
                        "neutral_pure": neutral_pure,
                        "audit_detail": audit_detail,
                        "portfolio_simulation": bool(portfolio_simulation),
                        "scoring_version": scoring_version,
                    },
                    "memory": {
                        "session_id": session_id,
                        "history_messages_used": len(history),
                        "max_memory_messages": MAX_MEMORY_MESSAGES,
                        "session_state": session_state,
                    },
                    "replay": {"id": replay_id, "created_at": replay_created_at, "error": replay_log_error},
                },
                "session_id": session_id,
            }
        )

    except Exception as e:
        # ✅ Log complet du traceback (au lieu de seulement str(e))
        print("❌ ERREUR /ask:", str(e))
        print(traceback.format_exc())
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500


@app.route("/test", methods=["GET"])
def test():
    return jsonify({"message": "✅ API Darwin Agent en ligne", "status": "ok"})


@app.route("/replay/<replay_id>", methods=["GET"])
def replay_get(replay_id: str):
    safe_replay_id = (replay_id or "").strip()
    if not safe_replay_id:
        return jsonify({"error": "❌ replay_id manquant"}), 400

    item = get_ask_log(safe_replay_id)
    if not item:
        return jsonify({"error": "❌ replay introuvable", "replay_id": safe_replay_id}), 404
    return jsonify({"replay": item, "status": "ok"})


@app.route("/replay", methods=["GET"])
def replay_list():
    session_id = str(request.args.get("session_id", "default")).strip() or "default"
    limit_raw = request.args.get("limit", "20")
    try:
        limit = int(limit_raw)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))

    items = list_ask_logs(session_id=session_id, limit=limit)
    return jsonify({"session_id": session_id, "count": len(items), "replays": items, "status": "ok"})


@app.route("/", defaults={"path": ""}, methods=["GET"])
@app.route("/<path:path>", methods=["GET"])
def serve_frontend(path: str):
    if not os.path.isdir(FRONTEND_BUILD_DIR):
        return (
            jsonify(
                {
                    "error": "Frontend build introuvable.",
                    "hint": "Exécute `cd frontend && npm run build` puis recharge cette URL.",
                    "api_test": "GET /test",
                    "ask_endpoint": "POST /ask",
                }
            ),
            404,
        )

    requested = os.path.join(FRONTEND_BUILD_DIR, path)
    if path and os.path.exists(requested):
        return send_from_directory(FRONTEND_BUILD_DIR, path)
    return send_from_directory(FRONTEND_BUILD_DIR, "index.html")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🚀 DÉMARRAGE DU SERVEUR DARWIN AGENT")
    print("=" * 60)
    print("📍 URL principale : http://localhost:5050")
    print("📬 Endpoint /ask  : POST http://localhost:5050/ask")
    print("🧪 Endpoint /test : GET  http://localhost:5050/test")
    print("=" * 60 + "\n")

    # ✅ IMPORTANT: désactive le reloader pour éviter de garder un état/imports incohérents
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5050")), debug=False, use_reloader=False)
    