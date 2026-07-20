import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

# Check existing test/regression scripts
test_scripts = []
for p in Path(".").rglob("*test*.py"):
    if "node_modules" not in str(p) and "__pycache__" not in str(p):
        test_scripts.append(p)
for p in Path(".").rglob("*regress*.py"):
    if "node_modules" not in str(p) and "__pycache__" not in str(p):
        test_scripts.append(p)
for p in Path(".").rglob("*golden*.py"):
    if "node_modules" not in str(p) and "__pycache__" not in str(p):
        test_scripts.append(p)

print("Existing test scripts:")
for p in sorted(test_scripts):
    print(f"  {p} ({p.stat().st_size} bytes)")

# Check e2e scripts
for p in Path("scripts").glob("e2e*.py"):
    print(f"  e2e: {p.name} ({p.stat().st_size} bytes)")

# Check pytest config
for cfg in ["pytest.ini", "setup.cfg", "pyproject.toml"]:
    if Path(cfg).exists():
        print(f"Config: {cfg}")

# Check frontend test files
for p in Path("frontend").rglob("*.test.tsx"):
    if "node_modules" not in str(p):
        print(f"Frontend test: {p}")
for p in Path("frontend").rglob("*.spec.tsx"):
    if "node_modules" not in str(p):
        print(f"Frontend spec: {p}")
