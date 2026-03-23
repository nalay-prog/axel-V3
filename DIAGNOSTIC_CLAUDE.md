# 🔍 DIAGNOSTIC: Claude ne reformule plus dans le pipeline
## Problème identifié et solutions

### 🚨 Problème décrit
L'utilisateur signale que "Claude ne reformule plus n'agit plus dans le pipeline", suggérant que:
- Claude ne répond plus aux requêtes
- Le pipeline d'orchestration ne fonctionne plus correctement
- Les réponses ne sont plus générées par Claude

### 🔬 Diagnostic créé
J'ai créé un système de diagnostic complet pour identifier la cause racine:

#### Fichiers créés:
1. **`diagnose_claude.py`** - Script de diagnostic automatique
2. **`backend/core/orchestrator_v3_debug.py`** - Version debug de l'orchestrateur
3. **`switch_to_debug.sh`** - Script pour basculer en mode debug

### 🛠️ Solutions possibles identifiées

#### 1. **API Key Anthropic manquante/invalide**
```bash
# Vérifier la configuration
grep ANTHROPIC_API_KEY .env

# Doit ressembler à:
ANTHROPIC_API_KEY=sk-ant-api03-...
```

#### 2. **Agent online contourne l'orchestrateur**
Le problème principal identifié: `agent_online.py` utilise `claude_agent_optimized` pour faire sa propre synthèse, court-circuitant l'orchestrateur v3.

#### 3. **Version d'orchestrateur incorrecte**
L'API pourrait utiliser v1/v2 au lieu de v3.

### 🧪 Procédure de diagnostic

#### Étape 1: Test automatique
```bash
cd /Users/nalay/Desktop/Darwin_Agent_Final
python3 diagnose_claude.py
```

#### Étape 2: Mode debug (si nécessaire)
```bash
# Bascule en mode debug pour voir les logs détaillés
chmod +x switch_to_debug.sh
./switch_to_debug.sh

# Testez l'API maintenant (logs détaillés dans console)

# Revenez à la normale
./switch_to_normal.sh
```

#### Étape 3: Vérifications manuelles
```bash
# Vérifier que l'API utilise v3 par défaut
grep "DARWIN_DEFAULT_VERSION" backend/routes/api.py

# Vérifier les imports dans agent_online.py
grep "claude_agent" backend/agents/agent_online.py

# Tester un appel API simple
curl -X POST http://localhost:5050/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Test Claude", "darwin_version": "v3"}'
```

### 🎯 Solutions recommandées

#### Solution 1: Corriger agent_online.py
Modifier `agent_online.py` pour ne plus utiliser `claude_agent_optimized` directement, mais laisser l'orchestrateur gérer la synthèse.

#### Solution 2: Forcer l'utilisation de v3
S'assurer que l'API utilise toujours `darwin_version: "v3"`.

#### Solution 3: Patch temporaire
Utiliser les patches créés dans `orchestrator_v3_patches.py` pour contourner les problèmes.

### 📊 Résultats attendus du diagnostic

Le script `diagnose_claude.py` va tester:
- ✅ Configuration API key
- ✅ Appel Claude direct
- ✅ Fonctionnement de l'orchestrateur
- ✅ Imports et dépendances

### 🚀 Actions immédiates

1. **Exécutez le diagnostic**: `python3 diagnose_claude.py`
2. **Si API key problème**: Configurez `ANTHROPIC_API_KEY` dans `.env`
3. **Si orchestrateur problème**: Utilisez le mode debug
4. **Si agent_online problème**: Modifiez pour ne plus court-circuiter l'orchestrateur

### 📝 Notes techniques

- L'orchestrateur v3 appelle `_call_claude()` pour la synthèse finale
- `agent_online.py` ne devrait retourner que des données brutes, pas des réponses synthétisées
- Le pipeline est: Intent → Sources → Evidence Pack → Prompts → Claude → Validation → Réponse

Ce diagnostic devrait identifier exactement où le pipeline se casse.