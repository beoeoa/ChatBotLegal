import { randomBytes } from 'node:crypto'
import { expect, test, type BrowserContext } from '@playwright/test'

const enabled = process.env.LIVE_PUBLIC_AUTH_ACCEPTANCE === 'true'
const apiUrl = (process.env.CHATBOTLEGAL_API_URL || 'http://127.0.0.1:5055').replace(/\/$/, '')
const appUrl = (process.env.E2E_BASE_URL || 'http://127.0.0.1:3000').replace(/\/$/, '')
const adminPassword = process.env.OPEN_NOTEBOOK_ADMIN_PASSWORD || process.env.OPEN_NOTEBOOK_PASSWORD || ''

async function closeContext(context: BrowserContext | null) {
  if (context) await context.close()
}

test.describe('live public account acceptance', () => {
  test.skip(!enabled, 'Set LIVE_PUBLIC_AUTH_ACCEPTANCE=true to run against the local system.')

  test('starts the configured Google provider flow', async ({ browser }) => {
    test.setTimeout(45_000)
    const context = await browser.newContext()
    try {
      const page = await context.newPage()
      await page.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded' })
      const googleButton = page.getByRole('button', { name: 'Tiếp tục với Google', exact: true })
      await expect(googleButton).toBeVisible()

      const popupPromise = context.waitForEvent('page', { timeout: 20_000 }).catch(() => null)
      await googleButton.click()
      const popup = await popupPromise
      await page.waitForTimeout(1_500)
      const providerError = page.getByText(
        /chưa được cấu hình|chưa được cho phép sử dụng đăng nhập Google|không kết nối được dịch vụ đăng nhập|Email đã tồn tại/i,
      ).first()
      if (await providerError.isVisible().catch(() => false)) {
        throw new Error(`Google provider failed on ${new URL(appUrl).hostname}: ${await providerError.innerText()}`)
      }
      expect(popup, 'Google provider did not open an OAuth window.').not.toBeNull()
      if (popup && !popup.isClosed()) {
        await popup.waitForURL(
          url => /accounts\.google\.com|\/__\/auth\/handler/.test(url.toString()),
          { timeout: 20_000 },
        )
        expect(popup.url()).toMatch(/accounts\.google\.com|\/__\/auth\/handler/)
        await popup.close()
      } else {
        await expect(page).toHaveURL(/\/search(?:\?|$)/)
      }
    } finally {
      await context.close()
    }
  })

  test('registers, logs in, resets the password, and cleans up the QA citizen', async ({ browser }) => {
    expect(adminPassword, 'Cần mật khẩu quản trị để khóa mềm tài khoản QA sau kiểm thử.').not.toBe('')

    const runId = `${Date.now().toString(36)}${randomBytes(3).toString('hex')}`.slice(-14)
    const username = `qa_citizen_${runId}`
    const email = `${username}@qa.invalid`
    const firstPassword = `Qa!First-${runId}-9aA`
    const nextPassword = `Qa!Next-${runId}-8bB`
    let createdUserId = ''
    let context: BrowserContext | null = null

    try {
      context = await browser.newContext()
      let page = await context.newPage()
      await page.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded' })
      await page.getByRole('button', { name: 'Đăng ký', exact: true }).click()
      await page.getByLabel('Tên đăng nhập').fill(username)
      await page.getByLabel('Họ và tên').fill('QA công dân xác nhận đăng nhập')
      await page.getByLabel('Giới tính').selectOption('unspecified')
      await page.getByLabel('Email').fill(email)
      await page.getByLabel('Số điện thoại').fill('0900000000')
      await page.getByLabel('Mật khẩu', { exact: true }).fill(firstPassword)
      await page.getByLabel('Nhập lại mật khẩu').fill(firstPassword)
      const registrationResponse = page.waitForResponse(
        response => response.url().endsWith('/api/auth/register') && response.request().method() === 'POST',
      )
      await page.getByRole('button', { name: 'Tạo tài khoản', exact: true }).click()
      const registration = await registrationResponse
      expect(registration.status()).toBe(201)
      createdUserId = String((await registration.json()).user_id || '')
      expect(createdUserId).not.toBe('')
      await expect(page).toHaveURL(/\/search(?:\?|$)/)
      await closeContext(context)
      context = null

      context = await browser.newContext()
      page = await context.newPage()
      await page.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded' })
      await page.getByLabel('Email hoặc tên đăng nhập').fill(username)
      await page.getByLabel('Mật khẩu', { exact: true }).fill(firstPassword)
      await page.locator('form').getByRole('button', { name: 'Đăng nhập', exact: true }).click()
      await expect(page).toHaveURL(/\/search(?:\?|$)/)
      await closeContext(context)
      context = null

      context = await browser.newContext()
      page = await context.newPage()
      await page.goto(`${appUrl}/login`, { waitUntil: 'domcontentloaded' })
      await page.getByRole('button', { name: 'Quên mật khẩu', exact: true }).click()
      await page.getByLabel('Email hoặc tên đăng nhập').fill(username)
      const forgotResponse = page.waitForResponse(
        response => response.url().endsWith('/api/auth/forgot-password') && response.request().method() === 'POST',
      )
      await page.getByRole('button', { name: 'Tạo hướng dẫn đặt lại', exact: true }).click()
      const forgot = await forgotResponse
      expect(forgot.status()).toBe(200)
      const resetToken = String((await forgot.json()).reset_token || '')
      expect(resetToken.length).toBeGreaterThan(15)
      await expect(page.getByLabel('Mã đặt lại mật khẩu')).toHaveValue(resetToken)
      await page.getByLabel('Mật khẩu mới').fill(nextPassword)
      const resetResponse = page.waitForResponse(
        response => response.url().endsWith('/api/auth/reset-password') && response.request().method() === 'POST',
      )
      await page.getByRole('button', { name: 'Đặt lại mật khẩu', exact: true }).click()
      expect((await resetResponse).status()).toBe(200)
      await expect(page.getByText('Đã đặt lại mật khẩu. Bạn có thể đăng nhập bằng mật khẩu mới.')).toBeVisible()

      await page.getByLabel('Email hoặc tên đăng nhập').fill(username)
      await page.getByLabel('Mật khẩu', { exact: true }).fill(nextPassword)
      await page.locator('form').getByRole('button', { name: 'Đăng nhập', exact: true }).click()
      await expect(page).toHaveURL(/\/search(?:\?|$)/)
    } finally {
      await closeContext(context)
      if (createdUserId) {
        const cleanup = await fetch(`${apiUrl}/api/users/${encodeURIComponent(createdUserId)}/soft-delete`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${adminPassword}`,
            'X-User-Role': 'admin',
            'X-Business-Reason': 'Cleanup exact QA citizen created by live public-auth acceptance.',
            'Content-Type': 'application/json',
          },
        })
        expect(cleanup.ok, `Không khóa mềm được tài khoản QA ${createdUserId}: HTTP ${cleanup.status}`).toBe(true)
      }
    }
  })
})
