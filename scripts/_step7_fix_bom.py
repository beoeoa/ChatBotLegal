from pathlib import Path
p = Path("scripts/crawl_forms_from_dvc.py")
text = p.read_bytes()
if text[:3] == b'\xef\xbb\xbf':
    text = text[3:]
    p.write_bytes(text)
    print("Removed BOM")
else:
    print("No BOM")

import ast
ast.parse(p.read_text(encoding="utf-8"))
print("SYNTAX OK after BOM removal")
