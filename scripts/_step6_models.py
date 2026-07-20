from pathlib import Path
text = Path("api/models.py").read_text(encoding="utf-8")
for i, line in enumerate(text.splitlines(), 1):
    if "class Ask" in line or "procedure" in line.lower() or "rag_trace" in line or "faq" in line.lower() or "recommended" in line:
        print(f"{i}: {line}")
print("---")
# Inspect response construction around 2270 and 2810
sp = Path("api/routers/search.py").read_text(encoding="utf-8")
lines = sp.splitlines()
for start in [2200, 2260, 2780, 2800]:
    print(f"\n==== around {start} ====")
    for i in range(start, min(start+40, len(lines)+1)):
        print(f"{i}: {lines[i-1][:180]}")
