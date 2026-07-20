from pathlib import Path
p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")
# Fix the broken quote
text = text.replace(
    '"role": role"}',
    '"role": role}'
)
# Also fix any other similar issues
# Check for the exact line
if '"role": role"}' in text:
    print("Still has broken quote")
else:
    print("Fixed broken quote")

p.write_text(text, encoding="utf-8", newline="\n")
import ast
try:
    ast.parse(p.read_text(encoding="utf-8"))
    print("syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    # Show context
    lines = p.read_text(encoding="utf-8").splitlines()
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+3)):
        print(f"{i+1}: {lines[i][:100]}")
