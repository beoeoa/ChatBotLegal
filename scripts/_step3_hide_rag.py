from pathlib import Path
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")
old_line = "      {ragTrace && ("
new_line = "      {ragTrace && role === 'admin' && ("
if old_line in text:
    text = text.replace(old_line, new_line, 1)
    p.write_text(text, encoding="utf-8", newline="\n")
    print("OK: Added role===admin guard to ragTrace block")
else:
    print("ERROR: ragTrace line not found")
