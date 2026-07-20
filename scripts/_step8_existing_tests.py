import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

# Check existing e2e_step12 test
existing = Path("scripts/e2e_step12_ask_search_form_regression.py")
if existing.exists():
    text = existing.read_text(encoding="utf-8")
    print(f"e2e_step12: {len(text)} chars")
    for i, line in enumerate(text.splitlines(), 1):
        if i <= 60 or any(k in line for k in ["def test_", "def main", "assert", "print(", "class ", "import "]):
            if i <= 80 or "def test_" in line or "def main" in line or "assert" in line or "print(" in line:
                print(f"{i}: {line[:160]}")

# Check e2e_smoke_test
smoke = Path("scripts/e2e_smoke_test.py")
if smoke.exists():
    text = smoke.read_text(encoding="utf-8")
    print(f"\ne2e_smoke: {len(text)} chars")
    for i, line in enumerate(text.splitlines(), 1):
        if i <= 60 or any(k in line for k in ["def test_", "def main", "assert", "print("]):
            if i <= 80 or "def test_" in line or "def main" in line or "assert" in line or "print(" in line:
                print(f"{i}: {line[:160]}")

# Check tests/ directory structure
tests_dir = Path("tests")
print("\ntests/ contents:")
for p in sorted(tests_dir.glob("*")):
    if p.is_file():
        print(f"  {p.name} ({p.stat().st_size} bytes)")
    else:
        print(f"  {p.name}/")
