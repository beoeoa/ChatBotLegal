import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

# Inspect StreamingResponse procedure/forms section
p = Path("frontend/src/components/search/StreamingResponse.tsx")
lines = p.read_text(encoding="utf-8").splitlines()
for i, line in enumerate(lines, 1):
    if 380 <= i <= 460:
        print(f"{i}: {line}")

print("\n=== search.py procedure match ===")
sp = Path("api/routers/search.py")
text = sp.read_text(encoding="utf-8")
for key in ["_match_procedure", "procedure_detail", "recommended_forms", "HAI_PHONG_PROCEDURES", "faq"]:
    print(key, text.count(key))

# Find function definitions related
for i, line in enumerate(text.splitlines(), 1):
    if any(k in line for k in ["def _match_procedure", "procedure_detail", "recommended_forms", "class AskResponse", "def ask"]):
        if any(k in line for k in ["def ", "procedure_detail", "recommended_forms", "AskResponse"]):
            print(f"{i}: {line[:160]}")
