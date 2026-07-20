import { describe, expect, it } from 'vitest'

import {
  buildArticleAnchor,
  buildInternalLegalUrl,
  getChunkHighlightState,
  isTargetArticle,
} from './legal-document-viewer-utils'

describe('legal document viewer utilities', () => {
  it('builds canonical citation viewer URLs', () => {
    expect(buildInternalLegalUrl('481400', '6', '2', 'a')).toBe(
      '/legal-documents/481400?article=6&clause=2&point=a',
    )
  })

  it('builds article anchor and strictly matches its article', () => {
    expect(buildArticleAnchor('35')).toBe('article-35')
    expect(isTargetArticle('35', '35')).toBe(true)
    expect(isTargetArticle('35', '36')).toBe(false)
  })

  it('highlights an explicitly verified clause and point only', () => {
    const matching = 'Kho\u1ea3n 2. \u0110i\u1ec3m a) N\u1ed9i dung \u00e1p d\u1ee5ng.'
    const different = 'Kho\u1ea3n 3. \u0110i\u1ec3m b) N\u1ed9i dung kh\u00e1c.'
    const absent = 'N\u1ed9i dung kh\u00f4ng c\u00f3 kho\u1ea3n/\u0111i\u1ec3m.'
    expect(getChunkHighlightState(matching, true, '2', 'a').highlight).toBe(true)
    expect(getChunkHighlightState(different, true, '2', 'a').highlight).toBe(false)
    expect(getChunkHighlightState(absent, true, '2', 'a').highlight).toBe(false)
  })
})
