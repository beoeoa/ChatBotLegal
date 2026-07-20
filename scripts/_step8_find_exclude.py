import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
for p in Path(".").rglob("*.py"):
    if "node_modules" in str(p) or "external" in str(p) or "__pycache__" in str(p):
        continue
    try:
        t=p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    if "exclude_chunk_ids" in t:
        print(p)
        for i,line in enumerate(t.splitlines(),1):
            if "exclude_chunk_ids" in line:
                print(f"  {i}: {line[:160]}")
