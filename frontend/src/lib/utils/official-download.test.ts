import { describe, expect, it } from 'vitest'
import { officialDownloadTarget } from './official-download'

describe('official form download credentials', () => {
  const origin = 'http://localhost:3000'
  const api = 'http://localhost:5055'
  it('authenticates only configured API routes', () => {
    expect(officialDownloadTarget('/api/forms/one/download', origin, api)).toEqual({ href: `${api}/api/forms/one/download`, authenticated: true })
    expect(officialDownloadTarget(`${origin}/api/forms/one/download`, origin, api).authenticated).toBe(true)
    expect(officialDownloadTarget('/api/forms/one/download', origin, '').authenticated).toBe(true)
  })
  it.each(['https://vbpl.vn/form.pdf', 'https://other.example/api/forms/file', '//other.example/api/file'])('does not send a credential to %s', url => {
    if (url.startsWith('//')) {
      expect(() => officialDownloadTarget(url, origin, api)).toThrow(/HTTPS/)
    } else {
      expect(officialDownloadTarget(url, origin, api).authenticated).toBe(false)
    }
  })
  it.each(['javascript:alert(1)', 'data:text/html,test', 'http://other.example/file', 'https://name:pass@example.com/file'])('rejects unsafe URL %s', url => {
    expect(() => officialDownloadTarget(url, origin, api)).toThrow()
  })
  it('does not authenticate non-API content on the application host', () => {
    expect(officialDownloadTarget('https://portal.example/files/form.pdf', 'https://portal.example', '').authenticated).toBe(false)
  })
})
