import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { getApiUrl, resetConfig } from './config'

describe('Config Priority', () => {
  const originalEnv = process.env
  const originalFetch = global.fetch
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.resetModules()
    resetConfig()
    process.env = { ...originalEnv }
    fetchMock.mockReset()
    global.fetch = fetchMock
  })

  afterEach(() => {
    process.env = originalEnv
    global.fetch = originalFetch
  })

  it('should prioritize runtime config over everything else', async () => {
    // Setup: Env var set, Runtime config returns explicit value
    process.env.NEXT_PUBLIC_API_URL = 'http://env-url.com'
    
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apiUrl: 'http://runtime-url.com' }),
    } as Response)

    // Mock the second fetch call (api/config check)
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ version: '1.0.0' }),
    } as Response)

    const url = await getApiUrl()
    expect(url).toBe('http://runtime-url.com')
  })

  it('should prefer the same-origin proxy over a stale build-time URL', async () => {
    // Setup: an old build-time URL is still set, while runtime config is empty.
    process.env.NEXT_PUBLIC_API_URL = 'http://env-url.com'
    
    // First fetch: /config returns empty apiUrl
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apiUrl: '' }),
    } as Response)

    // Second fetch: same-origin api/config succeeds without touching the old URL.
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ version: '1.0.0' }),
    } as Response)

    const url = await getApiUrl()
    expect(url).toBe('')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/config', expect.objectContaining({ cache: 'no-store' }))
  })

  it('should fall back to the environment URL when the same-origin proxy is unavailable', async () => {
    // Setup: Env var set, Runtime config returns empty object
    process.env.NEXT_PUBLIC_API_URL = 'http://env-url.com'
    
    // First fetch: /config returns {}
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({}), // Missing apiUrl
    } as Response)

    // Second fetch: same-origin proxy is unavailable.
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 502,
    } as Response)

    // Third fetch: the configured environment URL remains a valid fallback.
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ version: '1.0.0' }),
    } as Response)

    const url = await getApiUrl()
    expect(url).toBe('http://env-url.com')
  })

  it('should use default (relative path) if both runtime and env are missing', async () => {
    // Setup: Env var NOT set, Runtime config returns empty
    delete process.env.NEXT_PUBLIC_API_URL
    
    // First fetch: /config returns empty
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apiUrl: '' }),
    } as Response)

    // Second fetch: api/config check using default relative path
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ version: '1.0.0' }),
    } as Response)

    const url = await getApiUrl()
    expect(url).toBe('')
  })

  it('keeps the same-origin proxy usable when runtime metadata times out', async () => {
    delete process.env.NEXT_PUBLIC_API_URL

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apiUrl: '' }),
    } as Response)
    fetchMock.mockRejectedValueOnce(new DOMException('timed out', 'AbortError'))

    await expect(getApiUrl()).resolves.toBe('')
  })
})
