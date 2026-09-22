import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const firebase = vi.hoisted(() => ({
  createUserWithEmailAndPassword: vi.fn(),
  confirmPasswordReset: vi.fn(),
  getRedirectResult: vi.fn(),
  sendEmailVerification: vi.fn(),
  sendPasswordResetEmail: vi.fn(),
  signInWithEmailAndPassword: vi.fn(),
  signInWithPopup: vi.fn(),
  signInWithRedirect: vi.fn(),
  signOut: vi.fn(),
  updateProfile: vi.fn(),
  verifyPasswordResetCode: vi.fn(),
}))

const authState = vi.hoisted(() => ({
  authRequired: true,
  hasHydrated: true,
  isAuthenticated: false,
  role: null,
  checkAuthRequired: vi.fn(async () => true),
}))
const useAuthStoreMock = vi.hoisted(() => Object.assign(vi.fn(() => authState), {
  getState: () => authState,
}))
const authActions = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
  loginWithFirebase: vi.fn(),
}))

vi.mock('@/lib/firebase/client', () => ({
  firebaseEnabled: true,
  getFirebaseAuth: () => ({}),
}))
vi.mock('@firebase/auth', () => ({
  ...firebase,
  GoogleAuthProvider: vi.fn(),
}))
vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api.test'),
  getConfig: vi.fn(async () => ({ apiUrl: 'http://api.test', version: 'test', buildTime: 'test' })),
}))
vi.mock('@/lib/hooks/use-auth', () => ({
  useAuth: () => ({
    login: authActions.login,
    register: authActions.register,
    loginWithFirebase: authActions.loginWithFirebase,
    isLoading: false,
    error: null,
    mfaChallenge: null,
    setupTotp: vi.fn(),
    confirmTotp: vi.fn(),
  }),
}))
vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: useAuthStoreMock,
}))

import { LoginForm } from './LoginForm'

describe('LoginForm Firebase password reset', () => {
  beforeEach(() => {
    firebase.createUserWithEmailAndPassword.mockReset()
    firebase.confirmPasswordReset.mockReset()
    firebase.getRedirectResult.mockReset().mockResolvedValue(null)
    firebase.sendEmailVerification.mockReset()
    firebase.sendPasswordResetEmail.mockReset().mockResolvedValue(undefined)
    firebase.signInWithEmailAndPassword.mockReset()
    firebase.signInWithPopup.mockReset()
    firebase.signInWithRedirect.mockReset()
    firebase.signOut.mockReset().mockResolvedValue(undefined)
    firebase.updateProfile.mockReset()
    firebase.verifyPasswordResetCode.mockReset()
    authActions.login.mockReset()
    authActions.register.mockReset().mockResolvedValue(true)
    authActions.loginWithFirebase.mockReset()
    authState.checkAuthRequired.mockClear()
    window.history.replaceState({}, '', '/login')
  })

  it('creates a normal citizen account through the application backend', async () => {
    render(<LoginForm />)
    fireEvent.click(screen.getByRole('button', { name: 'Đăng ký' }))
    fireEvent.change(screen.getByPlaceholderText('Tên đăng nhập'), { target: { value: 'qa-citizen' } })
    fireEvent.change(screen.getByPlaceholderText('Họ và tên'), { target: { value: 'QA Test' } })
    fireEvent.change(screen.getByPlaceholderText('Email'), { target: { value: 'qa@example.invalid' } })
    fireEvent.change(screen.getByPlaceholderText('Số điện thoại'), { target: { value: '0000000000' } })
    fireEvent.change(screen.getByPlaceholderText('Mật khẩu (12+ ký tự)'), { target: { value: 'qa-password-1234' } })
    fireEvent.change(screen.getByPlaceholderText('Nhập lại mật khẩu'), { target: { value: 'qa-password-1234' } })
    fireEvent.submit(screen.getByPlaceholderText('Họ và tên').closest('form')!)

    await waitFor(() => expect(authActions.register).toHaveBeenCalledWith({
      username: 'qa-citizen',
      fullName: 'QA Test',
      email: 'qa@example.invalid',
      phone: '0000000000',
      password: 'qa-password-1234',
      gender: 'unspecified',
    }))
    expect(firebase.createUserWithEmailAndPassword).not.toHaveBeenCalled()
  })

  it('offers Google registration through the existing Firebase session flow', async () => {
    firebase.signInWithPopup.mockResolvedValue({
      user: { email: 'citizen@example.com', getIdToken: vi.fn().mockResolvedValue('google-id-token') },
    })
    authActions.loginWithFirebase.mockResolvedValue(true)
    render(<LoginForm />)

    fireEvent.click(screen.getByRole('button', { name: 'Đăng ký' }))
    fireEvent.click(screen.getByRole('button', { name: 'Đăng ký bằng Google' }))

    await waitFor(() => expect(firebase.signInWithPopup).toHaveBeenCalled())
    expect(authActions.loginWithFirebase).toHaveBeenCalledWith('google-id-token')
  })

  it('sends a Firebase reset link without requesting a local reset code', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<LoginForm />)

    fireEvent.click(screen.getByRole('button', { name: 'Quên mật khẩu' }))
    fireEvent.change(screen.getByPlaceholderText('Email tài khoản trực tuyến'), { target: { value: 'citizen@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Gửi liên kết đặt lại' }))

    await waitFor(() => expect(firebase.sendPasswordResetEmail).toHaveBeenCalledWith(
      {},
      'citizen@example.com',
      expect.objectContaining({ handleCodeInApp: false }),
    ))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByPlaceholderText('Mã đặt lại mật khẩu')).not.toBeInTheDocument()
    expect(await screen.findByText(/nếu email này thuộc tài khoản email trực tuyến/i)).toBeInTheDocument()
  })

  it('toggles visibility for the login password field', async () => {
    render(<LoginForm />)

    const passwordInput = screen.getByLabelText('Mật khẩu')
    expect(passwordInput).toHaveAttribute('type', 'password')

    fireEvent.change(passwordInput, { target: { value: 'secret-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hiện mật khẩu' }))

    expect(passwordInput).toHaveAttribute('type', 'text')
    expect(passwordInput).toHaveValue('secret-password')
    expect(screen.getByRole('button', { name: 'Ẩn mật khẩu' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: 'Ẩn mật khẩu' }))
    expect(passwordInput).toHaveAttribute('type', 'password')
  })

  it('verifies a Firebase reset link and confirms the new password in Firebase', async () => {
    window.history.replaceState({}, '', '/login?mode=resetPassword&oobCode=action-code')
    firebase.verifyPasswordResetCode.mockResolvedValue('citizen@example.com')
    firebase.confirmPasswordReset.mockResolvedValue(undefined)
    render(<LoginForm />)

    expect(await screen.findByText(/Liên kết hợp lệ cho citizen@example.com/)).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Mật khẩu mới (ít nhất 12 ký tự)'), { target: { value: 'FirebasePassword123!' } })
    fireEvent.click(screen.getByRole('button', { name: 'Đặt lại mật khẩu' }))

    await waitFor(() => expect(firebase.confirmPasswordReset).toHaveBeenCalledWith(
      {},
      'action-code',
      'FirebasePassword123!',
    ))
    expect(await screen.findByText(/Đã đặt lại mật khẩu/)).toBeInTheDocument()
  })
})
