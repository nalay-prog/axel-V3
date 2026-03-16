import os
import re
import sqlite3
from typing import Any, Dict, List, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KPI_SQL_DB_PATH = os.getenv(
    "KPI_SQL_DB_PATH",
    os.path.join(PROJECT_ROOT, "backend", "data", "kpi_store.sqlite3"),
)
KPI_SQL_TABLE = os.getenv("KPI_SQL_TABLE", "kpi_values")
PROVENANCE_FALLBACK = "non_renseigne"
KPI_PROVENANCE_REQUIRED_FIELDS = ["source", "date", "doc_id", "extraction_method"]


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _keywords(question: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z0-9_]+", (question or "").lower())
    blacklist = {"le", "la", "les", "de", "du", "des", "et", "ou", "un", "une", "sur", "pour", "dans", "avec"}
    out: List[str] = []
    for token in tokens:
        if len(token) <= 2 or token in blacklist:
            continue
        if token not in out:
            out.append(token)
    return out[:8]


def _table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(r[1]) for r in rows] if rows else []


def _first_non_empty(row: Dict[str, Any], candidates: List[str]) -> str:
    for key in candidates:
        value = _clean(str(row.get(key, "")))
        if value:
            return value
    return ""


def _build_kpi_provenance(row: Dict[str, Any]) -> Dict[str, Any]:
    source = _first_non_empty(row, ["source", "source_url", "url", "origin", "provider"])
    date = _first_non_empty(row, ["as_of", "date", "kpi_date", "updated_at", "published_at", "created_at"])
    doc_id = _first_non_empty(row, ["doc_id", "document_id", "source_id", "reference_id", "id"])
    extraction_method = _first_non_empty(
        row,
        ["extraction_method", "method", "extraction", "ingestion_method", "parser"],
    )

    normalized = {
        "source": source or PROVENANCE_FALLBACK,
        "date": date or PROVENANCE_FALLBACK,
        "doc_id": doc_id or PROVENANCE_FALLBACK,
        "extraction_method": extraction_method or PROVENANCE_FALLBACK,
    }
    normalized["methode_extraction"] = normalized["extraction_method"]
    normalized["is_complete"] = all(normalized[k] != PROVENANCE_FALLBACK for k in KPI_PROVENANCE_REQUIRED_FIELDS)
    return normalized


def _build_query(columns: List[str], terms: List[str], limit: int) -> tuple[str, List[Any]]:
    order_col = "updated_at" if "updated_at" in columns else ("as_of" if "as_of" in columns else None)
    where_cols = [c for c in ("metric", "context", "source") if c in columns]

    params: List[Any] = []
    where_clause = ""
    if terms and where_cols:
        clauses: List[str] = []
        for term in terms:
            sub = []
            for col in where_cols:
                sub.append(f"LOWER({col}) LIKE ?")
                params.append(f"%{term.lower()}%")
            clauses.append("(" + " OR ".join(sub) + ")")
        where_clause = "WHERE " + " OR ".join(clauses)

    order_clause = f"ORDER BY {order_col} DESC" if order_col else ""
    sql = f"SELECT * FROM {KPI_SQL_TABLE} {where_clause} {order_clause} LIMIT ?"
    params.append(max(1, limit))
    return sql.strip(), params


def _format_row(row: Dict[str, Any]) -> str:
    metric = _clean(str(row.get("metric", "KPI")))
    value = _clean(str(row.get("value", "")))
    unit = _clean(str(row.get("unit", "")))
    provenance = _build_kpi_provenance(row)

    value_block = f"{value} {unit}".strip() if value else "n/d"
    meta_parts = []
    meta_parts.append(f"date: {provenance['date']}")
    meta_parts.append(f"source: {provenance['source']}")
    meta_parts.append(f"doc_id: {provenance['doc_id']}")
    meta_parts.append(f"methode: {provenance['methode_extraction']}")
    meta_txt = f" ({', '.join(meta_parts)})" if meta_parts else ""
    return f"- {metric}: {value_block}{meta_txt}"


def ask_agent(
    question: str,
    history: Optional[List[dict]] = None,
    max_rows: int = 8,
) -> Dict[str, Any]:
    del history  # Couche KPI SQL: lecture structurée uniquement.

    if not os.path.exists(KPI_SQL_DB_PATH):
        return {
            "draft": "Aucune base SQL KPI disponible pour le moment.",
            "sources": [],
            "meta": {
                "tool": "sql_kpi",
                "knowledge_layer": "sql_kpi",
                "db_path": KPI_SQL_DB_PATH,
                "table": KPI_SQL_TABLE,
                "rows_count": 0,
                "warning": "kpi_db_missing",
            },
        }

    try:
        with sqlite3.connect(KPI_SQL_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            columns = _table_columns(conn, KPI_SQL_TABLE)
            if not columns:
                return {
                    "draft": "Base SQL KPI indisponible: table KPI non trouvée.",
                    "sources": [],
                    "meta": {
                        "tool": "sql_kpi",
                        "knowledge_layer": "sql_kpi",
                        "db_path": KPI_SQL_DB_PATH,
                        "table": KPI_SQL_TABLE,
                        "rows_count": 0,
                        "warning": "kpi_table_missing",
                    },
                }

            terms = _keywords(question)
            query, params = _build_query(columns=columns, terms=terms, limit=max_rows)
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]

        if not rows:
            return {
                "draft": "Aucune donnée KPI SQL pertinente trouvée pour cette question.",
                "sources": [],
                "meta": {
                    "tool": "sql_kpi",
                    "knowledge_layer": "sql_kpi",
                    "db_path": KPI_SQL_DB_PATH,
                    "table": KPI_SQL_TABLE,
                    "rows_count": 0,
                    "terms_used": terms,
                    "warning": "kpi_no_match",
                },
            }

        lines = [_format_row(r) for r in rows[:max_rows]]
        draft = "KPI SQL disponibles:\n" + "\n".join(lines)
        sources: List[Dict[str, Any]] = []
        compliance_flags: List[bool] = []
        for r in rows[:max_rows]:
            provenance = _build_kpi_provenance(r)
            compliance_flags.append(bool(provenance["is_complete"]))
            sources.append(
                {
                    "type": "sql_kpi",
                    "table": KPI_SQL_TABLE,
                    "metric": r.get("metric"),
                    "context": r.get("context"),
                    "value": r.get("value"),
                    "unit": r.get("unit"),
                    "source": provenance["source"],
                    "date": provenance["date"],
                    "doc_id": provenance["doc_id"],
                    "extraction_method": provenance["extraction_method"],
                    "methode_extraction": provenance["methode_extraction"],
                    "provenance": {
                        "source": provenance["source"],
                        "date": provenance["date"],
                        "doc_id": provenance["doc_id"],
                        "extraction_method": provenance["extraction_method"],
                        "methode_extraction": provenance["methode_extraction"],
                        "is_complete": provenance["is_complete"],
                    },
                }
            )
        total = len(compliance_flags)
        complete = sum(1 for ok in compliance_flags if ok)
        compliance_rate = round((complete / total) * 100.0, 2) if total else 0.0

        return {
            "draft": draft,
            "sources": sources,
            "meta": {
                "tool": "sql_kpi",
                "knowledge_layer": "sql_kpi",
                "db_path": KPI_SQL_DB_PATH,
                "table": KPI_SQL_TABLE,
                "rows_count": len(rows[:max_rows]),
                "terms_used": terms,
                "kpi_provenance_required_fields": KPI_PROVENANCE_REQUIRED_FIELDS,
                "kpi_provenance_fallback": PROVENANCE_FALLBACK,
                "kpi_provenance_complete_count": complete,
                "kpi_provenance_total_count": total,
                "kpi_provenance_compliance_rate": compliance_rate,
            },
        }
    except Exception as exc:
        return {
            "draft": f"Erreur SQL KPI: {str(exc)}",
            "sources": [],
            "meta": {
                "tool": "sql_kpi",
                "knowledge_layer": "sql_kpi",
                "db_path": KPI_SQL_DB_PATH,
                "table": KPI_SQL_TABLE,
                "rows_count": 0,
                "error": str(exc),
            },
        }
