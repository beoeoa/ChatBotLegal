import { describe, it, expect } from 'vitest'
import { canLoadTransformations } from '@/components/source/transformation-access'

describe('transformation access helper', () => {
  it('only allows admin', () => {
    expect(canLoadTransformations('admin')).toBe(true)
    expect(canLoadTransformations('officer')).toBe(false)
    expect(canLoadTransformations('citizen')).toBe(false)
    expect(canLoadTransformations(null)).toBe(false)
  })
})
