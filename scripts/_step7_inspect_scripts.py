import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# Inspect existing crawl scripts and download endpoints for soft error + official-only
for name in [
    "scripts/crawl_priority_official_forms.py",
    "scripts/ingest_priority_official_forms.py",
    "scripts/crawl_haiphong_official_forms.py",
    "scripts/audit_forms_inventory.py",
]:
    p = Path(name)
    text = p.read_text(encoding="utf-8", errors="replace")
    print("====", name, "size", len(text))
    for i, line in enumerate(text.splitlines(), 1):
        if i <= 80 or any(k in line.lower() for k in ["argparse", "max", "download", "priority", "dvc", "dichvucong", "def main", "if __name__"]):
            if i <= 100 or any(k in line.lower() for k in ["argparse", "max_", "download", "priority", "dvc", "dichvucong", "def main", "if __name__", "output", "approved", "official"]):
                print(f"{i}: {line[:180]}")
    print()
