#!/bin/bash
# apply_claude_patch.sh - Applique automatiquement le patch Claude

echo "🔧 APPLICATION DU PATCH CLAUDE"
echo "=============================="

# Vérifier que le fichier existe
if [ ! -f "backend/core/orchestrator_v3.py" ]; then
    echo "❌ Fichier backend/core/orchestrator_v3.py non trouvé"
    exit 1
fi

# Sauvegarde
echo "📁 Création de la sauvegarde..."
cp backend/core/orchestrator_v3.py backend/core/orchestrator_v3.py.backup
echo "✅ Sauvegarde créée: backend/core/orchestrator_v3.py.backup"

# Ajout de l'import
echo "📝 Ajout de l'import du patch..."
if ! grep -q "from .claude_fallback_patch import _call_claude_patched" backend/core/orchestrator_v3.py; then
    # Trouver la ligne après les autres imports
    sed -i '' '/from \.\.agents\.agent_sql_kpi import ask_agent as ask_sql_kpi/a\
from .claude_fallback_patch import _call_claude_patched
' backend/core/orchestrator_v3.py
    echo "✅ Import ajouté"
else
    echo "⚠️ Import déjà présent"
fi

# Remplacement de l'appel
echo "🔄 Remplacement de l'appel _call_claude..."
sed -i '' 's/answer_raw, llm_meta = _call_claude(/answer_raw, llm_meta = _call_claude_patched(/g' backend/core/orchestrator_v3.py
echo "✅ Appel remplacé"

echo ""
echo "🎯 PATCH APPLIQUÉ AVEC SUCCÈS!"
echo ""
echo "📋 Ce que fait le patch:"
echo "   ✅ Logs détaillés pour diagnostiquer les problèmes Claude"
echo "   ✅ Essai de plusieurs modèles en fallback"
echo "   ✅ Support HTTP si la lib anthropic échoue"
echo "   ✅ Messages d'erreur explicites"
echo "   ✅ Métriques de performance"
echo ""
echo "🔄 Redémarrez maintenant votre backend pour appliquer les changements"
echo ""
echo "Pour annuler: cp backend/core/orchestrator_v3.py.backup backend/core/orchestrator_v3.py"