export interface AuthState {
  isAuthenticated: boolean
  token: string | null
  role: 'officer' | 'citizen' | 'admin' | null
  isLoading: boolean
  error: string | null
}

export interface LoginCredentials {
  password: string
  role: 'officer' | 'citizen' | 'admin'
}
