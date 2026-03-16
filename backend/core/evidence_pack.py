import re
from typing import Any, Dict, List
from urllib.parse import urlparse


def _clean(text: Any) -> str:
    return str(text or "").strip()


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    domain = (parsed.netloc or "").strip().lower()
    return domain.replace("www.", "")


def _keyword_hits(text: str, keywords: List[str]) -> int:
    haystack = _clean(text).lower()
    hits = 0
    for keyword in keywords:
        token = _clean(keyword).lower()
        if token and token in haystack:
            hits += 1
    return hits


def _base_weight(layer: str) -> float:
    if layer == "sql_kpi":
        return 1.0
    if layer == "vector":
        return 0.92
    if layer == "web":
        return 0.82
    return 0.7


def _score_item(item: Dict[str, Any], keywords: List[str]) -> float:
    score = _base_weight(str(item.get("layer") or ""))
    score += min(0.2, _keyword_hits(item.get("search_text", ""), keywords) * 0.03)
    if _clean(item.get("date")):
        score += 0.05
    priority = _clean(item.get("priority"))
    if priority == "1":
        score += 0.08
    elif priority == "2":
        score += 0.04
    return round(score, 4)


def _normalize_web_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for result in results or []:
        title = _clean(result.get("title"))
        snippet = _clean(result.get("body"))
        url = _clean(result.get("href"))
        domain = _domain_from_url(url)
        search_text = " ".join([title, snippet, domain]).strip()
        items.append(
            {
                "layer": "web",
                "title": title or domain or "Source web",
                "snippet": snippet or title,
                "url": url,
                "domain": domain or _clean(result.get("source_name")) or "web",
                "date": _clean(result.get("date")),
                "priority": _clean(result.get("priority")),
                "search_text": search_text,
                "source": {
                    "source": title or domain or "web",
                    "domain": domain or "web",
                    "date": _clean(result.get("date")),
                    "url": url,
                },
            }
        )
    return items


def _normalize_sql_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for result in results or []:
        metric = _clean(result.get("metric")) or "KPI"
        context = _clean(result.get("context"))
        value = _clean(result.get("value"))
        unit = _clean(result.get("unit"))
        source = _clean(result.get("source"))
        date = _clean(result.get("date"))
        title = metric if not context else f"{metric} ({context})"
        value_text = " ".join([value, unit]).strip() or "n/d"
        snippet = f"{title}: {value_text}"
        search_text = " ".join([title, snippet, source]).strip()
        items.append(
            {
                "layer": "sql_kpi",
                "title": title,
                "snippet": snippet,
                "url": "",
                "domain": source or "sql_kpi",
                "date": date,
                "priority": "",
                "metric": metric,
                "value": value_text,
                "search_text": search_text,
                "source": {
                    "source": source or "sql_kpi",
                    "domain": source or "sql_kpi",
                    "date": date,
                    "url": "",
                },
            }
        )
    return items


def _normalize_vector_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for result in results or []:
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        content = _clean(result.get("content"))
        title = (
            _clean(metadata.get("title"))
            or _clean(metadata.get("source"))
            or _clean(metadata.get("file_name"))
            or "Document Darwin"
        )
        snippet = re.sub(r"\s+", " ", content)[:320]
        source_label = (
            _clean(metadata.get("source"))
            or _clean(metadata.get("file_name"))
            or _clean(metadata.get("doc_id"))
            or "vectorstore"
        )
        search_text = " ".join([title, snippet, source_label]).strip()
        items.append(
            {
                "layer": "vector",
                "title": title,
                "snippet": snippet,
                "url": "",
                "domain": "darwin_docs",
                "date": _clean(metadata.get("date")) or _clean(metadata.get("updated_at")),
                "priority": "",
                "search_text": search_text,
                "source": {
                    "source": source_label,
                    "domain": "darwin_docs",
                    "date": _clean(metadata.get("date")) or _clean(metadata.get("updated_at")),
                    "url": "",
                },
            }
        )
    return items


def build_evidence_pack(
    question: str,
    intent: Dict[str, Any],
    raw_material: Dict[str, Dict[str, Any]],
    max_items: int = 8,
) -> Dict[str, Any]:
    _ = question
    keywords = intent.get("keywords") if isinstance(intent.get("keywords"), list) else []
    normalized_by_layer: Dict[str, List[Dict[str, Any]]] = {}
    all_items: List[Dict[str, Any]] = []

    for layer, payload in (raw_material or {}).items():
        items: List[Dict[str, Any]]
        if layer == "web":
            items = _normalize_web_results(payload.get("results") or [])
        elif layer == "sql_kpi":
            items = _normalize_sql_results(payload.get("results") or [])
        elif layer == "vector":
            items = _normalize_vector_results(payload.get("results") or [])
        else:
            items = []

        for item in items:
            item["score"] = _score_item(item, keywords)
        normalized_by_layer[layer] = items
        all_items.extend(items)

    ranked = sorted(all_items, key=lambda item: float(item.get("score") or 0), reverse=True)
    selected: List[Dict[str, Any]] = []
    seen = set()
    for item in ranked:
        unique_key = _clean(item.get("url")) or (item.get("title"), item.get("snippet"))
        if unique_key in seen:
            continue
        seen.add(unique_key)
        selected.append(item)
        if len(selected) >= max_items:
            break

    selected_sources = [item.get("source") for item in selected if isinstance(item.get("source"), dict)]
    sources_by_layer = {
        layer: [item.get("source") for item in items if isinstance(item.get("source"), dict)]
        for layer, items in normalized_by_layer.items()
    }

    return {
        "items": selected,
        "sources": selected_sources,
        "sources_by_layer": sources_by_layer,
        "raw_count": len(all_items),
    }
