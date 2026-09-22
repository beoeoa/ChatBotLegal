import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

/**
 * Read-only browser acceptance for already provisioned local/UAT accounts.
 * This suite never creates users or changes business data. It is opt-in so a
 * normal mock E2E run cannot accidentally use real credentials.
 */
const enabled = process.env.LIVE_ROLE_READONLY_ACCEPTANCE === 'true'
const appUrl = (process.env.E2E_BASE_URL || 'http://127.0.0.1:3000').replace(/\/$/, '')
const apiUrl = (process.env.CHATBOTLEGAL_API_URL || 'http://127.0.0.1:5055').replace(/\/$/, '')

type Role = 'admin' | 'officer' | 'citizen'

type RoleCase = {
  role: Role
  username: string
  passwordKeys: string[]
  landing: string
  pages: string[]
}

const roleCases: RoleCase[] = [
  {
    role: 'admin',
    username: process.env.E2E_ADMIN_USERNAME || 'admin',
    passwordKeys: ['OPEN_NOTEBOOK_ADMIN_PASSWORD', 'OPEN_NOTEBOOK_PASSWORD'],
    landing: '/admin',
    pages: [
      '/admin',
      '/admin/activity',
      '/legal-management',
      '/legal-import',
      '/legal-library',
      '/procedures',
      '/procedure-management',
      '/departments',
      '/notebooks',
      '/faq-management',
      '/users',
      '/settings/api-keys',
      '/settings',
      '/account',
    ],
  },
  {
    role: 'officer',
    username: process.env.E2E_OFFICER_USERNAME || 'officer_hotich',
    passwordKeys: ['OPEN_NOTEBOOK_OFFICER_PASSWORD', 'OPEN_NOTEBOOK_PASSWORD'],
    landing: '/search',
    pages: [
      '/officer-dashboard',
      '/search',
      '/legal-library',
      '/procedures',
      '/notebooks',
      '/live-support',
      '/officer-proposals',
      '/account',
    ],
  },
  {
    role: 'citizen',
    username: process.env.E2E_CITIZEN_USERNAME || 'citizen01',
    passwordKeys: ['OPEN_NOTEBOOK_CITIZEN_PASSWORD', 'OPEN_NOTEBOOK_PASSWORD'],
    landing: '/search',
    pages: ['/search', '/procedures', '/live-support', '/account'],
  },
]

function parseEnv(text: string): Map<string, string> {
  const values = new Map<string, string>()
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const index = line.indexOf('=')
    const key = line.slice(0, index).trim()
    let value = line.slice(index + 1).trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    values.set(key, value)
  }
  return values
}

async function projectEnv(): Promise<Map<string, string>> {
  try {
    return parseEnv(await readFile(path.resolve(process.cwd(), '..', '.env'), 'utf8'))
  } catch {
    return new Map()
  }
}

async function bootstrapOfficerPassword(username: string): Promise<string> {
  try {
    const raw = await readFile(
      path.resolve(process.cwd(), '..', 'data', 'private', 'bootstrap_officer_credentials.json'),
      'utf8',
    )
    const payload = JSON.parse(raw) as {
      credentials?: Array<{ username?: string; password?: string }>
    }
    return payload.credentials?.find((entry) => entry.username === username)?.password || ''
  } catch {
    return ''
  }
}

async function configuredPassword(item: RoleCase): Promise<string> {
  const env = await projectEnv()
  const explicitKey = `E2E_${item.role.toUpperCase()}_PASSWORD`
  const explicit = process.env[explicitKey] || env.get(explicitKey)
  if (explicit) return explicit

  // The officer seed script writes one-time credentials outside .env. Read the
  // matching entry only inside the test process; never print it into a report.
  if (item.role === 'officer') {
    const bootstrap = await bootstrapOfficerPassword(item.username)
    if (bootstrap) return bootstrap
  }

  for (const key of item.passwordKeys) {
    const value = process.env[key] || env.get(key)
    if (value) return value
  }
  return ''
}

async function waitForLoginResult(page: Page): Promise<string> {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    const pathname = new URL(page.url()).pathname
    if (pathname !== '/login') return pathname
    const rejected = page.getByText('Sai username/email hoặc mật khẩu. Vui lòng thử lại.', { exact: true })
    if (await rejected.isVisible().catch(() => false)) return pathname
    await page.waitForTimeout(250)
  }
  return new URL(page.url()).pathname
}

async function inspectPage(page: Page, routePath: string, role: Role) {
  const serverErrors: string[] = []
  const deniedRequests: string[] = []
  const requestFailures: string[] = []
  const onResponse = (response: { status(): number; url(): string }) => {
    const status = response.status()
    if (status < 400) return
    const url = new URL(response.url())
    if (url.origin !== new URL(apiUrl).origin && url.origin !== new URL(appUrl).origin) return
    if (status >= 500) serverErrors.push(`${status} ${url.pathname}`)
    if (status === 401 || status === 403) deniedRequests.push(`${status} ${url.pathname}`)
  }
  const onRequestFailed = (request: { url(): string; failure(): { errorText: string } | null }) => {
    const error = request.failure()?.errorText || ''
    if (error !== 'net::ERR_ABORTED') requestFailures.push(`${new URL(request.url()).pathname}: ${error}`)
  }
  page.on('response', onResponse)
  page.on('requestfailed', onRequestFailed)
  try {
    await page.goto(`${appUrl}${routePath}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('main').first()).toContainText(/\S{3,}/, { timeout: 30_000 })
    if (routePath === '/search') {
      await expect(page.locator('#ask-question')).toBeVisible({ timeout: 30_000 })
    } else if (routePath === '/procedures') {
      await expect(page.getByRole('textbox', { name: 'Tìm thủ tục hành chính' })).toBeVisible({ timeout: 30_000 })
    } else if (routePath === '/live-support') {
      await expect(page.getByLabel('Danh sách phiên hỗ trợ')).toBeVisible({ timeout: 30_000 })
      if (role === 'citizen') {
        await expect(page.getByRole('button', { name: 'Tạo yêu cầu hỗ trợ' })).toBeVisible()
      }
    } else if (routePath === '/users') {
      await expect(page.getByRole('heading', { name: 'Quản lý tài khoản' }).first()).toBeVisible({ timeout: 30_000 })
    } else if (routePath === '/officer-proposals') {
      await expect(page.getByRole('heading', { name: 'Đề xuất văn bản cho quản trị viên' })).toBeVisible({ timeout: 30_000 })
    }
    const finalPath = new URL(page.url()).pathname.replace(/\/$/, '') || '/'
    expect(finalPath, `${routePath} redirected unexpectedly`).toBe(routePath)
    expect(serverErrors, `${routePath} returned a server error`).toEqual([])
    expect(deniedRequests, `${routePath} made a denied request`).toEqual([])
    expect(requestFailures, `${routePath} had a failed request`).toEqual([])
  } finally {
    page.off('response', onResponse)
    page.off('requestfailed', onRequestFailed)
  }
}

for (const item of roleCases) {
  test.describe(`${item.role} live read-only`, () => {
    test.skip(!enabled, 'Set LIVE_ROLE_READONLY_ACCEPTANCE=true to run with provisioned accounts.')
    test(`${item.role} can log in and load its current navigation`, async ({ browser }) => {
      test.setTimeout(8 * 60_000)
      const password = await configuredPassword(item)
      expect(
        password,
        `Missing password configuration for ${item.role}; set E2E_${item.role.toUpperCase()}_PASSWORD or provision the QA account`,
      ).not.toBe('')

      const context = await browser.newContext({ locale: 'vi-VN', viewport: { width: 1440, height: 900 } })
      try {
        const login = await context.newPage()
        await login.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
        await login.getByLabel('Email hoặc tên đăng nhập').fill(item.username)
        await login.getByLabel('Mật khẩu', { exact: true }).fill(password)
        await login.locator('form').getByRole('button', { name: 'Đăng nhập', exact: true }).click()
        const loginPath = await waitForLoginResult(login)
        if (loginPath === '/login') {
          throw new Error(
            `${item.role}/${item.username} login was rejected; provide the current QA account password through E2E_${item.role.toUpperCase()}_PASSWORD`,
          )
        }
        if (loginPath === '/change-password') {
          throw new Error(
            `${item.role}/${item.username} authenticated but is still forced to change its temporary password; read-only acceptance requires must_change_password=false`,
          )
        }
        await expect(login.locator('main').first()).toBeVisible({ timeout: 30_000 })

        const identity = await context.request.get(`${apiUrl}/api/users/me`, { timeout: 30_000 })
        expect(identity.status()).toBe(200)
        const me = await identity.json() as { role?: string; username?: string }
        expect(me.role).toBe(item.role)
        expect(me.username).toBe(item.username)
        expect(new URL(login.url()).pathname).toBe(item.landing)
        await login.close()

        for (const routePath of item.pages) {
          const page = await context.newPage()
          try {
            await inspectPage(page, routePath, item.role)
          } finally {
            await page.close()
          }
        }

        if (item.role !== 'admin') {
          const deniedApiPaths = ['/api/users', '/api/admin/control/dashboard']
          if (item.role === 'citizen') deniedApiPaths.push('/api/support/officer/queue')
          for (const apiPath of deniedApiPaths) {
            const response = await context.request.get(`${apiUrl}${apiPath}`, { timeout: 30_000 })
            expect(response.status(), `${item.role} must not read ${apiPath}`).toBe(403)
          }

          const deniedRoutes = item.role === 'officer'
            ? ['/users']
            : ['/users', '/officer-dashboard']
          for (const routePath of deniedRoutes) {
            const page = await context.newPage()
            try {
              await page.goto(`${appUrl}${routePath}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
              await expect(page, `${item.role} must be redirected from ${routePath}`)
                .toHaveURL(`${appUrl}/search`, { timeout: 30_000 })
            } finally {
              await page.close()
            }
          }
        }

        const logout = await context.request.post(`${apiUrl}/api/auth/logout`, { timeout: 30_000 })
        expect(logout.status(), `${item.role} logout must succeed`).toBe(200)
        const revoked = await context.request.get(`${apiUrl}/api/users/me`, { timeout: 30_000 })
        expect(revoked.status(), `${item.role} session must be revoked after logout`).toBe(401)
      } finally {
        await context.close()
      }
    })
  })
}
