import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== auth-store ===")
p=Path("frontend/src/lib/stores/auth-store.ts")
text=p.read_text(encoding="utf-8")
for i,line in enumerate(text.splitlines(),1):
    if any(k in line for k in ["login","availableRoles","officer","role","error","password","Bearer","X-User","setRole","fetch"]):
        print(f"{i}: {line[:180]}")

print("\n=== use-auth ===")
p=Path("frontend/src/lib/hooks/use-auth.ts")
for i,line in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
    print(f"{i}: {line[:180]}")

print("\n=== api/routers/auth.py ===")
p=Path("api/routers/auth.py")
for i,line in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
    print(f"{i}: {line[:180]}")
