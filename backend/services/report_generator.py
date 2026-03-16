# backend/report_generator.py
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def generate_professional_report(produit: str, montant: float, profil_client: str) -> str:
    prompt = f"""
Tu es un Conseiller en Gestion de Patrimoine hautement expérimenté et certifié,
comportant les meilleures pratiques professionnelles du secteur.

Génère un **rapport d'adéquation complet et structuré** pour un client
ayant le profil suivant :

=== PROFIL CLIENT ===
{profil_client}

=== DONNÉES D'INVESTISSEMENT ===
Produit recommandé : {produit}
Montant investi : {montant} euros

Le rapport doit être **clairement structuré** selon les sections suivantes :

1) Page de garde (client, conseiller, date, produit, montant)
2) Contexte du client
3) Description détaillée du produit {produit}
4) Justification de l'adéquation entre le profil client et le produit
5) Analyse des risques
6) Comparaison objective avec 2 à 3 alternatives pertinentes
7) Considérations fiscales associées
8) Conclusion
9) Disclaimer réglementaire final

Chaque section doit contenir :
- Des titres clairs
- Des paragraphes arguments
- Des éléments chiffrés ou comparatifs quand c’est pertinent
- Un niveau de langage **pro et conforme métier**

Commence la sortie par :
\"**RAPPORT D'ADEQUATION – PRODUIT {produit} – {montant}€**\"
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=3000
    )

    return response.choices[0].message.content
