export interface ChunkHighlightState {
  highlight: boolean
}

export interface TocNode {
  id: string
  title: string
  label: string
  level: 'chapter' | 'article' | 'clause' | 'point'
  articleNumber?: string
  clauseNumber?: string
  pointLetter?: string
  children?: TocNode[]
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

export function buildSectionAnchor(article: string, clause?: string, point?: string): string {
  let anchor = `article-${article.trim()}`
  if (clause) anchor += `-clause-${clause.trim()}`
  if (point) anchor += `-point-${point.trim()}`
  return anchor
}

export function isTargetArticle(article: string, targetArticle: string): boolean {
  return Boolean(article && targetArticle && article.trim().toLocaleLowerCase('vi-VN') === targetArticle.trim().toLocaleLowerCase('vi-VN'))
}

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

/**
 * Parse articles and content chunks into a hierarchical TOC tree (Chương -> Điều -> Khoản -> Điểm)
 */
export function buildVBPLLegalTocTree(
  articles: Array<{
    article_number?: string
    article_title?: string
    chunks?: Array<{ content?: string }>
  }>
): TocNode[] {
  const tree: TocNode[] = []
  let currentChapter: TocNode | null = null

  for (const art of articles) {
    const artNum = String(art.article_number || '').trim()
    if (!artNum) continue

    const artTitle = art.article_title ? `Điều ${artNum}. ${art.article_title}` : `Điều ${artNum}`

    // Parse clauses and points inside chunks
    const clauseNodes: TocNode[] = []
    const fullText = (art.chunks || []).map((c) => c.content || '').join('\n')

    // Detect Chapter headers in text if present (e.g. "Chương I", "Chương II")
    const chapterMatch = fullText.match(/^\\s*(Chương\\s+[I|V|X|L|C|D|M\d]+.*?)$/im)
    if (chapterMatch) {
      const chapText = chapterMatch[1].trim()
      currentChapter = {
        id: `chapter-${tree.length + 1}`,
        title: chapText,
        label: chapText,
        level: 'chapter',
        children: [],
      }
      tree.push(currentChapter)
    }

    // Match Clauses (Khoản 1, 2, 3...)
    const clauseRegex = /^\s*(\d+)\.\s+([^\n]+)/gm
    let clauseMatch: RegExpExecArray | null
    const clausesMap: Map<string, TocNode> = new Map()

    while ((clauseMatch = clauseRegex.exec(fullText)) !== null) {
      const cNum = clauseMatch[1]
      const cText = clauseMatch[2].substring(0, 50).trim()
      const cNode: TocNode = {
        id: buildSectionAnchor(artNum, cNum),
        title: `Khoản ${cNum}`,
        label: `Khoản ${cNum}: ${cText}...`,
        level: 'clause',
        articleNumber: artNum,
        clauseNumber: cNum,
        children: [],
      }
      clausesMap.set(cNum, cNode)
      clauseNodes.push(cNode)
    }

    // Match Points (Điểm a, b, c...)
    const pointRegex = /^\s*([a-đa-z])\)\s+([^\n]+)/gim
    let pointMatch: RegExpExecArray | null

    while ((pointMatch = pointRegex.exec(fullText)) !== null) {
      const pLetter = pointMatch[1].toLowerCase()
      const pText = pointMatch[2].substring(0, 40).trim()
      const pNode: TocNode = {
        id: buildSectionAnchor(artNum, undefined, pLetter),
        title: `Điểm ${pLetter}`,
        label: `Điểm ${pLetter}: ${pText}...`,
        level: 'point',
        articleNumber: artNum,
        pointLetter: pLetter,
      }
      // Attach to last clause or article
      if (clauseNodes.length > 0) {
        clauseNodes[clauseNodes.length - 1].children?.push(pNode)
      }
    }

    const articleNode: TocNode = {
      id: buildArticleAnchor(artNum),
      title: `Điều ${artNum}`,
      label: artTitle,
      level: 'article',
      articleNumber: artNum,
      children: clauseNodes,
    }

    if (currentChapter && currentChapter.children) {
      currentChapter.children.push(articleNode)
    } else {
      tree.push(articleNode)
    }
  }

  return tree
}
