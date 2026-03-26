#!/usr/bin/env python3
"""Test: Vérifier que Claude reformule vraiment et n'emprunte pas du brut"""

from backend.routes.router import ask_router

question = "SCPI rendement 2024"
print(f"Q: {question}\n")

result = ask_router(question=question, history=[])

# Vérifier ce qui se passe réellement
answer = result.get("answer", "")
sources = result.get("sources", [])

print("=" * 70)
print("SOURCES UTILISÉES:")
for i, src in enumerate(sources[:3], 1):
    if isinstance(src, dict):
        print(f"{i}. {src.get('source', 'unknown')}")
        print(f"   Domain: {src.get('domain')}")

print("\n" + "=" * 70)
print("RÉPONSE DE L'AGENT:")
print(answer[:600])

print("\n" + "=" * 70)
print("DIAGNOSTIC:")

# Est-ce que c'est du contenu brut ou une synthèse intelligent?
if "Analyse:" in answer or "Recommandation:" in answer or "Stratégie" in answer:
    print("✅ CLAUDE A REFORMULÉ - réponse structurée")
elif len(answer) > 300 and "\n" in answer:
    print("⚠️ CONTENU SIGNIFICATIF - mais vérifier si synthétisé")
else:
    print("❌ CONTENU BRUT - Claude ne reformule pas!")

# Vérifier si c'est juste du copier-coller
if answer.startswith("SCPI") or answer.startswith("2024"):
    print("❌ COMMENCE PAR UN MOT-CLÉ - signe de copier-coller")
    
print("\n✅ Résultat: Claude DOIT être le cerveau qui reformule intelligemment")
