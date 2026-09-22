import { expect, test } from '@playwright/test'

let lifecycleMethods: string[] = []

test.beforeEach(async ({ page }, testInfo) => {
  const appRole = testInfo.title.startsWith('citizen')
    ? 'citizen'
    : testInfo.title.startsWith('officer') ? 'officer' : 'admin'
  const editor = testInfo.title.startsWith('editor')
  lifecycleMethods = []

  await page.addInitScript(({ role }) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: { token: `phase2-${role}-token`, role, isAuthenticated: true, authMode: 'legacy_password', lastAuthCheck: Date.now() },
      version: 0,
    }))
  }, { role: appRole })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.includes('/api/legal/lifecycle/')) lifecycleMethods.push(request.method())
    if (path === '/api/config') return route.fulfill({ json: { version: 'phase2-test' } })
    if (path === '/api/auth/status') return route.fulfill({ json: { auth_enabled: true, available_roles: ['admin', 'officer', 'citizen'] } })
    if (path === '/api/users/me') return route.fulfill({ json: { id: `${appRole}-test`, username: appRole, role: appRole, auth_mode: 'legacy_password' } })
    if (appRole !== 'admin' && path.includes('/api/legal/lifecycle/')) return route.fulfill({ status: 403, json: { detail: { code: 'lifecycle_forbidden', message: 'Denied.' } } })
    if (path === '/api/legal/lifecycle/capabilities') return route.fulfill({ json: {
      actor_id: editor ? 'user:editor' : 'user:admin', authenticated_admin: true,
      writes_enabled: editor, editor, reviewer: false, activation_enabled: false,
    } })
    if (path === '/api/legal/lifecycle/drafts' && request.method() === 'GET') return route.fulfill({ json: { items: [], limit: 50, offset: 0 } })
    if (path === '/api/legal/lifecycle/drafts' && request.method() === 'POST') return route.fulfill({ status: 201, headers: { ETag: '"1"' }, json: {
      id: 'legal_document_draft:e2e001', state: 'draft', revision: 1,
      title: 'Văn bản Phase 2 thử nghiệm', law_number: '01/2026/QĐ-TEST',
    } })
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })
})

test('admin sees the closed gate without any lifecycle mutation', async ({ page }) => {
  await page.goto('/legal-management/drafts')
  await expect(page.getByRole('heading', { name: 'Bản nháp và phiên bản' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Chức năng biên tập đang tạm khóa' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Tài khoản chưa được phân quyền' })).toBeVisible()
  await expect(page.getByRole('button', { name: /kích hoạt/i })).toHaveCount(0)
  expect(new Set(lifecycleMethods)).toEqual(new Set(['GET']))
})

test('editor creates only a staging draft and has no activation control', async ({ page }) => {
  await page.goto('/legal-management/drafts')
  await page.getByLabel('Tiêu đề bản nháp').fill('Văn bản Phase 2 thử nghiệm')
  await page.getByLabel('Số ký hiệu bản nháp').fill('01/2026/QĐ-TEST')
  await page.getByLabel('URL nguồn chính thức').fill('https://vbpl.vn/example')
  await page.getByLabel('Nội dung bản nháp').fill('Điều 1. Nội dung thử nghiệm.')
  await page.getByLabel('Lý do thay đổi').fill('Tạo bản nháp cô lập để kiểm thử Phase 2.')
  await page.getByRole('button', { name: 'Lưu bản nháp' }).click()
  await expect(page.getByText('Đã tạo bản nháp trong khu vực chuẩn bị.')).toBeVisible()
  await expect(page.getByRole('button', { name: /kích hoạt/i })).toHaveCount(0)
  expect(lifecycleMethods).toContain('POST')
})

for (const role of ['citizen', 'officer'] as const) {
  test(`${role} is redirected before lifecycle data is exposed`, async ({ page }) => {
    await page.goto('/legal-management/drafts')
    await expect(page).toHaveURL(/\/search$/, { timeout: 30_000 })
    await expect(page.getByRole('heading', { name: 'Bản nháp và phiên bản' })).toHaveCount(0)
  })
}
