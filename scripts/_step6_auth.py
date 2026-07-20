import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
# How other routers do admin auth
for name in ["ward_procedures.py", "users.py", "legal_search.py"]:
    p = Path(f"api/routers/{name}")
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8")
    print("====", name)
    for i, line in enumerate(text.splitlines(), 1):
        if any(k in line for k in ["Depends", "get_current", "require", "admin", "HTTPException", "role"]):
            if i < 80 or "admin" in line.lower() or "get_current" in line or "Depends" in line:
                if any(k in line for k in ["Depends", "get_current", "require_admin", "role", "HTTPException", "from api"]):
                    print(f"{i}: {line[:140]}")
