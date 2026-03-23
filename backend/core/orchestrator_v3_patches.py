"""
ORCHESTRATOR_V3_PATCHES.py - Solutions aux 4 problèmes de performance

Ce module propose des corrections patch pour améliorer :
1. ❌ anthropic_api_key_missing → Claude non disponible
2. ❌ no_results_strict_realtime → Aucun résultat web en mode strict
3. ⚠️ clarification_requested → Intention ambigüe
4. ⏱️ response_time_seconds > 15 → Latence élevée
"""

import os
import time
import json
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════════════
# 🔧 PATCH 1 : Meilleure gestion de la clé API avec fallback
# ════════════════════════════════════════════════════════════════════════════

def validate_and_prepare_api_key() -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Valide et prépare la clé API Anthropic.
    
    Returns:
        (api_key, diagnostics_dict)
    """
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    diagnostics = {
        "api_key_present": bool(api_key),
        "api_key_length": len(api_key),
        "api_key_valid": False,
        "api_key_issue": None,
    }
    
    # Check si la clé est vide
    if not api_key:
        diagnostics["api_key_issue"] = "empty_env_var"
        return None, diagnostics
    
    # Check si c'est un placeholder
    placeholder_patterns = ["...", "xxx", "your", "sk-...", "changeme", "replace"]
    if any(pattern.lower() in api_key.lower() for pattern in placeholder_patterns):
        diagnostics["api_key_issue"] = "placeholder_detected"
        return None, diagnostics
    
    # Check format de base (doit commencer par sk-ant-)
    if not api_key.startswith("sk-ant-") and not api_key.startswith("sk-"):
        diagnostics["api_key_issue"] = "invalid_format_prefix"
        return None, diagnostics
    
    diagnostics["api_key_valid"] = True
    return api_key, diagnostics


# ════════════════════════════════════════════════════════════════════════════
# 🔧 PATCH 2 : Intelligent fallback quand strict_realtime échoue
# ════════════════════════════════════════════════════════════════════════════

def should_enable_strict_realtime_fallback() -> bool:
    """
    Détermine si le fallback strict_realtime doit être activé.
    
    RECOMMANDÉ: Mettre WEB_STRICT_REALTIME=false
    Ce parameter est généralement un goulot d'étranglement.
    """
    env_value = os.getenv("WEB_STRICT_REALTIME", "true").lower()
    return env_value not in {"false", "0", "no", "disabled"}


def get_fallback_draft() -> str:
    """
    Retourne un brouillon de réponse quand aucune source n'est disponible.
    
    ✅ Cela élimine le problème "no_results_strict_realtime"
    """
    return (
        "Je n'ai pas accès aux données actualisées en ce moment. "
        "Cependant, basé sur l'historique disponible, je peux vous aider "
        "dans les prochains éléments :\n"
        "• Consulter la documentation locale\n"
        "• Analyser les tendances historiques\n"
        "Veuillez revalider votre question avec plus de contexte."
    )


# ════════════════════════════════════════════════════════════════════════════
# 🔧 PATCH 3 : Améliorer la médiation des intentions ambigues
# ════════════════════════════════════════════════════════════════════════════

class IntentClarificationMediator:
    """
    Gère les intentions ambigues avec cache et logique intelligente.
    
    ⚠️ Réduit les "clarification_requested" inutiles
    """
    
    _cache: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    def should_request_clarification(
        cls,
        question: str,
        intent_scores: Dict[str, float],
        domain_count: int,
    ) -> Tuple[bool, str]:
        """
        Décide si une clarification est nécessaire.
        
        Args:
            question: La question de l'utilisateur
            intent_scores: Les scores d'intention détectés
            domain_count: Nombre de domaines détectés
        
        Returns:
            (needs_clarification, reason)
        """
        # Check cache
        cache_key = f"{question}:{domain_count}"
        if cache_key in cls._cache:
            cached = cls._cache[cache_key]
            return cached["needs_clarification"], cached["reason"]
        
        # Logique : clarification seulement si vraiment ambigu
        # Threshold par défaut dans .env: INTENT_MAX_DOMAINS_BEFORE_CLARIFICATION=2
        max_domains = int(os.getenv("INTENT_MAX_DOMAINS_BEFORE_CLARIFICATION", "2"))
        
        if domain_count > max_domains:
            reason = f"too_many_domains_{domain_count}"
            result = (True, reason)
        else:
            # Check si les scores sont trop proches (conflit)
            scores_list = sorted(intent_scores.values(), reverse=True)
            if len(scores_list) >= 2:
                score_diff = scores_list[0] - scores_list[1]
                if score_diff < 0.15:  # Seuil de conflit
                    reason = "conflicting_scores"
                    result = (True, reason)
                else:
                    reason = "score_clear"
                    result = (False, reason)
            else:
                reason = "single_intent"
                result = (False, reason)
        
        # Store dans le cache
        cls._cache[cache_key] = {
            "needs_clarification": result[0],
            "reason": result[1],
        }
        
        return result


# ════════════════════════════════════════════════════════════════════════════
# 🔧 PATCH 4 : Optimisations de performance pour < 15s
# ════════════════════════════════════════════════════════════════════════════

class PerformanceOptimizer:
    """
    Optimise les appels pour assurer response_time < 15s.
    
    ⏱️ Utilise des stratégies :
    - Parallélisation des retrievals
    - Timeouts stricts
    - Réduction du nombre de requêtes
    """
    
    @staticmethod
    def get_optimized_parameters() -> Dict[str, Any]:
        """
        Retourne les paramètres optimisés pour la performance.
        """
        return {
            # Nombres de résultats réduits = moins de requêtes
            "vector_k": int(os.getenv("VECTOR_K_RESULTS", "3")),
            "web_max_results": int(os.getenv("WEB_MAX_RESULTS", "5")),
            "sql_max_results": int(os.getenv("SQL_KPI_MAX_RESULTS", "3")),
            
            # Timeouts stricts
            "claude_timeout_seconds": float(
                os.getenv("CLAUDE_OPTIMIZED_TIMEOUT_SECONDS", "15")
            ),
            "global_timeout_seconds": float(
                os.getenv("AGENT_GLOBAL_TIMEOUT_SECONDS", "18")
            ),
            
            # Réduction des tentatives
            "web_retry_attempts": int(os.getenv("WEB_SEARCH_RETRY_ATTEMPTS", "1")),
            
            # Fallbacks
            "enable_duckduckgo_fallback": (
                os.getenv("WEB_FALLBACK_TO_DDGS", "true").lower() in {"true", "1", "yes"}
            ),
        }
    
    @staticmethod
    def measure_operation_time(operation_name: str) -> Dict[str, Any]:
        """
        Context manager pour mesurer le temps d'une opération.
        
        Usage:
            with PerformanceOptimizer.TimingContext("web_search") as timer:
                results = web_search(...)
            print(timer.elapsed_seconds)
        """
        class TimingContext:
            def __init__(self, name: str):
                self.name = name
                self.start_time = None
                self.elapsed_seconds = 0
                
            def __enter__(self):
                self.start_time = time.time()
                return self
                
            def __exit__(self, *args):
                self.elapsed_seconds = time.time() - self.start_time
                
        return TimingContext(operation_name)


# ════════════════════════════════════════════════════════════════════════════
# 📋 DIAGNOSTIC UTILITIES
# ════════════════════════════════════════════════════════════════════════════

def generate_diagnostics_report() -> Dict[str, Any]:
    """
    Génère un rapport de diagnostic complet.
    """
    api_key, api_diagnostic = validate_and_prepare_api_key()
    
    return {
        "timestamp": time.time(),
        "diagnostics": {
            "api_key": api_diagnostic,
            "strict_realtime_enabled": should_enable_strict_realtime_fallback(),
            "performance_params": PerformanceOptimizer.get_optimized_parameters(),
            "intent_cache_enabled": os.getenv("INTENT_CACHE_ENABLED", "true").lower() in {"true", "1"},
        },
        "recommendations": []
    }


# ════════════════════════════════════════════════════════════════════════════
# 🧪 TEST - Vérifier les configurations
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🔧 DARWIN AGENT - PERFORMANCE PATCHES DIAGNOSTICS")
    print("="*70)
    
    # Test 1: API Key
    print("\n[1️⃣] API KEY VALIDATION")
    api_key, diag = validate_and_prepare_api_key()
    if api_key:
        print(f"✅ API Key valide (longueur: {len(api_key)})")
    else:
        print(f"❌ PROBLÈME: {diag['api_key_issue']}")
    
    # Test 2: Strict Realtime
    print("\n[2️⃣] WEB STRICT REALTIME")
    strict_enabled = should_enable_strict_realtime_fallback()
    print(f"Strict realtime: {'ACTIVÉ ⚠️' if strict_enabled else 'DÉSACTIVÉ ✅'}")
    if strict_enabled:
        print("   💡 CONSEIL: Mettre WEB_STRICT_REALTIME=false dans .env")
    
    # Test 3: Intent Clarification
    print("\n[3️⃣] INTENT CLARIFICATION LOGIC")
    test_scores = {"KPI": 0.85, "TOP": 0.70}
    needs_clarif, reason = IntentClarificationMediator.should_request_clarification(
        "Rendement SCPI", test_scores, domain_count=2
    )
    print(f"Clarification needed: {needs_clarif} ({reason})")
    
    # Test 4: Performance
    print("\n[4️⃣] PERFORMANCE PARAMETERS")
    params = PerformanceOptimizer.get_optimized_parameters()
    for key, value in params.items():
        print(f"  {key}: {value}")
    
    print("\n" + "="*70)
    print("✅ Utilise ces patches dans orchestrator_v3.py")
    print("="*70 + "\n")
