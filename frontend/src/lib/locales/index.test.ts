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

describe('Unused Key Detection', () => {
  it(
    'all en-US leaf keys should be referenced in source files',
    () => {
      // These keys belong to the removed shared-role selector and the old
      // connection diagnostics panel. Keep them for locale parity while the
      // public login uses the current account-only flow.
      const legacyUnusedKeys = new Set([
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
      ])
      const srcDir = path.resolve(__dirname, '../../..')
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
      const unused = leafKeys.filter(key => !referenced.has(key) && !legacyUnusedKeys.has(key))

      expect(
        unused,
        `Found ${unused.length} unused i18n key(s):\n${unused.join('\n')}`,
      ).toEqual([])
    },
    60_000,
  )
})
