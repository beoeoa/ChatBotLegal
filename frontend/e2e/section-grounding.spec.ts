import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import matrix from '../../tests/fixtures/feature005_role_matrix.json'

type Role = 'citizen' | 'officer' | 'admin'
type RoleStorageState = {
  cookies: []
  origins: Array<{
    origin: string
    localStorage: Array<{ name: string; value: string }>
  }>
}
type MatrixCase = {
  id: string
  role: Role
  question: string
  requests_form?: boolean
  requires_trace?: boolean
}

const sectionFlagEnabled = process.env.LEGAL_SECTION_GROUNDING_ENABLED === 'true'
const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:3000'

function inMemoryStorage(role: Role): RoleStorageState | undefined {
  const token = process.env[`FEATURE005_${role.toUpperCase()}_TOKEN`]
  if (!token) return undefined
  return {
    cookies: [],
    origins: [{
      origin: new URL(baseUrl).origin,
      localStorage: [{
        name: 'auth-storage',
        value: JSON.stringify({
          state: { token, role, isAuthenticated: true },
          version: 0,
        }),
      }],
    }],
  }
}

const storageByRole: Record<Role, string | RoleStorageState | undefined> = {
  citizen: process.env.E2E_CITIZEN_AUTH_STORAGE || inMemoryStorage('citizen'),
  officer: process.env.E2E_OFFICER_AUTH_STORAGE || inMemoryStorage('officer'),
  admin: process.env.E2E_ADMIN_AUTH_STORAGE || process.env.E2E_AUTH_STORAGE || inMemoryStorage('admin'),
}

async function submitQuestion(page: Page, question: string) {
  const input = page.locator('#ask-question')
  // A production rollback probe may need to hydrate and verify the isolated
  // role token before the search surface is mounted.
  await expect(input).toBeVisible({ timeout: 15_000 })
  await input.fill(question)
  await input.press('Control+Enter')
  const assistant = page.getByTestId('ask-assistant-message')
  await expect(assistant).toBeVisible({ timeout: 90_000 })
  return assistant
}

test.describe('Feature 005 rollback journey', () => {
  test.use({ storageState: storageByRole.admin })

  test('keeps the legacy flat response when the flag is off', async ({ page }) => {
    test.skip(sectionFlagEnabled, 'This case verifies only the rollback path.')
    test.skip(!storageByRole.admin, 'Requires an isolated local admin storage state.')
    await page.goto('/search')
    await expect(page).toHaveURL(/\/search$/)
    const assistant = await submitQuestion(page, 'Đăng ký khai sinh cần hồ sơ gì?')
    await expect(assistant.getByTestId('answer-section')).toHaveCount(0)
  })
})

for (const role of ['citizen', 'officer', 'admin'] as const) {
  test.describe(`Feature 005 ${role} role matrix`, () => {
    test.use({ storageState: storageByRole[role] })

    for (const item of matrix.cases.filter((entry) => entry.role === role) as MatrixCase[]) {
      test(item.id, async ({ page }) => {
        test.skip(!sectionFlagEnabled, 'The role matrix runs only on the isolated flag-on runtime.')
        test.skip(!storageByRole[role], `Requires an isolated local ${role} storage state.`)
        await page.goto('/search')
        const assistant = await submitQuestion(page, item.question)
        const visibleText = await assistant.innerText()
        expect(visibleText).not.toMatch(/#ref-source|\blegal:|chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id/i)
        expect(visibleText).not.toMatch(/Internal Server Error|ASK_FAILED/i)
        await expect(assistant.getByTestId('answer-section')).not.toHaveCount(0)

        if (item.requests_form) {
          const officialForms = assistant.getByTestId('official-forms')
          const unavailable = assistant.getByTestId('forms-unavailable')
          expect((await officialForms.count()) + (await unavailable.count())).toBeGreaterThan(0)
        }

        if (item.requires_trace) {
          const traceButton = assistant.getByRole('button', { name: 'Quy trình RAG' })
          await expect(traceButton).toBeVisible()
          await traceButton.click()
          await expect(assistant.getByTestId('admin-evidence-coverage')).toBeVisible()
          await expect(assistant.getByTestId('form-provenance')).toBeVisible()
        }
      })
    }
  })
}
