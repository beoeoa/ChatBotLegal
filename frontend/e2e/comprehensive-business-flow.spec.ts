import { test, expect } from '@playwright/test'

const APP_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:3000'

const CITIZEN_TOKEN = 'Gubiuwx70mIZEPUYOSJIMwfYuSEB'
const OFFICER_TOKEN = 'ODqDVgi3TEQE5oVZB17a377adKnV'
const ADMIN_TOKEN = 'beoeoa123456'

function setAuthStorage(role: 'citizen' | 'officer' | 'admin', token: string) {
  return (page: any) => {
    return page.addInitScript(({ role, token }: { role: string; token: string }) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({
          state: {
            token,
            role,
            userId: `e2e-${role}`,
            username: `e2e_${role}`,
            email: `e2e_${role}@local.invalid`,
            isAuthenticated: true,
            hasHydrated: true,
            authMode: 'legacy_password',
            mustChangePassword: false,
          },
          version: 0,
        })
      )
    }, { role, token })
  }
}

test.describe('E2E Toàn diện Nghiệp vụ & Giao diện Trực quan', () => {

  test('1. Giao diện Chatbot & Người dân (Citizen Flow)', async ({ page }) => {
    test.setTimeout(90_000)
    await setAuthStorage('citizen', CITIZEN_TOKEN)(page)

    console.log('--- Đang kiểm tra giao diện Chatbot & Người dân ---')
    await page.goto(`${APP_URL}/search`, { waitUntil: 'domcontentloaded', timeout: 30_000 })

    // Kiểm tra các thành phần cốt lõi của màn hình chat
    const textarea = page.locator('#ask-question')
    await expect(textarea).toBeVisible({ timeout: 20_000 })
    console.log('✓ Ô nhập câu hỏi #ask-question hiển thị tốt')

    // Nhập câu hỏi kiểm thử Fastpath điều luật chính xác
    await textarea.fill('Điều 15 Luật Hộ tịch quy định gì?')
    await page.waitForTimeout(500)
    await textarea.press('Enter')
    console.log('✓ Đã gửi câu hỏi qua giao diện web')

    // Chờ câu trả lời của Chatbot render ra màn hình
    const answerContainer = page.locator('main').first()
    await expect(answerContainer).toContainText(/Hộ tịch|khai sinh|Trách nhiệm/i, { timeout: 45_000 })
    console.log('✓ Câu trả lời của Chatbot đã xuất hiện trên giao diện')

    // Chụp ảnh bằng chứng
    await page.screenshot({ path: 'test-results/01-citizen-chatbot-ui.png', fullPage: false })
    console.log('✓ Đã chụp ảnh màn hình: test-results/01-citizen-chatbot-ui.png')
  })

  test('2. Giao diện Cán bộ cấp xã (Officer Flow)', async ({ page }) => {
    test.setTimeout(60_000)
    await setAuthStorage('officer', OFFICER_TOKEN)(page)

    console.log('--- Đang kiểm tra giao diện Cán bộ cấp xã ---')
    // 2.1 Màn hình Danh mục Thủ tục hành chính
    await page.goto(`${APP_URL}/procedures`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 20_000 })
    const searchProcInput = page.getByRole('textbox', { name: /Tìm thủ tục|Tìm kiếm/i }).first()
    if (await searchProcInput.isVisible().catch(() => false)) {
      console.log('✓ Ô tìm kiếm thủ tục hành chính hiển thị tốt')
    }

    // 2.2 Màn hình Hỗ trợ trực tiếp / Hàng đợi công dân
    await page.goto(`${APP_URL}/live-support`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 20_000 })
    console.log('✓ Trang Hỗ trợ công dân (/live-support) tải thành công')

    // Chụp ảnh bằng chứng
    await page.screenshot({ path: 'test-results/02-officer-ui.png', fullPage: false })
    console.log('✓ Đã chụp ảnh màn hình: test-results/02-officer-ui.png')
  })

  test('3. Giao diện Quản trị viên (Admin Flow)', async ({ page }) => {
    test.setTimeout(60_000)
    await setAuthStorage('admin', ADMIN_TOKEN)(page)

    console.log('--- Đang kiểm tra giao diện Quản trị viên ---')
    // 3.1 Trang Admin Dashboard
    await page.goto(`${APP_URL}/admin`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 20_000 })
    console.log('✓ Bảng điều khiển Quản trị viên (/admin) tải thành công')

    // 3.2 Trang Quản lý Văn bản Pháp luật (/legal-management)
    await page.goto(`${APP_URL}/legal-management`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 20_000 })
    console.log('✓ Trang Quản lý văn bản pháp luật (/legal-management) tải thành công')

    // 3.3 Trang Thu thập & Đồng bộ VBPL (/legal-import)
    await page.goto(`${APP_URL}/legal-import`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await expect(page.locator('main').first()).toBeVisible({ timeout: 20_000 })
    console.log('✓ Trang Thu thập văn bản (/legal-import) tải thành công')

    // Chụp ảnh bằng chứng
    await page.screenshot({ path: 'test-results/03-admin-ui.png', fullPage: false })
    console.log('✓ Đã chụp ảnh màn hình: test-results/03-admin-ui.png')
  })

  test('4. Ranh giới Bảo mật Phân quyền Giao diện (Security Boundary)', async ({ page }) => {
    test.setTimeout(45_000)
    await setAuthStorage('citizen', CITIZEN_TOKEN)(page)

    console.log('--- Đang kiểm thử ranh giới bảo mật phân quyền ---')
    // Người dân cố tình truy cập vào trang Admin
    await page.goto(`${APP_URL}/admin`, { waitUntil: 'domcontentloaded', timeout: 20_000 })
    await page.waitForTimeout(2000)

    const currentUrl = page.url()
    const isProtected = !currentUrl.endsWith('/admin') || (await page.getByText(/không có quyền|forbidden|từ chối|đăng nhập/i).first().isVisible().catch(() => false))
    expect(isProtected).toBeTruthy()
    console.log(`✓ Người dân bị chặn khi vào /admin (URL chuyển về: ${new URL(currentUrl).pathname})`)

    // Chụp ảnh bằng chứng
    await page.screenshot({ path: 'test-results/04-security-boundary-ui.png', fullPage: false })
    console.log('✓ Đã chụp ảnh màn hình: test-results/04-security-boundary-ui.png')
  })

})
