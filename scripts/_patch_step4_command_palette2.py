from pathlib import Path
path = Path(r"frontend/src/components/common/CommandPalette.tsx")
text = path.read_text(encoding="utf-8")
old = """              <CommandItem
                value={`__search__ ${query}`}
                onSelect={handleSearch}
                forceMount
              >
                <Search className="h-4 w-4" />
                <span>{t('searchPage.searchResultsFor').replace('{query}', query)}</span>
              </CommandItem>
"""
new = """              {canUseSearchTab ? (
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
if old not in text:
    raise SystemExit('second search item not found')
text = text.replace(old, new, 1)
path.write_text(text, encoding='utf-8', newline='\n')
print('second search item patched')
