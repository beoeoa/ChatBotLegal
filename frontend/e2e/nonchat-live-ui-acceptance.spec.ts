import { expect, test, type Browser, type Page } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const enabled = process.env.LIVE_NONCHAT_UI_ACCEPTANCE === 'true'
const apiUrl = (process.env.CHATBOTLEGAL_API_URL || 'http://127.0.0.1:5055').replace(/\/$/, '')
const appUrl = (process.env.E2E_BASE_URL || 'http://127.0.0.1:3000').replace(/\/$/, '')
const adminPassword = process.env.OPEN_NOTEBOOK_ADMIN_PASSWORD || process.env.OPEN_NOTEBOOK_PASSWORD || ''
const businessReason = 'Live non-chat Admin and officer UI acceptance.'

type Unit = {
  id: string
  code: string
  name: string
  short_name?: string | null
  is_active: boolean
  domain_codes?: string[]
  domains?: string[]
}

type QaAccount = {
  id: string
  username: string
  password: string
  role: 'admin' | 'officer' | 'citizen'
  unitId?: string
  unitName?: string
}

type PageResult = {
  role: 'admin' | 'officer' | 'citizen'
  username: string
  unit_id?: string
  unit_name?: string
  path: string
  final_url: string
  heading: string
  content_ready_ms: number
  settled_ms: number
  body_characters: number
  horizontal_overflow_px: number
  unnamed_interactive_controls: number
  raw_internal_messages: string[]
  http_errors: Array<{ status: number; path: string }>
  request_failures: string[]
  aborted_requests: string[]
  console_errors: string[]
  page_errors: string[]
  timings?: { navigation: { response_ms: number; dom_content_loaded_ms: number }; resources: Array<{ path: string; duration_ms: number }> }
  screenshot?: string
  status: 'passed' | 'failed'
  failures: string[]
}

type AcceptanceReport = {
  schema_version: 'nonchat-live-ui-acceptance-v1'
  generated_at: string
  run_id: string
  status: 'passed' | 'failed'
  account_count: number
  active_unit_count: number
  accounts_cleaned: string[]
  login_results: Array<{ username: string; role: string; unit_id?: string; status: string; duration_ms: number }>
  pages: PageResult[]
  performance: {
    sample_count: number
    p50_content_ready_ms: number
    p95_content_ready_ms: number
    p50_settled_ms: number
    p95_settled_ms: number
    over_2_seconds: Array<{ username: string; path: string; settled_ms: number }>
  }
  failures: string[]
}

const adminPages = [
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
]

const officerPages = [
  '/officer-dashboard',
  '/search',
  '/legal-library',
  '/procedures',
  '/notebooks',
  '/live-support',
  '/officer-proposals',
  '/account',
]

const citizenPages = ['/search', '/procedures', '/live-support', '/account']

const rawInternalPatterns = [
  /\{\s*["']?detail["']?\s*:/i,
  /\b(?:document|candidate|organization|retrieval|vector|database|source|import|crawler)_[a-z0-9_]{3,}\b/i,
  /\b(?:Failed to fetch|Internal Server Error|Admin role required|Invalid password|Not Found)\b/i,
]

function percentile(values: number[], ratio: number): number {
  if (!values.length) return 0
  const ordered = [...values].sort((a, b) => a - b)
  return ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * ratio) - 1)]
}

function adminHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    Authorization: `Bearer ${adminPassword}`,
    'X-User-Role': 'admin',
    'X-Business-Reason': businessReason,
    'Content-Type': 'application/json',
    ...extra,
  }
}

async function apiJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init)
  const text = await response.text()
  if (!response.ok) {
    throw new Error(`${init.method || 'GET'} ${new URL(url).pathname} -> HTTP ${response.status}: ${text.slice(0, 500)}`)
  }
  return JSON.parse(text) as T
}

async function createQaAccounts(runId: string, accounts: QaAccount[]): Promise<{ accounts: QaAccount[]; units: Unit[] }> {
  const units = (await apiJson<Unit[]>(`${apiUrl}/api/settings/organization-units`, {
    headers: adminHeaders(),
  })).filter((unit) => unit.is_active)
  if (!units.length) throw new Error('Không có phòng ban đang hoạt động để kiểm thử role cán bộ.')

  const sharedQaPassword = `Qa!${randomBytes(18).toString('base64url')}9aA`
  const specs: Array<Omit<QaAccount, 'id'>> = [
    {
      username: `qa_admin_${runId}`,
      password: sharedQaPassword,
      role: 'admin',
    },
    {
      username: `qa_citizen_${runId}`,
      password: sharedQaPassword,
      role: 'citizen',
    },
    ...units.map((unit, index) => ({
      username: `qa_cb_${index + 1}_${runId}`,
      password: sharedQaPassword,
      role: 'officer' as const,
      unitId: unit.id,
      unitName: unit.name,
    })),
  ]
  for (const spec of specs) {
    const unit = spec.unitId ? units.find((item) => item.id === spec.unitId) : undefined
    const created = await apiJson<{ id: string; username: string }>(`${apiUrl}/api/users`, {
      method: 'POST',
      headers: adminHeaders(),
      body: JSON.stringify({
        username: spec.username,
        email: `${spec.username}@qa.invalid`,
        password: spec.password,
        role: spec.role,
        is_active: true,
        full_name: spec.role === 'admin' ? 'QA Admin nghiệm thu' : spec.role === 'citizen' ? 'QA Người dân nghiệm thu' : `QA Cán bộ ${spec.unitName}`,
        department: spec.unitName || null,
        organization_unit_id: spec.unitId || null,
        allowed_domains: unit?.domain_codes || unit?.domains || [],
        job_title: spec.role === 'officer' ? 'Cán bộ kiểm thử' : 'Quản trị viên kiểm thử',
        notes: 'Tài khoản QA tự động; xóa mềm sau nghiệm thu giao diện.',
        must_change_password: false,
      }),
    })
    accounts.push({ ...spec, id: created.id })
  }
  return { accounts, units }
}

async function cleanupQaAccounts(accounts: QaAccount[]): Promise<string[]> {
  const cleaned: string[] = []
  for (const account of [...accounts].reverse()) {
    try {
      await apiJson(`${apiUrl}/api/users/${encodeURIComponent(account.id)}/soft-delete`, {
        method: 'POST',
        headers: adminHeaders(),
      })
      cleaned.push(account.username)
    } catch {
      // Reported by the caller; never hide a cleanup failure.
    }
  }
  return cleaned
}

async function loginThroughUi(page: Page, account: QaAccount): Promise<number> {
  const started = Date.now()
  await page.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
  await page.getByLabel('Email hoặc tên đăng nhập').fill(account.username)
  await page.getByLabel('Mật khẩu', { exact: true }).fill(account.password)
  await page.locator('form').getByRole('button', { name: 'Đăng nhập', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login(?:\?|$)/, { timeout: 30_000 })
  await expect(page.locator('main').first()).toBeVisible({ timeout: 30_000 })
  return Date.now() - started
}

async function inspectPage(
  page: Page,
  account: QaAccount,
  routePath: string,
  screenshotRoot: string,
): Promise<PageResult> {
  const httpErrors: Array<{ status: number; path: string }> = []
  const requestFailures: string[] = []
  const abortedRequests: string[] = []
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const onResponse = (response: { status(): number; url(): string }) => {
    const status = response.status()
    if (status < 400) return
    const url = new URL(response.url())
    if (url.origin === new URL(apiUrl).origin || url.origin === new URL(appUrl).origin) {
      httpErrors.push({ status, path: url.pathname })
    }
  }
  const onRequestFailed = (request: { url(): string; failure(): { errorText: string } | null }) => {
    const path = new URL(request.url()).pathname
    const error = request.failure()?.errorText || 'không rõ lỗi'
    if (error === 'net::ERR_ABORTED') abortedRequests.push(path)
    else requestFailures.push(`${path}: ${error}`)
  }
  const onConsole = (message: { type(): string; text(): string }) => {
    if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 500))
  }
  const onPageError = (error: Error) => pageErrors.push(error.message.slice(0, 500))
  page.on('response', onResponse)
  page.on('requestfailed', onRequestFailed)
  page.on('console', onConsole)
  page.on('pageerror', onPageError)

  const failures: string[] = []
  const started = Date.now()
  await page.goto(`${appUrl}${routePath}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
  const main = page.locator('main').first()
  await expect(main).toBeVisible({ timeout: 30_000 })
  await page.waitForFunction(() => {
    const element = document.querySelector('main')
    if (!element) return false
    const text = (element.textContent || '').replace(/\s+/g, ' ').trim()
    return text.length >= 40
  }, undefined, { timeout: 30_000 })
  const contentReadyMs = Date.now() - started

  let settledWithinTwoSeconds = true
  // Keep observing slow pages so screenshots and functionality checks refer
  // to actual data, not a loading shell. The SLA stays strictly two seconds.
  await page.waitForFunction(() => {
    const main = document.querySelector('main')
    if (!main) return false
    const busy = Array.from(main.querySelectorAll('[aria-busy="true"], [data-loading="true"]'))
      .some((element) => {
        const html = element as HTMLElement
        const style = getComputedStyle(html)
        return style.display !== 'none' && style.visibility !== 'hidden'
      })
    const loadingLabel = Array.from(main.querySelectorAll('h1,h2,p,span,div'))
      .some((element) => {
        if (element.children.length) return false
        const text = (element.textContent || '').replace(/\s+/g, ' ').trim()
        if (!/^(Đang tải|Đang lấy dữ liệu|Đang kiểm tra)(?:[….\s]|$)/i.test(text)) return false
        const html = element as HTMLElement
        const style = getComputedStyle(html)
        return style.display !== 'none' && style.visibility !== 'hidden'
      })
    return !busy && !loadingLabel
  }, undefined, { timeout: 30_000 }).catch(() => {
    settledWithinTwoSeconds = false
    failures.push('Trang vẫn chưa tải xong dữ liệu sau 30 giây.')
  })
  const settledMs = Date.now() - started
  settledWithinTwoSeconds = settledWithinTwoSeconds && settledMs <= 2000
  // Visual stabilization for screenshots is not included in the data-ready timer.
  await page.waitForTimeout(100)

  const metrics = await page.evaluate(() => {
    const main = document.querySelector('main')
    const bodyText = (document.body.innerText || '').replace(/\s+/g, ' ').trim()
    const heading = main?.querySelector('h1,h2')?.textContent?.replace(/\s+/g, ' ').trim() || ''
    const controls = Array.from(main?.querySelectorAll('button,a[href],input,select,textarea') || [])
    const visible = controls.filter((element) => {
      const html = element as HTMLElement
      const style = getComputedStyle(html)
      return style.visibility !== 'hidden' && style.display !== 'none' && html.getBoundingClientRect().width > 0
    })
    const unnamed = visible.filter((element) => {
      const html = element as HTMLElement
      const input = element as HTMLInputElement
      const text = (html.innerText || input.value || '').trim()
      const label = html.getAttribute('aria-label') || html.getAttribute('title') || input.placeholder || ''
      return !text && !label
    })
    return {
      bodyText,
      heading,
      mainTextLength: (main?.textContent || '').trim().length,
      overflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      unnamedControls: unnamed.length,
      timings: {
        navigation: (() => {
          const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
          return { response_ms: Math.round(nav?.responseEnd || 0), dom_content_loaded_ms: Math.round(nav?.domContentLoadedEventEnd || 0) }
        })(),
        resources: performance.getEntriesByType('resource')
          .filter((entry) => /\/(api\/|config(?:$|\?))/.test(entry.name))
          .map((entry) => ({ path: new URL(entry.name).pathname, duration_ms: Math.round(entry.duration) })),
      },
    }
  })

  const rawInternalMessages = rawInternalPatterns
    .flatMap((pattern) => metrics.bodyText.match(new RegExp(pattern.source, `${pattern.flags}g`)) || [])
    .slice(0, 20)
  if (!metrics.heading) failures.push('Trang không có tiêu đề chính h1/h2 có thể nhận biết.')
  if (metrics.mainTextLength < 40) failures.push('Nội dung chính quá ít hoặc chưa tải.')
  if (metrics.overflow > 2) failures.push(`Trang tràn ngang ${metrics.overflow}px.`)
  if (!settledWithinTwoSeconds) failures.push('Dữ liệu chính chưa hiển thị ổn định trong 2 giây.')
  if (httpErrors.some((item) => item.status >= 500)) failures.push('Có API trả lỗi 5xx khi mở trang.')
  if (httpErrors.some((item) => item.status === 401 || item.status === 403)) failures.push('Trang gửi yêu cầu không được cấp quyền.')
  if (pageErrors.length) failures.push('Có lỗi JavaScript làm hỏng trang.')
  if (rawInternalMessages.length) failures.push('Giao diện lộ mã lỗi hoặc thuật ngữ nội bộ chưa Việt hóa.')

  const finalPath = new URL(page.url()).pathname.replace(/\/$/, '') || '/'
  const expectedPath = routePath.replace(/\/$/, '') || '/'
  if (finalPath !== expectedPath) failures.push(`Trang chuyển hướng ngoài dự kiến: ${finalPath}.`)

  const roleFolder = account.role === 'officer' ? `officer-${account.unitId || 'unknown'}` : account.role
  const safeName = routePath.replace(/^\//, '').replace(/[^a-zA-Z0-9_-]+/g, '-') || 'home'
  let screenshot: string | undefined
  if (process.env.QA_SCREENSHOTS === 'true' && process.env.E2E_PRIVACY_SAFE !== 'true') {
    screenshot = path.join(screenshotRoot, roleFolder, `${safeName}.png`)
    await mkdir(path.dirname(screenshot), { recursive: true })
    await page.screenshot({ path: screenshot, fullPage: false })
  }

  page.off('response', onResponse)
  page.off('requestfailed', onRequestFailed)
  page.off('console', onConsole)
  page.off('pageerror', onPageError)
  return {
    role: account.role,
    username: account.username,
    unit_id: account.unitId,
    unit_name: account.unitName,
    path: routePath,
    final_url: page.url(),
    heading: metrics.heading,
    content_ready_ms: contentReadyMs,
    settled_ms: settledMs,
    body_characters: metrics.mainTextLength,
    horizontal_overflow_px: metrics.overflow,
    unnamed_interactive_controls: metrics.unnamedControls,
    raw_internal_messages: rawInternalMessages,
    http_errors: httpErrors,
    request_failures: requestFailures,
    aborted_requests: abortedRequests,
    console_errors: consoleErrors,
    page_errors: pageErrors,
    timings: metrics.timings,
    screenshot,
    status: failures.length ? 'failed' : 'passed',
    failures,
  }
}

async function runAccount(
  browser: Browser,
  account: QaAccount,
  screenshotRoot: string,
): Promise<{ loginMs: number; pages: PageResult[] }> {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'vi-VN' })
  const page = await context.newPage()
  const loginMs = await loginThroughUi(page, account)
  await page.close()
  const paths = account.role === 'admin' ? adminPages : account.role === 'citizen' ? citizenPages : officerPages
  const pages: PageResult[] = []
  for (const routePath of paths) {
    // A new document shares login storage, but cannot inherit requests from the
    // preceding page (especially the post-login search screen).
    const page = await context.newPage()
    try {
      const result = await inspectPage(page, account, routePath, screenshotRoot)
      pages.push(result)
      console.log(`${account.role} ${routePath}: ${result.status}, data=${result.settled_ms}ms`)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      pages.push({
        role: account.role,
        username: account.username,
        unit_id: account.unitId,
        unit_name: account.unitName,
        path: routePath,
        final_url: page.url(),
        heading: '',
        content_ready_ms: 30_000,
        settled_ms: 30_000,
        body_characters: 0,
        horizontal_overflow_px: 0,
        unnamed_interactive_controls: 0,
        raw_internal_messages: [],
        http_errors: [],
        request_failures: [],
        aborted_requests: [],
        console_errors: [],
        page_errors: [],
        status: 'failed',
        failures: [`Không mở/đọc được trang: ${message}`],
      })
    } finally {
      await page.close()
    }
  }
  await context.close()
  return { loginMs, pages }
}

test.describe('live non-chat Admin/officer acceptance', () => {
  test.skip(!enabled, 'Set LIVE_NONCHAT_UI_ACCEPTANCE=true to run against the local system.')
  test.setTimeout(10 * 60_000)

  test('real login and direct UI checks for Admin plus every active department', async ({ browser }) => {
    expect(adminPassword, 'Cần cấu hình mật khẩu cục bộ để tạo tài khoản QA.').not.toBe('')
    const runId = `${Date.now().toString(36)}${randomBytes(3).toString('hex')}`.slice(-14)
    const reportRoot = path.resolve(process.cwd(), '..', 'reports', 'nonchat-acceptance')
    const screenshotRoot = path.join(reportRoot, `ui-${runId}`)
    const reportPath = path.join(reportRoot, `ui-${runId}.json`)
    await mkdir(reportRoot, { recursive: true })

    const accounts: QaAccount[] = []
    let activeUnitCount = 0
    let accountsCleaned: string[] = []
    const pages: PageResult[] = []
    const loginResults: AcceptanceReport['login_results'] = []
    const failures: string[] = []
    try {
      const created = await createQaAccounts(runId, accounts)
      activeUnitCount = created.units.length
      for (const account of accounts) {
        const loginStarted = Date.now()
        try {
          const result = await runAccount(browser, account, screenshotRoot)
          loginResults.push({
            username: account.username,
            role: account.role,
            unit_id: account.unitId,
            status: 'passed',
            duration_ms: result.loginMs,
          })
          pages.push(...result.pages)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          loginResults.push({
            username: account.username,
            role: account.role,
            unit_id: account.unitId,
            status: `failed: ${message}`,
            duration_ms: Date.now() - loginStarted,
          })
          failures.push(`${account.username}: đăng nhập hoặc khởi tạo phiên thất bại: ${message}`)
        }
      }
    } catch (error) {
      failures.push(error instanceof Error ? error.message : String(error))
    } finally {
      accountsCleaned = await cleanupQaAccounts(accounts)
      const missingCleanup = accounts.filter((account) => !accountsCleaned.includes(account.username))
      if (missingCleanup.length) failures.push(`Chưa xóa mềm ${missingCleanup.length} tài khoản QA.`)
    }

    for (const pageResult of pages) {
      if (pageResult.status === 'failed') {
        failures.push(`${pageResult.username} ${pageResult.path}: ${pageResult.failures.join(' ')}`)
      }
    }
    const contentValues = pages.map((item) => item.content_ready_ms)
    const settledValues = pages.map((item) => item.settled_ms)
    const report: AcceptanceReport = {
      schema_version: 'nonchat-live-ui-acceptance-v1',
      generated_at: new Date().toISOString(),
      run_id: runId,
      status: failures.length ? 'failed' : 'passed',
      account_count: accounts.length,
      active_unit_count: activeUnitCount,
      accounts_cleaned: accountsCleaned,
      login_results: loginResults,
      pages,
      performance: {
        sample_count: pages.length,
        p50_content_ready_ms: percentile(contentValues, 0.5),
        p95_content_ready_ms: percentile(contentValues, 0.95),
        p50_settled_ms: percentile(settledValues, 0.5),
        p95_settled_ms: percentile(settledValues, 0.95),
        over_2_seconds: pages
          .filter((item) => item.settled_ms > 2000)
          .map((item) => ({ username: item.username, path: item.path, settled_ms: item.settled_ms })),
      },
      failures,
    }
    await writeFile(reportPath, JSON.stringify(report, null, 2), 'utf8')
    expect(failures, `Xem báo cáo ${reportPath}`).toEqual([])
  })
})
