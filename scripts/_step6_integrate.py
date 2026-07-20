from pathlib import Path

# Check search.py for _match_procedure_detail and how it integrates with ask response
sp = Path("api/routers/search.py")
text = sp.read_text(encoding="utf-8")

# Find where procedure_match is called in ask_knowledge_base
for i, line in enumerate(text.splitlines(), 1):
    if "_match_procedure" in line or "procedure_match" in line or "faq" in line.lower():
        print(f"{i}: {line[:160]}")

print("\n=== AskResponse type ===")
types_path = Path("frontend/src/lib/types/search.ts")
types_text = types_path.read_text(encoding="utf-8")
for i, line in enumerate(types_text.splitlines(), 1):
    if "AskResponse" in line or "procedure" in line.lower() or "faq" in line.lower():
        print(f"{i}: {line}")
