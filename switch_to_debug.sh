#!/bin/bash
# switch_to_debug.sh - Bascule temporairement vers l'orchestrateur de debug
# pour diagnostiquer les problèmes avec Claude

echo "🔧 BASCULE VERS ORCHESTRATEUR DEBUG"
echo "==================================="

# Sauvegarde de l'original
cp backend/routes/router.py backend/routes/router.py.backup

# Modification temporaire du routeur pour utiliser la version debug
sed -i '' 's/from ..core.orchestrator_v3 import orchestrate_v3/from ..core.orchestrator_v3_debug import orchestrate_v3_debug as orchestrate_v3/g' backend/routes/router.py

echo "✅ Routeur modifié pour utiliser orchestrate_v3_debug"
echo ""
echo "🧪 Testez maintenant l'API - les logs détaillés apparaîtront"
echo ""
echo "Pour revenir à la normale, exécutez: ./switch_to_normal.sh"

# Script de retour à la normale
cat > switch_to_normal.sh << 'EOF'
#!/bin/bash
echo "🔄 RETOUR À L'ORCHESTRATEUR NORMAL"
cp backend/routes/router.py.backup backend/routes/router.py
rm backend/routes/router.py.backup
echo "✅ Routeur restauré"
EOF

chmod +x switch_to_normal.sh