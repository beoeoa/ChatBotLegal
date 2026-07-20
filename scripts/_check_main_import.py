from pathlib import Path
text = Path("api/main.py").read_text(encoding="utf-8")
# Print import block around routers
lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if 15 <= i <= 55 or "faq" in line or "ward_procedures" in line:
        print(f"{i}: {line}")
