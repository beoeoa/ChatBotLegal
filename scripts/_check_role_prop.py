from pathlib import Path
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
# Check interface and destructuring for role prop
for i, line in enumerate(lines, 1):
    if 127 <= i <= 160:
        print(f"{i}: {line}")
print("---")
# Check if role is passed from page.tsx
p2 = Path("frontend/src/app/(dashboard)/search/page.tsx")
text2 = p2.read_text(encoding="utf-8")
lines2 = text2.splitlines()
for i, line in enumerate(lines2, 1):
    if 925 <= i <= 945:
        print(f"{i}: {line}")
