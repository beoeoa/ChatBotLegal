import { expect, it, vi } from 'vitest'
vi.mock('@/lib/config', () => ({ getApiUrl: async () => '' }))
import { useAuthStore } from '@/lib/stores/auth-store'
import { apiClient } from './client'

it('sends the live session for every role without persisting bearer tokens', async () => {
  for (const role of ['citizen', 'officer', 'admin'] as const) {
    useAuthStore.setState({ token: 'memory-only-session', role })
    expect(localStorage.getItem('auth-storage')).not.toContain('memory-only-session')
    await apiClient.get('/users/me', { adapter: async config => {
      expect(config.headers.Authorization).toBe('Bearer memory-only-session')
      expect(config.headers['X-User-Role']).toBe(role)
      expect(config.withCredentials).toBe(true)
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    } })
  }
})
