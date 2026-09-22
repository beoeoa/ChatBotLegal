import { describe, expect, it } from 'vitest'
import { shouldOfferOfficerSupport } from './support-routing'

describe('shouldOfferOfficerSupport', () => {
  it('offers support for source-only fail-closed answers', () => {
    expect(shouldOfferOfficerSupport({ grounding_status: 'source_view_only', answer_mode: 'source_view_only' })).toBe(true)
  })

  it('offers support for cannot-verify answers even when the grounding label is partial', () => {
    expect(shouldOfferOfficerSupport({ grounding_status: 'partially_grounded', answer_status: 'cannot_verify' })).toBe(true)
  })

  it('offers support when soft grounding keeps an unverified answer visible', () => {
    expect(shouldOfferOfficerSupport({
      grounding_status: 'not_assessed',
      answer_status: 'unverified',
      outcome: 'partial',
      reason_code: 'SOFT_GROUNDING_WARNING',
    })).toBe(true)
  })

  it('does not offer support for a verified answer', () => {
    expect(shouldOfferOfficerSupport({ grounding_status: 'fully_grounded', answer_status: 'verified', answer_mode: 'normal', outcome: 'answered' })).toBe(false)
  })
})
