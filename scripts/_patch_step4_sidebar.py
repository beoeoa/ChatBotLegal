from pathlib import Path
text = Path(r"frontend/src/components/layout/AppSidebar.tsx").read_text(encoding="utf-8")

# Current: citizen/officer share same nav with /search
# Fix: citizen gets only Ask link (/search?mode=ask), officer/admin get both Ask + Search
old = """const getNavigation = (t: TFunction, role: 'officer' | 'citizen' | 'admin' | null) => {
  if (role === 'citizen' || role === 'officer') {
    return [
      {
        title: t('navigation.process'),
        items: [
          { name: t('navigation.askAndSearch'), href: '/search', icon: Search },
          { name: 'Thủ tục hành chính', href: '/procedures', icon: FileText },
          { name: t('navigation.notebooks'), href: '/notebooks', icon: Book },
        ],
      },
    ] as const
  }
"""
new = """const getNavigation = (t: TFunction, role: 'officer' | 'citizen' | 'admin' | null) => {
  if (role === 'citizen') {
    return [
      {
        title: t('navigation.process'),
        items: [
          { name: t('navigation.askAndSearch'), href: '/search?mode=ask', icon: Search },
          { name: 'Thủ tục hành chính', href: '/procedures', icon: FileText },
          { name: t('navigation.notebooks'), href: '/notebooks', icon: Book },
        ],
      },
    ] as const
  }
  // officer/admin: show both Ask and Search tabs
  if (role === 'officer') {
    return [
      {
        title: t('navigation.process'),
        items: [
          { name: t('navigation.askAndSearch'), href: '/search?mode=ask', icon: MessageCircleQuestion },
          { name: t('navigation.search'), href: '/search?mode=search', icon: Search },
          { name: 'Thủ tục hành chính', href: '/procedures', icon: FileText },
          { name: t('navigation.notebooks'), href: '/notebooks', icon: Book },
        ],
      },
    ] as const
  }
"""
if old not in text:
    raise SystemExit("sidebar nav block not found")
text = text.replace(old, new, 1)
print("OK: sidebar nav split")

# Also need to add MessageCircleQuestion import if not present
if "MessageCircleQuestion" not in text:
    old_import = "import {\n  Book,\n  Search,"
    new_import = "import {\n  Book,\n  Search,\n  MessageCircleQuestion,"
    if old_import in text:
        text = text.replace(old_import, new_import, 1)
        print("OK: added MessageCircleQuestion import")
    else:
        # Try alternate import pattern
        old_import2 = "import {\n  Book,\n  Search,"
        new_import2 = "import {\n  Book,\n  Search,\n  MessageCircleQuestion,"
        if old_import2 in text:
            text = text.replace(old_import2, new_import2, 1)
            print("OK: added MessageCircleQuestion import alt")
        else:
            print("WARN: MessageCircleQuestion import not found, checking...")

# Check if translation key 'navigation.search' exists
if "'navigation.search'" not in text and '"navigation.search"' not in text:
    print("NOTE: 'navigation.search' key may need adding to locale files")

Path(r"frontend/src/components/layout/AppSidebar.tsx").write_text(text, encoding="utf-8", newline="\n")
print("sidebar patched")
