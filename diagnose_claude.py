#!/usr/bin/env python3
"""
Script de diagnostic pour identifier pourquoi Claude ne fonctionne plus dans le pipeline
"""

import sys
import os

# Configuration du path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def test_claude_direct():
    """Test direct de l'appel Claude"""
    print("🔬 TEST 1: Appel Claude direct")
    print("=" * 50)

    try:
        from backend.core.orchestrator_v3 import _call_claude
        print("✅ Import de _call_claude réussi")

        result, meta = _call_claude(
            system_prompt="You are a helpful assistant. Respond briefly.",
            user_prompt="Hello, say 'OK' if you can read this."
        )

        print(f"📝 Result: '{result}'")
        print(f"📊 Meta: {meta}")

        if result and result.strip():
            print("✅ Claude fonctionne correctement!")
            return True
        else:
            print("❌ Claude n'a pas répondu ou réponse vide")
            if "warning" in meta:
                print(f"⚠️ Warning: {meta['warning']}")
            return False

    except Exception as e:
        print(f"❌ Erreur lors du test direct: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_orchestrator_debug():
    """Test de l'orchestrateur en mode debug"""
    print("\n🔬 TEST 2: Orchestrateur v3 Debug")
    print("=" * 50)

    try:
        from backend.core.orchestrator_v3_debug import orchestrate_v3_debug
        print("✅ Import de orchestrate_v3_debug réussi")

        result = orchestrate_v3_debug("Quel est le taux de distribution de Darwin RE01 ?")

        print(f"📝 Answer: {result['answer'][:200]}...")
        print(f"📊 Meta: {result.get('meta', {})}")

        if result['answer'] and result['answer'].strip():
            print("✅ Orchestrateur fonctionne!")
            return True
        else:
            print("❌ Orchestrateur n'a pas produit de réponse")
            return False

    except Exception as e:
        print(f"❌ Erreur lors du test orchestrateur: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_api_key_config():
    """Test de la configuration de l'API key"""
    print("\n🔬 TEST 3: Configuration API Key")
    print("=" * 50)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY non définie")
        return False

    from backend.core.orchestrator_v3 import _is_placeholder_key
    if _is_placeholder_key(api_key):
        print("❌ ANTHROPIC_API_KEY est un placeholder")
        return False

    print(f"✅ ANTHROPIC_API_KEY configurée (longueur: {len(api_key)})")
    print(f"🔑 Commence par: {api_key[:10]}...")

    # Test d'autres variables
    model = os.getenv("CLAUDE_OPTIMIZED_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
    print(f"🤖 Model: {model}")

    return True

def test_imports():
    """Test des imports nécessaires"""
    print("\n🔬 TEST 4: Imports et dépendances")
    print("=" * 50)

    try:
        import anthropic
        print(f"✅ anthropic importé (version: {getattr(anthropic, '__version__', 'unknown')})")
    except ImportError:
        print("❌ anthropic non installé")

    try:
        import httpx
        print(f"✅ httpx importé (version: {getattr(httpx, '__version__', 'unknown')})")
    except ImportError:
        print("⚠️ httpx non installé (utilisera urllib fallback)")

    # Test des agents
    try:
        from backend.agents.agent_online import web_search
        print("✅ agent_online.web_search importé")
    except ImportError as e:
        print(f"❌ agent_online.web_search import failed: {e}")

    try:
        from backend.core.evidence_pack import build_evidence_pack
        print("✅ evidence_pack.build_evidence_pack importé")
    except ImportError as e:
        print(f"❌ evidence_pack import failed: {e}")

    try:
        from backend.core.prompt_builder import build_prompt
        print("✅ prompt_builder.build_prompt importé")
    except ImportError as e:
        print(f"❌ prompt_builder import failed: {e}")

def main():
    print("🔍 DIAGNOSTIC COMPLET - Pourquoi Claude ne fonctionne plus?")
    print("=" * 60)

    # Test de la configuration
    api_ok = test_api_key_config()

    # Test des imports
    test_imports()

    # Test Claude direct
    claude_ok = test_claude_direct()

    # Test orchestrateur debug
    orchestrator_ok = test_orchestrator_debug()

    print("\n" + "=" * 60)
    print("📋 RÉSULTATS DU DIAGNOSTIC:")
    print(f"   API Key: {'✅' if api_ok else '❌'}")
    print(f"   Claude Direct: {'✅' if claude_ok else '❌'}")
    print(f"   Orchestrateur: {'✅' if orchestrator_ok else '❌'}")

    if not api_ok:
        print("\n💡 SOLUTION: Configurez ANTHROPIC_API_KEY dans .env")
    elif not claude_ok:
        print("\n💡 SOLUTION: Vérifiez que l'API key Anthropic est valide")
    elif not orchestrator_ok:
        print("\n💡 SOLUTION: Problème dans le pipeline de l'orchestrateur")
    else:
        print("\n✅ Tout semble fonctionner!")

if __name__ == "__main__":
    main()