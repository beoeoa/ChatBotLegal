from pathlib import Path
text = Path(r"frontend/src/components/layout/AppSidebar.tsx").read_text(encoding="utf-8")
checks = [
    "role === 'citizen'",
    "href: '/search?mode=ask'",
    "href: '/search?mode=search'",
    "MessageCircleQuestion",
]
for c in checks:
    print(("OK" if c in text else "MISS") + ": " + c)

# Check locale files for navigation.search key
locale_path = Path(r"frontend/src/lib/locales/vi-VN/index.ts")
if locale_path.exists():
    lt = locale_path.read_text(encoding="utf-8")
    if "navigation.search" in lt or "search:" in lt:
        print("OK: search key exists in locale")
    else:
        print("NOTE: navigation.search key may be missing from locale")
        # Show what navigation keys exist
        for line in lt.splitlines():
            if "navigation." in line.lower() and ":" in line:
                print(f"  {line.strip()}")
else:
    print("MISSING: locale file")
