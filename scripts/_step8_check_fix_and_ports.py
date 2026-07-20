from pathlib import Path
import re, ast

p = Path("scripts/legal_search_server.py")
t = p.read_text(encoding="utf-8")
print("bindparams count", t.count('bindparams(bindparam("exclude_chunk_ids"'))
print("if exclude_chunk_ids count", t.count("if exclude_chunk_ids:"))

# show fixed regions
for m in re.finditer(r"if exclude_chunk_ids:\n\s+statement = statement\.bindparams", t):
    start=max(0,m.start()-120); end=min(len(t), m.end()+80)
    print("---")
    print(t[start:end])

# also check how legal server is started
for name in ["scripts/start_legal_search.py","scripts/legal_search_server.py","notebook_data/legal_search_server.out.log","notebook_data/legal-search.out.log"]:
    pp=Path(name)
    print(name, "exists" if pp.exists() else "missing", pp.stat().st_size if pp.exists() else "")

# find process listeners on 8765 / 5055
import subprocess
out=subprocess.run(["netstat","-ano"], capture_output=True, text=True)
for line in out.stdout.splitlines():
    if ":8765" in line or ":5055" in line:
        if "LISTENING" in line:
            print(line)
