import { describe, it, expect } from 'vitest'
import fs from 'fs'
import path from 'path'
import { languages, loadLocale } from './index'
import { enUS } from './en-US'

const getKeys = (obj: Record<string, unknown>, prefix = ''): string[] => {
  return Object.keys(obj).reduce((res: string[], el) => {
    const val = obj[el]
    if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
      return [...res, ...getKeys(val as Record<string, unknown>, prefix + el + '.')]
    }
    return [...res, prefix + el]
  }, [])
}

describe('Locale Parity', () => {
  const enKeys = getKeys(enUS)

  const locales = languages.filter(({ code }) => code !== 'en-US')

  it.each(locales.map(({ code }) => [code] as const))(
    '%s should have the same keys as en-US',
    async (code) => {
      const resource = await loadLocale(code)
      const localeKeys = getKeys(resource as Record<string, unknown>)

      const missing = enKeys.filter(key => !localeKeys.includes(key))
      const extra = localeKeys.filter(key => !enKeys.includes(key))

      expect(missing, `Missing keys in ${code}: ${missing.join(', ')}`).toEqual([])
      expect(extra, `Extra keys in ${code}: ${extra.join(', ')}`).toEqual([])
    },
  )
})

describe('Vietnamese legal-profile workflow', () => {
  it('does not fall back to English on notebook actions and context status', async () => {
    const locale = await loadLocale('vi-VN')

    expect(locale.common.writeNote).toBe('Viết ghi chú')
    expect(locale.common.human).toBe('Cán bộ ghi')
    expect(locale.notebooks.archive).toBe('Lưu trữ')
    expect(locale.notebooks.deleteNotebook).toBe('Xóa hồ sơ pháp lý')
    expect(locale.notebooks.noNotesYet).toBe('Chưa có ghi chú')
    expect(locale.sources.addSource).toBe('Thêm văn bản')
    expect(locale.chat.contextLabel).toBe('Căn cứ đã chọn:')
    expect(locale.chat.contextEmpty).not.toMatch(/No sources|Toggle icons/i)
  })
})

describe('Unused Key Detection', () => {
  it(
    'all en-US leaf keys should be referenced in source files',
    () => {
      // These keys belong to the removed shared-role selector and the old
      // connection diagnostics panel. Keep them for locale parity while the
      // public login uses the current account-only flow.
      const legacyUnusedKeys = new Set([
        'common.podcast',
        'common.admin',
        'common.auditLog',
        'common.email',
        'common.new',
        'common.newSource',
        'common.newNotebook',
        'common.newPodcast',
        'common.unknown',
        'common.deleteForever',
        'common.nameRequired',
        'common.notebookLabel',
        // Model setup was consolidated into the Admin model/API-key screen;
        // these compatibility labels remain for older saved locale bundles.
        'common.modelLocal',
        'common.accessibility.transformationViews',
        'common.accessibility.podcastViews',
        'common.editTransformation',
        'apiErrors.transformationNotFound',
        'navigation.collect',
        'navigation.podcasts',
        'navigation.transformations',
        'navigation.transformation',
        'navigation.advanced',
        'sources.generateNewInsight',
        'sources.selectTransformation',
        'sources.deleteInsight',
        'sources.deleteInsightConfirm',
        'sources.insightGenerationStarted',
        'models.transformationModelLabel',
        'models.transformationModelDesc',
        'common.connectionError',
        'common.unableToConnect',
        'common.retryConnection',
        'common.diagnosticInfo',
        'common.version',
        'common.built',
        'common.apiUrl',
        'common.frontendUrl',
        'common.checkConsoleLogs',
        'auth.loginTitle',
        'auth.loginDesc',
        'auth.selectRole',
        'auth.officerDesc',
        'auth.citizenDesc',
        'auth.adminDesc',
        'auth.passwordPlaceholder',
        'auth.signingIn',
        'auth.signIn',
        'auth.connectErrorHint',
        'searchPage.usingCustomModels',
        'searchPage.usingDefaultModels',
        'searchPage.notSet',
        // Retained for locale parity after the notebook save flow and legacy
        // file-management controls were retired from the active UI.
        'common.yes',
        'searchPage.saveToNotebooks',
        'searchPage.saveToNotebook',
        'searchPage.saveSuccess',
        'searchPage.saveError',
        'searchPage.selectNotebook',
        'searchPage.saving',
        'settings.fileManagement',
        'settings.fileManagementDesc',
        'settings.autoDeleteFiles',
        'settings.autoDeletePlaceholder',
        'settings.filesHelp',
        // The current legal search surface keeps these labels in its
        // domain-specific Vietnamese UI copy; retain them for legacy locale
        // bundle compatibility until that surface is fully translated.
        'common.appName',
        'common.accessibility.searchKB',
        'common.accessibility.enterSearch',
        'common.accessibility.searchKBBtn',
        'searchPage.askAndSearch',
        'searchPage.chooseAMode',
        'searchPage.askBeta',
        'searchPage.askYourKb',
        'searchPage.askYourKbDesc',
        'searchPage.searchDesc',
        'searchPage.pressToSearch',
        'searchPage.searchType',
        'searchPage.vectorSearchWarning',
        'searchPage.textSearch',
        'searchPage.vectorSearch',
        'searchPage.searchIn',
        'searchPage.searchSources',
        'searchPage.searchNotes',
        'searchPage.resultsFound',
        'searchPage.matches',
        'chat.unableToLoadChat',
      ])
      // Podcast/Transformation code and routes were intentionally retired.
      // Keep their translations temporarily for locale parity and rollback of
      // historical records, but do not count them as active UI references.
      const retiredFeaturePrefixes = ['podcasts.', 'transformations.', 'advanced.']
      // All runtime translation references live under src/. Walking the
      // package root also enumerates node_modules/.next and can exceed the
      // test timeout on Windows before the ignore filter is applied.
      const srcDir = path.resolve(__dirname, '../..')
      const localesDir = path.resolve(__dirname)
      const ignoredSegments = new Set([
        '.next',
        'coverage',
        'dist',
        'node_modules',
        'locales',
      ])

      const files = fs.readdirSync(srcDir, { recursive: true }) as string[]
      const sourceFiles = files.filter(f => {
        const segments = f.split(/[\\/]/)
        if (segments.some(segment => ignoredSegments.has(segment))) return false
        const full = path.join(srcDir, f)
        if (full.startsWith(localesDir)) return false
        if (f.endsWith('.test.ts') || f.endsWith('.test.tsx')) return false
        return f.endsWith('.ts') || f.endsWith('.tsx')
      })

      // Normalize optional chaining (t?.common?.key -> t.common.key)
      // so that keys like "common.errorDetails" match "common?.errorDetails".
      const corpus = sourceFiles
        .map(f => fs.readFileSync(path.join(srcDir, f), 'utf-8'))
        .join('\n')
        .replace(/\?\./g, '.')
      const referenced = new Set<string>()

      const leafKeys = getKeys(enUS)
      for (const key of leafKeys) {
        if (corpus.includes(key)) referenced.add(key)
      }
      const unused = leafKeys.filter(
        key =>
          !referenced.has(key) &&
          !legacyUnusedKeys.has(key) &&
          !retiredFeaturePrefixes.some(prefix => key.startsWith(prefix)),
      )

      expect(
        unused,
        `Found ${unused.length} unused i18n key(s):\n${unused.join('\n')}`,
      ).toEqual([])
    },
    60_000,
  )
})
