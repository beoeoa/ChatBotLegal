from pathlib import Path
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if i <= 180 and any(k in line for k in ["role", "ragTrace", "interface", "type ", "export function", "export default", "props", "showRag"]):
        print(f"{i}: {line}")
print("--- around rag block ---")
for i, line in enumerate(lines, 1):
    if 450 <= i <= 530:
        print(f"{i}: {line}")
