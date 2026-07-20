from pathlib import Path
p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")
# Find GOLDEN definition
idx = text.find("GOLDEN =")
print("GOLDEN = at", idx)
if idx >= 0:
    print(repr(text[idx:idx+200]))
else:
    # Find all GOLDEN mentions
    for i, line in enumerate(text.splitlines(), 1):
        if "GOLDEN" in line:
            print(f"{i}: {line[:100]}")
