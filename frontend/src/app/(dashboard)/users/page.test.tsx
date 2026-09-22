import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const authState = vi.hoisted(() => ({
  token: 'admin-session',
  role: 'admin' as const,
  authRequired: true,
  userId: 'user_account:admin',
}))

const toastSuccess = vi.hoisted(() => vi.fn())

vi.mock('@/components/layout/SetupBanner', () => ({ SetupBanner: () => null }))
vi.mock('@/components/layout/AppSidebar', () => ({ AppSidebar: () => null }))

vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (state: typeof authState) => unknown) => selector(authState),
}))

vi.mock('sonner', () => ({
  toast: {
    success: toastSuccess,
    error: vi.fn(),
  },
}))

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api.test'),
  getConfig: vi.fn(async () => ({ systemName: 'Pháp luật Hải Phòng' })),
}))

import UsersPage, { futureDateTimeLocal } from './page'
import { QueryProvider } from '@/components/providers/QueryProvider'

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
  const renderUsersPage = () => render(<QueryProvider><UsersPage /></QueryProvider>)
  beforeAll(() => {
    class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  beforeEach(() => {
    toastSuccess.mockReset()
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      const data = url.includes('/api/users/ask-history')
        ? [{ id: 'user_ask_history:1', question: 'Tôi cần giấy tờ gì?', answer: 'Chuẩn bị hồ sơ theo hướng dẫn.' }]
        : [managedUser]
      return { ok: true, json: async () => data }
    }))
  })

  it('removes account switching and opens the system ask history', async () => {
    renderUsersPage()

    expect(await screen.findByTestId('user-management-page')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Đổi tài khoản' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('open-ask-history'))

    expect(await screen.findByRole('heading', { name: 'Lịch sử hỏi đáp' })).toBeInTheDocument()
    expect(screen.getByText('Tôi cần giấy tờ gì?')).toBeInTheDocument()
  })

  it('loads full ask history for the selected account', async () => {
    renderUsersPage()
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

  it('restores page interactivity after closing an action opened from the overflow menu', async () => {
    renderUsersPage()
    await screen.findByTestId('managed-user-nguyenvana')

    const overflowTrigger = screen.getByRole('button', { name: 'Thao tác khác cho nguyenvana' })
    fireEvent.keyDown(overflowTrigger, { key: 'ArrowDown' })
    const resetItem = await screen.findByText('Đặt lại mật khẩu')
    fireEvent.pointerMove(resetItem)
    fireEvent.click(resetItem)
    expect(await screen.findByRole('heading', { name: 'Đổi mật khẩu' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await waitFor(() => {
      expect(document.body.style.pointerEvents).not.toBe('none')
      expect(document.body).not.toHaveAttribute('data-scroll-locked')
    })

    fireEvent.keyDown(overflowTrigger, { key: 'ArrowDown' })
    expect(await screen.findByRole('menu')).toBeInTheDocument()
  })

  it('shows a success notification and ignores rapid duplicate create clicks', async () => {
    renderUsersPage()
    await screen.findByTestId('managed-user-nguyenvana')

    fireEvent.click(screen.getByRole('button', { name: 'Tạo tài khoản' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Tên đăng nhập'), { target: { value: 'taikhoanmoi' } })
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: 'taikhoanmoi@example.test' } })
    fireEvent.change(within(dialog).getByLabelText('Mật khẩu khởi tạo'), { target: { value: 'MatKhauKhoiTao123!' } })

    const submit = within(dialog).getByRole('button', { name: 'Tạo tài khoản' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(await screen.findByText('Đã tạo tài khoản taikhoanmoi thành công.')).toBeInTheDocument()
    expect(toastSuccess).toHaveBeenCalledWith('Đã tạo tài khoản taikhoanmoi thành công.')
    const postCalls = vi.mocked(fetch).mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
    expect(postCalls).toHaveLength(1)
  })

  it('creates a browser-safe local expiry for quick cross-unit grants', () => {
    const now = new Date('2026-09-01T03:00:00.000Z')
    const value = futureDateTimeLocal(7, now)

    expect(value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(new Date(value).getTime()).toBeGreaterThan(now.getTime())
  })
})
