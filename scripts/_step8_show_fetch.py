from pathlib import Path
import re
p=Path("scripts/legal_search_server.py")
t=p.read_text(encoding="utf-8")
# show both functions fully around exclude param usage
for name in ["_fetch_lexical_chunks","_fetch_fallback_chunks"]:
    i=t.find(f"def {name}")
    print("====", name, i)
    print(t[i:i+2200])
    print()
