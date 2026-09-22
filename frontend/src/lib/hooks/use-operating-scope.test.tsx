import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useOperatingScope } from './use-operating-scope'

const mocks = vi.hoisted(() => ({ get: vi.fn(), state: { role: 'officer', userId: 'qa-officer' } }))
vi.mock('@/lib/api/client', () => ({ apiClient: { get: mocks.get } }))
vi.mock('@/lib/stores/auth-store', () => ({ useAuthStore: (selector: (state: typeof mocks.state) => unknown) => selector(mocks.state) }))

const assigned = { mode: 'hybrid', domains: ['ho_tich_chung_thuc'], can_manage_content: true, proposal_units: [] }
afterEach(() => { cleanup(); vi.useRealTimers(); mocks.get.mockReset(); mocks.state.role = 'officer' })

describe('current officer operating scope', () => {
  it('removes write controls when a grant expires during an open session', async () => {
    vi.useFakeTimers()
    mocks.get.mockResolvedValueOnce({ data: assigned }).mockResolvedValue({ data: { ...assigned, domains: [], can_manage_content: false } })
    const view = renderHook(() => useOperatingScope())
    await act(async () => {})
    expect(view.result.current.scope?.can_manage_content).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(view.result.current.scope?.can_manage_content).toBe(false)
  })

  it('drops stale authorization and shows an error when refresh fails', async () => {
    mocks.get.mockResolvedValueOnce({ data: assigned }).mockRejectedValue(new Error('offline'))
    const view = renderHook(() => useOperatingScope())
    await act(async () => {})
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    expect(view.result.current.scope).toBeNull()
    expect(view.result.current.error).not.toBe('')
  })

  it('does not request officer-only scope for an admin', async () => {
    mocks.state.role = 'admin'
    renderHook(() => useOperatingScope())
    await act(async () => {})
    expect(mocks.get).not.toHaveBeenCalled()
  })
})
