from pathlib import Path

main = Path("api/main.py")
text = main.read_text(encoding="utf-8")

# 1) Add import for faq router
import_section = "from api.routers import (\n"
# Find the imports block
imports_match = text.find("from api.routers import (")
if imports_match < 0:
    raise SystemExit("Cannot find imports block")

# Find the closing parenthesis of the import tuple
close_paren = text.find(")", imports_match)
if close_paren < 0:
    raise SystemExit("Cannot find closing paren")

# Insert 'faq,' before the closing paren
before_close = text[:close_paren].rstrip()
if before_close.endswith(","):
    new_imports = before_close + "\n    faq,\n"
else:
    new_imports = before_close + ",\n    faq,\n"
text = text[:imports_match] + import_section + new_imports + text[close_paren+1:]
print("OK: Added faq import")

# 2) Register the router - add after ward_procedures
old_register = 'app.include_router(ward_procedures.router, prefix="/api", tags=["ward-procedures"])'
new_register = old_register + '\napp.include_router(faq.router, prefix="/api", tags=["faq"])'
if old_register in text:
    text = text.replace(old_register, new_register, 1)
    print("OK: Registered faq router")
else:
    # Try alternate pattern
    for line in text.splitlines():
        if "ward_procedures" in line and "include_router" in line:
            print(f"Found alternate: {line.strip()}")
    raise SystemExit("Could not find ward_procedures registration")

main.write_text(text, encoding="utf-8", newline="\n")
print(f"\nmain.py updated ({main.stat().st_size} bytes)")

# Verify
verify_text = main.read_text(encoding="utf-8")
print(f"Has faq import: {'faq,' in verify_text}")
print(f"Has faq router: {'faq.router' in verify_text}")
