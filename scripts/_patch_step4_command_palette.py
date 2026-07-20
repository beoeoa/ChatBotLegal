from pathlib import Path

path = Path(r"frontend/src/components/common/CommandPalette.tsx")
text = path.read_text(encoding="utf-8")

# Ensure auth store import
if "useAuthStore" not in text:
    old = "import { useTranslation } from '@/lib/hooks/use-translation'\n"
    new = (
        "import { useTranslation } from '@/lib/hooks/use-translation'\n"
        "import { useAuthStore } from '@/lib/stores/auth-store'\n"
    )
    if old not in text:
        raise SystemExit("missing useTranslation import")
    text = text.replace(old, new, 1)
    print("OK: import auth store")
else:
    print("SKIP: auth store import already present")

# Insert role/canUseSearchTab near start of component body.
# Find export default function and first hooks.
marker = "export default function CommandPalette"
idx = text.find(marker)
if idx < 0:
    # maybe named differently
    for cand in ["function CommandPalette", "export function CommandPalette", "const CommandPalette"]:
        idx = text.find(cand)
        if idx >= 0:
            break
if idx < 0:
    raise SystemExit("CommandPalette component not found")

# Find first line with const { t } or useTranslation inside component
comp_start = text.find("{", idx)
# Insert after first few hooks: look for useTranslation usage
ut = text.find("useTranslation", comp_start)
if ut < 0:
    raise SystemExit("useTranslation in component not found")
line_end = text.find("\n", ut)
# insert after that line
insert = "\n  const role = useAuthStore((state) => state.role) || 'citizen'\n  const canUseSearchTab = role === 'officer' || role === 'admin'\n"
if "const canUseSearchTab" not in text:
    text = text[:line_end+1] + insert + text[line_end+1:]
    print("OK: role/canUseSearchTab")
else:
    print("SKIP: canUseSearchTab already present")

# Patch handleSearch to force ask for citizen
old = """  const handleSearch = useCallback(() => {
    if (!query.trim()) return
    handleSelect(() => router.push(`/search?q=${encodeURIComponent(query)}&mode=search`))
  }, [handleSelect, router, query])
"""
new = """  const handleSearch = useCallback(() => {
    if (!query.trim()) return
    // Citizen cannot use Search tab; route to Ask instead.
    const mode = canUseSearchTab ? 'search' : 'ask'
    handleSelect(() => router.push(`/search?q=${encodeURIComponent(query)}&mode=${mode}`))
  }, [handleSelect, router, query, canUseSearchTab])
"""
if old not in text:
    raise SystemExit("handleSearch block not found")
text = text.replace(old, new, 1)
print("OK: handleSearch")

# Hide Search command item for citizen in both places.
# Replace both CommandItem blocks that call handleSearch with conditional.
# Safer approach: wrap onSelect and hide Search item visually when !canUseSearchTab.

# Pattern 1: top search/ask group
old = """            <CommandItem
              value={`__search__ ${query}`}
              onSelect={handleSearch}
              forceMount
            >
              <Search className="h-4 w-4" />
              <span>{t('searchPage.searchResultsFor').replace('{query}', query)}</span>
            </CommandItem>
"""
new = """            {canUseSearchTab ? (
            <CommandItem
              value={`__search__ ${query}`}
              onSelect={handleSearch}
              forceMount
            >
              <Search className="h-4 w-4" />
              <span>{t('searchPage.searchResultsFor').replace('{query}', query)}</span>
            </CommandItem>
            ) : null}
"""
count = text.count(old)
if count < 1:
    raise SystemExit("search command item pattern not found")
text = text.replace(old, new)
print(f"OK: hide search items x{count}")

path.write_text(text, encoding="utf-8", newline="\n")
print("command palette patched")
