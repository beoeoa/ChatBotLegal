import { afterEach, describe, expect, it, vi } from 'vitest'
import { isAskSseEnabled } from './ask-stream'


describe('Ask SSE feature flag', () => {
  afterEach(() => vi.unstubAllEnvs())

  it('enables the existing default streaming experience when configuration is absent', () => {
    vi.stubEnv('NEXT_PUBLIC_ASK_SSE_ENABLED', undefined)
    expect(isAskSseEnabled()).toBe(true)
    expect(isAskSseEnabled(undefined)).toBe(true)
  })

  it('honors deployment opt-out and rejects malformed explicit values', () => {
    vi.stubEnv('NEXT_PUBLIC_ASK_SSE_ENABLED', 'false')
    expect(isAskSseEnabled()).toBe(false)
    expect(isAskSseEnabled('')).toBe(false)
    expect(isAskSseEnabled('false')).toBe(false)
    expect(isAskSseEnabled('1')).toBe(false)
    expect(isAskSseEnabled('true')).toBe(true)
    expect(isAskSseEnabled(' TRUE ')).toBe(true)
    expect(isAskSseEnabled('yes')).toBe(false)
  })

  it('reads an explicit deployment opt-in', () => {
    vi.stubEnv('NEXT_PUBLIC_ASK_SSE_ENABLED', 'true')
    expect(isAskSseEnabled()).toBe(true)
  })
})
