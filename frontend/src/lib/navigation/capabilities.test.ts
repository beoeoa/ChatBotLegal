import { describe, expect, it } from 'vitest'

import { allowedPathsForRole, createActionsForRole, navigationForRole } from './capabilities'

function navigationPaths(role: 'citizen' | 'officer' | 'admin') {
  return navigationForRole(role).flatMap((group) =>
    group.items.map((item) => item.href.split('?')[0]),
  )
}

describe('role navigation capabilities', () => {
  it('uses the Admin dashboard as the first Admin destination', () => {
    expect(navigationPaths('admin')[0]).toBe('/admin')
    expect(allowedPathsForRole('admin')).toContain('/admin')
    expect(navigationPaths('officer')).not.toContain('/admin')
    expect(navigationPaths('citizen')).not.toContain('/admin')
  })

  it('exposes the auditable activity center only to Admin', () => {
    expect(navigationPaths('admin')).toContain('/admin/activity')
    expect(allowedPathsForRole('admin')).toContain('/admin/activity')
    expect(navigationPaths('officer')).not.toContain('/admin/activity')
    expect(navigationPaths('citizen')).not.toContain('/admin/activity')
  })

  it('shows legal repository management only to Admin', () => {
    expect(navigationPaths('admin')).toContain('/legal-management')
    expect(navigationPaths('officer')).not.toContain('/legal-management')
    expect(navigationPaths('citizen')).not.toContain('/legal-management')
  })

  it('removes live support from every Admin navigation surface', () => {
    expect(navigationPaths('admin')).not.toContain('/live-support')
    expect(allowedPathsForRole('admin')).not.toContain('/live-support')
  })

  it('keeps live support available for citizens and officers', () => {
    expect(navigationPaths('citizen')).toContain('/live-support')
    expect(navigationPaths('officer')).toContain('/live-support')
  })

  it('gives officer and admin a private legal-profile create action', () => {
    expect(createActionsForRole('officer').map((item) => item.id)).toEqual(['notebook'])
    expect(createActionsForRole('admin').map((item) => item.id)).toContain('notebook')
    expect(createActionsForRole('citizen')).toEqual([])
  })
})
