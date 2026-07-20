from pathlib import Path

# Check current state of key files
files_to_check = [
    "api/routers/search.py",
    "frontend/src/app/(dashboard)/search/page.tsx",
    "frontend/src/components/search/StreamingResponse.tsx",
    "frontend/src/components/common/CommandPalette.tsx",
    "frontend/src/components/layout/AppSidebar.tsx",
    "api/routers/faq.py",
    "notebook_data/faq_seed.json",
]

for f in files_to_check:
    p = Path(f)
    if p.exists():
        print(f"OK: {f} ({p.stat().st_size} bytes)")
    else:
        print(f"MISSING: {f}")

# Check if FAQ router exists
faq_router = Path("api/routers/faq.py")
if faq_router.exists():
    print(f"OK: faq.py exists")
else:
    print("MISSING: faq.py")

# Check notebook_data structure
nb_dir = Path("notebook_data")
if nb_dir.exists():
    print(f"notebook_data contents:")
    for item in nb_dir.iterdir():
        print(f"  {item.name}")
else:
    print("MISSING: notebook_data")
