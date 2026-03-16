import os
from dotenv import load_dotenv
from tqdm import tqdm

from langchain.document_loaders import UnstructuredFileLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Chroma
from langchain.embeddings.openai import OpenAIEmbeddings

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
print("DEBUG OPENAI_KEY =", OPENAI_KEY)

DOCS_PATH = "data/docs"
documents = []

for filename in tqdm(os.listdir(DOCS_PATH)):
    file_path = os.path.join(DOCS_PATH, filename)
    if os.path.isfile(file_path):
        loader = UnstructuredFileLoader(file_path)
        docs = loader.load()
        documents.extend(docs)

splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
texts = splitter.split_documents(documents)

embedding = OpenAIEmbeddings(openai_api_key=OPENAI_KEY)

db = Chroma.from_documents(texts, embedding, persist_directory="vectorstore")
db.persist()
print("✅ Base vectorielle enregistrée dans 'vectorstore'")
