from pathlib import Path
p = Path("frontend/src/app/(dashboard)/search/page.tsx")
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if "StreamingResponse" in line or "ragTrace=" in line or "showRagTrace" in line or "role=" in line and "Streaming" in "".join(lines[max(0,i-5):i+5]):
        print(f"{i}: {line}")
print("--- StreamingResponse usage ---")
for i, line in enumerate(lines, 1):
    if "StreamingResponse" in line:
        for j in range(i, min(i+20, len(lines)+1)):
            print(f"{j}: {lines[j-1]}")
        break
