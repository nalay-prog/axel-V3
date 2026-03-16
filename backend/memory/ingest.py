import os
from dotenv import load_dotenv
from tqdm import tqdm

from langchain_community.document_loaders import UnstructuredFileLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings

# Charger la clé OpenAI depuis .env
load_dotenv()
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

print("DEBUG OPENAI_KEY =", repr(OPENAI_KEY))
if not OPENAI_KEY:
    raise ValueError("La clé OPENAI_API_KEY est vide ou non chargée.")

DOCS_PATH = "data/docs"
documents = []
print("📥 Chargement des documents...")

for filename in tqdm(os.listdir(DOCS_PATH)):
    file_path = os.path.join(DOCS_PATH, filename)

    # Ignore les fichiers système invisibles comme .DS_Store
    if filename.startswith("."):
        continue

    if os.path.isfile(file_path):
        try:
            print(f"📄 Lecture du fichier : {file_path}")
            loader = UnstructuredFileLoader(file_path)
            docs = loader.load()
            documents.extend(docs)
        except Exception as e:
            print(f"❌ Erreur avec le fichier {file_path} : {e}")

print(f"✅ {len(documents)} documents chargés.")

# Découpage en chunks
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=100
)
texts = splitter.split_documents(documents)

print(f"🧩 {len(texts)} morceaux générés.")

# Embeddings OpenAI
embedding = OpenAIEmbeddings(
    api_key=OPENAI_KEY
)

# Création de la base Chroma
db = Chroma.from_documents(
    texts,
    embedding,
    persist_directory="vectorstore"
)
db.persist()

print("✅ Base vectorielle enregistrée dans 'vectorstore'")
