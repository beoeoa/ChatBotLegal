import axios, { AxiosResponse } from 'axios'
import { getApiUrl } from '@/lib/config'

// API client with runtime-configurable base URL
// The base URL is fetched from the API config endpoint on first request
// Timeout increased to 10 minutes (600000ms = 600s) to accommodate slow LLM operations
// (transformations, insights generation, chat) especially on slower hardware (Ollama, LM Studio)
// Note: Frontend uses milliseconds, backend uses seconds
// Local LLMs can take several minutes for complex questions with large contexts
export const apiClient = axios.create({
  timeout: 600000, // 600 seconds = 10 minutes
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: false,
})

// Request interceptor to add base URL and auth header
apiClient.interceptors.request.use(async (config) => {
  // Set the base URL dynamically from runtime config
  if (!config.baseURL) {
    const apiUrl = await getApiUrl()
    config.baseURL = `${apiUrl}/api`
  }

  let state: { role?: string; token?: string } | null = null
  if (typeof window !== 'undefined') {
    const authStorage = localStorage.getItem('auth-storage')
    if (authStorage) {
      try {
        const parsed = JSON.parse(authStorage)
        state = parsed?.state || null
        if (state?.token) {
          config.headers.Authorization = `Bearer ${state.token}`
        }
        if (state?.role) {
          config.headers['X-User-Role'] = state.role
        }
      } catch (error) {
        console.error('Error parsing auth storage:', error)
      }
    }
  }

  // Admin detail access requires an explicit business reason. The value is
  // stored only for this browser session after the confirmation dialog.
  if (typeof window !== 'undefined' && state?.role === 'admin' && config.url) {
    const notebookMatch = window.location.pathname.match(/^\/notebooks\/([^/]+)/)
    if (notebookMatch && !config.headers['X-Business-Reason']) {
      const key = `business-reason:notebook:${decodeURIComponent(notebookMatch[1])}`
      const reason = sessionStorage.getItem(key)
      if (reason) config.headers['X-Business-Reason'] = reason
    }
  }

  // Handle FormData vs JSON content types
  if (config.data instanceof FormData) {
    // Remove any Content-Type header to let browser set multipart boundary
    delete config.headers['Content-Type']
  } else if (config.method && ['post', 'put', 'patch'].includes(config.method.toLowerCase())) {
    config.headers['Content-Type'] = 'application/json'
  }

  return config
})

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error) => {
    const requestUrl = typeof error.config?.url === 'string'
      ? error.config.url.split('?')[0]
      : ''
    const isSessionCheck = requestUrl === '/users/me' || requestUrl.endsWith('/api/users/me')
    const responseData = error.response?.data
    const passwordChangeCode = responseData?.code
      || (typeof responseData?.detail === 'object' ? responseData.detail?.code : undefined)
    const passwordChangeRequired = error.response?.status === 403
      && passwordChangeCode === 'must_change_password'

    if (passwordChangeRequired && typeof window !== 'undefined') {
      // A temporary account is still authenticated and needs a password change,
      // not a forced logout.
      if (window.location.pathname !== '/change-password') {
        window.location.href = '/change-password'
      }
    } else if (error.response?.status === 401 && isSessionCheck && typeof window !== 'undefined') {
      // Only the dedicated profile endpoint proves the stored session expired.
      // Optional dashboard requests may fail independently and must not clear a
      // valid citizen/officer session as a side effect.
      if (window.location.pathname !== '/login') {
        localStorage.removeItem('auth-storage')
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

export default apiClient

