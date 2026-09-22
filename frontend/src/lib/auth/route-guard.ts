import type { UserRole } from '@/lib/stores/auth-store'
import { ADMIN_LANDING_PATH, allowedPathsForRole } from '@/lib/navigation/capabilities'

const EXTRA_ALLOWED_PATHS: Partial<Record<UserRole, string[]>> = {
  citizen: ['/legal-docs', '/legal-documents'],
  // `/sources` is the established URL used by the current sidebar while
  // `/legal-library` is the canonical navigation URL. Both render the same
  // read-only public catalog and must share the same role boundary.
  officer: ['/legal-docs', '/legal-documents', '/sources'],
  admin: ['/legal-docs', '/legal-documents', '/sources'],
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
