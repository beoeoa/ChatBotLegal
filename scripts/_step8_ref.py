from pathlib import Path
import json

# Read existing e2e_step12 for reference patterns
existing = Path("scripts/e2e_step12_ask_search_form_regression.py").read_text(encoding="utf-8")
print(f"Existing e2e_step12: {len(existing)} chars")
print("Has auth:", "def auth" in existing)
print("Has record:", "def record" in existing)
print("Has main:", "def main" in existing)

# Check API base URL
for line in existing.splitlines():
    if "API =" in line or "api_base" in line.lower() or "localhost" in line.lower():
        print(line[:120])
