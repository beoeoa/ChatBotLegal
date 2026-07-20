export interface ChunkHighlightState {
  highlight: boolean
}

/**
 * Strip diacritics and lowercase for safe Vietnamese comparison.
 */
function normalize(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\u0111/g, 'd')
    .replace(/\u0110/g, 'D')
    .toLocaleLowerCase('vi-VN')
}

export function buildInternalLegalUrl(docId: string, article?: string, clause?: string, point?: string): string {
  const params = newSearchParams()
  if (article) params.set('article', article)
  if (clause) params.set('clause', clause)
  if (point) params.set('point', point)
  const query = params.toString()
  return `/legal-documents/${encodeURIComponent(docId)}${query ? `?${query}` : ''}`
}

function newSearchParams() { return new URLSearchParams() }

export function buildArticleAnchor(article: string): string {
  return `article-${article.trim()}`
}

export function isTargetArticle(article: string, targetArticle: string): boolean {
  return Boolean(article && targetArticle && article.trim().toLocaleLowerCase('vi-VN') === targetArticle.trim().toLocaleLowerCase('vi-VN'))
}

/**
 * Highlight only a clause/point explicitly present in imported text. If the
 * citation's clause/point cannot be verified, do not bold or mark a guessed
 * subsection; the verified article card remains highlighted instead.
 */
export function getChunkHighlightState(content: string, targetArticle: boolean, clause = '', point = ''): ChunkHighlightState {
  if (!targetArticle) return { highlight: false }
  if (!clause && !point) return { highlight: true }

  const text = normalize(content)
  const normClause = normalize(clause)
  const normPoint = normalize(point)

  const clauseMatch = normClause
    ? (text.includes(`khoan ${normClause}`) || new RegExp(`^\\s*${escapeRegex(normClause)}[.)]`, 'm').test(text))
    : true

  const pointMatch = normPoint
    ? (text.includes(`diem ${normPoint}`) || new RegExp(`^\\s*${escapeRegex(normPoint)}[.)]`, 'm').test(text))
    : true

  return { highlight: clauseMatch && pointMatch }
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
