import { describe, expect, it } from 'vitest'
import { shouldLoadAdminModelMetadata } from './model-access'

describe('shouldLoadAdminModelMetadata', () => {
  it.each(['citizen', 'officer', '', undefined])('keeps admin endpoints disabled for %s', (role) => {
    expect(shouldLoadAdminModelMetadata(role, true)).toBe(false)
  })

  it('enables admin model metadata only after hydration', () => {
    expect(shouldLoadAdminModelMetadata('admin', false)).toBe(false)
    expect(shouldLoadAdminModelMetadata('admin', true)).toBe(true)
  })
})
