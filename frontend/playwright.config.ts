import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:3000',
    // Release artifacts must not retain legal questions, answers or tokens.
    trace: process.env.E2E_PRIVACY_SAFE === 'true' ? 'off' : 'retain-on-failure',
    // CI can use Playwright's bundled browser while local gates may point to an
    // already-installed Chromium executable without downloading new software.
    launchOptions: process.env.PLAYWRIGHT_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
      : undefined,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
