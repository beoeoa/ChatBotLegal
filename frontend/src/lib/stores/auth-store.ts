import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { getApiUrl } from '@/lib/config'
import { sessionSecurityHeaders } from '@/lib/api/session-security'
import { formatApiError } from '@/lib/utils/error-handler'

export type UserRole = 'officer' | 'citizen' | 'admin'
export type MfaChallenge =
  | { type: 'code' }
  | { type: 'setup'; setupToken: string }

export type TotpSetup = {
  secret: string
  provisioningUri: string
  confirmToken: string
}

export type FirebaseProfileInput = {
  gender?: 'male' | 'female' | 'unspecified'
  fullName?: string
  phone?: string
}

export type CitizenRegistrationInput = {
  username: string
  email: string
  password: string
  fullName?: string
  phone?: string
  gender?: 'male' | 'female' | 'unspecified'
}

interface AuthState {
  isAuthenticated: boolean
  token: string | null
  role: UserRole | null
  userId: string | null
  username: string | null
  email: string | null
  authMode: 'legacy_password' | 'user_session' | 'cookie_session' | null
  isLoading: boolean
  error: string | null
  lastAuthCheck: number | null
  isCheckingAuth: boolean
  hasHydrated: boolean
  authRequired: boolean | null
  availableRoles: UserRole[]
  mustChangePassword: boolean
  mfaChallenge: MfaChallenge | null
  setHasHydrated: (state: boolean) => void
  checkAuthRequired: () => Promise<boolean>
  login: (identifier: string, password: string, totpCode?: string) => Promise<boolean>
  register: (input: CitizenRegistrationInput) => Promise<boolean>
  exchangeFirebaseToken: (idToken: string, profile?: FirebaseProfileInput) => Promise<boolean>
  linkFirebaseAccount: (idToken: string, identifier: string, password: string) => Promise<boolean>
  setupTotp: (setupToken: string) => Promise<TotpSetup | null>
  confirmTotp: (confirmToken: string, code: string) => Promise<boolean>
  selectRole: (role: UserRole) => void
  logout: () => Promise<void>
  checkAuth: () => Promise<boolean>
}

function clearSessionState() {
  if (typeof window !== 'undefined') {
    try {
      Object.keys(sessionStorage).filter(key => key.startsWith('admin-snapshot:')).forEach(key => sessionStorage.removeItem(key))
    } catch { /* Storage may be unavailable. */ }
  }
  return {
    isAuthenticated: false,
    token: null,
    role: null,
    userId: null,
    username: null,
    email: null,
    authMode: null,
    mustChangePassword: false,
    mfaChallenge: null,
  }
}

function apiErrorMessage(detail: unknown, fallback: string): string {
  return formatApiError({ detail }, fallback)
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
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
      hasHydrated: false,
      authRequired: null,
      availableRoles: ['citizen'],
      mustChangePassword: false,
      mfaChallenge: null,

      setHasHydrated: (state: boolean) => {
        set({ hasHydrated: state })
      },

      checkAuthRequired: async () => {
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/status`, {
            cache: 'no-store',
            credentials: 'include',
          })

          if (!response.ok) {
            throw new Error(`Auth status check failed: ${response.status}`)
          }

          const data = await response.json()
          const required = data.auth_enabled || false
          const availableRoles = Array.isArray(data.available_roles) && data.available_roles.length > 0
            ? data.available_roles
            : ['citizen']
          set({ authRequired: required, availableRoles })

          if (!required) {
            set({ isAuthenticated: true, token: 'not-required', role: 'citizen' })
          }

          return required
        } catch (error) {
          console.error('Failed to check auth status:', error)
          if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
            set({
              error: 'Không kết nối được hệ thống. Vui lòng kiểm tra mạng rồi thử lại.',
              authRequired: null,
            })
          } else {
            set({ authRequired: true })
          }
          throw error
        }
      },

      // Kept for compatibility with older UI code. Public login no longer lets
      // users choose privileged roles.
      selectRole: (role: UserRole) => {
        set({ role })
      },

      login: async (identifier: string, password: string, totpCode?: string) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()

          const response = await fetch(`${apiUrl}/api/auth/login`, {
            method: 'POST',
            credentials: 'include',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              identifier: identifier.trim() || null,
              password,
              ...(totpCode?.trim() ? { totp_code: totpCode.trim() } : {}),
            }),
          })

          if (response.ok) {
            const data = await response.json()
            set({
              isAuthenticated: true,
              token: data.token || null,
              role: data.role,
              userId: data.user_id || null,
              username: data.username || null,
              email: data.email || null,
              authMode: data.auth_mode || 'legacy_password',
              mustChangePassword: Boolean(data.must_change_password),
              mfaChallenge: null,
              isLoading: false,
              lastAuthCheck: Date.now(),
              error: null,
            })
            return true
          }

          let responseBody: Record<string, unknown> = {}
          try {
            responseBody = (await response.json()) as Record<string, unknown>
          } catch {
            responseBody = {}
          }
          const detail = responseBody.detail
          const detailRecord = detail && typeof detail === 'object'
            ? detail as Record<string, unknown>
            : {}
          const mfaCode = typeof detailRecord.code === 'string' ? detailRecord.code : ''
          if (
            response.status === 428
            && mfaCode === 'MFA_SETUP_REQUIRED'
            && typeof detailRecord.setup_token === 'string'
          ) {
            set({
              ...clearSessionState(),
              mfaChallenge: {
                type: 'setup',
                setupToken: detailRecord.setup_token,
              },
              error: null,
              isLoading: false,
            })
            return false
          }
          if (
            response.status === 401
            && (mfaCode === 'MFA_CODE_REQUIRED' || mfaCode === 'MFA_CODE_INVALID')
          ) {
            set({
              ...clearSessionState(),
              mfaChallenge: { type: 'code' },
              error: mfaCode === 'MFA_CODE_INVALID'
                ? 'Mã xác thực không đúng hoặc đã hết hạn.'
                : null,
              isLoading: false,
            })
            return false
          }

          let errorMessage = 'Đăng nhập thất bại'
          if (response.status === 401) {
            errorMessage = 'Sai username/email hoặc mật khẩu. Vui lòng thử lại.'
          } else if (response.status === 403) {
            errorMessage = 'Tài khoản không có quyền đăng nhập hoặc đang bị khóa.'
          } else if (response.status >= 500) {
            errorMessage = 'Lỗi máy chủ. Vui lòng thử lại sau.'
          } else {
            errorMessage = apiErrorMessage(
              responseBody?.detail ?? responseBody?.message,
              'Đăng nhập không thành công. Vui lòng kiểm tra thông tin và thử lại.',
            )
          }

          set({
            ...clearSessionState(),
            error: errorMessage,
            isLoading: false,
          })
          return false
        } catch (error) {
          console.error('Network error during auth:', error)
          let errorMessage = 'Đăng nhập thất bại'
          if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
            errorMessage = 'Không kết nối được hệ thống. Vui lòng kiểm tra mạng rồi thử lại.'
          } else if (error instanceof Error) {
            errorMessage = formatApiError(error, 'Không thể đăng nhập. Vui lòng thử lại.')
          } else {
            errorMessage = 'Có lỗi không xác định khi đăng nhập'
          }

          set({
            ...clearSessionState(),
            error: errorMessage,
            isLoading: false,
          })
          return false
        }
      },

      register: async (input: CitizenRegistrationInput) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/register`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              username: input.username.trim(),
              email: input.email.trim(),
              password: input.password,
              ...(input.fullName?.trim() ? { full_name: input.fullName.trim() } : {}),
              ...(input.phone?.trim() ? { phone: input.phone.trim() } : {}),
              gender: input.gender || 'unspecified',
            }),
          })
          const data = await response.json().catch(() => ({})) as Record<string, unknown>
          if (!response.ok) {
            set({
              ...clearSessionState(),
              isLoading: false,
              error: apiErrorMessage(
                data.detail ?? data.message,
                'Không thể tạo tài khoản. Vui lòng kiểm tra thông tin và thử lại.',
              ),
            })
            return false
          }
          set({
            isAuthenticated: true,
            token: typeof data.token === 'string' ? data.token : null,
            role: (data.role as UserRole) || 'citizen',
            userId: typeof data.user_id === 'string' ? data.user_id : null,
            username: typeof data.username === 'string' ? data.username : input.username.trim(),
            email: typeof data.email === 'string' ? data.email : input.email.trim(),
            authMode: (data.auth_mode as AuthState['authMode']) || 'cookie_session',
            mustChangePassword: false,
            mfaChallenge: null,
            isLoading: false,
            lastAuthCheck: Date.now(),
            error: null,
          })
          return true
        } catch (error) {
          set({
            ...clearSessionState(),
            isLoading: false,
            error: formatApiError(
              error,
              'Không kết nối được hệ thống đăng ký. Vui lòng thử lại.',
            ),
          })
          return false
        }
      },

      exchangeFirebaseToken: async (idToken: string, profile?: FirebaseProfileInput) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/firebase/session`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id_token: idToken,
              ...(profile?.gender ? { gender: profile.gender } : {}),
              ...(profile?.fullName?.trim() ? { full_name: profile.fullName.trim() } : {}),
              ...(profile?.phone?.trim() ? { phone: profile.phone.trim() } : {}),
            }),
          })
          const data = await response.json().catch(() => ({})) as Record<string, unknown>
          if (!response.ok) {
            set({
              ...clearSessionState(),
              isLoading: false,
              error: apiErrorMessage(data.detail ?? data.message, 'Không thể xác thực tài khoản.'),
            })
            return false
          }
          set({
            isAuthenticated: true,
            token: typeof data.token === 'string' ? data.token : null,
            role: data.role as UserRole,
            userId: typeof data.user_id === 'string' ? data.user_id : null,
            username: typeof data.username === 'string' ? data.username : null,
            email: typeof data.email === 'string' ? data.email : null,
            authMode: (data.auth_mode as AuthState['authMode']) || 'cookie_session',
            mustChangePassword: false,
            mfaChallenge: null,
            isLoading: false,
            lastAuthCheck: Date.now(),
            error: null,
          })
          return true
        } catch {
          set({
            ...clearSessionState(),
            isLoading: false,
            error: 'Không kết nối được dịch vụ đăng nhập Google.',
          })
          return false
        }
      },

      linkFirebaseAccount: async (idToken: string, identifier: string, password: string) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/firebase/link`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id_token: idToken,
              identifier: identifier.trim(),
              password,
            }),
          })
          const data = await response.json().catch(() => ({})) as Record<string, unknown>
          if (!response.ok) {
            set({
              ...clearSessionState(),
              isLoading: false,
              error: apiErrorMessage(data.detail ?? data.message, 'Không thể liên kết tài khoản Google.'),
            })
            return false
          }
          set({
            isAuthenticated: true,
            token: typeof data.token === 'string' ? data.token : null,
            role: data.role as UserRole,
            userId: typeof data.user_id === 'string' ? data.user_id : null,
            username: typeof data.username === 'string' ? data.username : null,
            email: typeof data.email === 'string' ? data.email : null,
            authMode: (data.auth_mode as AuthState['authMode']) || 'cookie_session',
            mustChangePassword: Boolean(data.must_change_password),
            mfaChallenge: null,
            isLoading: false,
            lastAuthCheck: Date.now(),
            error: null,
          })
          return true
        } catch (error) {
          set({
            ...clearSessionState(),
            isLoading: false,
            error: formatApiError(error, 'Không kết nối được dịch vụ liên kết Google.'),
          })
          return false
        }
      },

      setupTotp: async (setupToken: string) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/totp/setup`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ setup_token: setupToken }),
          })
          if (!response.ok) {
            throw new Error('TOTP_SETUP_FAILED')
          }
          const data = await response.json()
          set({ isLoading: false })
          return {
            secret: data.secret,
            provisioningUri: data.provisioning_uri,
            confirmToken: data.confirm_token,
          }
        } catch {
          set({
            isLoading: false,
            error: 'Không thể tạo cấu hình xác thực hai lớp. Hãy đăng nhập lại.',
            mfaChallenge: null,
          })
          return null
        }
      },

      confirmTotp: async (confirmToken: string, code: string) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/totp/confirm`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              confirm_token: confirmToken,
              code: code.trim(),
            }),
          })
          if (!response.ok) {
            set({
              isLoading: false,
              error: 'Mã xác thực không đúng hoặc đã hết hạn.',
            })
            return false
          }
          const data = await response.json()
          set({
            isAuthenticated: true,
            token: data.token || null,
            role: data.role,
            userId: data.user_id || null,
            username: data.username || null,
            email: data.email || null,
            authMode: data.auth_mode || 'cookie_session',
            mustChangePassword: Boolean(data.must_change_password),
            mfaChallenge: null,
            isLoading: false,
            lastAuthCheck: Date.now(),
            error: null,
          })
          return true
        } catch {
          set({
            isLoading: false,
            error: 'Không kết nối được dịch vụ xác thực hai lớp.',
          })
          return false
        }
      },

      logout: async () => {
        const state = get()
        const controller = new AbortController()
        const timeout = window.setTimeout(() => controller.abort(), 5000)
        try {
          const apiUrl = await getApiUrl()
          await fetch(`${apiUrl}/api/auth/logout`, {
            method: 'POST',
            credentials: 'include',
            signal: controller.signal,
            headers: {
              ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
              ...sessionSecurityHeaders('POST'),
            },
          })
        } catch {
          // Local session state must still be cleared when the server is
          // temporarily unreachable.
        } finally {
          try {
            const { firebaseEnabled, getFirebaseAuth } = await import('@/lib/firebase/client')
            if (firebaseEnabled) {
              const { signOut } = await import('@firebase/auth')
              await signOut(getFirebaseAuth())
            }
          } catch {
            // Firebase may not have an active browser session.
          }
          window.clearTimeout(timeout)
          set({
            ...clearSessionState(),
            error: null,
          })
        }
      },

      checkAuth: async () => {
        const state = get()
        const {
          token,
          authMode,
          lastAuthCheck,
          isCheckingAuth,
          isAuthenticated,
        } = state

        if (isCheckingAuth) {
          return isAuthenticated
        }

        if (!token && authMode !== 'cookie_session') {
          return false
        }

        const now = Date.now()
        if (isAuthenticated && lastAuthCheck && (now - lastAuthCheck) < 30000) {
          return true
        }

        set({ isCheckingAuth: true })

        try {
          const apiUrl = await getApiUrl()

          const response = await fetch(`${apiUrl}/api/users/me`, {
            method: 'GET',
            credentials: 'include',
            headers: {
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
              'X-User-Role': state.role || 'citizen',
              'Content-Type': 'application/json',
            },
          })

          if (response.ok) {
            const data = await response.json()
            set({
              isAuthenticated: true,
              role: data.role || state.role,
              userId: data.id || null,
              username: data.username || null,
              email: data.email || null,
              authMode: data.auth_mode || state.authMode || 'legacy_password',
              mustChangePassword: Boolean(data.profile?.must_change_password),
              lastAuthCheck: now,
              isCheckingAuth: false,
            })
            return true
          }

          set({
            ...clearSessionState(),
            lastAuthCheck: null,
            isCheckingAuth: false,
          })
          return false
        } catch (error) {
          console.error('checkAuth error:', error)
          set({
            lastAuthCheck: null,
            isCheckingAuth: false,
            error: 'Không thể xác nhận phiên do máy chủ tạm thời không phản hồi. Phiên đăng nhập của bạn vẫn được giữ; hãy thử lại sau ít phút.',
          })
          return state.isAuthenticated
        }
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        // P0 Security: do not persist bearer token in localStorage (XSS).
        // cookie_session relies on HttpOnly cookie + withCredentials.
        isAuthenticated: state.isAuthenticated,
        role: state.role,
        userId: state.userId,
        username: state.username,
        email: state.email,
        authMode: state.authMode,
        mustChangePassword: state.mustChangePassword,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      },
    },
  ),
)
