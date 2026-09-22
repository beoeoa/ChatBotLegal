import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => ''),
}))

import { useAuthStore } from './auth-store'


describe('production cookie auth state', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.setState({
      isAuthenticated: false,
      token: null,
      role: null,
      userId: null,
      username: null,
      email: null,
      authMode: null,
      isLoading: false,
      error: null,
      lastAuthCheck: null,
      isCheckingAuth: false,
      mustChangePassword: false,
      mfaChallenge: null,
    })
    vi.restoreAllMocks()
  })

  it('keeps a production session token out of localStorage', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({
        authenticated: true,
        role: 'admin',
        token: null,
        auth_mode: 'cookie_session',
        user_id: 'user_account:test',
        username: 'admin',
        email: 'admin@example.test',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(
      useAuthStore.getState().login('admin', 'valid-password'),
    ).resolves.toBe(true)

    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().authMode).toBe('cookie_session')
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({
      credentials: 'include',
    }))
    expect(localStorage.getItem('auth-storage')).not.toContain('valid-password')
  })

  it('does not create a session when production requires TOTP enrollment', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({
        detail: {
          code: 'MFA_SETUP_REQUIRED',
          setup_token: 'signed-setup-ticket',
        },
      }), {
        status: 428,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(
      useAuthStore.getState().login('admin', 'valid-password'),
    ).resolves.toBe(false)

    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().mfaChallenge).toEqual({
      type: 'setup',
      setupToken: 'signed-setup-ticket',
    })
    expect(localStorage.getItem('auth-storage')).not.toContain('signed-setup-ticket')
  })

  it('completes TOTP enrollment into an HttpOnly cookie session', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({
        authenticated: true,
        role: 'officer',
        token: null,
        auth_mode: 'cookie_session',
        user_id: 'user_account:officer',
        username: 'officer',
        email: 'officer@example.test',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(
      useAuthStore.getState().confirmTotp('signed-confirm-ticket', '123456'),
    ).resolves.toBe(true)

    expect(useAuthStore.getState().authMode).toBe('cookie_session')
    expect(useAuthStore.getState().token).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/totp/confirm', expect.objectContaining({
      credentials: 'include',
    }))
    expect(localStorage.getItem('auth-storage')).not.toContain('signed-confirm-ticket')
  })

  it('waits for server logout before clearing the account session', async () => {
    let finishLogout: ((response: Response) => void) | undefined
    const responsePromise = new Promise<Response>((resolve) => {
      finishLogout = resolve
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/api/auth/logout')) {
        return responsePromise
      }
      return Promise.resolve(new Response(JSON.stringify({ apiUrl: 'http://localhost:5055' }), { status: 200 }))
    })
    useAuthStore.setState({
      isAuthenticated: true,
      token: null,
      role: 'admin',
      userId: 'user_account:admin',
      username: 'admin',
      authMode: 'cookie_session',
    })

    const logoutPromise = useAuthStore.getState().logout()
    expect(useAuthStore.getState().userId).toBe('user_account:admin')

    finishLogout?.(new Response(null, { status: 204 }))
    await logoutPromise

    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().userId).toBeNull()
  })
})
