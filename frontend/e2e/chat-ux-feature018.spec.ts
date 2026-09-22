import { expect, test, type Page } from '@playwright/test'

async function seedCitizen(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: {
        token: 'isolated-feature018-citizen',
        userId: 'feature018-citizen',
        role: 'citizen',
        isAuthenticated: true,
        hasHydrated: true,
      },
      version: 0,
    }))
  })

  const messages = Array.from({ length: 30 }, (_, index) => ({
    id: `message-${index + 70}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    content: `Tin nhắn ${index + 70}`,
    status: index % 2 === 0 ? undefined : 'complete',
    created_at: new Date(Date.UTC(2026, 7, 13, 8, index)).toISOString(),
  }))
  const conversation = {
    id: 'conversation-feature018',
    title: 'Hội thoại 100 tin',
    role_context: 'citizen',
    status: 'active',
    owner_user_id: 'feature018-citizen',
    created_at: '2026-08-13T08:00:00Z',
    last_message_at: '2026-08-13T08:30:00Z',
    expires_at: '2027-08-13T08:00:00Z',
    message_count: 100,
    messages,
    next_cursor: 'older-page-cursor',
    has_older_messages: true,
  }

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/auth/status') return route.fulfill({ json: { auth_enabled: true } })
    if (url.pathname === '/api/users/me') return route.fulfill({ json: { id: 'feature018-citizen', role: 'citizen' } })
    if (url.pathname === '/api/config') return route.fulfill({ json: {} })
    if (url.pathname.replace(/\/$/, '') === '/api/conversations' && request.method() === 'GET') return route.fulfill({ json: [conversation] })
    if (url.pathname.replace(/\/$/, '') === '/api/conversations' && request.method() === 'POST') {
      return route.fulfill({ status: 201, json: { ...conversation, id: 'new-clean-chat', title: 'Cuộc trò chuyện mới', message_count: 0, messages: [], next_cursor: null, has_older_messages: false } })
    }
    if (url.pathname === '/api/conversations/conversation-feature018' || url.pathname === '/api/conversations/latest') return route.fulfill({ json: conversation })
    if (url.pathname === '/api/conversations/conversation-feature018/messages' && request.method() === 'GET') {
      return route.fulfill({ json: { messages: [{ id: 'message-69', role: 'assistant', content: 'Tin nhắn 69', status: 'complete', created_at: '2026-08-13T07:59:00Z' }], next_cursor: 'older-page-two', has_more: true } })
    }
    if (url.pathname === '/api/models/defaults') return route.fulfill({ json: {} })
    return route.fulfill({ json: [] })
  })
}

for (const viewport of [
  { name: 'mobile', width: 360, height: 740 },
  { name: 'tablet', width: 768, height: 900 },
  { name: 'desktop', width: 1440, height: 900 },
]) {
  test(`long chat keeps controls reachable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await seedCitizen(page)
    await page.goto('/search')

    await expect(page.locator('#ask-question')).toBeVisible()
    if (viewport.width < 1024) {
      await page.getByRole('button', { name: 'Lịch sử', exact: true }).click()
      await expect(page.getByRole('button', { name: 'Mới', exact: true })).toBeVisible()
      await page.getByRole('button', { name: /Hội thoại 100 tin/ }).click()
    } else {
      await expect(page.getByRole('button', { name: 'Mới', exact: true })).toBeVisible()
    }
    await expect(page.locator('#ask-question')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Tải tin nhắn cũ' })).toBeVisible()
  })
}

test('keyboard creates a clean chat without carrying the previous answer', async ({ page }) => {
  await seedCitizen(page)
  await page.goto('/search')
  const newChat = page.getByRole('button', { name: /Cuộc trò chuyện mới|Mới/ }).last()
  await newChat.focus()
  await page.keyboard.press('Enter')

  await expect(page.getByText('Tin nhắn 99')).toHaveCount(0)
  await expect(page.locator('#ask-question')).toHaveValue('')
  // The important contract is that the newly-created composer is usable and
  // does not carry the old answer. Focus may legitimately remain on the
  // activation button after a route transition.
  await expect(page.locator('#ask-question')).toBeEditable()
})
