import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _is_placeholder_key(value: Optional[str]) -> bool:
    token = (value or "").strip().lower()
    if not token:
        return True
    placeholders = {"...", "xxx", "your_key", "your-api-key", "changeme", "replace_me"}
    if token in placeholders:
        return True
    return token.startswith("your_") or token.startswith("sk-...")


def _first_non_placeholder_from_dotenv(key: str) -> Optional[str]:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() != key:
                continue
            candidate = value.strip().strip("'").strip('"')
            if not _is_placeholder_key(candidate):
                return candidate
    except Exception:
        return None
    return None


def _read_env_prefer_non_placeholder(key: str) -> Optional[str]:
    direct = os.getenv(key)
    if not _is_placeholder_key(direct):
        return direct
    return _first_non_placeholder_from_dotenv(key) or direct


@dataclass
class SearchResult:
    """Search result enriched with source-priority metadata."""

    url: str
    title: str
    snippet: str
    source_name: str
    priority: int  # 1=max, 3=min
    date: Optional[str] = None
    data_type: Optional[str] = None  # "tdvm", "price", "news", "analysis"


class PrioritizedWebSearch:
    """Web search with SCPI-oriented source prioritization."""

    def __init__(self) -> None:
        self.serper_api_key = _read_env_prefer_non_placeholder("SERPER_API_KEY")
        self.serpapi_api_key = _read_env_prefer_non_placeholder("SERPAPI_API_KEY")
        self.provider = (os.getenv("PRIORITIZED_WEB_PROVIDER", "auto") or "auto").strip().lower()
        self.max_queries = max(3, int(os.getenv("PRIORITIZED_WEB_MAX_QUERIES", "12")))

        # Priority 1 = preferred sources requested by user.
        self.priority_sources: Dict[int, Dict[str, Dict[str, Any]]] = {
            1: {
                "pierrepapier.fr": {
                    "name": "Pierre-Papier",
                    "specialty": ["tdvm", "prices", "performance", "rankings"],
                    "url_patterns": [
                        r"pierrepapier\.fr/scpi/",
                        r"pierrepapier\.fr/classement",
                    ],
                    "scrape_config": {
                        "tdvm_selector": ".performance-value",
                        "price_selector": ".price-info",
                    },
                },
                "francescpi.com": {
                    "name": "France SCPI",
                    "specialty": ["analysis", "comparisons", "guides"],
                    "url_patterns": [
                        r"francescpi\.com/",
                    ],
                    "scrape_config": {
                        "analysis_selector": ".article-content",
                    },
                },
                "aspim.fr": {
                    "name": "L'ASPIM (Association)",
                    "specialty": ["official_data", "regulations", "statistics"],
                    "url_patterns": [
                        r"aspim\.fr/",
                    ],
                    "scrape_config": {
                        "stats_selector": ".data-table",
                    },
                },
                "amf-france.org": {
                    "name": "AMF",
                    "specialty": ["regulations", "official_guidance", "compliance"],
                    "url_patterns": [
                        r"amf-france\.org/",
                    ],
                },
                "centraledesscpi.com": {
                    "name": "La Centrale des SCPI",
                    "specialty": ["comparisons", "scpi_details", "market_data"],
                    "url_patterns": [
                        r"centraledesscpi\.com/",
                    ],
                },
            },
            2: {
                "meilleurescpi.com": {
                    "name": "MeilleureSCPI.com",
                    "specialty": ["rankings", "tdvm", "comparisons"],
                },
                "francetransactions.com": {
                    "name": "France Transactions",
                    "specialty": ["guides", "analysis"],
                },
                "primaliance.com": {
                    "name": "Primaliance",
                    "specialty": ["products", "scpi_search", "comparisons"],
                },
                "louveinvest.com": {
                    "name": "Louve Invest",
                    "specialty": ["digital_scpi_platform", "comparisons"],
                },
                "epargnoo.com": {
                    "name": "Epargnoo",
                    "specialty": ["scpi_cashback", "yield"],
                },
                "homunity.com": {
                    "name": "Homunity",
                    "specialty": ["digital_real_estate_investing", "scpi_data"],
                },
                "avenuedesinvestisseurs.fr": {
                    "name": "Avenue des Investisseurs",
                    "specialty": ["education", "pedagogy", "comparisons"],
                },
                "finance-heros.fr": {
                    "name": "Finance Heros",
                    "specialty": ["simple_comparisons", "education"],
                },
                "meilleurtaux-placement.com": {
                    "name": "Meilleurtaux Placement",
                    "specialty": ["comparisons", "news"],
                },
                "investissement-locatif.com": {
                    "name": "Investissement Locatif",
                    "specialty": ["analysis", "comparisons"],
                },
                "capital.fr": {
                    "name": "Capital",
                    "specialty": ["news", "analysis"],
                },
            },
            3: {
                "lesechos.fr": {"name": "Les Echos", "specialty": ["news"]},
                "boursorama.com": {"name": "Boursorama", "specialty": ["data"]},
                "capital.fr": {"name": "Capital", "specialty": ["economic_news", "scpi_news"]},
                "reddit.com": {"name": "Reddit", "specialty": ["raw_investor_opinions"]},
            },
        }

        # Legacy aliases found in prompts/snippets.
        self.source_aliases: Dict[str, List[str]] = {
            "pierre-papier": ["pierrepapier.fr"],
            "france-scpi": ["francescpi.com", "france-scpi.fr"],
            "aspim": ["aspim.fr"],
            "amf": ["amf-france.org"],
            "la-centrale-scpi": ["centraledesscpi.com"],
            "centrale-scpi": ["centraledesscpi.com"],
            "meilleurescpi": ["meilleurescpi.com"],
            "france-transactions": ["francetransactions.com"],
            "primaliance": ["primaliance.com"],
            "louve-invest": ["louveinvest.com"],
            "epargnoo": ["epargnoo.com"],
            "homunity": ["homunity.com"],
            "avenue-investisseurs": ["avenuedesinvestisseurs.fr"],
            "finance-heros": ["finance-heros.fr"],
            "les-echos": ["lesechos.fr"],
            "reddit": ["reddit.com"],
            "capital": ["capital.fr"],
        }

        self.search_templates: Dict[str, Dict[str, str]] = {
            "tdvm": {
                "pierre-papier": "site:pierrepapier.fr {scpi_name} TD 2026 taux distribution",
                "france-scpi": "site:francescpi.com {scpi_name} taux distribution",
                "aspim": "site:aspim.fr SCPI {scpi_name} statistiques",
                "meilleurescpi": "site:meilleurescpi.com {scpi_name} rendement SCPI",
                "la-centrale-scpi": "site:centraledesscpi.com {scpi_name} rendement",
            },
            "price": {
                "pierre-papier": "site:pierrepapier.fr {scpi_name} prix part souscription",
                "france-scpi": "site:francescpi.com {scpi_name} valeur part",
                "la-centrale-scpi": "site:centraledesscpi.com {scpi_name} prix part",
                "primaliance": "site:primaliance.com {scpi_name} prix part",
            },
            "comparison": {
                "pierre-papier": "site:pierrepapier.fr classement SCPI {query}",
                "france-scpi": "site:francescpi.com comparatif SCPI {query}",
                "la-centrale-scpi": "site:centraledesscpi.com comparatif SCPI {query}",
                "meilleurescpi": "site:meilleurescpi.com classement SCPI {query}",
                "finance-heros": "site:finance-heros.fr SCPI comparatif {query}",
                "avenue-investisseurs": "site:avenuedesinvestisseurs.fr SCPI comparatif {query}",
            },
            "news": {
                "aspim": "site:aspim.fr SCPI publication {query}",
                "amf": "site:amf-france.org SCPI AMF {query}",
                "pierre-papier": "site:pierrepapier.fr {query} actualite",
                "france-scpi": "site:francescpi.com {query} analyse",
                "capital": "site:capital.fr SCPI {query}",
                "les-echos": "site:lesechos.fr SCPI {query}",
                "reddit": "site:reddit.com/r/vosfinances SCPI {query}",
            },
            "general": {
                "aspim": "site:aspim.fr SCPI {query}",
                "amf": "site:amf-france.org SCPI {query}",
                "pierre-papier": "site:pierrepapier.fr SCPI {query}",
                "france-scpi": "site:francescpi.com SCPI {query}",
                "la-centrale-scpi": "site:centraledesscpi.com SCPI {query}",
            },
        }

    def search(
        self,
        query: str,
        search_type: str = "general",
        max_results: int = 10,
        min_priority: int = 3,
    ) -> List[SearchResult]:
        """
        Search with source prioritization.

        Args:
            query: User search query.
            search_type: "tdvm", "price", "comparison", "news", "general".
            max_results: Maximum number of unique results.
            min_priority: Highest allowed priority level number (1..3).
                1 => only level 1
                2 => levels 1+2
                3 => levels 1+2+3
        """
        all_results: List[SearchResult] = []
        max_results = max(1, int(max_results or 1))
        min_priority = max(1, min(3, int(min_priority or 3)))

        logger.info(f"[prioritized-web] query='{query}' type={search_type} max={max_results}")
        if not self.serper_api_key and not self.serpapi_api_key:
            logger.warning("[prioritized-web] disabled: no SERPER_API_KEY / SERPAPI_API_KEY configured")
            return []
        priority_queries = self._build_priority_queries(query=query, search_type=search_type)
        queries_executed = 0

        for priority_level in sorted(priority_queries.keys()):
            if priority_level > min_priority:
                continue

            for source_key, search_query in priority_queries.get(priority_level, []):
                if queries_executed >= self.max_queries:
                    break
                try:
                    results = self._search(search_query, num_results=min(10, max_results * 2))
                except Exception as exc:
                    logger.warning(f"[prioritized-web] search error source={source_key}: {exc}")
                    results = []
                queries_executed += 1

                for item in results:
                    link = str(item.get("link") or "").strip()
                    if not link:
                        continue
                    if not self._matches_source(link, source_key, priority_level):
                        continue
                    all_results.append(
                        SearchResult(
                            url=link,
                            title=str(item.get("title") or "").strip(),
                            snippet=str(item.get("snippet") or "").strip(),
                            source_name=self._get_source_name(source_key, priority_level),
                            priority=priority_level,
                            date=str(item.get("date") or "").strip() or None,
                            data_type=search_type,
                        )
                    )

                if len(all_results) >= max_results:
                    break
            if queries_executed >= self.max_queries or len(all_results) >= max_results:
                break

        # If too few prioritized results, enrich with a generic query.
        if len(all_results) < max(3, max_results // 2) and queries_executed < self.max_queries:
            generic_results = self._search(query, num_results=min(10, max_results * 2))
            for item in generic_results:
                link = str(item.get("link") or "").strip()
                if not link:
                    continue
                all_results.append(
                    SearchResult(
                        url=link,
                        title=str(item.get("title") or "").strip(),
                        snippet=str(item.get("snippet") or "").strip(),
                        source_name=self._extract_domain(link),
                        priority=self._get_url_priority(link),
                        date=str(item.get("date") or "").strip() or None,
                        data_type="general",
                    )
                )

        # Sort by source priority, then by recency when parseable.
        sorted_results = sorted(
            all_results,
            key=lambda x: (x.priority, -self._sortable_date_value(x.date)),
        )

        # Deduplicate by URL while preserving order.
        seen_urls = set()
        unique_results: List[SearchResult] = []
        for result in sorted_results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            unique_results.append(result)
            if len(unique_results) >= max_results:
                break

        logger.info(
            "[prioritized-web] done total=%s p1=%s p2=%s p3=%s",
            len(unique_results),
            sum(1 for r in unique_results if r.priority == 1),
            sum(1 for r in unique_results if r.priority == 2),
            sum(1 for r in unique_results if r.priority == 3),
        )
        return unique_results

    def _search(self, query: str, num_results: int = 10) -> List[Dict[str, Any]]:
        provider = (self.provider or "auto").lower()
        if provider == "serpapi":
            providers = ["serpapi", "serper"]
        elif provider == "serper":
            providers = ["serper", "serpapi"]
        else:
            providers = ["serper", "serpapi"]

        errors: List[str] = []
        for name in providers:
            try:
                if name == "serper":
                    rows = self._search_serper(query, num_results=num_results)
                else:
                    rows = self._search_serpapi(query, num_results=num_results)
                if rows:
                    return rows
            except Exception as exc:
                errors.append(f"{name}:{exc}")
                continue

        if errors:
            logger.warning(
                "[prioritized-web] all providers failed for query='%s': %s",
                query[:100],
                " | ".join(errors),
            )
        return []

    def _build_priority_queries(self, query: str, search_type: str) -> Dict[int, List[Tuple[str, str]]]:
        queries_by_priority: Dict[int, List[Tuple[str, str]]] = {1: [], 2: [], 3: []}
        scpi_name = self._extract_scpi_name(query) or query

        if search_type in self.search_templates:
            templates = self.search_templates[search_type]
            for source_key, template in templates.items():
                search_query = template.format(
                    scpi_name=scpi_name,
                    query=query,
                    scpi1=scpi_name,
                    scpi2="",
                    category="",
                ).strip()
                priority = self._get_source_priority(source_key)
                queries_by_priority[priority].append((source_key, search_query))

        # Also add generic site queries for every known source.
        for priority_level, sources in self.priority_sources.items():
            for domain in sources.keys():
                queries_by_priority[priority_level].append((domain, f"site:{domain} {query}".strip()))

        # Deduplicate query tuples per priority.
        for level in queries_by_priority:
            seen = set()
            deduped: List[Tuple[str, str]] = []
            for source_key, q in queries_by_priority[level]:
                key = (source_key, q)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append((source_key, q))
            queries_by_priority[level] = deduped

        return queries_by_priority

    def _search_serper(self, query: str, num_results: int = 10) -> List[Dict[str, Any]]:
        if not self.serper_api_key:
            return []

        payload = {
            "q": query,
            "num": max(1, min(10, int(num_results or 10))),
            "gl": "fr",
            "hl": "fr",
        }
        headers = {
            "X-API-KEY": self.serper_api_key,
            "Content-Type": "application/json",
        }

        # Prefer httpx when present, fallback to urllib.
        if httpx is not None:
            resp = httpx.post(
                "https://google.serper.dev/search",
                headers=headers,
                json=payload,
                timeout=12.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("organic", []) if isinstance(data, dict) else []

        req = Request(
            "https://google.serper.dev/search",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(req, timeout=12.0) as res:
            raw = res.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data.get("organic", []) if isinstance(data, dict) else []

    def _search_serpapi(self, query: str, num_results: int = 10) -> List[Dict[str, Any]]:
        if not self.serpapi_api_key:
            return []

        params = {
            "engine": "google",
            "q": query,
            "api_key": self.serpapi_api_key,
            "num": max(1, min(10, int(num_results or 10))),
            "hl": "fr",
            "gl": "fr",
        }

        if httpx is not None:
            resp = httpx.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=12.0,
            )
            resp.raise_for_status()
            data = resp.json()
        else:
            req = Request(
                "https://serpapi.com/search.json?" + urlencode(params),
                method="GET",
            )
            with urlopen(req, timeout=12.0) as res:
                raw = res.read().decode("utf-8", errors="replace")
                data = json.loads(raw)

        organic = data.get("organic_results", []) if isinstance(data, dict) else []
        normalized: List[Dict[str, Any]] = []
        for row in organic:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "title": str(row.get("title") or ""),
                    "link": str(row.get("link") or ""),
                    "snippet": str(row.get("snippet") or ""),
                    "date": str(row.get("date") or ""),
                }
            )
        return normalized

    def _source_domains_for_key(self, source_key: str, priority: int) -> List[str]:
        key = (source_key or "").lower().strip()
        if key in self.source_aliases:
            return [d.lower().strip() for d in self.source_aliases[key]]

        sources = self.priority_sources.get(priority, {})
        if key in sources:
            return [key]

        # Partial match fallback.
        domains: List[str] = []
        for level_sources in self.priority_sources.values():
            for domain in level_sources.keys():
                if key in domain or domain in key:
                    domains.append(domain)
        return domains or [key]

    def _matches_source(self, url: str, source_key: str, priority: int) -> bool:
        domain = self._extract_domain(url).lower().lstrip("www.")
        candidates = self._source_domains_for_key(source_key, priority)
        for expected in candidates:
            expected = expected.lower().lstrip("www.")
            if domain == expected or domain.endswith(f".{expected}"):
                return True
        return False

    def _get_source_name(self, source_key: str, priority: int) -> str:
        key = (source_key or "").lower().strip()
        sources = self.priority_sources.get(priority, {})
        if key in sources:
            return str(sources[key].get("name") or source_key)
        # Alias name fallback.
        for level_sources in self.priority_sources.values():
            for domain, cfg in level_sources.items():
                if key == domain or key in domain:
                    return str(cfg.get("name") or domain)
        return source_key

    def _get_url_priority(self, url: str) -> int:
        domain = self._extract_domain(url).lower().lstrip("www.")
        for priority, sources in self.priority_sources.items():
            for source_domain in sources.keys():
                expected = source_domain.lower().lstrip("www.")
                if domain == expected or domain.endswith(f".{expected}"):
                    return priority
        return 3

    def _get_source_priority(self, source_key: str) -> int:
        key = (source_key or "").lower().strip()
        alias_domains = self.source_aliases.get(key, [key])
        for priority, sources in self.priority_sources.items():
            domains = [d.lower().strip() for d in sources.keys()]
            if any(alias in domains for alias in alias_domains):
                return priority
            if any(any(alias in domain for domain in domains) for alias in alias_domains):
                return priority
        return 3

    def _extract_domain(self, url: str) -> str:
        try:
            return (urlparse(url).netloc or "").lower().strip() or url
        except Exception:
            match = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
            return match.group(1).lower() if match else (url or "")

    def _extract_scpi_name(self, query: str) -> Optional[str]:
        known_scpi = [
            "Corum Origin",
            "Corum XL",
            "Primopierre",
            "PFO2",
            "Immorente",
            "Epargne Pierre",
            "Pierval Sante",
            "Activimmo",
            "Remake Live",
            "Eurovalys",
            "Cristal Rente",
            "Efimmo",
            "Iroko Zen",
        ]
        qn = (query or "").lower()
        for scpi in known_scpi:
            if scpi.lower() in qn:
                return scpi
        return None

    def _sortable_date_value(self, raw_date: Optional[str]) -> float:
        if not raw_date:
            return 0.0
        text = str(raw_date).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except Exception:
                continue
        # Year fallback.
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            try:
                return datetime(int(year_match.group(1)), 1, 1).timestamp()
            except Exception:
                return 0.0
        return 0.0

    def scrape_data(self, search_result: SearchResult) -> Dict[str, Any]:
        """
        Optional scraping for priority-1 sources.
        Works even without BeautifulSoup by returning snippet-only fallback.
        """
        if search_result.priority > 1:
            return {"raw_content": search_result.snippet}
        if BeautifulSoup is None:
            return {"raw_content": search_result.snippet}

        headers = {"User-Agent": "Mozilla/5.0"}
        body_bytes: Optional[bytes] = None
        if httpx is not None:
            try:
                resp = httpx.get(search_result.url, headers=headers, timeout=12.0, follow_redirects=True)
                resp.raise_for_status()
                body_bytes = resp.content
            except Exception:
                body_bytes = None
        if body_bytes is None:
            try:
                req = Request(search_result.url, headers=headers, method="GET")
                with urlopen(req, timeout=12.0) as res:
                    body_bytes = res.read()
            except Exception:
                return {"raw_content": search_result.snippet}

        try:
            soup = BeautifulSoup(body_bytes, "html.parser")
            main = soup.find("article") or soup.find("main") or soup.body
            if not main:
                return {"raw_content": search_result.snippet}
            text = main.get_text(separator="\n", strip=True)
            return {"main_content": text[:5000]}
        except Exception:
            return {"raw_content": search_result.snippet}

    def format_results_for_context(self, results: List[SearchResult]) -> str:
        if not results:
            return "No results found."

        lines: List[str] = [f"WEB SOURCES ({len(results)} results):", ""]
        by_priority: Dict[int, List[SearchResult]] = {1: [], 2: [], 3: []}
        for result in results:
            by_priority.setdefault(result.priority, []).append(result)

        labels = {
            1: "P1 PRIORITY SOURCES",
            2: "P2 RELIABLE COMPLEMENTARY SOURCES",
            3: "P3 GENERAL SOURCES",
        }
        for level in [1, 2, 3]:
            entries = by_priority.get(level) or []
            if not entries:
                continue
            lines.append(labels[level])
            lines.append("-" * 50)
            for idx, result in enumerate(entries, start=1):
                lines.append(f"{idx}. {result.title}")
                lines.append(f"Source: {result.source_name}")
                lines.append(f"URL: {result.url}")
                lines.append(f"Date: {result.date or 'N/A'}")
                lines.append(f"Snippet: {result.snippet}")
                lines.append("")
        return "\n".join(lines).strip()


# Global instance for reuse
web_search_prioritized = PrioritizedWebSearch()
