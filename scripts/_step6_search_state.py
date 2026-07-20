from pathlib import Path

# Check current state of search.py integration points
sp = Path("api/routers/search.py")
text = sp.read_text(encoding="utf-8")

# Find _match_procedure_detail function
idx = text.find("def _match_procedure_detail")
if idx >= 0:
    print("=== _match_procedure_detail ===")
    for i, line in enumerate(text[idx:idx+500].splitlines(), 1):
        print(f"{idx+i}: {line[:160]}")

print("\n=== _procedure_response_fields ===")
idx2 = text.find("def _procedure_response_fields")
if idx2 >= 0:
    for i, line in enumerate(text[idx2:idx2+400].splitlines(), 1):
        print(f"{idx2+i}: {line[:160]}")

print("\n=== AskResponse model ===")
mp = Path("api/models.py").read_text(encoding="utf-8")
for i, line in enumerate(mp.splitlines(), 1):
    if "class AskResponse" in line or "procedure" in line.lower() or "faq" in line.lower():
        print(f"{i}: {line}")
