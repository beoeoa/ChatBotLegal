import { describe, expect, it } from 'vitest'

import { getAuthRedirect } from './route-guard'

describe('getAuthRedirect', () => {
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

  it('allows an officer to open proposals and the weekly monitor', () => {
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
