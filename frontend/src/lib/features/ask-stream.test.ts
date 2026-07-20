import { describe, expect, it } from 'vitest'
import { isAskSseEnabled } from './ask-stream'


describe('Ask SSE feature flag', () => {
  it('defaults to false and requires an explicit true value', () => {
    expect(isAskSseEnabled(undefined)).toBe(false)
    expect(isAskSseEnabled('')).toBe(false)
    expect(isAskSseEnabled('false')).toBe(false)
    expect(isAskSseEnabled('1')).toBe(false)
    expect(isAskSseEnabled('true')).toBe(true)
    expect(isAskSseEnabled(' TRUE ')).toBe(true)
  })
})
