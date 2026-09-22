import { useAuthStore } from '@/lib/stores/auth-store'

const PREFIX = 'admin-snapshot:v1:'
const MAX_AGE = 30 * 60 * 1000

function scopedKey(key: string): string | null {
  const auth = useAuthStore.getState()
  return auth.isAuthenticated && auth.role === 'admin' && auth.userId
    ? `${PREFIX}${auth.userId}:${key}` : null
}

export function readAdminSnapshot<T>(key: string): T | null {
  if (typeof window === 'undefined') return null
  const scoped = scopedKey(key)
  if (!scoped) return null
  try {
    const item = JSON.parse(sessionStorage.getItem(scoped) || 'null')
    if (!item || typeof item.savedAt !== 'number' || Date.now() - item.savedAt > MAX_AGE) return null
    return item.data as T
  } catch { return null }
}

export function writeAdminSnapshot<T>(key: string, data: T): void {
  if (typeof window === 'undefined') return
  const scoped = scopedKey(key)
  if (!scoped) return
  try { sessionStorage.setItem(scoped, JSON.stringify({ savedAt: Date.now(), data })) } catch { /* Cache is optional. */ }
}
