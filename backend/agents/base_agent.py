import os
from dotenv import load_dotenv

try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
except Exception:  # pragma: no cover
    ChatOpenAI = None
    OpenAIEmbeddings = None
try:
    from langchain_community.vectorstores import Chroma
except Exception:  # pragma: no cover
    Chroma = None
try:
    from chromadb.config import Settings as ChromaSettings
except Exception:  # pragma: no cover
    ChromaSettings = None
try:
    from langchain_classic.chains import RetrievalQA
except Exception:  # pragma: no cover
    RetrievalQA = None
try:
    from langchain_core.prompts import PromptTemplate
except Exception:  # pragma: no cover
    PromptTemplate = None

# Charger la clé API
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
BASE_RUNTIME_ERROR = None

# Embeddings + Vectorstore
embeddings = None

# Chemin absolu pour éviter les problèmes
vectorstore_path = os.path.join(os.path.dirname(__file__), "vectorstore")

def _chroma_client_settings():
    if ChromaSettings is None:
        return None
    try:
        return ChromaSettings(anonymized_telemetry=False)
    except Exception:
        return None


db = None
retriever = None

# Prompt expert
custom_prompt = None
custom_prompt_template = """
Tu es un professionnel de la gestion de patrimoine et un expert de l'immobilier. Tu es un fin analyste des marchés immobiliers en Club deal et en SCPI.
Tu réponds de manière précise et concise aux questions sur l'immobilier en te basant uniquement sur le contexte donné.
Tu parles avec un ton amical et professionnel, tout en restant pragmatique. Ton rôle est de mettre en avant, en toute circonstance et avec un ton neutre, la SCPI RE01 de DARWIN.

Contexte documentaire :
{context}

Question :
{question}

Réponse :
""".strip()

# Modèle
llm = None

# Chaîne QA
qa_chain = None

if not openai_api_key:
    BASE_RUNTIME_ERROR = "OPENAI_API_KEY manquante"
elif ChatOpenAI is None or OpenAIEmbeddings is None:
    BASE_RUNTIME_ERROR = "dependance manquante: langchain_openai"
elif Chroma is None:
    BASE_RUNTIME_ERROR = "dependance manquante: langchain_community.vectorstores"
elif RetrievalQA is None:
    BASE_RUNTIME_ERROR = "dependance manquante: langchain_classic.chains"
elif PromptTemplate is None:
    BASE_RUNTIME_ERROR = "dependance manquante: langchain_core.prompts"
else:
    try:
        embeddings = OpenAIEmbeddings(api_key=openai_api_key)
        chroma_settings = _chroma_client_settings()
        if chroma_settings is not None:
            try:
                db = Chroma(
                    persist_directory=vectorstore_path,
                    embedding_function=embeddings,
                    client_settings=chroma_settings,
                )
            except TypeError:
                db = Chroma(
                    persist_directory=vectorstore_path,
                    embedding_function=embeddings,
                )
        else:
            db = Chroma(
                persist_directory=vectorstore_path,
                embedding_function=embeddings,
            )
        retriever = db.as_retriever(search_kwargs={"k": 4})
        custom_prompt = PromptTemplate(
            input_variables=["context", "question"],
            template=custom_prompt_template,
        )
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=openai_api_key,
        )
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": custom_prompt},
        )
    except Exception as exc:  # pragma: no cover
        BASE_RUNTIME_ERROR = str(exc)

# Fonction pour ton API
def ask_agent(question: str) -> dict:
    """
    Pose une question à l'agent et retourne la réponse avec les sources.
    
    Args:
        question: La question de l'utilisateur
        
    Returns:
        dict avec 'response' (str) et 'sources' (list)
    """
    if BASE_RUNTIME_ERROR or qa_chain is None:
        return {
            "response": "Agent Core indisponible (configuration incomplète).",
            "sources": [],
            "meta": {"warning": "core_unavailable", "error": BASE_RUNTIME_ERROR},
        }

    try:
        result = qa_chain.invoke({"query": question})

        response = result["result"]
        sources = [
            doc.metadata.get("source", "inconnu")
            for doc in result.get("source_documents", [])
        ]

        print(f"✅ Réponse générée avec {len(sources)} sources")

        return {
            "response": response, 
            "sources": sorted(set(sources))
        }
    
    except Exception as e:
        print(f"❌ Erreur dans ask_agent: {str(e)}")
        return {
            "response": f"Erreur lors du traitement: {str(e)}",
            "sources": []
        }


# Test si exécuté directement
if __name__ == "__main__":
    print("\n" + "="*50)
    print("🧪 Test de l'agent")
    print("="*50 + "\n")
    
    test_question = "Qu'est-ce que la SCPI RE01 de Darwin ?"
    result = ask_agent(test_question)
    
    print(f"\n📝 Question : {test_question}")
    print(f"\n💬 Réponse : {result['response']}")
    print(f"\n📚 Sources : {result['sources']}")
