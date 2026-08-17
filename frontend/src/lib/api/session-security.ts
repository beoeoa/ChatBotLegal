export const CSRF_COOKIE_NAME = 'chatbotlegal_csrf'
export const CSRF_HEADER_NAME = 'X-CSRF-Token'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE'])

export function getCsrfToken(): string | null {
  if (typeof document === 'undefined') return null
  for (const item of document.cookie.split(';')) {
    const [rawName, ...rawValue] = item.trim().split('=')
    if (rawName === CSRF_COOKIE_NAME) {
      try {
        return decodeURIComponent(rawValue.join('='))
      } catch {
        return null
      }
    }
  }
  return null
}

export function sessionSecurityHeaders(method?: string): Record<string, string> {
  if (!method || SAFE_METHODS.has(method.toUpperCase())) return {}
  const csrf = getCsrfToken()
  return csrf ? { [CSRF_HEADER_NAME]: csrf } : {}
}
