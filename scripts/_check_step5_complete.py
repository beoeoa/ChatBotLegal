from pathlib import Path

# Check if faq router is registered in main.py
main = Path("api/main.py")
text = main.read_text(encoding="utf-8")

if "faq" in text.lower():
    print("OK: faq router already registered")
else:
    print("MISSING: faq router not registered in main.py")
    # Show where to add it
    for i, line in enumerate(text.splitlines(), 1):
        if "ward_procedures.router" in line or "legal_crawl_api.router" in line:
            print(f"  Line {i}: {line.strip()}")

# Check faq.py structure
faq = Path("api/routers/faq.py")
faq_text = faq.read_text(encoding="utf-8")
print(f"\nfaq.py stats:")
print(f"  size={faq.stat().st_size}")
print(f"  has router: {'APIRouter' in faq_text}")
print(f"  has endpoints: {faq_text.count('@router')}")
print(f"  has require_admin: {'require_admin' in faq_text}")

# Check faq_seed.json
seed = Path("notebook_data/faq_seed.json")
import json
seed_data = json.loads(seed.read_text(encoding="utf-8"))
print(f"\nfaq_seed.json:")
print(f"  total={len(seed_data)}")
print(f"  approved={sum(1 for f in seed_data if f.get('review_status')=='approved')}")
domains = set(f.get('domain') for f in seed_data)
print(f"  domains: {sorted(domains)}")
