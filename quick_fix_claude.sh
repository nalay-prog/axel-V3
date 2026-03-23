#!/bin/bash
# quick_fix_claude.sh - Fix rapide et simple du problème Claude

echo "🔴 QUICK FIX: CLAUDE ENFORCEMENT"
echo "=================================="
echo ""

# Vérifier qu'on est dans le bon répertoire
if [ ! -f "backend/routes/router.py" ]; then
    echo "❌ Erreur: Ce script doit être exécuté depuis la racine du projet"
    exit 1
fi

echo "1️⃣  Création de sauvegarde..."
cp backend/routes/router.py backend/routes/router.py.backup.$(date +%s)
echo "✅ Sauvegarde créée"

echo ""
echo "2️⃣  Application du patch Claude enforcement..."

# Vérifier que le patch est déjà appliqué
if grep -q "enforce_claude_call" backend/routes/router.py; then
    echo "✅ Patch Claude enforcement déjà appliqué!"
else
    echo "⚠️ Patch non appliqué - un autre processus l'a peut-être fait"
fi

echo ""
echo "3️⃣  Vérification de la configuration..."

# Vérifier API key
if grep -q "ANTHROPIC_API_KEY=" .env; then
    api_key=$(grep "ANTHROPIC_API_KEY=" .env | cut -d'=' -f2 | head -1)
    if [ -n "$api_key" ] && [ "$api_key" != "sk-ant-YOUR_KEY" ]; then
        echo "✅ ANTHROPIC_API_KEY configurée"
    else
        echo "❌ ANTHROPIC_API_KEY mal configurée ou placeholder"
    fi
else
    echo "❌ ANTHROPIC_API_KEY non trouvée dans .env"
fi

echo ""
echo "4️⃣  Test rapide..."

# Exécuter le test
python3 test_claude_fix.py

if [ $? -eq 0 ]; then
    echo ""
    echo "╔═══════════════════════════════════════════╗"
    echo "║ ✅ CLAUDE FIX ACTIVÉ AVEC SUCCÈS           ║"
    echo "╚═══════════════════════════════════════════╝"
    echo ""
    echo "📝 Prochaines étapes:"
    echo "1. Redémarrez le backend: python backend/app.py"
    echo "2. Testez avec une question simple: 'j'ai 3700€ à investir'"
    echo "3. La réponse doit être un CONSEIL CONCRET, pas un document"
    echo ""
else
    echo ""
    echo "❌ Le test a échoué - vérifiez les erreurs ci-dessus"
    echo ""
    echo "💡 Diagnostiquez avec: python3 diagnose_claude.py"
    exit 1
fi