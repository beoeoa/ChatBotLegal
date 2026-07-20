import ast
from pathlib import Path
p = Path("scripts/legal_search_server.py")
try:
    ast.parse(p.read_text(encoding="utf-8"))
    print("syntax OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")

# Verify the fix
t = p.read_text(encoding="utf-8")
print("bindparams count:", t.count('bindparams(bindparam("exclude_chunk_ids"'))
print("if exclude_chunk_ids count:", t.count("if exclude_chunk_ids:"))
