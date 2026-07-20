import type { UserRole } from '@/lib/stores/auth-store'

const ROLE_ALLOWED_PATHS: Partial<Record<UserRole, string[]>> = {
  citizen: [
    '/search',
    '/procedures',
    '/legal-docs',
    '/legal-documents',
    '/live-support',
  ],
  officer: [
    '/search',
    '/sources',
    '/notebooks',
    '/procedures',
    '/legal-docs',
    '/legal-documents',
    '/live-support',
    '/officer-proposals',
  ],
}

type RouteGuardInput = {
  isAuthenticated: boolean
  role: UserRole | null
  mustChangePassword: boolean
  pathname: string
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
    return role === 'admin' ? '/notebooks' : '/search'
  }

  const allowedPaths = ROLE_ALLOWED_PATHS[role]
  if (allowedPaths && !allowedPaths.some((path) => pathname.startsWith(path))) {
    return '/search'
  }

  return null
}
