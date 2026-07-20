from pathlib import Path
p = Path("scripts/legal_search_server.py")
t = p.read_text(encoding="utf-8")

# Count occurrences of the problematic bindparams
count = t.count(').bindparams(bindparam("exclude_chunk_ids", expanding=True))')
print(f"Found {count} occurrences of bindparams(exclude_chunk_ids)")

# Also check if there are similar patterns
import re
matches = list(re.finditer(r'\.bindparams\(bindparam\("exclude_chunk_ids"', t))
print(f"Regex matches: {len(matches)}")
for m in matches:
    start = max(0, m.start()-50)
    end = min(len(t), m.end()+50)
    print(f"  at {m.start()}: ...{t[start:end]}...")
