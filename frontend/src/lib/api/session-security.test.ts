import { describe, expect, it } from 'vitest'

import {
  CSRF_HEADER_NAME,
  getCsrfToken,
  sessionSecurityHeaders,
} from './session-security'


describe('session security', () => {
  it('reads the double-submit CSRF cookie without exposing the session cookie', () => {
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      value: 'theme=dark; chatbotlegal_csrf=csrf-value%2Fone',
      writable: true,
    })

    expect(getCsrfToken()).toBe('csrf-value/one')
  })

  it('adds CSRF only to unsafe requests', () => {
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      value: 'chatbotlegal_csrf=csrf-value',
      writable: true,
    })

    expect(sessionSecurityHeaders('GET')).toEqual({})
    expect(sessionSecurityHeaders('POST')).toEqual({
      [CSRF_HEADER_NAME]: 'csrf-value',
    })
  })
})
