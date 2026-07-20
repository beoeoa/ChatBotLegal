import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

# Check search router mount prefix
main = Path("api/main.py").read_text(encoding="utf-8")
for i, line in enumerate(main.splitlines(), 1):
    if "include_router" in line:
        print(f"{i}: {line}")

# Check search router prefix
sp = Path("api/routers/search.py").read_text(encoding="utf-8")
for i, line in enumerate(sp.splitlines(), 1):
    if "APIRouter" in line or "prefix=" in line:
        print(f"{i}: {line}")

# Check AskRequest model fields
mp = Path("api/models.py").read_text(encoding="utf-8")
for i, line in enumerate(mp.splitlines(), 1):
    if "class AskRequest" in line or "class AskResponse" in line:
        print(f"{i}: {line}")
        # Print next 20 lines
        lines = mp.splitlines()
        for j in range(i, min(i+25, len(lines))):
            print(f"{j}: {lines[j-1]}")
        break
