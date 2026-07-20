from pathlib import Path
main = Path("api/main.py")
if not main.exists():
    print("MISSING main.py")
else:
    text = main.read_text(encoding="utf-8")
    print(f"size={len(text)}")
    for i, line in enumerate(text.splitlines(), 1):
        if any(k in line for k in ["include_router", "from api.routers", "from .routers", "import", "app =", "FastAPI"]):
            if any(k in line for k in ["include_router", "routers", "app =", "FastAPI("]):
                print(f"{i}: {line}")
