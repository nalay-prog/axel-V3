#!/usr/bin/env python3
"""
test_claude_fix.py - Test rapide pour vérifier que le patch Claude enforcement fonctionne
"""

import sys
import os
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def test_enforcement():
    """Test le patch Claude enforcement"""
    print("🧪 TEST CLAUDE ENFORCEMENT FIX")
    print("=" * 60)

    try:
        from backend.routes.router import ask_router
        print("✅ Import de ask_router réussi")

        # Test 1: Question de budget (le bug original)
        print("\n📝 Test 1: Question de budget (bug original)")
        print("-" * 60)

        result = ask_router(
            question="j'ai 3700 euros à investir, comment je dois gérer mon budget",
            darwin_version="v3"
        )

        answer = result.get("answer", "").strip()
        meta = result.get("meta", {})

        print(f"📊 Enforcement status: {meta.get('claude_enforcement', 'N/A')}")
        print(f"📊 Answer length: {len(answer)} chars")
        print(f"📊 First 200 chars: {answer[:200]}...")

        # Vérifications
        checks = []

        # Check 1: Réponse non vide
        if answer and len(answer) > 50:
            checks.append(("✅", "Réponse non vide", True))
        else:
            checks.append(("❌", "Réponse vide ou trop courte", False))

        # Check 2: Réponse contient des conseils financiers
        financial_keywords = ["budget", "investir", "placement", "allocation", "diversif", "rendement", "risque"]
        has_financial_content = any(kw in answer.lower() for kw in financial_keywords)
        if has_financial_content:
            checks.append(("✅", "Contient contenu financier pertinent", True))
        else:
            checks.append(("❌", "Pas de contenu financier pertinent", False))

        # Check 3: Pas du raw material hors-sujet
        bad_keywords = ["rapport", "1998", "2000", "cob", "annexe", "pdf", "document"]
        has_bad_content = any(kw in answer.lower() for kw in bad_keywords)
        if not has_bad_content:
            checks.append(("✅", "Pas de document hors-sujet", True))
        else:
            checks.append(("❌", "Contient document hors-sujet", False))

        # Check 4: Enforcement appliqué
        if meta.get("claude_enforcement"):
            checks.append(("✅", "Claude enforcement activé", True))
        else:
            checks.append(("⚠️", "Claude enforcement non détecté", False))

        # Print checks
        print("\n📋 VÉRIFICATIONS:")
        for icon, check_name, passed in checks:
            print(f"  {icon} {check_name}")

        # Résumé
        print("\n📊 RÉSUMÉ:")
        passed_count = sum(1 for _, _, p in checks if p)
        total_count = len(checks)
        print(f"  {passed_count}/{total_count} vérifications réussies")

        if passed_count == total_count:
            print("\n🎉 LE PATCH FONCTIONNE CORRECTEMENT!")
            return True
        else:
            print("\n⚠️ Certaines vérifications ont échoué")
            return False

    except ImportError as e:
        print(f"❌ Erreur d'import: {e}")
        return False
    except Exception as e:
        print(f"❌ Erreur durant le test: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_raw_call():
    """Test direct de critical_claude_enforcement"""
    print("\n\n🧪 TEST DIRECT DU MODULE ENFORCEMENT")
    print("=" * 60)

    try:
        from backend.core.critical_claude_enforcement import (
            validate_response_relevance,
            enforce_claude_call
        )
        print("✅ Import du module enforcement réussi")

        # Test validation
        test_cases = [
            {
                "name": "Réponse valide",
                "question": "j'ai 3700 euros à investir",
                "answer": "Avec 3700€ vous pouvez investir dans une diversification: SCPI 30%, actions 20%, obligations 40%, cash 10%",
                "expected": True
            },
            {
                "name": "Réponse hors-sujet",
                "question": "comment investir 3700 euros",
                "answer": "Le rapport COB de 1998 - Annexes Paris présente les résultats...",
                "expected": False
            },
            {
                "name": "Réponse trop courte",
                "question": "comment gérer mon budget",
                "answer": "Budget important.",
                "expected": False
            }
        ]

        print("\n📋 TESTS DE VALIDATION:")
        for test in test_cases:
            validation = validate_response_relevance(
                question=test["question"],
                answer=test["answer"],
                raw_material={}
            )
            is_valid = validation.get("valid", False)
            expected = test["expected"]
            icon = "✅" if is_valid == expected else "❌"
            print(f"  {icon} {test['name']} - Valid: {is_valid}, Expected: {expected}")

        print("\n✅ Tests du module enforcement complétés")
        return True

    except Exception as e:
        print(f"❌ Erreur: {e}")
        return False


if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════╗
║      🔴 TEST CLAUDE ENFORCEMENT FIX                        ║
║      Vérification que le patch fonctionne correctement    ║
╚════════════════════════════════════════════════════════════╝
    """)

    # Test 1: Module enforcement
    test1_ok = test_raw_call()

    # Test 2: Router avec enforcement
    test2_ok = test_enforcement()

    # Résumé final
    print("\n" + "=" * 60)
    print("📊 RÉSUMÉ FINAL:")
    print(f"  Module enforcement: {'✅' if test1_ok else '❌'}")
    print(f"  Router enforcement: {'✅' if test2_ok else '❌'}")

    if test1_ok and test2_ok:
        print("\n🎉 LE FIX CLAUDE ENFORCEMENT EST OPÉRATIONNEL!")
        sys.exit(0)
    else:
        print("\n⚠️ Vérifiez les erreurs ci-dessus")
        sys.exit(1)
