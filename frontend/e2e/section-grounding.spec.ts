import { expect, test } from '@playwright/test'

const statePath = process.env.E2E_AUTH_STORAGE
const sectionFlagEnabled = process.env.LEGAL_SECTION_GROUNDING_ENABLED === 'true'
const query = process.env.E2E_SECTION_QUERY || 'Tranh chấp thửa đất giải quyết thế nào; lệ phí bao nhiêu?'

test.describe('Feature 005 section-grounded Ask journey', () => {
  test.use({ storageState: statePath || undefined })

  test.beforeEach(async ({ page }) => {
    test.skip(!statePath, 'Requires a local test-account storage state; credentials are never stored in this repository.')
    await page.goto('/search')
    await expect(page.locator('#ask-question')).toBeVisible()
  })

  test('keeps the legacy flat response when the flag is off', async ({ page }) => {
    test.skip(sectionFlagEnabled, 'This case verifies only the rollback path.')
    await page.locator('#ask-question').fill(query)
    await page.locator('#ask-question').press('Control+Enter')
    await expect(page.getByTestId('ask-assistant-message')).toBeVisible()
    await expect(page.getByTestId('answer-section')).toHaveCount(0)
  })

  test('renders verified and localized insufficient sections without internal markers', async ({ page }) => {
    test.skip(!sectionFlagEnabled, 'Run on the isolated local flag-on runtime.')
    await page.locator('#ask-question').fill(query)
    await page.locator('#ask-question').press('Control+Enter')

    await expect(page.getByTestId('answer-section-status-sufficiently_evidenced')).toBeVisible()
    await expect(page.getByTestId('answer-section-status-insufficiently_evidenced')).toBeVisible()
    await expect(page.getByTestId('answer-section-citations')).toBeVisible()
    const visibleText = await page.locator('[data-testid="ask-assistant-message"]').innerText()
    expect(visibleText).not.toMatch(/#ref-source|\blegal:|chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id/i)
  })

  test('shows a localized clarifying question rather than replacing a verified section', async ({ page }) => {
    test.skip(!sectionFlagEnabled, 'Run on the isolated local flag-on runtime.')
    await page.locator('#ask-question').fill(query)
    await page.locator('#ask-question').press('Control+Enter')
    await expect(page.getByTestId('answer-section-status-sufficiently_evidenced')).toBeVisible()
    await expect(page.getByText('Bạn có thể nêu rõ thủ tục hoặc tình tiết cụ thể cần xác minh không?')).toBeVisible()
  })
})
