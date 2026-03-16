"""
Agent conversationnel CGP base sur LangChain.

Fonctionnalites:
- Indexation de PDF Darwin depuis des URLs (avec parallelisme)
- Recherche web pour actualites/reglementation
- Reponses en francais
- Citations systematiques des sources:
  - PDF: URL + numero de page
  - Web: URL
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
try:
    from chromadb.config import Settings as ChromaSettings
except Exception:  # pragma: no cover
    ChromaSettings = None
from dotenv import load_dotenv
try:
    from langchain.agents import create_agent as _lc_create_agent
except Exception:  # pragma: no cover
    _lc_create_agent = None
try:
    from langchain.agents import initialize_agent, AgentType
except Exception:  # pragma: no cover
    initialize_agent = None
    AgentType = None
try:
    from langchain_chroma import Chroma
except Exception:  # pragma: no cover
    Chroma = None
try:
    from langchain_community.document_loaders import PyPDFLoader
except Exception:  # pragma: no cover
    PyPDFLoader = None
try:
    from langchain_core.documents import Document
except Exception:  # pragma: no cover
    Document = None
try:
    from langchain_core.tools import tool
except Exception:  # pragma: no cover
    tool = None
try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
except Exception:  # pragma: no cover
    ChatOpenAI = None
    OpenAIEmbeddings = None
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:  # pragma: no cover
    RecursiveCharacterTextSplitter = None

try:
    from ddgs import DDGS
except Exception:
    DDGS = None


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _clean(value: str) -> str:
    return (value or "").strip()


def _chroma_client_settings():
    if ChromaSettings is None:
        return None
    try:
        return ChromaSettings(anonymized_telemetry=False)
    except Exception:
        return None


SYSTEM_PROMPT_FR = """
Tu es un assistant expert en gestion de patrimoine (CGP), strictement factuel et utile.
Tu reponds toujours en francais.

Regles:
1. Pour les questions sur les produits Darwin (frais, conditions, clauses), utilise search_darwin_pdfs.
2. Pour les questions d'actualite, de marche ou de reglementation recente, utilise search_web.
3. Si besoin, utilise les 2 outils avant de conclure.
4. N'invente jamais de chiffre, de date ou de source.
5. Si une information n'est pas disponible, dis-le explicitement et indique ce qu'il faut verifier.
6. Structure ta reponse: synthese, details, puis conclusion actionnable.

Important:
- Les extraits d'outils contiennent deja les URLs et les pages.
- Appuie-toi dessus pour raisonner.
""".strip()


@dataclass(frozen=True)
class SourceRef:
    kind: str  # "pdf" ou "web"
    url: str
    page: Optional[int] = None


class DarwinPDFIndexer:
    """Telecharge et indexe des PDFs distants dans Chroma."""

    def __init__(
        self,
        pdf_urls: Sequence[str],
        persist_directory: str = "vectorstore/darwin_cgp",
        collection_name: str = "darwin_cgp_docs",
        embedding_model: str = "text-embedding-3-small",
        chunk_size: int = 1200,
        chunk_overlap: int = 150,
        max_workers: int = 4,
    ) -> None:
        if OpenAIEmbeddings is None:
            raise RuntimeError("Dependance manquante: langchain_openai")
        if Chroma is None:
            raise RuntimeError("Dependance manquante: langchain_chroma")
        if PyPDFLoader is None:
            raise RuntimeError("Dependance manquante: langchain_community.document_loaders")
        if RecursiveCharacterTextSplitter is None:
            raise RuntimeError("Dependance manquante: langchain_text_splitters")

        self.pdf_urls = [u.strip() for u in pdf_urls if u and u.strip()]
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_workers = max_workers

        self.embeddings = OpenAIEmbeddings(model=embedding_model)
        self.vectorstore: Optional[Chroma] = None

        self.cache_dir = Path(".cache/darwin_pdf")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_pdf_cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.pdf"

    def _download_pdf(self, url: str, timeout: float = 45.0) -> Path:
        output_path = self._get_pdf_cache_path(url)
        if output_path.exists():
            return output_path

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)
        return output_path

    @staticmethod
    def _page_number(metadata: Dict) -> Optional[int]:
        page_raw = metadata.get("page")
        if page_raw is None:
            return None
        try:
            # PyPDFLoader renvoie souvent une base 0.
            return int(page_raw) + 1
        except Exception:
            return None

    def _load_one_pdf(self, url: str) -> List[Document]:
        local_pdf = self._download_pdf(url)
        loader = PyPDFLoader(str(local_pdf))
        docs = loader.load()
        for d in docs:
            d.metadata = d.metadata or {}
            d.metadata["source_url"] = url
            d.metadata["source"] = url
            d.metadata["page_number"] = self._page_number(d.metadata)
        return docs

    def _load_all_pdfs_parallel(self) -> List[Document]:
        all_docs: List[Document] = []
        if not self.pdf_urls:
            return all_docs

        workers = max(1, min(self.max_workers, len(self.pdf_urls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_url = {pool.submit(self._load_one_pdf, url): url for url in self.pdf_urls}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    docs = future.result()
                    all_docs.extend(docs)
                    logger.info("PDF charge: %s (%d pages)", url, len(docs))
                except Exception as exc:
                    logger.warning("Echec chargement PDF %s: %s", url, exc)
        return all_docs

    def build_or_load(self, force_reindex: bool = False) -> Chroma:
        if force_reindex:
            shutil.rmtree(self.persist_directory, ignore_errors=True)

        chroma_settings = _chroma_client_settings()
        if chroma_settings is not None:
            try:
                store = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=self.persist_directory,
                    embedding_function=self.embeddings,
                    client_settings=chroma_settings,
                )
            except TypeError:
                store = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=self.persist_directory,
                    embedding_function=self.embeddings,
                )
        else:
            store = Chroma(
                collection_name=self.collection_name,
                persist_directory=self.persist_directory,
                embedding_function=self.embeddings,
            )

        try:
            count = store._collection.count()  # type: ignore[attr-defined]
        except Exception:
            count = 0

        if count > 0 and not force_reindex:
            logger.info("Index charge depuis %s (%d chunks)", self.persist_directory, count)
            self.vectorstore = store
            return store

        docs = self._load_all_pdfs_parallel()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_documents(docs)

        if chunks:
            store.add_documents(chunks)
            logger.info("Index cree: %d chunks", len(chunks))
        else:
            logger.warning("Aucun chunk indexe (verifiez les URLs PDF).")

        self.vectorstore = store
        return store


class CGPConversationAgent:
    """Agent LangChain (GPT-4 + tools PDF/Web) avec citations obligatoires."""

    def __init__(
        self,
        pdf_urls: Sequence[str],
        model_name: str = "gpt-4o-mini",
        persist_directory: str = "vectorstore/darwin_cgp",
        collection_name: str = "darwin_cgp_docs",
        pdf_top_k: int = 6,
        web_top_k: int = 6,
    ) -> None:
        load_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY manquante.")
        if ChatOpenAI is None:
            raise RuntimeError("Dependance manquante: langchain_openai")
        if tool is None:
            raise RuntimeError("Dependance manquante: langchain_core.tools")

        self.pdf_top_k = pdf_top_k
        self.web_top_k = web_top_k
        self.llm = ChatOpenAI(model=model_name, temperature=0)
        self.indexer = DarwinPDFIndexer(
            pdf_urls=pdf_urls,
            persist_directory=persist_directory,
            collection_name=collection_name,
        )
        self.vectorstore: Optional[Chroma] = None

    def initialize(self, force_reindex: bool = False) -> None:
        self.vectorstore = self.indexer.build_or_load(force_reindex=force_reindex)

    @staticmethod
    def _clean_excerpt(text: str, max_chars: int = 700) -> str:
        compact = " ".join((text or "").split())
        return compact[:max_chars]

    @staticmethod
    def _extract_page(metadata: Dict) -> Optional[int]:
        page = metadata.get("page_number")
        if page is not None:
            try:
                return int(page)
            except Exception:
                return None
        raw = metadata.get("page")
        if raw is not None:
            try:
                return int(raw) + 1
            except Exception:
                return None
        return None

    def _build_tools(self, used_sources: List[SourceRef]):
        if self.vectorstore is None:
            raise RuntimeError("Agent non initialise. Appelez initialize() avant ask().")

        @tool("search_darwin_pdfs")
        def search_darwin_pdfs(query: str) -> str:
            """
            Cherche des informations dans les PDFs Darwin indexes.
            Retourne des extraits avec URL source et numero de page.
            """
            docs = self.vectorstore.similarity_search(query, k=self.pdf_top_k)
            if not docs:
                return "Aucun resultat trouve dans les PDFs Darwin."

            lines: List[str] = []
            for idx, doc in enumerate(docs, start=1):
                metadata = doc.metadata or {}
                url = str(metadata.get("source_url") or metadata.get("source") or "unknown")
                page = self._extract_page(metadata)
                used_sources.append(SourceRef(kind="pdf", url=url, page=page))
                snippet = self._clean_excerpt(doc.page_content)
                page_text = str(page) if page is not None else "n/a"
                lines.append(
                    f"[PDF {idx}] URL: {url} | Page: {page_text}\n"
                    f"Extrait: {snippet}"
                )
            return "\n\n".join(lines)

        @tool("search_web")
        def search_web(query: str) -> str:
            """
            Recherche web pour actualites, reglementation ou donnees recentes.
            Retourne URL + extrait.
            """
            if DDGS is None:
                return "Recherche web indisponible: package `ddgs` non installe."

            results: List[Tuple[str, str, str]] = []
            with DDGS() as ddgs_client:
                for item in ddgs_client.text(query, max_results=self.web_top_k * 2):
                    title = (item.get("title") or "").strip()
                    href = (item.get("href") or "").strip()
                    body = (item.get("body") or "").strip()
                    if not href:
                        continue
                    results.append((title, href, body))
                    if len(results) >= self.web_top_k:
                        break

            if not results:
                return "Aucun resultat web pertinent."

            lines: List[str] = []
            for idx, (title, href, body) in enumerate(results, start=1):
                used_sources.append(SourceRef(kind="web", url=href, page=None))
                body_short = self._clean_excerpt(body, max_chars=500)
                lines.append(f"[WEB {idx}] {title}\nURL: {href}\nExtrait: {body_short}")
            return "\n\n".join(lines)

        return [search_darwin_pdfs, search_web]

    @staticmethod
    def _extract_answer_text(agent_result: Dict) -> str:
        messages = agent_result.get("messages") if isinstance(agent_result, dict) else None
        if messages:
            content = getattr(messages[-1], "content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")).strip())
                    elif isinstance(item, str):
                        parts.append(item.strip())
                return "\n".join([p for p in parts if p]).strip()
        return str(agent_result).strip()

    def _build_runtime_agent(self, tools):
        if _lc_create_agent is not None:
            return _lc_create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=SYSTEM_PROMPT_FR,
            )
        if initialize_agent is not None and AgentType is not None:
            return initialize_agent(
                tools=tools,
                llm=self.llm,
                agent=AgentType.OPENAI_FUNCTIONS,
                verbose=False,
            )
        raise RuntimeError("Aucune fabrique d'agent compatible n'est disponible (langchain.agents).")

    def _invoke_runtime_agent(self, agent, messages: List[Dict[str, str]]) -> str:
        # New LangChain API agent
        if _lc_create_agent is not None:
            result = agent.invoke({"messages": messages})
            return self._extract_answer_text(result)

        # Legacy initialize_agent API
        history_parts: List[str] = []
        question_txt = ""
        for msg in messages:
            role = _clean(str(msg.get("role") or "")).lower()
            content = _clean(str(msg.get("content") or ""))
            if not content:
                continue
            if role == "user":
                question_txt = content
            elif role == "assistant":
                history_parts.append(f"Assistant: {content}")
            elif role == "system":
                history_parts.append(f"System: {content}")

        if history_parts:
            question_txt = "\n".join(history_parts[-6:] + [f"Question: {question_txt}"])

        result = agent.invoke({"input": question_txt})
        if isinstance(result, dict):
            out = _clean(str(result.get("output") or ""))
            if out:
                return out
        return _clean(str(result))

    @staticmethod
    def _format_sources(used_sources: Iterable[SourceRef]) -> str:
        seen = set()
        lines: List[str] = []
        for src in used_sources:
            if src.kind == "pdf":
                label = f"- {src.url} (page {src.page if src.page is not None else 'n/a'})"
            else:
                label = f"- {src.url}"
            if label in seen:
                continue
            seen.add(label)
            lines.append(label)
        return "\n".join(lines) if lines else "- Aucune source exploitable."

    def ask(self, question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        """
        Pose une question a l'agent.
        history format:
        [
          {"role": "user", "content": "..."},
          {"role": "assistant", "content": "..."}
        ]
        """
        if not question or not question.strip():
            return "Question vide."
        if self.vectorstore is None:
            raise RuntimeError("Agent non initialise. Appelez initialize() avant ask().")

        used_sources: List[SourceRef] = []
        tools = self._build_tools(used_sources)
        agent = self._build_runtime_agent(tools)

        messages: List[Dict[str, str]] = []
        for msg in history or []:
            role = str(msg.get("role", "")).strip().lower()
            content = str(msg.get("content", "")).strip()
            if role in {"user", "assistant", "system"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question.strip()})

        answer = self._invoke_runtime_agent(agent, messages)
        sources_block = self._format_sources(used_sources)
        return f"{answer}\n\nSources:\n{sources_block}"


def demo() -> None:
    """
    Exemple d'utilisation local.
    """
    pdf_urls = [
        "https://darwin.fr/documents/dynavie.pdf",
        "https://darwin.fr/documents/scpi-cristal.pdf",
        "https://darwin.fr/documents/tarifs-2024.pdf",
    ]

    agent = CGPConversationAgent(
        pdf_urls=pdf_urls,
        model_name=os.getenv("CGP_MODEL", "gpt-4o"),
        persist_directory="vectorstore/darwin_cgp",
        collection_name="darwin_cgp_docs",
    )

    # Premier lancement: force_reindex=True pour indexer les PDFs.
    # Ensuite, force_reindex=False pour reutiliser l'index.
    agent.initialize(force_reindex=False)

    questions = [
        "Quels sont les frais du contrat Dynavie ?",
        "Compare les SCPI Cristal Rente et Primopierre.",
        "Quelle est la derniere reglementation sur l'assurance-vie en France ?",
    ]
    history: List[Dict[str, str]] = []

    for q in questions:
        print("\n" + "=" * 80)
        print("Question:", q)
        answer = agent.ask(q, history=history)
        print("Reponse:\n", answer)
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    demo()
