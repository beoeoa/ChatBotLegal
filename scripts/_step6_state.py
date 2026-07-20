import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
import json

faq_router = Path("api/routers/faq.py")
faq_seed = Path("notebook_data/faq_seed.json")
main_py = Path("api/main.py")
streaming = Path("frontend/src/components/search/StreamingResponse.tsx")
search_page = Path("frontend/src/app/(dashboard)/search/page.tsx")

print("=== FAQ FILES ===")
if faq_router.exists():
    text = faq_router.read_text(encoding="utf-8")
    print(f"faq.py: {faq_router.stat().st_size} bytes")
    print(f"  endpoints: {text.count('@router')}")
    print(f"  require_admin: {'require_admin' in text}")
    print(f"  list_faqs: {'list_faqs' in text}")
    print(f"  create_faq: {'create_faq' in text}")
    print(f"  seed: {'seed' in text}")
else:
    print("faq.py MISSING")

if faq_seed.exists():
    seed = json.loads(faq_seed.read_text(encoding="utf-8"))
    print(f"faq_seed.json: {len(seed)} items")
    print(f"  domains: {sorted({f.get('domain') for f in seed})}")
    print(f"  approved: {sum(1 for f in seed if f.get('review_status')=='approved')}")
else:
    print("faq_seed.json MISSING")

main_text = main_py.read_text(encoding="utf-8")
print("=== MAIN.PY ===")
print(f"faq import: {'faq,' in main_text}")
print(f"faq.router: {'faq.router' in main_text}")

stream_text = streaming.read_text(encoding="utf-8")
print("=== STREAMING UI ===")
print(f"procedureDetail forms: {'procedureDetail?.forms' in stream_text}")
print(f"FAQAccordion: {'FAQAccordion' in stream_text}")
print(f"faq: {'faq' in stream_text.lower()}")

# Check if frontend faq component exists
faq_comp = list(Path("frontend/src").rglob("*FAQ*")) + list(Path("frontend/src").rglob("*faq*"))
print("=== FRONTEND FAQ FILES ===")
for p in faq_comp:
    print(p)
