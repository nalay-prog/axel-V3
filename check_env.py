import os
from dotenv import load_dotenv, find_dotenv

print("📦 Chemin .env trouvé :", find_dotenv())
load_dotenv(find_dotenv())

clé = os.getenv("OPENAI_API_KEY")
print("🔐 Clé API lue :", clé)

if clé is None:
    print("⛔ ERREUR : la clé est vide")
else:
    print("✅ La clé est bien chargée")
