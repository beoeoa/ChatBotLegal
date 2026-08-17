import { describe, expect, it } from 'vitest'

import { getDateLocale } from './date-locale'

describe('getDateLocale', () => {
  it('uses the Vietnamese date formatter for vi-VN', () => {
    expect(getDateLocale('vi-VN').code).toBe('vi')
  })

  it('keeps English as the fallback for unknown languages', () => {
    expect(getDateLocale('unknown').code).toBe('en-US')
  })
})
