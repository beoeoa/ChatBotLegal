from pathlib import Path

p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")

# Fix: search router mounted at /api prefix, so full path is /api/search/ask
old = '''        # Try common ask endpoints (search router mounted at /api prefix)
        endpoints = [
            f"{API}/search/ask",
            f"{API}/search/ask/simple",
        ]'''
new = '''        # Try common ask endpoints (search router mounted at /api prefix)
        endpoints = [
            f"{API}/api/search/ask",
            f"{API}/api/search/ask/simple",
        ]'''
if old in text:
    text = text.replace(old, new, 1)
    print("OK: fixed to /api/search/ask")
else:
    print("WARN: endpoint block not found")

# Also fix health/config check
old_health = '''            h = client.get(f"{API}/config", headers=auth("admin"), timeout=10.0)'''
new_health = '''            h = client.get(f"{API}/api/config", headers=auth("admin"), timeout=10.0)'''
if old_health in text:
    text = text.replace(old_health, new_health, 1)
    print("OK: fixed config endpoint")

p.write_text(text, encoding="utf-8", newline="\n")
import ast
ast.parse(p.read_text(encoding="utf-8"))
print("syntax OK")
