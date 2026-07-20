from pathlib import Path
p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")
# Find ask function
idx = text.find("def ask(client")
print("idx", idx)
print(repr(text[idx:idx+800]))
