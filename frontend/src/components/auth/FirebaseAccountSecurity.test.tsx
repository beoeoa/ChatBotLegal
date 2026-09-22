import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiPost = vi.hoisted(() => vi.fn())
const logout = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const routerPush = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api/client', () => ({ apiClient: { post: apiPost } }))
vi.mock('@/lib/firebase/client', () => ({
  firebaseEnabled: true,
  getFirebaseAuth: () => ({}),
}))
vi.mock('@firebase/auth', () => ({
  onAuthStateChanged: (_auth: unknown, callback: (user: null) => void) => {
    callback(null)
    return () => undefined
  },
  sendEmailVerification: vi.fn(),
  sendPasswordResetEmail: vi.fn(),
  verifyBeforeUpdateEmail: vi.fn(),
}))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush }),
}))
vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (state: { email: string; userId: string; logout: typeof logout }) => unknown) => selector({
    email: 'admin@local.dev',
    userId: 'user_account:admin',
    logout,
  }),
}))

import { FirebaseAccountSecurity } from './FirebaseAccountSecurity'

describe('FirebaseAccountSecurity', () => {
  beforeEach(() => {
    apiPost.mockReset()
    logout.mockClear()
    routerPush.mockClear()
  })

  it('gives a server-backed account a working password-change form', async () => {
    apiPost.mockResolvedValue({ data: { success: true } })
    render(<FirebaseAccountSecurity />)

    fireEvent.change(screen.getByLabelText('Mật khẩu hiện tại'), { target: { value: 'OldPassword123!' } })
    fireEvent.change(screen.getByLabelText('Mật khẩu mới'), { target: { value: 'NewPassword123!' } })
    fireEvent.change(screen.getByLabelText('Xác nhận mật khẩu mới'), { target: { value: 'NewPassword123!' } })
    fireEvent.click(screen.getByRole('button', { name: 'Đổi mật khẩu' }))

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/users/me/change-password', {
      current_password: 'OldPassword123!',
      new_password: 'NewPassword123!',
    }))
    expect(await screen.findByRole('status')).toHaveTextContent('Đã đổi mật khẩu')
    expect(screen.getByRole('button', { name: 'Đăng nhập lại' })).toBeInTheDocument()
  })

  it('validates the new password before calling the backend', () => {
    render(<FirebaseAccountSecurity />)

    fireEvent.change(screen.getByLabelText('Mật khẩu hiện tại'), { target: { value: 'OldPassword123!' } })
    fireEvent.change(screen.getByLabelText('Mật khẩu mới'), { target: { value: 'short' } })
    fireEvent.change(screen.getByLabelText('Xác nhận mật khẩu mới'), { target: { value: 'short' } })
    fireEvent.click(screen.getByRole('button', { name: 'Đổi mật khẩu' }))

    expect(apiPost).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('ít nhất 12 ký tự')
  })
})
