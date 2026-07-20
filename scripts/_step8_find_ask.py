import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
import re

# Find actual ask endpoints from search router
sp = Path("api/routers/search.py").read_text(encoding="utf-8")
for i, line in enumerate(sp.splitlines(), 1):
    if "@router." in line or "async def ask" in line or "def ask" in line or "prefix" in line and "router" in line:
        if "@router" in line or "async def ask" in line or "APIRouter" in line:
            print(f"{i}: {line[:160]}")

print("--- main include ---")
main = Path("api/main.py").read_text(encoding="utf-8")
for i, line in enumerate(main.splitlines(), 1):
    if "search" in line and "include_router" in line:
        print(f"{i}: {line}")
