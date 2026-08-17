import { describe, expect, it } from 'vitest'

describe('ask turn protection', () => {
  it('documents the synchronous duplicate-submit guard', async () => {
    const source = await import('node:fs/promises')
    const page = await source.readFile(
      'src/app/(dashboard)/search/page.tsx',
      'utf8',
    )
    expect(page).toContain('askSubmitLockRef')
    expect(page).toContain('askSubmitLockRef.current = true')
    expect(page).toContain('currentSessionIdRef.current = conversation.id')
    expect(page).toContain('currentSessionIdRef.current || currentSessionId')
    expect(page).toContain("previous?.role === 'assistant'")
  })
})
