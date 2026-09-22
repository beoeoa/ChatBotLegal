import { describe, expect, it } from 'vitest'

import { getAuthRedirect } from './route-guard'

describe('getAuthRedirect', () => {
  it('allows only Admin to open the operations dashboard', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'admin',
      mustChangePassword: false,
      pathname: '/admin',
    })).toBeNull()
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/admin',
    })).toBe('/search')
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'citizen',
      mustChangePassword: false,
      pathname: '/admin',
    })).toBe('/search')
  })

  it('keeps Admin out of the retired shared legal Q&A surface', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'admin',
      mustChangePassword: false,
      pathname: '/search',
    })).toBe('/admin')
  })

  it('keeps a temporary-password officer on the change-password page', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: true,
      pathname: '/change-password',
    })).toBeNull()
  })

  it('sends a temporary-password officer to change-password from search', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: true,
      pathname: '/search',
    })).toBe('/change-password')
  })

  it('allows an officer to open the manual proposal page', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/officer-proposals',
    })).toBeNull()
  })

  it('keeps citizens out of the officer proposal page', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'citizen',
      mustChangePassword: false,
      pathname: '/officer-proposals',
    })).toBe('/search')
  })

  it('allows citizens to open the frequently asked questions page', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'citizen',
      mustChangePassword: false,
      pathname: '/procedures',
    })).toBeNull()
  })

  it('allows officers to open the frequently asked questions page', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/procedures',
    })).toBeNull()
  })

  it('keeps citizens out of the shared legal document catalog', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'citizen',
      mustChangePassword: false,
      pathname: '/sources',
    })).toBe('/search')
  })

  it('allows officers to browse the shared legal document catalog', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/sources',
    })).toBeNull()
  })

  it('allows admins to follow the established shared catalog URL', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'admin',
      mustChangePassword: false,
      pathname: '/sources',
    })).toBeNull()
  })

  it('allows officer and admin legal profiles but redirects citizens', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/notebooks',
    })).toBeNull()
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'admin',
      mustChangePassword: false,
      pathname: '/notebooks',
    })).toBeNull()
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'citizen',
      mustChangePassword: false,
      pathname: '/notebooks',
    })).toBe('/search')
  })

  it.each(['/advanced', '/legal-quality'])(
    'sends an admin on the retired route %s to the Admin dashboard',
    (pathname) => {
      expect(getAuthRedirect({
        isAuthenticated: true,
        role: 'admin',
        mustChangePassword: false,
        pathname,
      })).toBe('/admin')
    },
  )

  it.each(['/admin-dashboard', '/admin-control', '/admin-evil'])(
    'does not treat the legacy or lookalike route %s as a child of /admin',
    (pathname) => {
      expect(getAuthRedirect({
        isAuthenticated: true,
        role: 'admin',
        mustChangePassword: false,
        pathname,
      })).toBe('/admin')
    },
  )

  it('does not expose an administration route to an officer', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/admin-control',
    })).toBe('/search')
  })

  it('keeps legal repository management Admin-only', () => {
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'admin',
      mustChangePassword: false,
      pathname: '/legal-management',
    })).toBeNull()
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'admin',
      mustChangePassword: false,
      pathname: '/legal-management/validity',
    })).toBeNull()
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'officer',
      mustChangePassword: false,
      pathname: '/legal-management/validity',
    })).toBe('/search')
    expect(getAuthRedirect({
      isAuthenticated: true,
      role: 'citizen',
      mustChangePassword: false,
      pathname: '/legal-management/42',
    })).toBe('/search')
  })

  it.each(['citizen', 'officer'] as const)(
    'allows a %s to open an internal citation document',
    (role) => {
      expect(getAuthRedirect({
        isAuthenticated: true,
        role,
        mustChangePassword: false,
        pathname: '/legal-documents/109385',
      })).toBeNull()
    },
  )
})
