import type { UserRole } from '@/lib/stores/auth-store'
import { ADMIN_LANDING_PATH, allowedPathsForRole } from '@/lib/navigation/capabilities'

const EXTRA_ALLOWED_PATHS: Partial<Record<UserRole, string[]>> = {
  citizen: ['/legal-docs', '/legal-documents'],
  officer: ['/legal-docs', '/legal-documents'],
  admin: ['/legal-docs', '/legal-documents'],
}

type RouteGuardInput = {
  isAuthenticated: boolean
  role: UserRole | null
  mustChangePassword: boolean
  pathname: string
}

function matchesRouteBoundary(pathname: string, allowedPath: string): boolean {
  return pathname === allowedPath || pathname.startsWith(`${allowedPath}/`)
}

export function getAuthRedirect({
  isAuthenticated,
  role,
  mustChangePassword,
  pathname,
}: RouteGuardInput): string | null {
  if (!isAuthenticated || !role) return '/login'

  // Keep temporary-password sessions on this page. Applying the normal role
  // allow-list here creates a /change-password <-> /search redirect loop.
  if (mustChangePassword) {
    return pathname === '/change-password' ? null : '/change-password'
  }

  if (pathname === '/change-password') {
    return role === 'admin' ? ADMIN_LANDING_PATH : '/search'
  }

  const allowedPaths = [...allowedPathsForRole(role), ...(EXTRA_ALLOWED_PATHS[role] || [])]
  if (!allowedPaths.some((path) => matchesRouteBoundary(pathname, path))) {
    return role === 'admin' ? ADMIN_LANDING_PATH : '/search'
  }

  return null
}
