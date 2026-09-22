import { expect, test } from '@playwright/test'
import { installIsolatedApi } from './isolated-api'

/**
 * Opt-in browser smoke for the three production roles. Tokens are supplied
 * only by the local test environment and are never written to artifacts.
 * The large RAG corpus evaluation belongs to scripts/run_rag_evaluation.py;
 * this suite checks the representative UI surface and role bootstrapping.
 */
const roleCases = [
  { role: 'citizen', account: 'citizen01', path: '/search' },
  { role: 'officer', account: 'officer_hotich', path: '/search' },
  { role: 'admin', account: 'admin', path: '/admin' },
] as const

for (const item of roleCases) {
  test(`${item.role} can load its protected surface`, async ({ page }) => {
    await installIsolatedApi(page, item.role)

    await page.goto(item.path)
    await expect(page.locator('body')).toBeVisible()

    if (item.role !== 'admin') {
      await expect(page.locator('#ask-question')).toBeVisible()
      await expect(page.getByRole('button', { name: 'Hỏi', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Đính kèm tệp', exact: true })).toBeVisible()
    } else {
      await expect(page).toHaveURL(/\/admin/)
    }
  })
}
