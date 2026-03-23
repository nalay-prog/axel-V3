#!/usr/bin/env python3
"""
Test script pour vérifier si Claude fonctionne dans l'orchestrateur
"""

import sys
import os

# Configuration du path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from backend.core.orchestrator_v3 import _call_claude
    print("✅ Import de _call_claude réussi")

    print("🔄 Test de l'appel Claude...")
    result, meta = _call_claude(
        system_prompt="You are a helpful assistant.",
        user_prompt="Hello, respond with just 'OK' please."
    )

    print(f"📝 Result: '{result}'")
    print(f"📊 Meta: {meta}")

    if result and result.strip():
        print("✅ Claude fonctionne correctement!")
    else:
        print("❌ Claude n'a pas répondu ou réponse vide")
        if "warning" in meta:
            print(f"⚠️ Warning: {meta['warning']}")

except Exception as e:
    print(f"❌ Erreur lors du test: {e}")
    import traceback
    traceback.print_exc()