import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readAdminSnapshot, writeAdminSnapshot } from './admin-snapshot-cache'
import { useAuthStore } from '@/lib/stores/auth-store'

describe('admin snapshot cache', () => {
  beforeEach(() => {
    sessionStorage.clear()
    useAuthStore.setState({ isAuthenticated: true, role: 'admin', userId: 'a' })
  })
  it('reuses actual data only within the same account and role', () => {
    writeAdminSnapshot('dashboard', { total: 288 })
    expect(readAdminSnapshot('dashboard')).toEqual({ total: 288 })
    useAuthStore.setState({ userId: 'b' })
    expect(readAdminSnapshot('dashboard')).toBeNull()
    useAuthStore.setState({ userId: 'a', role: 'citizen' })
    expect(readAdminSnapshot('dashboard')).toBeNull()
  })
  it('does not reuse expired snapshots or fail when storage is corrupt', () => {
    writeAdminSnapshot('dashboard', { total: 288 })
    const now = Date.now()
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now + 31 * 60 * 1000)
    expect(readAdminSnapshot('dashboard')).toBeNull()
    clock.mockRestore()
    sessionStorage.setItem('admin-snapshot:v1:a:dashboard', 'broken')
    expect(readAdminSnapshot('dashboard')).toBeNull()
  })
})
