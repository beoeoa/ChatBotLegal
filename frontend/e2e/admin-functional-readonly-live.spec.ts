import { expect, test, type BrowserContext, type Page } from '@playwright/test'

const enabled = process.env.LIVE_ADMIN_FUNCTIONAL_READONLY === 'true'
const appUrl = (process.env.E2E_BASE_URL || 'http://127.0.0.1:3000').replace(/\/$/, '')
const apiUrl = (process.env.CHATBOTLEGAL_API_URL || 'http://127.0.0.1:5055').replace(/\/$/, '')

let adminContext: BrowserContext

async function inspect(route: string, run: (page: Page) => Promise<void>) {
  const page = await adminContext.newPage()
  const writes: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      writes.push(`${request.method()} ${url.pathname}`)
    }
  })
  try {
    await page.goto(`${appUrl}${route}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 30_000 })
    await run(page)
    expect(writes, `Read-only Admin check must not write while visiting ${route}`).toEqual([])
  } finally {
    await page.close()
  }
}

test.describe('live Admin functional read-only acceptance', () => {
  test.skip(!enabled, 'Set LIVE_ADMIN_FUNCTIONAL_READONLY=true and E2E_ADMIN_PASSWORD to use the live Admin account.')
  test.setTimeout(90_000)

  test.beforeAll(async ({ browser }) => {
    const password = process.env.E2E_ADMIN_PASSWORD
    expect(password, 'E2E_ADMIN_PASSWORD is required for live Admin acceptance').toBeTruthy()
    adminContext = await browser.newContext({ locale: 'vi-VN', viewport: { width: 1440, height: 900 } })
    const page = await adminContext.newPage()
    await page.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
    await page.getByLabel('Email hoặc tên đăng nhập').fill(process.env.E2E_ADMIN_USERNAME || 'admin')
    await page.getByLabel('Mật khẩu', { exact: true }).fill(password!)
    await page.locator('form').getByRole('button', { name: 'Đăng nhập', exact: true }).click()
    await expect(page).toHaveURL(`${appUrl}/admin`, { timeout: 30_000 })
    const response = await adminContext.request.get(`${apiUrl}/api/users/me`)
    expect(response.status()).toBe(200)
    expect((await response.json() as { role?: string }).role).toBe('admin')
    await page.close()
  })

  test.afterAll(async () => {
    if (!adminContext) return
    await adminContext.request.post(`${apiUrl}/api/auth/logout`).catch(() => undefined)
    await adminContext.close()
  })

  test('dashboard switches all four operational tabs', async () => {
    await inspect('/admin', async (page) => {
      await expect(page.getByRole('heading', { name: 'Tổng quan hệ thống' })).toBeVisible()
      for (const name of [/Cảnh báo & Dịch vụ/, /Phòng ban & Dữ liệu/, /Trợ lý AI & Kho tri thức/, /Người dùng & Kiểm toán/]) {
        const tab = page.getByRole('tab', { name })
        await tab.click()
        await expect(tab).toHaveAttribute('data-state', 'active')
      }
    })
  })

  test('activity filters can be applied without changing records', async () => {
    await inspect('/admin/activity', async (page) => {
      await expect(page.getByRole('heading', { name: 'Nhật ký quản trị' })).toBeVisible()
      await page.locator('#activity-role').selectOption('admin')
      await page.getByRole('button', { name: 'Lọc nhật ký' }).click()
      await expect(page.locator('#activity-role')).toHaveValue('admin')
      await expect(page.getByRole('button', { name: 'Xuất Excel' })).toBeVisible()
    })
  })

  test('legal inventory filters and validity view work', async () => {
    await inspect('/legal-management', async (page) => {
      await expect(page.getByRole('heading', { name: 'Kho văn bản pháp luật' })).toBeVisible()
      await page.getByRole('textbox', { name: 'Tìm văn bản' }).fill('QA-NOT-FOUND-ADMIN-READONLY')
      await page.getByRole('button', { name: 'Áp dụng bộ lọc' }).click()
      await expect(page).toHaveURL(/q=QA-NOT-FOUND-ADMIN-READONLY/)
      await page.getByRole('button', { name: 'Xóa lọc' }).click()
      await expect(page).toHaveURL(`${appUrl}/legal-management`)
      await page.getByRole('link', { name: 'Văn bản cần kiểm tra hiệu lực' }).click()
      await expect(page).toHaveURL(`${appUrl}/legal-management/validity`)
    })
  })

  test('import queue and manual intake tabs remain usable', async () => {
    await inspect('/legal-import', async (page) => {
      await expect(page.getByRole('heading', { name: 'Tiếp nhận và duyệt văn bản' })).toBeVisible()
      const intake = page.getByRole('tab', { name: 'Thêm văn bản' })
      await intake.click()
      await expect(intake).toHaveAttribute('data-state', 'active')
      const proposals = page.getByRole('tab', { name: /Đề xuất chờ duyệt/ })
      await proposals.click()
      await expect(proposals).toHaveAttribute('data-state', 'active')
    })
  })

  test('public library and procedure search are available to Admin', async () => {
    await inspect('/legal-library', async (page) => {
      await expect(page.locator('main').first()).toContainText(/Kho|Văn bản|Tra cứu/i)
    })
    await inspect('/procedures', async (page) => {
      const search = page.getByRole('textbox', { name: 'Tìm thủ tục hành chính' })
      await expect(search).toBeVisible()
      await search.fill('QA-NOT-FOUND-ADMIN-READONLY')
      await expect(search).toHaveValue('QA-NOT-FOUND-ADMIN-READONLY')
    })
  })

  test('procedure and form management surfaces are reachable', async () => {
    await inspect('/procedure-management', async (page) => {
      await expect(page.getByRole('heading', { name: 'Thủ tục và biểu mẫu' })).toBeVisible()
      await expect(page.getByRole('link', { name: 'Kho thủ tục công khai' })).toHaveAttribute('href', '/procedures')
    })
    await inspect('/faq-management', async (page) => {
      await expect(page.getByRole('heading', { name: 'Quản lý thủ tục hành chính' })).toBeVisible()
      await page.getByPlaceholder('Tìm theo tên hoặc nội dung hướng dẫn...').fill('QA-NOT-FOUND-ADMIN-READONLY')
      await expect(page.getByPlaceholder('Tìm theo tên hoặc nội dung hướng dẫn...')).toHaveValue('QA-NOT-FOUND-ADMIN-READONLY')
    })
  })

  test('department and user filters can be inspected without saving', async () => {
    await inspect('/departments', async (page) => {
      await expect(page.getByRole('heading', { name: 'Quản lý phòng ban' })).toBeVisible()
      await page.getByRole('textbox', { name: 'Tìm phòng ban hoặc lĩnh vực' }).fill('Công an')
      await expect(page.locator('article').first()).toBeVisible()
      await page.getByRole('button', { name: 'Thêm phòng ban' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await page.keyboard.press('Escape')
    })
    await inspect('/users', async (page) => {
      await expect(page.getByRole('heading', { name: 'Quản lý tài khoản' }).first()).toBeVisible()
      await page.getByLabel('Lọc theo vai trò').selectOption('officer')
      await expect(page.getByLabel('Lọc theo vai trò')).toHaveValue('officer')
      await page.getByRole('button', { name: 'Tạo tài khoản' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await page.keyboard.press('Escape')
    })
  })

  test('notebooks, AI settings, system settings and account load', async () => {
    for (const [route, heading] of [
      ['/notebooks', /Hồ sơ pháp lý/],
      ['/settings/api-keys', /API|Model|mô hình/i],
      ['/settings', /Cài đặt/],
      ['/account', /Tài khoản của tôi/],
    ] as const) {
      await inspect(route, async (page) => {
        await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible({ timeout: 30_000 })
      })
    }
  })
})
