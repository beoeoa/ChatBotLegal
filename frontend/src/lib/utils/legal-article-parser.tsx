import type { AskResponse } from '@/lib/types/search'

export type LegalCitationItem = NonNullable<AskResponse['citations']>[number]

/**
 * Regex for Vietnamese legal instruments and article references:
 * Matches patterns such as:
 * - Điều 9 Nghị định 123/2015/NĐ-CP
 * - Khoản 5 Điều 2 Nghị định 07/2025/NĐ-CP
 * - Điều 13, Điều 16 Luật Hộ tịch 2014
 * - Thông tư số 04/2020/TT-BTP
 * - Nghị định 120/2025/NĐ-CP
 */
const LEGAL_ARTICLE_REGEX = /(?:(?:Điểm\s+[a-zđ]\s+)?(?:Khoản\s+\d+[a-zA-Z]?\s+)?Điều\s+\d+[a-zA-Z]?(?:\s*,\s*Điều\s+\d+[a-zA-Z]?)*(?:\s+(?:(?:Luật|Bộ luật|Nghị định|Thông tư|Nghị quyết|Quyết định)\s+(?:số\s+)?(?:[\d/]+[A-Za-z0-9/À-ỹĐđ_-]*|[A-ZÀ-ỸĐ][A-Za-zÀ-ỹĐđ\s]+(?:\s+năm\s+\d{4}|\s+\d{4}))))?|(?:Điểm\s+[a-zđ]\s+)?Khoản\s+\d+[a-zA-Z]?(?:\s+(?:(?:Luật|Bộ luật|Nghị định|Thông tư|Nghị quyết|Quyết định)\s+(?:số\s+)?[\d/]+[A-Za-z0-9/À-ỹĐđ_-]*))?|(?:Luật|Bộ luật|Nghị định|Thông tư|Nghị quyết|Quyết định)\s+(?:số\s+)?[\d/]+[A-Za-z0-9/À-ỹĐđ_-]*|(?:Luật\s+[A-ZÀ-ỸĐ][A-Za-zÀ-ỹĐđ\s]+(?:\s+năm\s+\d{4}|\s+\d{4})))/gu

const LAW_NUMBER_EXTRACT_REGEX = /([\d]+(?:\/[\d]+)?\/[A-Za-z0-9/À-ỹĐđ_-]+)/u
const ARTICLE_NUMBER_EXTRACT_REGEX = /Điều\s+(\d+[a-zA-Z]?)/i
const CLAUSE_NUMBER_EXTRACT_REGEX = /Khoản\s+(\d+[a-zA-Z]?)/i
const POINT_NUMBER_EXTRACT_REGEX = /Điểm\s+([a-zđ])/i

export interface ResolvedLegalLink {
  text: string
  url: string
  isInternal: boolean
  articleNumber?: string
  lawNumber?: string
}

function buildVerifiedViewerUrl(
  docId: string,
  article?: string,
  clause?: string,
  point?: string,
): string {
  const params = new URLSearchParams()
  if (article) params.set('article', article)
  if (clause) params.set('clause', clause)
  if (point) params.set('point', point)
  const query = params.toString()
  return `/legal-documents/${encodeURIComponent(docId)}${query ? `?${query}` : ''}`
}

function verifiedViewerDocId(citation: LegalCitationItem): string {
  const explicit = String(citation.doc_id || '').replace(/^legal:/, '').trim()
  if (explicit) return explicit
  const viewer = String(citation.viewer_url || citation.internal_url || '').trim()
  const match = viewer.match(/^\/legal-documents\/([^?/#]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

function preciseLegalLinksEnabled(): boolean {
  return ['1', 'true', 'yes', 'on'].includes(
    String(process.env.NEXT_PUBLIC_CHAT_PRECISE_LEGAL_LINKS_V1_ENABLED || '')
      .trim()
      .toLowerCase(),
  )
}

function cleanLegalUnit(value: string | undefined, prefix: string): string {
  const normalized = String(value || '').trim().replace(new RegExp(`^${prefix}\\s+`, 'i'), '')
  if ((prefix === 'Điều' || prefix === 'khoản') && /^0+(?:\D|$)/.test(normalized)) return ''
  return normalized ? `${prefix} ${normalized}` : ''
}

export function inferLegalDocumentKind(lawNumber: string): string {
  const normalized = String(lawNumber || '').toLocaleLowerCase('vi')
  if (/\/qh\d*\b/.test(normalized)) return 'Luật'
  if (/\/n[đd]-cp\b/.test(normalized)) return 'Nghị định'
  if (/\/tt-/.test(normalized)) return 'Thông tư'
  if (/\/nq-/.test(normalized)) return 'Nghị quyết'
  if (/\/q[đd]-/.test(normalized)) return 'Quyết định'
  return 'Văn bản'
}

export function formatLegalDocumentName(documentTitle?: string | null, lawNumber?: string | null): string {
  const title = String(documentTitle || '').trim()
  const number = String(lawNumber || '').replace(/^số\s*:?\s*/i, '').trim()
  if (!number) return title || 'Nguồn pháp lý'
  const explicitKind = title.match(/^(Bộ luật|Luật|Nghị định|Thông tư|Nghị quyết|Quyết định)(?:\s|$)/i)?.[1]
  const kind = explicitKind || inferLegalDocumentKind(number)
  // Instrument numbers identify decrees/circulars without their long subject.
  // Preserve short law names supplied by verified metadata, never invent one.
  if (!['luật', 'bộ luật', 'văn bản'].includes(kind.toLocaleLowerCase('vi'))) return `${kind} số ${number}`
  const titleWithoutNumberPrefix = title.replace(/^số\s*:?\s*/i, '').trim()
  const usefulTitle = titleWithoutNumberPrefix && titleWithoutNumberPrefix.toLocaleLowerCase('vi') !== number.toLocaleLowerCase('vi')
    ? title
    : ''
  if (usefulTitle.toLocaleLowerCase('vi').includes(number.toLocaleLowerCase('vi'))) return usefulTitle
  const namedTitle = usefulTitle && !/^(Luật|Bộ luật|Nghị định|Thông tư|Nghị quyết|Quyết định|Văn bản)(?:\s|$)/i.test(usefulTitle) ? `${kind} ${usefulTitle}` : usefulTitle
  return namedTitle ? `${namedTitle} số ${number}` : `${kind} số ${number}`
}

export function formatLegalCitationLabel(citation: LegalCitationItem): string {
  const units = [
    cleanLegalUnit(citation.point_number, 'điểm'),
    cleanLegalUnit(citation.clause_number, 'khoản'),
    cleanLegalUnit(citation.article_number, 'Điều'),
  ].filter(Boolean)
  const document = formatLegalDocumentName(citation.document_title || citation.label, citation.law_number)
  return [...units, document].filter(Boolean).join(' ').trim() || 'Nguồn pháp lý'
}

export function buildOfficialArticleUrl(sourceUrl: string, article: string): string {
  if (!article) return sourceUrl
  const baseUrl = sourceUrl.split('#', 1)[0]
  return `${baseUrl}#:~:text=${encodeURIComponent(`Điều ${article}`)}`
}

/**
 * Given a legal citation text string and an optional list of verified citations from the backend,
 * resolves the target link URL and precise article scroll anchor.
 * Ensures URLs always point to either valid in-app document views or direct fulltext official pages.
 */
export function resolveLegalArticleTarget(
  text: string,
  citations?: LegalCitationItem[]
): ResolvedLegalLink | null {
  const cleanText = text.trim()
  if (!cleanText) return null

  const lawMatch = LAW_NUMBER_EXTRACT_REGEX.exec(cleanText)
  const articleMatch = ARTICLE_NUMBER_EXTRACT_REGEX.exec(cleanText)
  const clauseMatch = CLAUSE_NUMBER_EXTRACT_REGEX.exec(cleanText)
  const pointMatch = POINT_NUMBER_EXTRACT_REGEX.exec(cleanText)
  const lawNum = lawMatch ? lawMatch[1] : ''
  const artNum = articleMatch ? articleMatch[1] : ''
  const clauseNum = clauseMatch ? clauseMatch[1] : ''
  const pointNum = pointMatch ? pointMatch[1] : ''

  // 1. Check exact match in backend citations
  if (citations && citations.length > 0) {
    const candidates = citations.filter((cit) => {
      const citLaw = cit.law_number || ''
      const citTitle = cit.document_title || ''
      const titleLawMatch = LAW_NUMBER_EXTRACT_REGEX.exec(citTitle)
      const citationLawNumber = citLaw || (titleLawMatch ? titleLawMatch[1] : '')

      // Reviewed procedure/form sources do not always project ``law_number``
      // separately, but their verified title may still contain the exact
      // instrument number. Extracting that number is deterministic and avoids
      // attaching a merely related source card to an unrelated provision.
      if (
        lawNum
        && citationLawNumber
        && (
          citationLawNumber.includes(lawNum)
          || lawNum.includes(citationLawNumber)
        )
      ) {
        return true
      }
      if (citTitle && cleanText.toLowerCase().includes(citTitle.toLowerCase())) {
        return true
      }
      if (!lawNum && artNum && String(cit.article_number || '') === artNum) {
        if (clauseNum && cit.clause_number && String(cit.clause_number) !== clauseNum) return false
        if (pointNum && cit.point_number && String(cit.point_number).toLowerCase() !== pointNum.toLowerCase()) return false
        return true
      }
      return !lawNum && !artNum && clauseNum && String(cit.clause_number || '') === clauseNum
    })

    const rankedCandidates = [...candidates].sort((left, right) => {
      const score = (citation: LegalCitationItem): number => {
        if (citation.viewer_url || citation.internal_url || citation.doc_id) return 3
        if (String(citation.source_url || '').includes('vbpl.vn')) return 2
        if (citation.source_url) return 1
        return 0
      }
      return score(right) - score(left)
    })
    let matchedCitation: LegalCitationItem | null = null
    if (lawNum && rankedCandidates.length > 0) {
      // Multiple source cards may represent the same legal instrument (for
      // example an internal record plus two official mirrors). That is not an
      // ambiguity: prefer the in-app document, then VBPL, then another source.
      matchedCitation = rankedCandidates[0]
    } else {
      const uniqueDocuments = new Map<string, LegalCitationItem>()
      for (const candidate of rankedCandidates) {
        const key = String(
          candidate.law_number
          || candidate.doc_id
          || candidate.viewer_url
          || candidate.internal_url
          || candidate.source_url
          || '',
        ).trim()
        if (key && !uniqueDocuments.has(key)) uniqueDocuments.set(key, candidate)
      }
      matchedCitation = uniqueDocuments.size === 1 ? [...uniqueDocuments.values()][0] : null
    }

    if (matchedCitation) {
      const docId = verifiedViewerDocId(matchedCitation)
      const effectiveArt = artNum || matchedCitation.article_number || ''
      const sourceUrl = matchedCitation.source_url || ''

      // Prefer the in-app viewer because it can open and highlight the exact
      // article while still exposing the official source from the document page.
      if (docId) {
        return {
          text: cleanText,
          url: buildVerifiedViewerUrl(
            docId,
            effectiveArt,
            preciseLegalLinksEnabled() ? clauseNum : '',
            preciseLegalLinksEnabled() ? pointNum : '',
          ),
          isInternal: true,
          articleNumber: effectiveArt,
          lawNumber: matchedCitation.law_number || lawNum,
        }
      }

      // If source URL is a direct official portal link (e.g. VBPL, Chinhphu, etc.)
      if (
        sourceUrl &&
        !sourceUrl.includes('dichvucong.gov.vn') &&
        !sourceUrl.includes('vbpq-timkiem.aspx') &&
        (sourceUrl.startsWith('http://') || sourceUrl.startsWith('https://'))
      ) {
        // Text fragments are document-local. Always rebuild the fragment from
        // the verified article metadata so an old/wrong source fragment cannot
        // open a different provision of the same VBPL document.
        const externalUrl = buildOfficialArticleUrl(sourceUrl, effectiveArt)
        return {
          text: cleanText,
          url: externalUrl,
          isInternal: false,
          articleNumber: effectiveArt,
          lawNumber: matchedCitation.law_number || lawNum,
        }
      }

    }
  }

  if (!preciseLegalLinksEnabled() && lawNum && (!citations || citations.length === 0)) {
    return {
      text: cleanText,
      url: buildVerifiedViewerUrl(lawNum, artNum),
      isInternal: true,
      articleNumber: artNum,
      lawNumber: lawNum,
    }
  }

  // V3 never invents an internal document id from prose. Unresolved
  // references remain bold and non-interactive until this message has
  // matching backend citation metadata.
  return null
}

/**
 * Enriches markdown text:
 * 1. Bold all legal citations & articles.
 * 2. Transform every resolvable legal provision into an interactive in-app / official link.
 * 3. Keep unresolved provisions bold without inventing a destination.
 * 4. Remove all emojis/icons from the text links.
 * 5. Safely preserve existing markdown links and code blocks without nesting brackets.
 */
export function enrichMarkdownWithArticleLinks(
  content: string,
  citations?: LegalCitationItem[]
): string {
  if (!content) return ''
  // Direct answers already have links bound to request-local evidence IDs.
  // Resolving their labels again can change an E1 link to another article of
  // the same law. Preserve these links and do not invent links from prose.
  if (citations?.some(citation => citation.evidence_ids?.length)) return content

  // Step 1: Normalize any existing markdown links that might already enclose a law citation
  // e.g. [Nghị định 123/2015/NĐ-CP](https://...) -> replace with unified target without double brackets
  let normalizedContent = content.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (fullLink, linkText, linkHref) => {
      const match = linkText.trim()
      const resolved = resolveLegalArticleTarget(match, citations)
      const targetUrl = resolved?.url || linkHref
      return `[**${match}**](${targetUrl})`
    }
  )

  // DeepSeek may already bold a provision. Upgrade that formatting to the
  // same verified link instead of producing nested Markdown or leaving it as
  // non-interactive bold text.
  normalizedContent = normalizedContent.replace(
    /(?<!\[)\*\*([^*\n]+)\*\*(?!\]\()/g,
    (full, linkText: string) => {
      if (!/(?:Điều|Khoản|Điểm)\s+\w+|\d{1,4}\/\d{4}\//u.test(linkText)) return full
      const resolved = resolveLegalArticleTarget(linkText, citations)
      return resolved?.url ? `[**${linkText}**](${resolved.url})` : full
    },
  )

  // Step 2: Split text into tokens (code blocks vs normal text) to protect code formatting
  const tokens = normalizedContent.split(/(```[\s\S]*?```|`[^`]+`|\[\*\*[^*]+\*\*\]\([^)]+\))/g)

  const processedTokens = tokens.map((token) => {
    // Skip code blocks and already converted links
    if (token.startsWith('`') || token.startsWith('[**')) {
      return token
    }

    // Replace plain legal citation text
    return token.replace(LEGAL_ARTICLE_REGEX, (match) => {
      const resolved = resolveLegalArticleTarget(match, citations)
      if (resolved && resolved.url) {
        return `[**${match}**](${resolved.url})`
      }
      return `**${match}**`
    })
  })

  return processedTokens.join('')
}
