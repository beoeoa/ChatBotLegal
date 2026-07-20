from pathlib import Path
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if 139 <= i <= 155:
        print(f"{i}: {line}")
