import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const authState = vi.hoisted(() => ({
  token: 'admin-session',
  role: 'admin' as const,
  authRequired: true,
  userId: 'user_account:admin',
}))

vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (state: typeof authState) => unknown) => selector(authState),
}))

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api.test'),
}))

import UsersPage from './page'

const managedUser = {
  id: 'user_account:citizen-1',
  username: 'nguyenvana',
  email: 'nguyenvana@example.test',
  role: 'citizen',
  is_active: true,
  created: '2026-08-01T08:00:00Z',
  profile: { full_name: 'Nguyễn Văn A' },
}

describe('UsersPage ask history', () => {
  beforeAll(() => {
    class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      const data = url.includes('/api/users/ask-history')
        ? [{ id: 'user_ask_history:1', question: 'Tôi cần giấy tờ gì?', answer: 'Chuẩn bị hồ sơ theo hướng dẫn.' }]
        : [managedUser]
      return { ok: true, json: async () => data }
    }))
  })

  it('removes account switching and opens the system ask history', async () => {
    render(<UsersPage />)

    expect(await screen.findByTestId('user-management-page')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Đổi tài khoản' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('open-ask-history'))

    expect(await screen.findByRole('heading', { name: 'Lịch sử hỏi đáp' })).toBeInTheDocument()
    expect(screen.getByText('Tôi cần giấy tờ gì?')).toBeInTheDocument()
  })

  it('loads full ask history for the selected account', async () => {
    render(<UsersPage />)
    await screen.findByTestId('managed-user-nguyenvana')

    fireEvent.click(screen.getByRole('button', { name: 'Xem hồ sơ nguyenvana' }))
    const detailDialog = await screen.findByRole('dialog')
    fireEvent.click(within(detailDialog).getByRole('button', { name: 'Lịch sử hỏi đáp' }))

    expect(await screen.findByRole('heading', { name: 'Lịch sử hỏi đáp · Nguyễn Văn A' })).toBeInTheDocument()
    await waitFor(() => {
      const fetchMock = vi.mocked(fetch)
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes('user_id=user_account%3Acitizen-1'))).toBe(true)
    })
    expect(screen.getByText('Chuẩn bị hồ sơ theo hướng dẫn.')).toBeInTheDocument()
  })
})
