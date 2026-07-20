'use client'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { CheckCircle, Sparkles, Lightbulb, ChevronDown, BookOpen } from 'lucide-react'
import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import type React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { convertReferencesToMarkdownLinks } from '@/lib/utils/source-references'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { useTranslation } from '@/lib/hooks/use-translation'
import { toast } from 'sonner'
import type { AskResponse, RagTrace } from '@/lib/types/search'
import { FAQAccordion } from './FAQAccordion'

type ProcedureDetail = NonNullable<AskResponse['procedure_detail']>
type ProcedureForm = ProcedureDetail['forms'][number]
type RecommendedForm = NonNullable<AskResponse['recommended_forms']>[number]
type DisplayForm = ProcedureForm | RecommendedForm
type LegalCitation = NonNullable<AskResponse['citations']>[number]

export function sanitizeDisplayAnswer(content: string, citations?: LegalCitation[]): string {
  // Keep answer clean for citizens/officers: no internal ids or technical bracket citations.
  // Backend still retains structured citations/rag_trace for audit.
  let text = content || ''

  const toNaturalCitation = (raw: string): string => {
    const body = (raw || '').trim()
    if (!body) return ''
    // [legal:481400 - 60/2014/QH13 - Điều 35] or [123/2015/NĐ-CP - Điều 29]
    const withoutLegal = body.replace(/^legal\s*:\s*/i, '').trim()
    const cleaned = withoutLegal.replace(/^\d+\s*[-–—:]\s*/, '').trim() || withoutLegal
    const lawMatch = cleaned.match(/(\d{1,4}\/\d{4}\/[A-Za-zÀ-ỹĐđ0-9\-]+)/i)
    const artMatch = cleaned.match(/(?:điều|dieu)\s*(\d+[a-zA-Z]?)/i)
    const law = lawMatch?.[1] || ''
    const art = artMatch?.[1] || ''
    let title = ''
    if (law && cleaned.includes(law)) {
      title = cleaned.split(law)[0].replace(/[-–—:,]/g, ' ').trim()
    }
    const inferPrefix = (value: string): string => {
      const v = value.toLowerCase()
      if (/\/qh\d*\b/.test(v) || v.includes('luật') || v.includes('luat')) return 'Luật'
      if (/\/nđ-cp|\/nd-cp/.test(v) || v.includes('nghị định') || v.includes('nghi dinh')) return 'Nghị định'
      if (/\/tt-/.test(v) || v.includes('thông tư') || v.includes('thong tu')) return 'Thông tư'
      if (/\/qđ-|\/qd-/.test(v) || v.includes('quyết định') || v.includes('quyet dinh')) return 'Quyết định'
      return ''
    }

    let head = ''
    if (title && law) head = title.toLowerCase().includes(law.toLowerCase()) ? title : `${title} ${law}`.trim()
    else if (title) head = title
    else if (law) {
      const prefix = inferPrefix(`${title} ${law}`)
      head = prefix ? `${prefix} ${law}` : law
    } else {
      head = cleaned
    }
    if (art) return `${head}, Điều ${art}`
    return head
  }

  // Remove/replace [legal:...] first
  text = text.replace(/\[\s*legal\s*:\s*([^\]]+)\]/gi, (_m, body: string) => {
    const cleanBody = String(body || '').trim()
    const natural = toNaturalCitation(cleanBody)
    if (!natural || /^\d+$/.test(natural.trim())) return ''
    
    return `**${natural}**`
  })
  // bare legal:123
  text = text.replace(/\blegal\s*:\s*\d+\b/gi, '')
  // bracket law citations like [123/2015/NĐ-CP - Điều 29]
  text = text.replace(/\[([^\]]{3,120})\]/g, (full, body: string) => {
    const raw = String(body || '')
    if (/^https?:\/\//i.test(raw) || /^(source|note|source_insight)\s*:/i.test(raw)) return full
    if (/\d{1,4}\/\d{4}\//.test(raw) || /(?:điều|dieu)\s*\d+/i.test(raw)) {
      const natural = toNaturalCitation(raw)
      if (natural) {
        return `**${natural}**`
      }
      return ''
    }
    return full
  })
  // Normalize "60/2014/QH13 - Điều 35" and "(Điều 35)"
  text = text.replace(
    /(\d{1,4}\/\d{4}\/[A-Za-zÀ-ỹĐđ0-9\-]+)\s*[-–—]\s*(?:Đi[eè]u|Điều)\s*(\d+[a-zA-Z]?)/gi,
    (_m, law: string, art: string) => {
      const prefix = /\/qh/i.test(law) ? 'Luật ' : /\/n[đd]-cp/i.test(law) ? 'Nghị định ' : /\/tt-/i.test(law) ? 'Thông tư ' : ''
      const natural = `${prefix}${law}, Điều ${art}`.replace(/\s+/g, ' ').trim()
      return `**${natural}**`
    }
  )
  text = text.replace(
    /(\d{1,4}\/\d{4}\/[A-Za-zÀ-ỹĐđ0-9\-]+)\s*\(\s*(?:Đi[eè]u|Điều)\s*(\d+[a-zA-Z]?)\s*\)/gi,
    (_m, law: string, art: string) => {
      const prefix = /\/qh/i.test(law) ? 'Luật ' : /\/n[đd]-cp/i.test(law) ? 'Nghị định ' : /\/tt-/i.test(law) ? 'Thông tư ' : ''
      const natural = `${prefix}${law}, Điều ${art}`.replace(/\s+/g, ' ').trim()
      return `**${natural}**`
    }
  )
  // Drop residual long source appendix headers if model still emits them
  text = text.replace(/^##\s*Căn cứ\s*\/\s*Nguồn[^\n]*\n?/gim, '')
  text = text.replace(/^###\s*Liên kết nguồn\s*\n?/gim, '')

  // Auto-map any residual raw law numbers from citations to markdown links if not already wrapped
  if (citations && citations.length > 0) {
    for (const c of citations) {
      const lawNum = String(c.law_number || '').trim()
      if (lawNum && lawNum.length > 3) {
        const escapedLawNum = lawNum.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')
        const regex = new RegExp(`(?:(luật|nghị định|thông tư|quyết định|nghị quyết)\\s+(?:số\\s+)?)?\\b${escapedLawNum}\\b`, 'gi')
        text = text.replace(regex, (match, p1, offset) => {
          const before = text.substring(Math.max(0, offset - 35), offset)
          const after = text.substring(offset + match.length, Math.min(text.length, offset + match.length + 35))
          if (before.includes('#ref-') || before.includes('](') || after.startsWith(')')) {
            return match
          }
          return `**${match}**`
        })
      }
    }
  }

  return text
    .replace(/#ref-source-[\w-]+/gi, '')
    .replace(/\b(?:legal\s*:\s*\d+|chunk[_ -]?id\s*[:=]\s*[\w-]+|trace[_ -]?id\s*[:=]\s*[\w-]+|packet[_ -]?id\s*[:=]\s*[\w-]+)\b/gi, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ ]{2,}/g, ' ')
    .replace(/\(\s*\)/g, '')
    .trim()
}

function isExternalUrl(value?: string | null): boolean {
  return Boolean(value && /^https?:\/\//i.test(value))
}

function getAuthToken(): string {
  if (typeof window === 'undefined') return ''
  try {
    const raw = localStorage.getItem('auth-storage')
    if (!raw) return ''
    const parsed = JSON.parse(raw)
    return String(parsed?.state?.token || '')
  } catch {
    return ''
  }
}

async function downloadLegalPdf(docId: string, article?: string, fileBase?: string) {
  const token = getAuthToken()
  if (!token) {
    toast.error('Bạn cần đăng nhập để tải văn bản pháp lý.')
    return
  }
  const qs = article ? `?article=${encodeURIComponent(article)}` : ''
  const relativeEndpoint = `/api/legal/docs/${encodeURIComponent(docId)}/download.pdf${qs}`
  const headers: HeadersInit = { Authorization: `Bearer ${token}` }
  let response = await fetch(relativeEndpoint, { headers })
  if (!response.ok) {
    try {
      const { getApiUrl } = await import('@/lib/config')
      const baseUrl = (await getApiUrl()) || ''
      if (baseUrl) {
        response = await fetch(`${baseUrl.replace(/\/$/, '')}${relativeEndpoint}`, { headers })
      }
    } catch {
      // ignore fallback errors
    }
  }
  if (!response.ok) {
    let message = 'Không tải được PDF văn bản nội bộ.'
    try {
      const payload = await response.json()
      if (payload?.detail) message = String(payload.detail)
    } catch {
      // ignore
    }
    toast.error(message)
    return
  }
  const blob = await response.blob()
  const objectUrl = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = `${fileBase || docId}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(objectUrl)
  toast.success('Đã tải PDF văn bản.')
}


function makeSafeFileName(value: string, fallback = 'bieu-mau'): string {
  const cleaned = (value || fallback)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
  return cleaned || fallback
}


function normalizeFormDisplayName(form: DisplayForm | Record<string, unknown> | null | undefined): string {
  const record = (form || {}) as Record<string, unknown>
  const preferredKeys = ['display_name', 'form_title', 'detected_form_name', 'title', 'name']
  const filenameKeys = ['file_name', 'source_package_title', 'source_package_path', 'local_path', 'download_url']

  const looksHumanReadable = (value: string): boolean => {
    const text = (value || '').trim()
    const lower = text.toLowerCase()
    if (!text) return false
    if (lower.includes('.signed')) return false
    if (/\.(pdf|docx?|xlsx?|xls|zip|rar|html?)$/i.test(text)) return false
    if (/\b[0-9a-f]{16,}\b/i.test(text)) return false
    return /\s/.test(text) || /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i.test(text)
  }

  const cleanupFilenameLike = (input: string): string => {
    let raw = (input || '').replace(/\\/g, '/').split(/[?#]/)[0].replace(/\/+$/g, '')
    if (raw.includes('/')) raw = raw.slice(raw.lastIndexOf('/') + 1)
    raw = raw
      .replace(/\.signed\d*.*$/i, '')
      .replace(/(\.(pdf|docx?|xlsx?|xls|zip|rar|html?))+$/i, '')
      .replace(/^[0-9a-f]{12,}[-_]+/i, '')
      .replace(/\b[0-9a-f]{16,}\b/gi, '')
      .replace(/\b\d{15,}\b/g, '')
      .replace(/\b20\d{6,}\b/g, '')
      .replace(/\.\.+/g, ' ')
      .replace(/[_\-.]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (!raw) return ''

    const replacements: Array<[RegExp, string]> = [
      [/\bq[đd]\b/gi, 'Quyết định'],
      [/\btthc\b/gi, 'thủ tục hành chính'],
      [/\bubnd\b/gi, 'UBND'],
      [/\bbo tu phap\b/gi, 'Bộ Tư pháp'],
      [/\bhai phong\b/gi, 'Hải Phòng'],
      [/\bto khai\b/gi, 'Tờ khai'],
      [/\bdang ky\b/gi, 'đăng ký'],
      [/\bkhai tu\b/gi, 'khai tử'],
      [/\bket hon\b/gi, 'kết hôn'],
      [/\bxac nhan\b/gi, 'xác nhận'],
      [/\btinh trang hon nhan\b/gi, 'tình trạng hôn nhân'],
      [/\bcu tru\b/gi, 'cư trú'],
      [/\bdat dai\b/gi, 'đất đai'],
      [/\bxay dung\b/gi, 'xây dựng'],
      [/\blinh vuc\b/gi, 'lĩnh vực'],
    ]
    for (const [pattern, replacement] of replacements) raw = raw.replace(pattern, replacement)

    return raw
      .split(' ')
      .map((word) => {
        if (!word) return word
        if (/^[A-ZĐ]+$/.test(word) || /\d/.test(word)) return word
        if (/^(và|về|của|cho|tại|theo|với|trong|ngoài)$/i.test(word)) return word.toLowerCase()
        return word.charAt(0).toUpperCase() + word.slice(1)
      })
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim()
  }

  const preferred = String(preferredKeys.map((key) => record[key]).find((value) => String(value || '').trim()) || '').trim()
  if (looksHumanReadable(preferred)) return preferred.replace(/\s+/g, ' ').trim()

  const filenameLike = preferred || String(filenameKeys.map((key) => record[key]).find((value) => String(value || '').trim()) || '').trim()
  return cleanupFilenameLike(filenameLike) || 'Biểu mẫu'
}

interface StrategyData {
  reasoning: string
  searches: Array<{ term: string; instructions: string }>
}

interface StreamingResponseProps {
  isStreaming: boolean
  strategy: StrategyData | null
  answers: string[]
  finalAnswer: string | null
  ragTrace?: RagTrace | null
  procedureDetail?: ProcedureDetail | null
  recommendedForms?: AskResponse['recommended_forms'] | null
  citations?: AskResponse['citations']
  answerSections?: AskResponse['answer_sections']
  groundingStatus?: string | null
  role?: 'citizen' | 'officer' | 'admin'
  showRagTrace?: boolean
  faqs?: Array<{ id: string; question: string; answer: string; steps?: string[]; form_ids?: string[]; domain?: string; ward_scope?: string | null }> | null
}

export function StreamingResponse({
  isStreaming,
  strategy,
  answers,
  finalAnswer,
  ragTrace,
  procedureDetail,
  recommendedForms,
  citations,
  answerSections,
  role = 'citizen',
  showRagTrace = false,
  faqs,
}: StreamingResponseProps) {
  const [strategyOpen, setStrategyOpen] = useState(false)
  const [answersOpen, setAnswersOpen] = useState(false)
  const [traceOpen, setTraceOpen] = useState(false)
  const { openModal } = useModalManager()
  const { t } = useTranslation()
  const router = useRouter()

  const handleDownloadForm = async (
    procId: string,
    formIndex: number,
    formName: string,
    downloadUrl?: string | null,
    fileType?: string | null,
    canDownload: boolean = true,
  ) => {
    if (!canDownload) {
      toast.error('Biểu mẫu này chưa có file chính thức đã duyệt')
      return
    }

    try {
      if (isExternalUrl(downloadUrl)) {
        window.open(downloadUrl as string, '_blank', 'noopener,noreferrer')
        toast.success('Đã mở nguồn tải biểu mẫu trong tab mới.')
        return
      }

      // Prefer relative /api path (Next rewrite proxy). Fallback to absolute API URL if needed.
      const relativeEndpoint = downloadUrl && downloadUrl.startsWith('/')
        ? (downloadUrl.startsWith('/api/') ? downloadUrl : `/api${downloadUrl}`)
        : `/api/procedures/${procId}/forms/${formIndex}`

      const authStorage = localStorage.getItem('auth-storage')
      let token = ''
      if (authStorage) {
        try {
          const { state } = JSON.parse(authStorage)
          token = state?.token || ''
        } catch {
          token = ''
        }
      }

      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {}
      let response = await fetch(relativeEndpoint, { headers })

      if (!response.ok) {
        try {
          const { getApiUrl } = await import('@/lib/config')
          const baseUrl = (await getApiUrl()) || ''
          if (baseUrl) {
            const absoluteEndpoint = `${baseUrl.replace(/\/$/, '')}${relativeEndpoint}`
            response = await fetch(absoluteEndpoint, { headers })
          }
        } catch (fallbackErr) {
          console.error('Absolute form download fallback failed:', fallbackErr)
        }
      }

      if (!response.ok) {
        let message = 'Biểu mẫu chưa có file hợp lệ, cần admin cập nhật'
        if (response.status === 401 || response.status === 403) {
          message = 'Hết phiên đăng nhập hoặc không có quyền tải biểu mẫu này.'
        } else {
          try {
            const payload = await response.json()
            if (payload?.detail) message = String(payload.detail)
            else if (payload?.message) message = String(payload.message)
          } catch {
            // ignore non-JSON error bodies
          }
        }
        toast.error(message)
        return
      }

      const blob = await response.blob()
      if (!blob.size) {
        toast.error('Biểu mẫu này chưa có file chính thức đã duyệt')
        return
      }

      const extension = (fileType || 'docx').replace(/^\./, '') || 'docx'
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${makeSafeFileName(formName, `${procId}_bieu_mau_${formIndex + 1}`)}.${extension}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
      toast.success('Đã tải xuống biểu mẫu thành công.')
    } catch (err) {
      console.error('Form download failed:', err)
      const errMsg = err instanceof Error ? err.message : String(err)
      toast.error(`Không thể tải biểu mẫu: ${errMsg}`)
    }
  }

  const handleReferenceClick = (type: string, id: string) => {
    if (type === 'source') {
      const cleanId = id.replace(/^legal:/, '').trim()
      // Find matching article number if any
      const matchingCit = citations?.find((c: LegalCitation) => {
        const cId = String(c.doc_id || c.chunk_id || '').replace(/^legal:/, '').trim()
        return cId === cleanId
      })
      const artNum = matchingCit?.article_number || ''
      const queryStr = artNum ? `?article=${encodeURIComponent(artNum)}` : ''
      router.push(`/legal-documents/${encodeURIComponent(cleanId)}${queryStr}`)
      return
    }

    const modalType = type === 'source_insight' ? 'insight' : type as 'source' | 'note' | 'insight'

    try {
      openModal(modalType, id)
      // Note: The modal system uses URL parameters and doesn't // no rethrowors for missing items.
      // The modal component itself will handle displaying "not found" states.
      // This try-catch is here for future enhancements or unexpected errors.
    } catch {
      const typeLabel = type === 'source_insight' ? 'insight' : type
      toast.error(t('common.itemNotFound').replace('{type}', typeLabel))
    }
  }

  const hasAnswerSections = Boolean(answerSections?.length)

  if (!strategy && !answers.length && !finalAnswer && !hasAnswerSections && !isStreaming) {
    return null
  }

  return (
    <div
      className="space-y-5 mt-6"
      role="region"
      aria-label={t('common.accessibility.askResponse')}
      aria-live="polite"
      aria-busy={isStreaming}
    >
      {/* Strategy Section - Collapsible */}
      {strategy && (
        <Collapsible open={strategyOpen} onOpenChange={setStrategyOpen}>
          <Card>
            <CardHeader>
              <CollapsibleTrigger className="flex items-center justify-between w-full hover:opacity-80">
                <CardTitle className="text-base flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-primary" />
                  {t('common.strategy')}
                </CardTitle>
                <ChevronDown className={`h-4 w-4 transition-transform ${strategyOpen ? 'rotate-180' : ''}`} />
              </CollapsibleTrigger>
            </CardHeader>
            <CollapsibleContent>
              <CardContent className="space-y-3 pt-0">
                <div>
                  <p className="text-sm text-muted-foreground mb-2">{t('common.reasoning')}:</p>
                  <p className="text-sm">{strategy.reasoning}</p>
                </div>
                {strategy.searches.length > 0 && (
                  <div>
                    <p className="text-sm text-muted-foreground mb-2">{t('common.searchTerms')}:</p>
                    <div className="space-y-2">
                      {strategy.searches.map((search, i) => (
                        <div key={i} className="flex items-start gap-2">
                          <Badge variant="outline" className="mt-0.5">{i + 1}</Badge>
                          <div className="flex-1">
                            <p className="text-sm font-medium">{search.term}</p>
                            <p className="text-xs text-muted-foreground">{search.instructions}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      {/* Individual Answers Section - Collapsible */}
      {answers.length > 0 && (
        <Collapsible open={answersOpen} onOpenChange={setAnswersOpen}>
          <Card>
            <CardHeader>
              <CollapsibleTrigger className="flex items-center justify-between w-full hover:opacity-80">
                <CardTitle className="text-base flex items-center gap-2">
                  <Lightbulb className="h-4 w-4 text-primary" />
                  {t('common.individualAnswers').replace('{count}', answers.length.toString())}
                </CardTitle>
                <ChevronDown className={`h-4 w-4 transition-transform ${answersOpen ? 'rotate-180' : ''}`} />
              </CollapsibleTrigger>
            </CardHeader>
            <CollapsibleContent>
              <CardContent className="space-y-2 pt-0">
                {answers.map((answer, i) => (
                  <div key={i} className="p-3 rounded-md bg-muted">
                    <p className="text-sm">{answer}</p>
                  </div>
                ))}
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      {/* Structured sections replace the aggregate prose when available. */}
      {hasAnswerSections && answerSections?.map((section) => {
        const content = section.status === 'sufficiently_evidenced'
          ? section.answer
          : section.status === 'partially_evidenced'
            ? section.guidance
            : section.limitation
        const statusLabel = section.status === 'sufficiently_evidenced'
          ? 'Đã xác minh'
          : section.status === 'partially_evidenced'
            ? 'Hướng dẫn tham khảo'
            : 'Chưa đủ căn cứ'
        return (
          <Card key={section.issue_id} className="border-primary" data-testid="answer-section">
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <CheckCircle className="h-4 w-4 text-primary" />
                {section.title}
              </CardTitle>
              <Badge variant="outline" data-testid={`answer-section-status-${section.status}`}>{statusLabel}</Badge>
            </CardHeader>
            <CardContent className="space-y-3">
              {content && <FinalAnswerContent content={sanitizeDisplayAnswer(content)} onReferenceClick={handleReferenceClick} />}
              {section.limitation && section.status !== 'insufficiently_evidenced' && (
                <p className="text-sm text-muted-foreground">{section.limitation}</p>
              )}
              {section.clarifying_question && (
                <p className="text-sm text-muted-foreground">{section.clarifying_question}</p>
              )}
              {section.status === 'sufficiently_evidenced' && section.citations.length > 0 && (
                <div className="space-y-1" data-testid="answer-section-citations">
                  <p className="text-xs font-medium text-muted-foreground">Căn cứ đã xác minh</p>
                  <div className="flex flex-wrap gap-2">
                    {section.citations.map((citation, index) => {
                      const sourceName = [citation.document_title, citation.law_number]
                        .filter(Boolean)
                        .join(' ')
                      const label = [sourceName, citation.article_number ? `Điều ${citation.article_number}` : '']
                        .filter(Boolean)
                        .join(', ')
                      return citation.source_url ? (
                        <a
                          key={`${citation.law_number || citation.document_title || 'source'}-${index}`}
                          href={citation.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-primary hover:underline"
                        >
                          {label || 'Xem nguồn chính thức'}
                        </a>
                      ) : (
                        <span key={`${citation.law_number || citation.document_title || 'source'}-${index}`} className="text-xs text-muted-foreground">
                          {label || 'Nguồn đã xác minh'}
                        </span>
                      )
                    })}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )
      })}

      {/* Legacy flat Answer Section - Always Open */}
      {!hasAnswerSections && finalAnswer && (
        <Card className="border-primary">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-primary" />
              {t('common.finalAnswer')}
            </CardTitle>
          </CardHeader>
          <CardContent className="max-h-[80vh] md:max-h-[85vh] overflow-y-auto pr-4">
            <FinalAnswerContent
              content={sanitizeDisplayAnswer(finalAnswer, citations)}
              onReferenceClick={handleReferenceClick}
              citations={citations}
            />
          </CardContent>
        </Card>
      )}

      {/* Widget Thủ tục Hành chính Cấu trúc */}
      
      {/* Căn cứ pháp lý - compact citation links (internal only) */}
      {citations && citations.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-muted-foreground flex items-center gap-1.5">
            <BookOpen className="h-4 w-4" />
            Căn cứ pháp lý
          </h4>
          <div className="flex flex-wrap gap-2">
            {citations.slice(0, 3).map((cit, idx) => {
              const lawNum = cit.law_number || ""
              const artNum = cit.article_number || ""
              const docTitle = cit.document_title || ""
              
              // Tạo nhãn tự nhiên dạng: Số hiệu — Điều X
              const labelParts = []
              if (lawNum) {
                labelParts.push(lawNum)
              } else if (docTitle) {
                labelParts.push(docTitle)
              }
              if (artNum) {
                labelParts.push("Điều " + artNum)
              }
              const displayLabel = labelParts.join(" — ") || cit.label || "Văn bản pháp luật"
              const docId = cit.doc_id ? String(cit.doc_id).replace(/^legal:/, "").trim() : ""
              
              const viewerUrl = String(cit.internal_url || '').trim() || (docId
                ? `/legal-documents/${encodeURIComponent(docId)}${artNum ? `?article=${encodeURIComponent(artNum)}` : ''}`
                : '')
              const sourceUrl = String(cit.source_url || '').trim()
              const hasSourceUrl = /^https?:\/\//i.test(sourceUrl)
                

              return (
                <div key={idx} className="flex items-center gap-2 flex-wrap rounded-md border border-border/50 bg-card/50 px-3 py-1.5 text-xs transition-colors hover:bg-card">
                  <span className="font-medium text-foreground">{displayLabel}</span>
                  <span className="text-muted-foreground/30">|</span>
                  {docId ? (
                    <div className="flex items-center gap-2.5">
                      <Link
                        href={viewerUrl}
                        className="text-primary font-semibold hover:underline flex items-center gap-0.5"
                      >
                        Xem văn bản
                      </Link>
                      {hasSourceUrl && (
                        <a
                          href={sourceUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-muted-foreground hover:text-foreground hover:underline"
                        >
                          Nguồn gốc
                        </a>
                      )}
                      <button
                        type="button"
                        onClick={() => void downloadLegalPdf(docId, artNum || undefined, lawNum || docTitle || docId)}
                        className="text-muted-foreground hover:text-foreground hover:underline flex items-center gap-0.5"
                      >
                        Tải PDF
                      </button>
                    </div>
                  ) : (
                    <span className="text-muted-foreground italic">Không có link xem</span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

{faqs && faqs.length > 0 && (
        <FAQAccordion faqs={faqs} />
      )}

{((procedureDetail?.forms && procedureDetail.forms.length > 0) || (recommendedForms && recommendedForms.length > 0)) && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2 text-primary">
              <Sparkles className="h-4 w-4" />
              Biểu mẫu liên quan
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Chỉ tải được biểu mẫu đã có file chính thức đã duyệt. Biểu mẫu thiếu hoặc lỗi file sẽ hiển thị trạng thái &quot;Chưa có file hợp lệ&quot;.
            </p>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2 sm:grid-cols-2">
              {(recommendedForms && recommendedForms.length > 0 ? recommendedForms : (procedureDetail?.forms || [])).map((form: DisplayForm, idx: number) => {
                const isReference = form.official_level === 'reference' || form.review_status === 'candidate_pending_review'
                const hasOfficialFile = form.has_official_file === true
                const canDownload = Boolean(form.download_url) && hasOfficialFile && !isReference
                const displayName = normalizeFormDisplayName(form)
                const procedureId = 'procedure_id' in form ? form.procedure_id : undefined
                return (
                  <div key={idx} className="flex flex-col gap-3 rounded-lg border bg-background p-3 hover:border-primary/50 transition-all">
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-medium text-sm leading-relaxed break-words">{displayName}</p>
                      <Badge variant={canDownload ? 'default' : 'secondary'} className="shrink-0 text-[10px] px-1.5 py-0.5">
                        {canDownload ? 'Chính thức' : 'Chưa có file hợp lệ'}
                      </Badge>
                    </div>
                    <div className="flex justify-end border-t border-muted/50 pt-2">
                      {canDownload ? (
                        <button
                          type="button"
                          onClick={() => handleDownloadForm(procedureId || procedureDetail?.id || 'unknown', idx, displayName, form.download_url, form.file_type, true)}
                          className="text-xs font-semibold text-primary hover:underline flex items-center gap-1"
                        >
                          Tải biểu mẫu{form.file_type ? ` (.${form.file_type.replace(/^\./, '')})` : ''}
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled
                          className="text-xs font-medium text-muted-foreground cursor-not-allowed"
                          title="Biểu mẫu chưa có file hợp lệ, cần admin cập nhật"
                        >
                          Chưa có file hợp lệ</button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {ragTrace && role === 'admin' && showRagTrace && (
        <Collapsible open={traceOpen} onOpenChange={setTraceOpen}>
          <Card>
            <CardHeader>
              <CollapsibleTrigger className="flex items-center justify-between w-full hover:opacity-80">
                <CardTitle className="text-base flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-primary" />
                  Quy trình RAG
                </CardTitle>
                <ChevronDown className={`h-4 w-4 transition-transform ${traceOpen ? 'rotate-180' : ''}`} />
              </CollapsibleTrigger>
            </CardHeader>
            <CollapsibleContent>
              <CardContent className="space-y-4 pt-0 text-sm">
                <TraceBlock title="Câu hỏi đầu vào">
                  <p>{ragTrace.input_question || 'Không có dữ liệu'}</p>
                </TraceBlock>
                <TraceBlock title="Lĩnh vực được chọn/nhận diện">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">Chọn: {ragTrace.selected_domain || 'Tự nhận diện'}</Badge>
                    <Badge variant="outline">
                      Nhận diện: {ragTrace.detected_domain?.name || 'Chưa rõ'}
                    </Badge>
                  </div>
                </TraceBlock>
                <TraceBlock title="Chunk được truy xuất">
                  <div className="space-y-2 max-h-80 overflow-y-auto pr-2">
                    {(ragTrace.retrieved_chunks || []).map((chunk) => (
                      <div key={chunk.chunk_id} className="rounded-md border p-3">
                        <div className="mb-1 flex flex-wrap gap-2">
                          <Badge variant="outline">chunk {chunk.chunk_id}</Badge>
                          {chunk.score !== undefined && <Badge variant="secondary">score {chunk.score}</Badge>}
                          {chunk.domain && <Badge>{chunk.domain}</Badge>}
                        </div>
                        <p className="font-medium">
                          {chunk.law_number || 'Không rõ số hiệu'} - Điều {chunk.article_number}: {chunk.article_title}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">{chunk.document_title}</p>
                        <p className="mt-2 whitespace-pre-wrap text-xs">{chunk.content_preview}</p>
                      </div>
                    ))}
                  </div>
                </TraceBlock>
                <TraceBlock title="Văn bản/chunk bị lọc">
                  {Array.isArray(ragTrace.filtered_candidates) && ragTrace.filtered_candidates.length > 0 ? (
                    <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
                      {JSON.stringify(ragTrace.filtered_candidates || [], null, 2)}
                    </pre>
                  ) : (
                    <p className="text-muted-foreground">Không có nguồn nào bị loại ở bước lọc cuối.</p>
                  )}
                </TraceBlock>
                <TraceBlock title="Nguồn đưa vào LLM">
                  {Array.isArray(ragTrace.llm_sources) && ragTrace.llm_sources.length > 0 ? (
                    <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
                      {JSON.stringify(ragTrace.llm_sources || [], null, 2)}
                    </pre>
                  ) : (
                    <p className="text-muted-foreground">Chưa có nguồn hợp lệ được đưa vào LLM.</p>
                  )}
                </TraceBlock>
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      {/* Loading Indicator */}
      {isStreaming && !finalAnswer && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoadingSpinner size="sm" />
          <span>{t('searchPage.processingQuestion')}</span>
        </div>
      )}
    </div>
  )
}

function TraceBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="font-medium">{title}</h4>
      {children}
    </section>
  )
}

// Helper component to render final answer with clickable references
function FinalAnswerContent({
  content,
  onReferenceClick,
  citations,
}: {
  content: string
  onReferenceClick: (type: string, id: string) => void
  citations?: AskResponse['citations']
}) {
  const router = useRouter()
  // Convert references to markdown links
  const markdownWithLinks = convertReferencesToMarkdownLinks(content)

  // Custom link component to intercept external VBPL links and route them internally
  const LinkComponent = ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href?: string; children?: React.ReactNode }) => {
    // 1. Check if this is an internal reference link (#ref-source-id)
    if (href?.startsWith('#ref-')) {
      const parts = href.substring(5).split('-')
      const type = parts[0]
      const id = parts.slice(1).join('-')
      return (
        <button
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            onReferenceClick(type, id)
          }}
          className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 underline decoration-dashed decoration-blue-500/60 hover:decoration-blue-700 underline-offset-4 cursor-pointer inline font-semibold transition-colors duration-150"
          type="button"
        >
          {children}
        </button>
      )
    }

    // 2. Intercept external VBPL / Thư viện pháp luật links if matching citations exist
    const hrefLower = (href || '').toLowerCase()
    if (hrefLower && (hrefLower.includes('vbpl.vn') || hrefLower.includes('thuvienphapluat.vn'))) {
      const match = citations?.find(c => {
        const url = (c.source_url || '').toLowerCase()
        return url && (url.includes(hrefLower) || hrefLower.includes(url))
      })
      if (match && match.doc_id) {
        const cleanId = String(match.doc_id).replace(/^legal:/, '').trim()
        const article = match.article_number || ''
        const queryStr = article ? `?article=${encodeURIComponent(article)}` : ''
        return (
          <button
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              router.push(`/legal-documents/${encodeURIComponent(cleanId)}${queryStr}`)
            }}
            className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 underline decoration-dashed decoration-blue-500/60 hover:decoration-blue-700 underline-offset-4 cursor-pointer inline font-semibold transition-colors duration-150"
            type="button"
          >
            {children}
          </button>
        )
      }
    }

    // Fallback standard external link
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props} className="text-primary hover:underline">
        {children}
      </a>
    )
  }

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert break-words prose-a:break-all prose-p:leading-relaxed prose-headings:mt-4 prose-headings:mb-2">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: LinkComponent,
          table: ({ children }) => (
            <div className="my-4 overflow-x-auto">
              <table className="min-w-full border-collapse border border-border">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-muted">{children}</thead>,
          tbody: ({ children }) => <tbody>{children}</tbody>,
          tr: ({ children }) => <tr className="border-b border-border">{children}</tr>,
          th: ({ children }) => <th className="border border-border px-3 py-2 text-left font-semibold">{children}</th>,
          td: ({ children }) => <td className="border border-border px-3 py-2">{children}</td>,
        }}
      >
        {markdownWithLinks}
      </ReactMarkdown>
    </div>
  )
}
