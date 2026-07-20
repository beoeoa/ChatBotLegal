from pathlib import Path
lines=Path("scripts/legal_search_server.py").read_text(encoding="utf-8").splitlines()
for i in range(940,1040):
    print(f"{i+1}: {lines[i]}")
print("---")
for i in range(1100,1180):
    print(f"{i+1}: {lines[i]}")
