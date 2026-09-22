import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import matrix from '../../tests/fixtures/feature005_role_matrix.json'
import { installIsolatedApi } from './isolated-api'

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

async function submitQuestion(page: Page, question: string) {
  const input = page.locator('#ask-question')
  // A production rollback probe may need to hydrate and verify the isolated
  // role token before the search surface is mounted.
  await expect(input).toBeVisible({ timeout: 15_000 })
  await expect(input).toBeEnabled({ timeout: 15_000 })
  await input.fill(question)
  await input.press('Control+Enter')
  const assistant = page.getByTestId('ask-assistant-message')
  await expect(assistant).toBeVisible({ timeout: 90_000 })
  return assistant
}

test.describe('Feature 005 rollback journey', () => {
  test('keeps the legacy flat response when the flag is off', async ({ page }) => {
    await installIsolatedApi(page, 'citizen', { answer: { answer_sections: [] } })
    await page.goto('/search?new=1')
    await expect(page).toHaveURL(/\/search(?:\?new=1)?$/)
    const assistant = await submitQuestion(page, 'Đăng ký khai sinh cần hồ sơ gì?')
    await expect(assistant.getByTestId('answer-section')).toHaveCount(0)
  })
})

for (const role of ['citizen', 'officer', 'admin'] as const) {
  test.describe(`Feature 005 ${role} role matrix`, () => {
    for (const item of matrix.cases.filter((entry) => entry.role === role) as MatrixCase[]) {
      test(item.id, async ({ page }) => {
        await installIsolatedApi(page, role, {
          answer: item.requests_form ? { forms_unavailable: true } : undefined,
        })
        await page.goto('/search?new=1')
        if (role === 'admin') {
          // Current route policy separates administration from citizen/officer
          // Q&A. Verify that boundary rather than bypassing it with a test token.
          await expect(page).toHaveURL(/\/admin(?:\?.*)?$/)
          await expect(page.locator('#ask-question')).toHaveCount(0)
          return
        }
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
