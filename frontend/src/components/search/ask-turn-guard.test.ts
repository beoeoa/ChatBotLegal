import { describe, expect, it } from 'vitest'

describe('ask turn protection', () => {
  it('documents the synchronous duplicate-submit guard', async () => {
    const source = await import('node:fs/promises')
    const page = await source.readFile(
      'src/app/(dashboard)/search/page.tsx',
      'utf8',
    )
    const pagination = await source.readFile(
      'src/lib/utils/conversation-message-pages.ts',
      'utf8',
    )
    expect(page).toContain('askSubmitLockRef')
    expect(page).toContain('askSubmitLockRef.current = true')
    expect(page).toContain("e.key === 'Enter'")
    expect(page).toContain('!e.shiftKey')
    expect(page).toContain('!e.nativeEvent.isComposing')
    expect(page).toContain('setCurrentSessionId(conversation.id)')
    expect(page).toContain('activeSessionId = currentSessionId')
    expect(page).toContain('normalizeConversationMessages(conversation.messages)')
    expect(pagination).toContain("previous?.role === 'assistant'")
  })
})
