import { expect, test } from '@playwright/test'

const observedManagementMethods: string[] = []
let deniedManagementReads = 0

const summary = {
  observed_at: '2026-08-09T08:00:00Z',
  as_of: '2026-08-09',
  documents: { total: 1, active: 1, inactive: 0, missing_source: 0, missing_metadata: 0 },
  structure: { articles: 2, chunks: 2 },
  tiers: { core: 1, expanded: 0 },
  validity: { status: 'available', counts: { active: 1 } },
  operations: { status: 'available', pending_document_candidates: 22, import_queue: { pending: 0 } },
  vectors: { status: 'unavailable', reason_code: 'VECTOR_STORE_TIMEOUT', message: 'Kho vector chua phan hoi.' },
  faq_impacts: { status: 'unavailable', reason_code: 'FAQ_RELATIONSHIPS_NOT_CONFIGURED', message: 'Chua co lien ket FAQ.' },
}

const documentItem = {
  doc_id: 1,
  document_title: 'Quyet dinh thu nghiem quan ly van ban',
  law_number: '01/2026/QD-TEST',
  document_type: 'Quyet dinh',
  issuing_agency: 'UBND thanh pho Hai Phong',
  stored_status: 'active',
  as_of_status: 'active',
  validity_status: 'active',
  effective_date: '2026-01-01',
  source_url: 'https://example.gov.vn/01-2026',
  retrieval_tier: 'core',
  article_count: 2,
  chunk_count: 2,
  quality_flags: [],
}

test.beforeEach(async ({ page }, testInfo) => {
  const role = testInfo.title.startsWith('citizen')
    ? 'citizen'
    : testInfo.title.startsWith('officer')
      ? 'officer'
      : 'admin'
  observedManagementMethods.length = 0
  deniedManagementReads = 0
  await page.addInitScript((seededRole) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: {
        token: `isolated-${seededRole}-token`,
        role: seededRole,
        isAuthenticated: true,
        authMode: 'legacy_password',
        lastAuthCheck: Date.now(),
      },
      version: 0,
    }))
  }, role)

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.includes('/api/legal/management/')) {
      observedManagementMethods.push(request.method())
      if (role !== 'admin') {
        deniedManagementReads += 1
        return route.fulfill({ status: 403, json: { detail: 'Admin role required.' } })
      }
    }

    if (url.pathname === '/api/config') {
      return route.fulfill({ json: { version: 'test' } })
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { auth_enabled: true, available_roles: ['admin'] } })
    }
    if (url.pathname === '/api/users/me') {
      return route.fulfill({ json: { id: `${role}-test`, username: role, role, auth_mode: 'legacy_password' } })
    }
    if (url.pathname === '/api/legal/management/summary') {
      return route.fulfill({ json: summary })
    }
    if (url.pathname === '/api/legal/management/documents') {
      return route.fulfill({ json: { items: [documentItem], total: 1, limit: 30, offset: 0, as_of: '2026-08-09', observed_at: summary.observed_at } })
    }
    if (url.pathname === '/api/legal/management/documents/1') {
      return route.fulfill({ json: {
        observed_at: summary.observed_at,
        document: documentItem,
        structure: { article_count: 2, chunk_count: 2, articles: [{ article_number: '1', title: 'Pham vi' }] },
        relationships: { status: 'unavailable', message: 'Chua co bang quan he.' },
        validity: { status: 'available', observations: [], events: [], decisions: [] },
        vectors: summary.vectors,
        faq_impacts: summary.faq_impacts,
        versions: { status: 'available', legacy_version: null, items: [] },
        audit: { status: 'available', items: [] },
      } })
    }
    return route.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })
})

test('admin can inspect metadata and explicit unavailable states without mutations', async ({ page }) => {
  await page.goto('/legal-management')
  await expect(page.getByRole('heading', { name: /Kho văn bản pháp luật/i })).toBeVisible()
  await expect(page.getByText('01/2026/QD-TEST')).toBeVisible()
  await page.getByText('Tình trạng kho tra cứu', { exact: true }).click()
  await expect(page.getByText('Không thể đọc dữ liệu tại thời điểm này.', { exact: true })).toBeVisible()
  await expect(page.getByText('Tìm chính xác số, ký hiệu: Chưa sẵn sàng')).toBeVisible()
  await expect(page.locator('main').getByRole('button', { name: /^(Xóa|Lưu|Cập nhật|Nhập văn bản)$/i })).toHaveCount(0)

  await expect(page.getByRole('link', { name: /Quyet dinh thu nghiem/i })).toHaveAttribute('href', '/legal-management/1')
  await page.goto('/legal-management/1')
  await expect(page).toHaveURL(/\/legal-management\/1$/, { timeout: 30_000 })
  await expect(page.getByRole('link', { name: 'Mở nguồn' })).toHaveAttribute(
    'href',
    'https://example.gov.vn/01-2026',
    { timeout: 30_000 },
  )
  await expect(page.locator('main').getByRole('button', { name: /^(Xóa|Lưu|Cập nhật|Nhập văn bản)$/i })).toHaveCount(0)
  expect(observedManagementMethods.length).toBeGreaterThan(0)
  expect(new Set(observedManagementMethods)).toEqual(new Set(['GET']))
})

for (const role of ['citizen', 'officer'] as const) {
  test(`${role} is redirected before management data is read`, async ({ page }) => {
    await page.goto('/legal-management')
    await expect(page).toHaveURL(/\/search$/, { timeout: 30_000 })
    await expect(page.getByRole('heading', { name: /Quản lý kho văn bản/i })).toHaveCount(0)
    // Authorization is enforced before any management metadata request. A
    // zero-request result is the desired privacy and performance behavior.
    expect(observedManagementMethods).toEqual([])
    expect(deniedManagementReads).toBe(0)
  })
}
