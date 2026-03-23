#!/usr/bin/env python3
"""
deployment_status.py - Check deployment status and next steps
"""

import os
import sys

def check_status():
    """Check if Claude fix is deployed"""
    print("🔍 DEPLOYMENT STATUS CHECK")
    print("=" * 60)
    
    checks = {
        "critical_claude_enforcement.py": ("backend/core/critical_claude_enforcement.py", "Module enforcement"),
        "Router modified": ("backend/routes/router.py", "Router enforcement"),
        "Test suite": ("test_claude_fix.py", "Tests"),
        "Verification script": ("verify_claude_fix.py", "Pre-deploy check"),
        "Installation script": ("quick_fix_claude.sh", "Auto-install"),
        "Documentation": ("CLAUDE_FIX_SUMMARY.md", "Main docs"),
        "Action guide": ("ACTION_NOW_CLAUDE_FIX.md", "Quick start"),
        "Before/After": ("BEFORE_AFTER_CLAUDE_FIX.md", "Examples"),
        "Changelog": ("CHANGELOG_CLAUDE_FIX.md", "Changes"),
        "Diagnostics": ("diagnose_claude.py", "Diagnostic tool"),
    }
    
    all_ok = True
    
    print("\n📋 FILES CHECK:")
    for check_name, (file_path, description) in checks.items():
        exists = os.path.exists(file_path)
        icon = "✅" if exists else "❌"
        print(f"  {icon} {check_name:30} - {description}")
        if not exists:
            all_ok = False
    
    # Check router modification
    print("\n⚙️ ROUTER MODIFICATION:")
    try:
        with open("backend/routes/router.py", "r") as f:
            content = f.read()
            has_import = "enforce_claude_call" in content
            has_enforcement = "enforce_claude_call(" in content
            
        if has_import and has_enforcement:
            print("  ✅ Router has enforce_claude_call imported")
            print("  ✅ Router uses enforce_claude_call wrapper")
        else:
            print("  ❌ Router not properly modified")
            all_ok = False
    except Exception as e:
        print(f"  ❌ Error checking router: {e}")
        all_ok = False
    
    # Check API key
    print("\n🔑 CONFIGURATION:")
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        if api_key.startswith("sk-ant-"):
            print(f"  ✅ ANTHROPIC_API_KEY configured")
        else:
            print(f"  ⚠️ ANTHROPIC_API_KEY suspicious: {api_key[:20]}...")
    else:
        print(f"  ❌ ANTHROPIC_API_KEY not set in environment")
    
    # Status summary
    print("\n" + "=" * 60)
    if all_ok:
        print("✅ CLAUDE FIX DEPLOYMENT READY")
        print("\nNEXT STEPS:")
        print("1. Run: python3 verify_claude_fix.py")
        print("2. Restart backend")
        print("3. Test with: python3 test_claude_fix.py")
        return 0
    else:
        print("❌ SOME FILES MISSING OR ISSUES DETECTED")
        print("\nTO FIX:")
        print("1. Run: ./quick_fix_claude.sh")
        print("2. Or run: ./commit_claude_fix.sh and git push")
        return 1

if __name__ == "__main__":
    sys.exit(check_status())
