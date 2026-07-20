import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { getApiUrl } from '@/lib/config'

export type UserRole = 'officer' | 'citizen' | 'admin'

interface AuthState {
  isAuthenticated: boolean
  token: string | null
  role: UserRole | null
  userId: string | null
  username: string | null
  email: string | null
  authMode: 'legacy_password' | 'user_session' | null
  isLoading: boolean
  error: string | null
  lastAuthCheck: number | null
  isCheckingAuth: boolean
  hasHydrated: boolean
  authRequired: boolean | null
  availableRoles: UserRole[]
  mustChangePassword: boolean
  setHasHydrated: (state: boolean) => void
  checkAuthRequired: () => Promise<boolean>
  login: (identifier: string, password: string) => Promise<boolean>
  selectRole: (role: UserRole) => void
  logout: () => void
  checkAuth: () => Promise<boolean>
}

function clearSessionState() {
  return {
    isAuthenticated: false,
    token: null,
    role: null,
    userId: null,
    username: null,
    email: null,
    authMode: null,
    mustChangePassword: false,
  }
}

function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => apiErrorMessage(item, ''))
      .filter((message) => message.trim())
    return messages.length > 0 ? messages.join('; ') : fallback
  }

  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    const message = record.message ?? record.msg ?? record.detail
    const normalized = apiErrorMessage(message, '')
    if (normalized) {
      const location = Array.isArray(record.loc)
        ? record.loc.filter((part) => typeof part === 'string' || typeof part === 'number').join('.')
        : ''
      return location ? `${normalized} (${location})` : normalized
    }
  }

  return fallback
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

      setHasHydrated: (state: boolean) => {
        set({ hasHydrated: state })
      },

      checkAuthRequired: async () => {
        try {
          const apiUrl = await getApiUrl()
          const response = await fetch(`${apiUrl}/api/auth/status`, {
            cache: 'no-store',
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
              error: 'Không kết nối được backend. Hãy kiểm tra API đã bật chưa.',
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

      login: async (identifier: string, password: string) => {
        set({ isLoading: true, error: null })
        try {
          const apiUrl = await getApiUrl()

          const response = await fetch(`${apiUrl}/api/auth/login`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ identifier: identifier.trim() || null, password }),
          })

          if (response.ok) {
            const data = await response.json()
            set({
              isAuthenticated: true,
              token: data.token,
              role: data.role,
              userId: data.user_id || null,
              username: data.username || null,
              email: data.email || null,
              authMode: data.auth_mode || 'legacy_password',
              mustChangePassword: Boolean(data.must_change_password),
              isLoading: false,
              lastAuthCheck: Date.now(),
              error: null,
            })
            return true
          }

          let errorMessage = 'Đăng nhập thất bại'
          if (response.status === 401) {
            errorMessage = 'Sai username/email hoặc mật khẩu. Vui lòng thử lại.'
          } else if (response.status === 403) {
            errorMessage = 'Tài khoản không có quyền đăng nhập hoặc đang bị khóa.'
          } else if (response.status >= 500) {
            errorMessage = 'Lỗi máy chủ. Vui lòng thử lại sau.'
          } else {
            try {
              const body = (await response.json()) as Record<string, unknown>
              errorMessage = apiErrorMessage(
                body?.detail ?? body?.message,
                `Đăng nhập thất bại (${response.status})`,
              )
            } catch {
              errorMessage = `Đăng nhập thất bại (${response.status})`
            }
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
            errorMessage = 'Không kết nối được backend. Hãy kiểm tra API đã bật chưa.'
          } else if (error instanceof Error) {
            errorMessage = `Lỗi kết nối: ${error.message}`
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

      logout: () => {
        set({
          ...clearSessionState(),
          error: null,
        })
      },

      checkAuth: async () => {
        const state = get()
        const { token, lastAuthCheck, isCheckingAuth, isAuthenticated } = state

        if (isCheckingAuth) {
          return isAuthenticated
        }

        if (!token) {
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
            headers: {
              Authorization: `Bearer ${token}`,
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
        token: state.token,
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
