import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# Search auth/login/officer related files
files = [
    "frontend/src/components/auth/LoginForm.tsx",
    "frontend/src/lib/stores/auth-store.ts",
    "frontend/src/lib/hooks/use-auth.ts",
    "api/routers/auth.py",
    "api/auth.py",
    "api/user_service.py",
]
for f in files:
    p = Path(f)
    print(("OK" if p.exists() else "MISS"), f, p.stat().st_size if p.exists() else 0)

print("\n=== LoginForm officer ===")
p = Path("frontend/src/components/auth/LoginForm.tsx")
if p.exists():
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if any(k in line.lower() for k in ["officer", "role", "login", "error", "can bo", "cán bộ", "password", "router.push"]):
            print(f"{i}: {line[:160]}")
