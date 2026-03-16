# backend/agents/agent_core.py
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False


load_dotenv()

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCS_PATH = os.getenv("DARWIN_DOCS_PATH", os.path.join(PROJECT_ROOT, "data", "docs"))
CORE_MODEL = "raw_local_retriever"

CORE_RUNTIME_ERROR: Optional[str] = None
db = None
llm = None
CORE_PROMPT = None


def _normalize(text: str) -> str:
    value = (text or "").lower().strip()
    value = value.replace("é", "e").replace("è", "e").replace("ê", "e")
    value = value.replace("à", "a").replace("â", "a").replace("î", "i")
    value = value.replace("ô", "o").replace("ù", "u").replace("û", "u")
    value = value.replace("ç", "c")
    return re.sub(r"\s+", " ", value)


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", _normalize(text))
    blacklist = {
        "les",
        "des",
        "une",
        "pour",
        "avec",
        "dans",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "sur",
    }
    out: List[str] = []
    for token in tokens:
        if token in blacklist:
            continue
        if token not in out:
            out.append(token)
    return out


def _split_text(text: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
    content = (text or "").strip()
    if not content:
        return []

    chunks: List[str] = []
    start = 0
    while start < len(content):
        end = min(len(content), start + chunk_size)
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(content):
            break
        start = max(end - overlap, start + 1)
    return chunks


@lru_cache(maxsize=1)
def _load_documents() -> List[Dict[str, str]]:
    if not os.path.isdir(DOCS_PATH):
        return []

    documents: List[Dict[str, str]] = []
    for filename in sorted(os.listdir(DOCS_PATH)):
        if filename.startswith("."):
            continue

        path = os.path.join(DOCS_PATH, filename)
        if not os.path.isfile(path):
            continue

        lower_name = filename.lower()
        if lower_name.endswith(".txt") or lower_name.endswith(".md"):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    content = handle.read().strip()
            except Exception:
                continue
            if not content:
                continue
            documents.append(
                {
                    "title": filename,
                    "source": path,
                    "content": content,
                }
            )
            continue

        # Conserve un repère minimum sur les docs non texte, sans dépendance externe.
        if lower_name.endswith(".pdf"):
            documents.append(
                {
                    "title": filename,
                    "source": path,
                    "content": f"Document PDF Darwin disponible: {filename}",
                }
            )

    return documents


@lru_cache(maxsize=1)
def _build_chunks() -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for document in _load_documents():
        for index, chunk in enumerate(_split_text(document["content"])):
            chunks.append(
                {
                    "title": document["title"],
                    "source": document["source"],
                    "content": chunk,
                    "chunk_index": index,
                    "search_text": _normalize(" ".join([document["title"], chunk])),
                }
            )
    return chunks


def _build_query(question: str, history: Optional[List[dict]]) -> str:
    if not history:
        return (question or "").strip()

    lines: List[str] = []
    for msg in history[-3:]:
        role = str(msg.get("role", "")).strip().upper() or "USER"
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append(f"QUESTION: {(question or '').strip()}")
    return "\n".join(lines).strip()


def retrieve_raw(
    question: str,
    history: Optional[List[dict]] = None,
    k: int = 4,
) -> Dict[str, Any]:
    if CORE_RUNTIME_ERROR:
        return {
            "results": [],
            "meta": {
                "tool": "core_raw",
                "knowledge_layer": "rag_darwin",
                "warning": "core_unavailable",
                "error": CORE_RUNTIME_ERROR,
            },
        }

    chunks = _build_chunks()
    if not chunks:
        return {
            "results": [],
            "meta": {
                "tool": "core_raw",
                "knowledge_layer": "rag_darwin",
                "warning": "core_empty",
            },
        }

    query_text = _build_query(question=question, history=history)
    tokens = _tokenize(query_text)
    scored: List[Dict[str, Any]] = []

    for item in chunks:
        search_text = str(item.get("search_text") or "")
        hits = 0
        for token in tokens:
            if token in search_text:
                hits += 1
        if hits <= 0:
            continue
        title_bonus = 1 if any(token in _normalize(str(item.get("title") or "")) for token in tokens) else 0
        score = (hits * 10) + title_bonus - (int(item.get("chunk_index") or 0) * 0.01)
        scored.append(
            {
                "score": score,
                "content": item["content"],
                "metadata": {
                    "title": item["title"],
                    "source": item["source"],
                    "chunk_index": item["chunk_index"],
                },
            }
        )

    ranked = sorted(scored, key=lambda row: float(row.get("score") or 0), reverse=True)
    results = ranked[: max(1, k)]

    return {
        "results": [
            {
                "content": str(row.get("content") or ""),
                "metadata": dict(row.get("metadata", {}) or {}),
            }
            for row in results
        ],
        "meta": {
            "tool": "core_raw",
            "knowledge_layer": "rag_darwin",
            "mode": "raw_local_retriever",
            "rows_count": len(results),
            "documents_loaded": len(_load_documents()),
        },
    }


def ask_agent(question: str, history: Optional[List[dict]] = None, k: int = 4) -> Dict:
    """
    Couche Darwin recentrée: récupération brute locale + résumé déterministe.
    """
    retrieval = retrieve_raw(question=question, history=history, k=k)
    results = retrieval.get("results") if isinstance(retrieval.get("results"), list) else []
    meta = dict(retrieval.get("meta", {}) or {})

    if not results:
        return {
            "draft": "Aucun extrait documentaire Darwin pertinent trouvé.",
            "sources": [],
            "meta": {
                **meta,
                "tool": "core",
                "warning": meta.get("warning") or "core_no_match",
                "model": CORE_MODEL,
            },
        }

    lines: List[str] = ["Extraits Darwin disponibles :"]
    sources: List[Dict[str, Any]] = []
    for item in results[:k]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        title = str(metadata.get("title") or "Document Darwin")
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        snippet = content[:260] + ("..." if len(content) > 260 else "")
        lines.append(f"- {title}: {snippet}")
        sources.append(
            {
                "content": str(item.get("content") or "")[:300],
                "metadata": metadata,
            }
        )

    return {
        "draft": "\n".join(lines),
        "sources": sources,
        "meta": {
            **meta,
            "tool": "core",
            "knowledge_layer": "rag_darwin",
            "k": k,
            "nb_sources": len(sources),
            "model": CORE_MODEL,
        },
    }


if not _load_documents():
    CORE_RUNTIME_ERROR = f"Aucun document Darwin texte exploitable dans {DOCS_PATH}"


if __name__ == "__main__":
    result = ask_agent(
        question="Quels sont les frais de Darwin RE01 ?",
        history=[
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Bonjour, je suis Darwin !"},
        ],
        k=5,
    )

    print("📄 DRAFT:", result["draft"][:200])
    print("📚 SOURCES:", len(result["sources"]), "documents")
    print("🔍 META:", result["meta"])
