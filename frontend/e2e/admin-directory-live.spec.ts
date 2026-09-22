import { expect, test } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const enabled = process.env.LIVE_ADMIN_DIRECTORY_ACCEPTANCE === 'true'
const apiUrl = (process.env.CHATBOTLEGAL_API_URL || 'http://127.0.0.1:5055').replace(/\/$/, '')
const adminPassword = process.env.OPEN_NOTEBOOK_ADMIN_PASSWORD || process.env.OPEN_NOTEBOOK_PASSWORD || ''
const businessReason = 'Live Admin directory dropdown acceptance.'

type DomainAssignment = { domain_code?: string }
type Unit = {
  id: string
  name: string
  short_name?: string | null
  is_active: boolean
  domain_codes?: string[]
  domain_assignments?: DomainAssignment[]
}
type Domain = { code: string; name: string; is_active: boolean }
type Settings = { organization_units?: Unit[]; legal_domains?: Domain[] }

function headers(extra: Record<string, string> = {}) {
  return {
    Authorization: `Bearer ${adminPassword}`,
    'X-User-Role': 'admin',
    'X-Business-Reason': businessReason,
    'Content-Type': 'application/json',
    ...extra,
  }
}

async function apiJson<T>(route: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiUrl}${route}`, init)
  const text = await response.text()
  if (!response.ok) throw new Error(`${route} -> HTTP ${response.status}: ${text.slice(0, 300)}`)
  return JSON.parse(text) as T
}

function normalized(values: string[]): string[] {
  return values.map(value => value.replace(/\s+/g, ' ').trim()).filter(Boolean).sort()
}

test.describe('live Admin organization-directory acceptance', () => {
  test.skip(!enabled, 'Set LIVE_ADMIN_DIRECTORY_ACCEPTANCE=true to run the live acceptance probe.')

  test('all Admin dropdowns use active units and their configured domains', async ({ page }) => {
    test.setTimeout(150_000)
    expect(adminPassword, 'Missing Admin password for live acceptance').not.toBe('')
    const settings = await apiJson<Settings>('/api/settings', { headers: headers() })
    const units = settings.organization_units || []
    const domains = settings.legal_domains || []
    const activeUnits = units.filter(unit => unit.is_active)
    const inactiveUnits = units.filter(unit => !unit.is_active)
    expect(activeUnits.length).toBeGreaterThan(0)

    const preferredUnit = activeUnits.find(unit => [
      ...(unit.domain_codes || []),
      ...(unit.domain_assignments || []).map(item => item.domain_code || ''),
    ].includes('kinh_te')) || activeUnits[0]
    const allowedCodes = new Set([
      ...(preferredUnit.domain_codes || []),
      ...(preferredUnit.domain_assignments || []).map(item => item.domain_code || ''),
    ].filter(Boolean))
    const expectedDomains = domains.filter(domain => domain.is_active && allowedCodes.has(domain.code))

    const suffix = randomBytes(6).toString('hex')
    const username = `qa_admin_directory_${suffix}`
    const password = `Qa!${randomBytes(20).toString('base64url')}9aA`
    let accountId = ''
    const report: Record<string, unknown> = {
      schema_version: 'admin-directory-live-v1',
      generated_at: new Date().toISOString(),
      credentials_recorded: false,
      preferred_unit_id: preferredUnit.id,
      active_unit_count: activeUnits.length,
      inactive_unit_count: inactiveUnits.length,
      expected_domain_codes: expectedDomains.map(domain => domain.code),
      checks: {},
    }

    try {
      const created = await apiJson<{ id: string }>('/api/users', {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({
          username,
          email: `${username}@qa.invalid`,
          password,
          role: 'admin',
          is_active: true,
          full_name: 'QA Admin directory acceptance',
          must_change_password: false,
          notes: 'Disposable Admin UI acceptance account.',
        }),
      })
      accountId = created.id

      await page.goto('/login', { waitUntil: 'domcontentloaded' })
      await page.getByLabel('Email hoặc tên đăng nhập').fill(username)
      await page.getByLabel('Mật khẩu', { exact: true }).fill(password)
      await page.locator('form').getByRole('button', { name: 'Đăng nhập', exact: true }).click()
      await expect(page).not.toHaveURL(/\/login(?:\?|$)/, { timeout: 30_000 })

      await page.goto('/admin', { waitUntil: 'domcontentloaded' })
      const vectorCoverageCard = page.getByText('Độ phủ dữ liệu tra cứu', { exact: true }).locator('..').locator('..')
      await expect(vectorCoverageCard).toBeVisible({ timeout: 30_000 })
      await expect(vectorCoverageCard).not.toContainText('Chưa xác định', { timeout: 30_000 })
      await expect(vectorCoverageCard).toContainText(/\d+(?:[,.]\d+)?\s*%/, { timeout: 30_000 })
      const vectorCoverageText = (await vectorCoverageCard.innerText()).replace(/\s+/g, ' ').trim()

      await page.goto('/legal-import?tab=import', { waitUntil: 'domcontentloaded' })
      const receiving = page.getByRole('combobox', { name: 'Phòng ban tiếp nhận' })
      await expect(receiving).toBeVisible({ timeout: 30_000 })
      await receiving.click()
      // The settings request is independent from the route shell. Wait for
      // the first configured unit instead of asserting against the temporary
      // two-option placeholder rendered during hydration.
      await expect(page.getByRole('option', { name: activeUnits[0].name, exact: true })).toBeVisible({ timeout: 30_000 })
      const importUnitOptions = normalized(await page.getByRole('option').allTextContents())
      await page.keyboard.press('Escape')
      const activeUnitNames = normalized(activeUnits.map(unit => unit.name))
      const inactiveUnitNames = normalized(inactiveUnits.flatMap(unit => [unit.name, unit.short_name || '']))
      for (const name of activeUnitNames) expect(importUnitOptions).toContain(name)
      for (const name of inactiveUnitNames) expect(importUnitOptions).not.toContain(name)

      await receiving.click()
      await page.getByRole('option', { name: preferredUnit.name, exact: true }).click()
      const importDomain = page.getByRole('combobox', { name: 'Lĩnh vực' })
      await importDomain.click()
      const importDomainOptions = normalized(await page.getByRole('option').allTextContents())
      await page.keyboard.press('Escape')
      expect(importDomainOptions).toEqual(normalized(expectedDomains.map(domain => domain.name)))

      const economicDomain = expectedDomains.find(domain => domain.code === 'kinh_te')
      let economicFallbackVisible: boolean | null = null
      if (economicDomain) {
        await importDomain.click()
        await page.getByRole('option', { name: economicDomain.name, exact: true }).click()
        economicFallbackVisible = await page.getByRole('combobox', { name: 'Chủ đề dữ liệu pháp lý cụ thể' }).isVisible()
        // Kinh tế is now a first-class legal domain. It must not fall back to
        // a legacy storage-topic selector; the normal sector input remains.
        expect(economicFallbackVisible).toBe(false)
        await expect(page.locator('#sector')).toBeVisible()
        await expect(page.getByText(/chưa có trường dữ liệu tương ứng/i)).toHaveCount(0)
      }

      await page.goto('/procedure-management', { waitUntil: 'domcontentloaded' })
      const procedureDepartment = page.locator('#forms-unit')
      await expect(procedureDepartment).toBeVisible({ timeout: 30_000 })
      await expect(procedureDepartment.locator(`option[value="${activeUnits[0].id}"]`)).toHaveCount(1, { timeout: 30_000 })
      const procedureUnitOptions = normalized(await procedureDepartment.locator('option').allTextContents())
      for (const name of activeUnitNames) expect(procedureUnitOptions).toContain(name)
      for (const name of inactiveUnitNames) expect(procedureUnitOptions).not.toContain(name)
      await procedureDepartment.selectOption(preferredUnit.id)
      const procedureDomains = normalized(
        (await page.locator('#forms-domain option').allTextContents()).filter(value => !/Tất cả lĩnh vực/i.test(value)),
      )
      expect(procedureDomains).toEqual(normalized(expectedDomains.map(domain => domain.name)))

      await page.getByText('Thêm biểu mẫu', { exact: true }).click()
      const proposalDepartment = page.locator('#proposal-department')
      await expect(proposalDepartment).toBeVisible()
      await expect(proposalDepartment.locator(`option[value="${activeUnits[0].id}"]`)).toHaveCount(1, { timeout: 30_000 })
      const proposalUnitOptions = normalized(await proposalDepartment.locator('option').allTextContents())
      for (const name of activeUnitNames) expect(proposalUnitOptions).toContain(name)
      for (const name of inactiveUnitNames) expect(proposalUnitOptions).not.toContain(name)
      await proposalDepartment.selectOption(preferredUnit.id)
      const proposalDomains = normalized(
        (await page.locator('#procedure-domain option').allTextContents()).filter(value => !/Tất cả lĩnh vực/i.test(value)),
      )
      expect(proposalDomains).toEqual(normalized(expectedDomains.map(domain => domain.name)))

      await page.goto('/legal-management/25475', { waitUntil: 'domcontentloaded' })
      const assignButton = page.getByRole('button', { name: 'Phân công phòng ban' })
      await expect(assignButton).toBeVisible({ timeout: 30_000 })
      await assignButton.click()
      const dialog = page.getByRole('dialog', { name: 'Phân công phòng ban' })
      await expect(dialog).toBeVisible()
      const assignmentLabels = normalized(await dialog.locator('fieldset label').allTextContents())
      const activeAssignmentNames = normalized(activeUnits.map(unit => unit.short_name || unit.name))
      const inactiveAssignmentNames = normalized(inactiveUnits.flatMap(unit => [unit.name, unit.short_name || '']))
      expect(assignmentLabels).toEqual(activeAssignmentNames)
      for (const name of inactiveAssignmentNames) expect(assignmentLabels).not.toContain(name)

      report.checks = {
        legal_import_active_units_only: true,
        dashboard_vector_coverage_visible: true,
        legal_import_domains_follow_selected_unit: true,
        economic_domain_is_first_class_without_storage_fallback: economicFallbackVisible === false,
        procedure_management_active_units_only: true,
        procedure_management_domains_follow_selected_unit: true,
        add_form_active_units_only: true,
        add_form_domains_follow_selected_unit: true,
        document_assignment_active_units_only: true,
      }
      report.observed = {
        dashboard_vector_coverage: vectorCoverageText,
        legal_import_units: importUnitOptions,
        legal_import_domains: importDomainOptions,
        procedure_management_units: procedureUnitOptions,
        procedure_management_domains: procedureDomains,
        add_form_units: proposalUnitOptions,
        add_form_domains: proposalDomains,
        document_assignment_units: assignmentLabels,
      }
    } finally {
      if (accountId) {
        await apiJson(`/api/users/${encodeURIComponent(accountId)}/soft-delete`, {
          method: 'POST',
          headers: headers(),
        })
        report.qa_account_cleaned = true
      }
      const stamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)
      const output = path.resolve('..', 'reports', 'nonchat-acceptance', `admin-directory-live-${stamp}.json`)
      await mkdir(path.dirname(output), { recursive: true })
      await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    }
  })
})
