from pathlib import Path
import ast

p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")
print("has GOLDEN =", "GOLDEN =" in text)
print("has parse_sse", "def parse_sse_response" in text)
print("has def ask", "def ask(client" in text)
print("has score_citizen", "def score_citizen" in text)
print("has main", "def main" in text)
try:
    ast.parse(text)
    print("syntax OK")
except SyntaxError as e:
    print("syntax ERR", e)

# show around end of ask and start of score_citizen
lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if line.startswith("def ask(") or line.startswith("def score_") or line.startswith("def parse_sse") or line.startswith("def main") or "GOLDEN" in line:
        print(f"{i}: {line[:120]}")
