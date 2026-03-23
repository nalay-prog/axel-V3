#!/bin/bash
# commit_claude_fix.sh - Commit automatique du fix Claude

echo "📝 COMMIT CLAUDE ENFORCEMENT FIX"
echo "==============================="
echo ""

# Check if git is available
if ! command -v git &> /dev/null; then
    echo "❌ Git n'est pas installé"
    exit 1
fi

# Check if we're in a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "❌ Ce répertoire n'est pas un dépôt git"
    exit 1
fi

# Verify files exist
files_to_add=(
    "backend/core/critical_claude_enforcement.py"
    "backend/routes/router.py"
    "test_claude_fix.py"
    "verify_claude_fix.py"
    "quick_fix_claude.sh"
    "CLAUDE_FIX_SUMMARY.md"
    "BEFORE_AFTER_CLAUDE_FIX.md"
    "ACTION_NOW_CLAUDE_FIX.md"
    "CHANGELOG_CLAUDE_FIX.md"
    "diagnose_claude.py"
)

echo "📋 Fichiers à commiter:"
for file in "${files_to_add[@]}"; do
    if [ -f "$file" ]; then
        echo "  ✅ $file"
    else
        echo "  ⚠️ $file (non trouvé)"
    fi
done

echo ""
echo "🔄 Stage changes..."
git add backend/core/critical_claude_enforcement.py 2>/dev/null
git add backend/routes/router.py 2>/dev/null
git add test_claude_fix.py 2>/dev/null
git add verify_claude_fix.py 2>/dev/null
git add quick_fix_claude.sh 2>/dev/null
git add CLAUDE_FIX_SUMMARY.md 2>/dev/null
git add BEFORE_AFTER_CLAUDE_FIX.md 2>/dev/null
git add ACTION_NOW_CLAUDE_FIX.md 2>/dev/null
git add CHANGELOG_CLAUDE_FIX.md 2>/dev/null
git add diagnose_claude.py 2>/dev/null

echo "✅ Changes staged"

echo ""
echo "📝 Commit message:"
commit_message="🔴 [CRITICAL] Fix Claude enforcement in pipeline

ISSUE: Claude not reformulating answers, returning raw material
EXAMPLE: Q: invest 3700€ → A: COB Report 1998 (off-topic)

SOLUTION: Critical Claude enforcement wrapper
- Validates all Claude responses for pertinence
- Auto-reformulates if invalid
- Intelligent fallback if error
- Detailed logs for debugging

FILES ADDED:
- backend/core/critical_claude_enforcement.py (NEW)
- backend/routes/router.py (MODIFIED)
- test_claude_fix.py, verify_claude_fix.py (NEW)
- Documentation and verification scripts (NEW)

VERIFICATION:
  python3 verify_claude_fix.py
  python3 test_claude_fix.py

DEPLOYMENT:
  1. ./quick_fix_claude.sh
  2. Restart backend
  3. Test with investment questions

GUARANTEE: Claude WILL synthesize all responses, never raw material"

echo "$commit_message"
echo ""

# Create the commit
git commit -m "🔴 [CRITICAL] Fix Claude enforcement in pipeline

ISSUE: Claude not reformulating answers, returning raw material
EXAMPLE: Q: invest 3700€ → A: COB Report 1998 (off-topic)

SOLUTION: Critical Claude enforcement wrapper
- Validates all Claude responses for pertinence
- Auto-reformulates if invalid
- Intelligent fallback if error
- Detailed logs for debugging

FILES:
- backend/core/critical_claude_enforcement.py (validation module)
- backend/routes/router.py (enforcement integration)
- test_claude_fix.py, verify_claude_fix.py (testing)
- Documentation and guides added

VERIFICATION:
  python3 verify_claude_fix.py
  python3 test_claude_fix.py

DEPLOYMENT:
  1. ./quick_fix_claude.sh
  2. Restart backend
  3. Test with investment questions

GUARANTEE: Claude WILL synthesize all responses correctly"

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Commit créé avec succès!"
    echo ""
    echo "📊 Changements:"
    git log -1 --stat --oneline
    echo ""
    echo "🚀 Prochaine étape: git push"
else
    echo "❌ Erreur lors du commit"
    exit 1
fi