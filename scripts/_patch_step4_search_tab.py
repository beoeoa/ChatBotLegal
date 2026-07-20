from pathlib import Path

path = Path(r"frontend/src/app/(dashboard)/search/page.tsx")
text = path.read_text(encoding="utf-8")

replacements = []

old = """  const role = useAuthStore((state) => state.role) || 'citizen'
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const hasHydrated = useAuthStore((state) => state.hasHydrated)
"""
new = """  const role = useAuthStore((state) => state.role) || 'citizen'
  const canUseSearchTab = role === 'officer' || role === 'admin'
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const hasHydrated = useAuthStore((state) => state.hasHydrated)
"""
replacements.append(("role block", old, new))

old = """  const rawMode = searchParams?.get('mode')
  const urlMode = rawMode === 'search' ? 'search' : 'ask'

  // Tab state (controlled)
  const [activeTab, setActiveTab] = useState<'ask' | 'search'>(
    urlMode === 'search' ? 'search' : 'ask'
  )

  // Search state
  const [searchQuery, setSearchQuery] = useState(urlMode === 'search' ? urlQuery : '')
"""
new = """  const rawMode = searchParams?.get('mode')
  // Citizen cannot use Search tab; force ask even if mode=search is in URL.
  const urlMode: 'ask' | 'search' =
    rawMode === 'search' && (role === 'officer' || role === 'admin') ? 'search' : 'ask'

  // Tab state (controlled)
  const [activeTab, setActiveTab] = useState<'ask' | 'search'>(
    urlMode === 'search' ? 'search' : 'ask'
  )

  // Search state
  const [searchQuery, setSearchQuery] = useState(urlMode === 'search' ? urlQuery : '')
"""
replacements.append(("urlMode block", old, new))

old = """    if (urlMode === 'search') {
      handleSearch()
      hasAutoTriggeredRef.current = true
    } else if (urlMode === 'ask' && modelDefaults?.default_chat_model) {
"""
new = """    if (urlMode === 'search' && canUseSearchTab) {
      handleSearch()
      hasAutoTriggeredRef.current = true
    } else if (urlMode === 'ask' && modelDefaults?.default_chat_model) {
"""
replacements.append(("auto-trigger block", old, new))

old = """  }, [urlQuery, urlMode, modelsLoading, modelDefaults, handleSearch, handleAsk])
"""
new = """  }, [urlQuery, urlMode, modelsLoading, modelDefaults, handleSearch, handleAsk, canUseSearchTab])
"""
replacements.append(("auto-trigger deps", old, new))

old = """  // Handle URL param changes while on page (e.g., from command palette again)
  useEffect(() => {
    const currentQ = searchParams?.get('q') || ''
    const rawCurrentMode = searchParams?.get('mode')
    const currentMode = rawCurrentMode === 'search' ? 'search' : 'ask'

    // Check if URL params have changed
    if (currentQ !== lastUrlParamsRef.current.q || currentMode !== lastUrlParamsRef.current.mode) {
      lastUrlParamsRef.current = { q: currentQ, mode: currentMode }

      if (currentQ) {
        // Update state based on mode
        if (currentMode === 'search') {
          setSearchQuery(currentQ)
          setActiveTab('search')
          // Reset trigger flag so we auto-trigger with new params
          hasAutoTriggeredRef.current = false
        } else {
          setAskQuestion(currentQ)
          setActiveTab('ask')
          hasAutoTriggeredRef.current = false
        }
      }
    }
  }, [searchParams])
"""
new = """  // Handle URL param changes while on page (e.g., from command palette again)
  useEffect(() => {
    const currentQ = searchParams?.get('q') || ''
    const rawCurrentMode = searchParams?.get('mode')
    const currentMode: 'ask' | 'search' =
      rawCurrentMode === 'search' && canUseSearchTab ? 'search' : 'ask'

    // Check if URL params have changed
    if (currentQ !== lastUrlParamsRef.current.q || currentMode !== lastUrlParamsRef.current.mode) {
      lastUrlParamsRef.current = { q: currentQ, mode: currentMode }

      if (currentQ) {
        // Update state based on mode
        if (currentMode === 'search') {
          setSearchQuery(currentQ)
          setActiveTab('search')
          // Reset trigger flag so we auto-trigger with new params
          hasAutoTriggeredRef.current = false
        } else {
          setAskQuestion(currentQ)
          setActiveTab('ask')
          hasAutoTriggeredRef.current = false
        }
      } else if (!canUseSearchTab) {
        setActiveTab('ask')
      }
    }
  }, [searchParams, canUseSearchTab])

  // Citizen must never stay on Search tab (direct link, stale state, role switch).
  useEffect(() => {
    if (!canUseSearchTab && activeTab === 'search') {
      setActiveTab('ask')
    }
  }, [canUseSearchTab, activeTab])
"""
replacements.append(("url change effect", old, new))

old = """        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'ask' | 'search')} className="w-full space-y-6">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t('searchPage.chooseAMode')}</p>
            <TabsList aria-label={t('common.accessibility.searchKB')} className="w-full max-w-xl">
              <TabsTrigger value="ask">
                <MessageCircleQuestion className="h-4 w-4" />
                {t('searchPage.askBeta')}
              </TabsTrigger>
              <TabsTrigger value="search">
                <Search className="h-4 w-4" />
                {t('searchPage.search')}
              </TabsTrigger>
            </TabsList>
          </div>
"""
new = """        <Tabs
          value={canUseSearchTab ? activeTab : 'ask'}
          onValueChange={(v) => {
            if (!canUseSearchTab && v === 'search') return
            setActiveTab(v as 'ask' | 'search')
          }}
          className="w-full space-y-6"
        >
          {canUseSearchTab ? (
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t('searchPage.chooseAMode')}</p>
              <TabsList aria-label={t('common.accessibility.searchKB')} className="w-full max-w-xl">
                <TabsTrigger value="ask">
                  <MessageCircleQuestion className="h-4 w-4" />
                  {t('searchPage.askBeta')}
                </TabsTrigger>
                <TabsTrigger value="search">
                  <Search className="h-4 w-4" />
                  {t('searchPage.search')}
                </TabsTrigger>
              </TabsList>
            </div>
          ) : null}
"""
replacements.append(("tabs ui", old, new))

for name, old, new in replacements:
    if old not in text:
        raise SystemExit(f"MISSING: {name}")
    text = text.replace(old, new, 1)
    print(f"OK: {name}")

# Wrap search TabsContent
old = '          <TabsContent value="search" className="mt-6">'
new = '          {canUseSearchTab ? (\n          <TabsContent value="search" className="mt-6">'
if old not in text:
    raise SystemExit("MISSING: search tabscontent open")
text = text.replace(old, new, 1)
print("OK: search tabscontent open")

end_marker = "          </TabsContent>\n        </Tabs>"
# Find the LAST occurrence because ask TabsContent also ends with </TabsContent>
# Search content is the last TabsContent before </Tabs>
idx = text.rfind(end_marker)
if idx < 0:
    raise SystemExit("MISSING: search tabscontent close")
text = text[:idx] + "          </TabsContent>\n          ) : null}\n        </Tabs>" + text[idx + len(end_marker):]
print("OK: search tabscontent close")

path.write_text(text, encoding="utf-8", newline="\n")
print("search page patched")
