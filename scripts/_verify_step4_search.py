from pathlib import Path
text = Path(r"frontend/src/app/(dashboard)/search/page.tsx").read_text(encoding="utf-8")
checks = [
    "canUseSearchTab",
    "role === 'officer' || role === 'admin'",
    "rawMode === 'search' && (role === 'officer' || role === 'admin')",
    "urlMode === 'search' && canUseSearchTab",
    "Citizen must never stay on Search tab",
    "canUseSearchTab ? activeTab : 'ask'",
    "{canUseSearchTab ? (",
    'TabsContent value="search"',
    ") : null}",
]
for c in checks:
    print(("OK" if c in text else "MISS") + ": " + c)

lines = text.splitlines()
for i, line in enumerate(lines, 1):
    if any(k in line for k in ["canUseSearchTab", "urlMode", 'TabsContent value="search"', "onValueChange", "Citizen must never", "Tabs"]):
        if any(k in line for k in ["canUseSearchTab", "urlMode", 'TabsContent value="search"', "onValueChange", "Citizen must never", "<Tabs", "TabsList", "TabsTrigger value=\"search\""]):
            print(f"{i}: {line}")
