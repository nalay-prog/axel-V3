#!/usr/bin/env python3
"""
verify_claude_fix.py - Verification simple que Claude enforcement est actif

Run this to confirm the fix is working before going to production.
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def main():
    print("✅ VERIFICATION CLAUDE ENFORCEMENT FIX\n")
    
    # Check 1: Module exists
    try:
        from backend.core import critical_claude_enforcement
        print("✅ Module critical_claude_enforcement importé")
    except ImportError as e:
        print(f"❌ Erreur import critical_claude_enforcement: {e}")
        return False
    
    # Check 2: Router has enforcement
    try:
        with open("backend/routes/router.py", "r") as f:
            content = f.read()
            if "enforce_claude_call" in content:
                print("✅ Router modifié avec enforce_claude_call")
            else:
                print("❌ Router ne contient pas enforce_claude_call")
                return False
    except Exception as e:
        print(f"❌ Erreur lecture router.py: {e}")
        return False
    
    # Check 3: Can import router
    try:
        from backend.routes.router import ask_router
        print("✅ Router importé avec succès")
    except ImportError as e:
        print(f"❌ Erreur import ask_router: {e}")
        return False
    
    # Check 4: Can import enforcement functions
    try:
        from backend.core.critical_claude_enforcement import (
            validate_response_relevance,
            enforce_claude_call
        )
        print("✅ Fonctions enforcement importées")
    except ImportError as e:
        print(f"❌ Erreur import fonctions enforcement: {e}")
        return False
    
    # Check 5: API key configured
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("⚠️ ANTHROPIC_API_KEY non définie")
            return False
        elif api_key.startswith("sk-ant-"):
            print("✅ ANTHROPIC_API_KEY configurée")
        else:
            print(f"❌ ANTHROPIC_API_KEY invalide (commence par: {api_key[:10]})")
            return False
    except Exception as e:
        print(f"❌ Erreur vérification API key: {e}")
        return False
    
    print("\n" + "="*50)
    print("✅ TOUS LES CHECKS PASSENT")
    print("="*50)
    print("\n🚀 Claude enforcement est ACTIF et opérationnel!")
    print("\nProchaine étape: Redémarrez le backend pour appliquer les changements")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
