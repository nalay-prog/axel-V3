# rapport_adéquation.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

llm = ChatOpenAI(
    model_name="gpt-4",
    temperature=0.3,
    openai_api_key=OPENAI_API_KEY
)

def generer_rapport_adequation(produit, montant, profil_client):
    prompt = f"""
Tu es un conseiller en gestion de patrimoine expert. Rédige un rapport d'adéquation conforme aux standards CGP pour un client au profil suivant :

- Profil client : {profil_client}
- Montant investi : {montant} €
- Produit : {produit}

Le rapport doit comprendre :

1. Présentation du client
2. Objectifs de l’investissement
3. Description du produit {produit}
4. Justification de l’adéquation avec le profil
5. Risques éventuels
6. Conclusion

Sois clair, structuré et professionnel.
"""

    return llm.invoke(prompt).content
