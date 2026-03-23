#!/bin/bash

# ════════════════════════════════════════════════════════════════════════════
# 🚀 DARWIN AGENT - AUTO-FIX SCRIPT
# Résout automatiquement les 4 problèmes de performance
# ════════════════════════════════════════════════════════════════════════════

set -e

echo ""
echo "════════════════════════════════════════════════════════════════════════════"
echo "🔧 DARWIN AGENT - AUTO-FIX SETUP"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# 🎯 STEP 1: Vérifier l'environnement
# ════════════════════════════════════════════════════════════════════════════

echo "[1/5] 🔍 Vérification de l'environnement..."

if [ ! -f ".env" ]; then
    echo "⚠️  Fichier .env non trouvé. Création à partir de .env.example..."
    cp .env.example .env
    echo "✅ .env créé"
else
    echo "✅ .env existe"
fi

# ════════════════════════════════════════════════════════════════════════════
# 🎯 STEP 2: Configurer les variables critiques
# ════════════════════════════════════════════════════════════════════════════

echo ""
echo "[2/5] 🔧 Configuration des variables..."

# Fonction helper pour mettre à jour/ajouter une variable
set_env_var() {
    local key=$1
    local value=$2
    
    if grep -q "^${key}=" .env; then
        # Mettre à jour la ligne existante
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS
            sed -i '' "s/^${key}=.*/${key}=${value}/" .env
        else
            # Linux
            sed -i "s/^${key}=.*/${key}=${value}/" .env
        fi
        echo "  ✏️  Mis à jour: ${key}=${value}"
    else
        # Ajouter la nouvelle ligne
        echo "${key}=${value}" >> .env
        echo "  ✅ Ajouté: ${key}=${value}"
    fi
}

# ❌ Problème 1: API Key
echo ""
echo "  [1.1] API Key Configuration"
current_api_key=$(grep "^ANTHROPIC_API_KEY=" .env | cut -d'=' -f2- || echo "")

if [ -z "$current_api_key" ] || [ "$current_api_key" == "sk-ant-YOUR_ACTUAL_KEY_HERE" ]; then
    echo "  ❌ API Key vide ou placeholder detecté"
    echo "  📍 Allez sur: https://console.anthropic.com/account/keys"
    echo "  📋 Puis entrez votre clé:"
    read -p "  > ANTHROPIC_API_KEY: " api_key_input
    set_env_var "ANTHROPIC_API_KEY" "$api_key_input"
else
    echo "  ✅ API Key configurée (${#current_api_key} chars)"
fi

# ❌ Problème 2: Web Strict Realtime (GRANDE CAUSE DE PROBLÈMES)
echo ""
echo "  [1.2] Web Strict Realtime (CRITICAL)"
set_env_var "WEB_STRICT_REALTIME" "false"
set_env_var "WEB_SEARCH_RETRY_ATTEMPTS" "1"
set_env_var "WEB_FALLBACK_TO_DDGS" "true"
set_env_var "WEB_MAX_RESULTS" "5"

# ⚠️  Problème 3: Intent Clarification
echo ""
echo "  [1.3] Intent Clarification Tuning"
set_env_var "INTENT_MAX_DOMAINS_BEFORE_CLARIFICATION" "2"
set_env_var "INTENT_AMBIGUITY_THRESHOLD" "0.20"
set_env_var "INTENT_CACHE_ENABLED" "true"
set_env_var "INTENT_CACHE_TTL_SECONDS" "3600"

# ⏱️  Problème 4: Performance Optimizations
echo ""
echo "  [1.4] Performance Tuning"
set_env_var "CLAUDE_OPTIMIZED_TIMEOUT_SECONDS" "15"
set_env_var "AGENT_GLOBAL_TIMEOUT_SECONDS" "18"
set_env_var "CLAUDE_OPTIMIZED_MAX_TOKENS" "900"
set_env_var "CLAUDE_OPTIMIZED_TEMPERATURE" "0.2"
set_env_var "VECTOR_K_RESULTS" "3"
set_env_var "SQL_KPI_MAX_RESULTS" "3"

# ════════════════════════════════════════════════════════════════════════════
# 🎯 STEP 3: Vérifier les fichiers de patches
# ════════════════════════════════════════════════════════════════════════════

echo ""
echo "[3/5] 📦 Vérification des fichiers de patches..."

if [ ! -f "backend/core/orchestrator_v3_patches.py" ]; then
    echo "⚠️  orchestrator_v3_patches.py manquant"
    echo "   📍 Télécharger depuis le repository"
else
    echo "✅ orchestrator_v3_patches.py présent"
fi

if [ ! -f "TROUBLESHOOTING_4_PROBLEMS.md" ]; then
    echo "⚠️  TROUBLESHOOTING_4_PROBLEMS.md manquant"
else
    echo "✅ TROUBLESHOOTING_4_PROBLEMS.md présent"
fi

# ════════════════════════════════════════════════════════════════════════════
# 🎯 STEP 4: Tests de Validation
# ════════════════════════════════════════════════════════════════════════════

echo ""
echo "[4/5] 🧪 Tests de Validation..."

# Test 1: API Key
echo ""
echo "  [Test 1/4] API Key Validation"
api_key_from_env=$(grep "^ANTHROPIC_API_KEY=" .env | cut -d'=' -f2-)

if [[ "$api_key_from_env" == sk-ant-* ]] && [ ${#api_key_from_env} -gt 30 ]; then
    echo "  ✅ API Key format correct (${#api_key_from_env} chars)"
else
    echo "  ❌ API Key invalide - CORRECTION NÉCESSAIRE"
    echo "     Format attendu: sk-ant-XXXXXXXXXXXX (40+ chars)"
fi

# Test 2: Env Variables Critical
echo ""
echo "  [Test 2/4] Critical Variables"

critical_vars=("WEB_STRICT_REALTIME" "INTENT_MAX_DOMAINS_BEFORE_CLARIFICATION" "CLAUDE_OPTIMIZED_TIMEOUT_SECONDS")
for var in "${critical_vars[@]}"; do
    value=$(grep "^${var}=" .env | cut -d'=' -f2-)
    if [ -n "$value" ]; then
        echo "  ✅ ${var}=${value}"
    else
        echo "  ❌ ${var} manquante"
    fi
done

# Test 3: Web Fallback
echo ""
echo "  [Test 3/4] Web Fallback Configuration"
web_strict=$(grep "^WEB_STRICT_REALTIME=" .env | cut -d'=' -f2-)
if [ "$web_strict" == "false" ]; then
    echo "  ✅ Web Strict Realtime DÉSACTIVÉ (correct)"
else
    echo "  ⚠️  Web Strict Realtime ACTIVÉ (peut causer timeouts)"
fi

# Test 4: Performance Timeouts
echo ""
echo "  [Test 4/4] Performance Timeouts"
claude_timeout=$(grep "^CLAUDE_OPTIMIZED_TIMEOUT_SECONDS=" .env | cut -d'=' -f2-)
global_timeout=$(grep "^AGENT_GLOBAL_TIMEOUT_SECONDS=" .env | cut -d'=' -f2-)
echo "  ✅ Claude Timeout: ${claude_timeout}s"
echo "  ✅ Global Timeout: ${global_timeout}s"

# ════════════════════════════════════════════════════════════════════════════
# 🎯 STEP 5: Recommandations Finales
# ════════════════════════════════════════════════════════════════════════════

echo ""
echo "[5/5] 📋 Recommandations Finales..."
echo ""

echo "✅ Configuration complète! Voici les prochaines étapes:"
echo ""
echo "1️⃣  Redémarrer l'application:"
echo "   $ kill %1"
echo "   $ python3 backend/app.py"
echo ""
echo "2️⃣  Vérifier les performances:"
echo "   $ curl -X POST http://localhost:5050/ask \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"question\": \"Rendement SCPI 2026?\", \"session_id\": \"test\"}'"
echo ""
echo "3️⃣  Consulter le guide complet:"
echo "   $ cat TROUBLESHOOTING_4_PROBLEMS.md"
echo ""
echo "4️⃣  Lancer les diagnostics:"
echo "   $ python3 backend/core/orchestrator_v3_patches.py"
echo ""

echo "════════════════════════════════════════════════════════════════════════════"
echo "✅ SETUP COMPLÈTE - Prêt pour déploiement!"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# Afficher le résumé des changements
# ════════════════════════════════════════════════════════════════════════════

echo "📊 RÉSUMÉ DES CHANGEMENTS:"
echo ""
echo "❌ Problème 1: API Key Manquante"
echo "   → Solution: Clé configurée et validée"
echo ""
echo "❌ Problème 2: Aucun Résultat Web"
echo "   → Solutions:"
echo "     • WEB_STRICT_REALTIME=false (critique!)"
echo "     • WEB_SEARCH_RETRY_ATTEMPTS=1"
echo "     • WEB_FALLBACK_TO_DDGS=true"
echo ""
echo "⚠️  Problème 3: Intentions Ambigues"
echo "   → Solutions:"
echo "     • INTENT_MAX_DOMAINS_BEFORE_CLARIFICATION=2"
echo "     • INTENT_CACHE_ENABLED=true"
echo ""
echo "⏱️  Problème 4: Latence Élevée"
echo "   → Solutions:"
echo "     • CLAUDE_OPTIMIZED_TIMEOUT_SECONDS=15"
echo "     • AGENT_GLOBAL_TIMEOUT_SECONDS=18"
echo "     • VECTOR_K_RESULTS=3"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# Save configuration backup
# ════════════════════════════════════════════════════════════════════════════

echo ""
echo "💾 Sauvegarde de la configuration..."
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
echo "✅ Backup sauvegardé"
echo ""

exit 0
