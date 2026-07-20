from pathlib import Path
import re

p = Path("scripts/legal_search_server.py")
t = p.read_text(encoding="utf-8")

# Replace both bindparams occurrences
# The issue is that bindparams is called even when exclude_chunk_ids is empty
# Fix: only call bindparams when exclude_chunk_ids is non-empty

# Pattern 1: In _fetch_lexical_chunks
# Change:
#   ).bindparams(bindparam("exclude_chunk_ids", expanding=True))
#   with self._engine.connect() as connection:
#       rows = [dict(row) for row in connection.execute(statement, params).mappings()]
# To:
#   if exclude_chunk_ids:
#       statement = statement.bindparams(bindparam("exclude_chunk_ids", expanding=True))
#   with self._engine.connect() as connection:
#       rows = [dict(row) for row in connection.execute(statement, params).mappings()]

old = ''').bindparams(bindparam("exclude_chunk_ids", expanding=True))
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]'''

new = ''')
        if exclude_chunk_ids:
            statement = statement.bindparams(bindparam("exclude_chunk_ids", expanding=True))
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]'''

count = t.count(old)
print(f"Exact matches: {count}")
if count > 0:
    t = t.replace(old, new)
    p.write_text(t, encoding="utf-8", newline="\n")
    print(f"Replaced {count} occurrences")
else:
    # Try with different whitespace
    pattern = r'\)\.bindparams\(bindparam\("exclude_chunk_ids", expanding=True\)\)\s*\n(\s+)with self\._engine\.connect\(\) as connection:\s*\n\s+rows = \[dict\(row\) for row in connection\.execute\(statement, params\)\.mappings\(\)\]'
    def replacer(m):
        indent = m.group(1)
        return f')\n{indent}if exclude_chunk_ids:\n{indent}    statement = statement.bindparams(bindparam("exclude_chunk_ids", expanding=True))\n{indent}with self._engine.connect() as connection:\n{indent}    rows = [dict(row) for row in connection.execute(statement, params).mappings()]'
    
    new_t, n = re.subn(pattern, replacer, t)
    if n > 0:
        p.write_text(new_t, encoding="utf-8", newline="\n")
        print(f"Regex replaced {n} occurrences")
    else:
        print("No matches found with regex either")
        # Show exact content around the bindparams
        for m in re.finditer(r'\.bindparams\(bindparam\("exclude_chunk_ids"', t):
            start = max(0, m.start()-20)
            end = min(len(t), m.end()+100)
            print(f"Context: {repr(t[start:end])}")
