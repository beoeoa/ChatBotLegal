from pathlib import Path
p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")
# Check if GOLDEN exists
print("GOLDEN in text:", "GOLDEN" in text)
# Find where GOLDEN should be (after ask function)
idx = text.find("def parse_sse_response")
if idx >= 0:
    # Find next def after ask
    rest = text[idx:]
    defs = []
    for i, line in enumerate(rest.split("\n")):
        if line.startswith("def ") or line.startswith("# ==="):
            defs.append((i, line[:80]))
    print("Defs after parse_sse_response:")
    for d in defs:
        print(f"  {d[0]}: {d[1]}")
