from pathlib import Path
text = Path(r"frontend/src/components/common/CommandPalette.tsx").read_text(encoding="utf-8")
checks = [
    "useAuthStore",
    "canUseSearchTab",
    "const mode = canUseSearchTab ? 'search' : 'ask'",
    "mode=${mode}",
]
for c in checks:
    print(("OK" if c in text else "MISS") + ": " + c)
print("search items:", text.count("value={`__search__ ${query}`}"))
print("guarded search items:", text.count("{canUseSearchTab ? ("))
