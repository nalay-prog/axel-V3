import os
import sqlite3
import json
import uuid
from threading import Lock
from typing import Any, Dict, List, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "backend", "memory", "memory_store.sqlite3")
DB_PATH = os.getenv("MEMORY_DB_PATH", DEFAULT_DB_PATH)

_DB_LOCK = Lock()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_parent_dir(DB_PATH)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_store() -> None:
    with _DB_LOCK:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session_id_id ON messages(session_id, id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ask_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    replay_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    intent TEXT,
                    sources_json TEXT NOT NULL,
                    sources_by_layer_json TEXT NOT NULL,
                    scoring_json TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ask_logs_replay_id ON ask_logs(replay_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ask_logs_session_id_id ON ask_logs(session_id, id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_state (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({}, ensure_ascii=False)


def _json_loads(payload: str) -> Any:
    text = (payload or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def get_history(session_id: str, max_messages: int = 20) -> List[dict]:
    if not session_id:
        return []

    with _DB_LOCK:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max_messages),
            ).fetchall()

    # rows are newest-first; return chronological order.
    rows = list(reversed(rows))
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def append_message(
    session_id: str,
    role: str,
    content: str,
    max_messages: int = 20,
) -> None:
    if not session_id or not role:
        return

    safe_content = (content or "").strip()
    if not safe_content:
        return

    init_store()

    with _DB_LOCK:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO messages(session_id, role, content)
                VALUES (?, ?, ?)
                """,
                (session_id, role, safe_content),
            )

            # Keep only the latest max_messages entries for the session.
            conn.execute(
                """
                DELETE FROM messages
                WHERE session_id = ?
                  AND id NOT IN (
                    SELECT id
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (session_id, session_id, max_messages),
            )
            conn.commit()


def append_turn(
    session_id: str,
    user_content: str,
    assistant_content: str,
    max_messages: int = 20,
) -> None:
    append_message(
        session_id=session_id,
        role="user",
        content=user_content,
        max_messages=max_messages,
    )
    append_message(
        session_id=session_id,
        role="assistant",
        content=assistant_content,
        max_messages=max_messages,
    )


def clear_history(session_id: str) -> None:
    if not session_id:
        return
    init_store()
    with _DB_LOCK:
        with _connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_state WHERE session_id = ?", (session_id,))
            conn.commit()


def get_session_state(session_id: str) -> Dict[str, Any]:
    safe_session = (session_id or "").strip()
    if not safe_session:
        return {}
    init_store()
    with _DB_LOCK:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT state_json
                FROM session_state
                WHERE session_id = ?
                LIMIT 1
                """,
                (safe_session,),
            ).fetchone()
    if not row:
        return {}
    data = _json_loads(row["state_json"])
    return data if isinstance(data, dict) else {}


def set_session_state(session_id: str, state: Dict[str, Any]) -> None:
    safe_session = (session_id or "").strip()
    if not safe_session:
        return
    payload = state if isinstance(state, dict) else {}
    init_store()
    with _DB_LOCK:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO session_state(session_id, state_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (safe_session, _json_dumps(payload)),
            )
            conn.commit()


def clear_session_state(session_id: str) -> None:
    safe_session = (session_id or "").strip()
    if not safe_session:
        return
    init_store()
    with _DB_LOCK:
        with _connect() as conn:
            conn.execute("DELETE FROM session_state WHERE session_id = ?", (safe_session,))
            conn.commit()


def append_ask_log(
    session_id: str,
    question: str,
    intent: Optional[str],
    sources: Any,
    sources_by_layer: Any,
    scoring: Any,
    answer: str,
    meta: Any,
    max_logs: int = 500,
) -> Dict[str, Optional[str]]:
    safe_session = (session_id or "default").strip() or "default"
    safe_question = (question or "").strip()
    safe_answer = (answer or "").strip()
    if not safe_question or not safe_answer:
        return {"replay_id": None, "created_at": None}

    init_store()
    replay_id = uuid.uuid4().hex

    with _DB_LOCK:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO ask_logs(
                    replay_id,
                    session_id,
                    question,
                    intent,
                    sources_json,
                    sources_by_layer_json,
                    scoring_json,
                    answer,
                    meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    replay_id,
                    safe_session,
                    safe_question,
                    (intent or "").strip() or None,
                    _json_dumps(sources if sources is not None else []),
                    _json_dumps(sources_by_layer if sources_by_layer is not None else {}),
                    _json_dumps(scoring if scoring is not None else {}),
                    safe_answer,
                    _json_dumps(meta if meta is not None else {}),
                ),
            )

            if max_logs > 0:
                conn.execute(
                    """
                    DELETE FROM ask_logs
                    WHERE session_id = ?
                      AND id NOT IN (
                        SELECT id
                        FROM ask_logs
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                      )
                    """,
                    (safe_session, safe_session, max_logs),
                )

            row = conn.execute(
                """
                SELECT replay_id, created_at
                FROM ask_logs
                WHERE replay_id = ?
                """,
                (replay_id,),
            ).fetchone()
            conn.commit()

    if not row:
        return {"replay_id": replay_id, "created_at": None}
    return {
        "replay_id": row["replay_id"],
        "created_at": row["created_at"],
    }


def get_ask_log(replay_id: str) -> Optional[Dict[str, Any]]:
    safe_replay_id = (replay_id or "").strip()
    if not safe_replay_id:
        return None
    init_store()
    with _DB_LOCK:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT replay_id, session_id, question, intent,
                       sources_json, sources_by_layer_json, scoring_json,
                       answer, meta_json, created_at
                FROM ask_logs
                WHERE replay_id = ?
                LIMIT 1
                """,
                (safe_replay_id,),
            ).fetchone()
    if not row:
        return None
    return {
        "replay_id": row["replay_id"],
        "session_id": row["session_id"],
        "question": row["question"],
        "intent": row["intent"],
        "sources": _json_loads(row["sources_json"]),
        "sources_by_layer": _json_loads(row["sources_by_layer_json"]),
        "scoring_used": _json_loads(row["scoring_json"]),
        "answer": row["answer"],
        "meta": _json_loads(row["meta_json"]),
        "created_at": row["created_at"],
    }


def list_ask_logs(session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    safe_session = (session_id or "").strip() or "default"
    safe_limit = max(1, min(int(limit), 100))
    init_store()
    with _DB_LOCK:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT replay_id, session_id, question, intent,
                       sources_json, sources_by_layer_json, scoring_json,
                       answer, meta_json, created_at
                FROM ask_logs
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_session, safe_limit),
            ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "replay_id": row["replay_id"],
                "session_id": row["session_id"],
                "question": row["question"],
                "intent": row["intent"],
                "sources": _json_loads(row["sources_json"]),
                "sources_by_layer": _json_loads(row["sources_by_layer_json"]),
                "scoring_used": _json_loads(row["scoring_json"]),
                "answer": row["answer"],
                "meta": _json_loads(row["meta_json"]),
                "created_at": row["created_at"],
            }
        )
    return out


# Initialize once on module import.
init_store()
