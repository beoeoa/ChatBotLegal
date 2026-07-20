from pathlib import Path
import ast

p = Path("scripts/audit_forms_inventory.py")
data = p.read_bytes()
if data.startswith(b"\xef\xbb\xbf"):
    p.write_bytes(data[3:])
    print("Removed BOM from audit_forms_inventory.py")
else:
    # also strip if text starts with BOM char
    text = p.read_text(encoding="utf-8-sig")
    p.write_text(text, encoding="utf-8", newline="\n")
    print("Rewrote audit_forms_inventory.py without BOM via utf-8-sig")

ast.parse(p.read_text(encoding="utf-8"))
print("SYNTAX OK")
