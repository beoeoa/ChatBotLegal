'use client'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  AlertCircle,
  ArrowRight,
  ChevronDown,
  CircleHelp,
  Clock3,
  FileCheck2,
  FileText,
  Landmark,
  Lightbulb,
  ListChecks,
  Scale,
  ShieldCheck,
  Sparkles,
  WalletCards,
} from 'lucide-react'
import { useState } from 'react'
import { useLegalPreview } from '@/components/legal/LegalPreviewProvider'
import type React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { remarkLegalBreaks } from '@/lib/utils/remark-legal-breaks'
import { convertReferencesToMarkdownLinks } from '@/lib/utils/source-references'
import { buildOfficialArticleUrl, enrichMarkdownWithArticleLinks, formatLegalCitationLabel } from '@/lib/utils/legal-article-parser'
import { answerAlreadyHasSalutation } from '@/lib/utils/chat-address'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { useTranslation } from '@/lib/hooks/use-translation'
import { toast } from 'sonner'
import { formatApiError } from '@/lib/utils/error-handler'
import type { AskResponse, RagTrace } from '@/lib/types/search'
import { LegalAnswerCard } from './LegalAnswerCard'

type ProcedureDetail = NonNullable<AskResponse['procedure_detail']>
type ProcedureForm = ProcedureDetail['forms'][number]
type RecommendedForm = NonNullable<AskResponse['recommended_forms']>[number]
type DisplayForm = ProcedureForm | RecommendedForm
type LegalCitation = NonNullable<AskResponse['citations']>[number]
type LegalAnswerSection = NonNullable<AskResponse['answer_sections']>[number]

export function sanitizeDisplayAnswer(content: string, citations?: LegalCitation[], compact = false): string {
  // Resolve request-local evidence markers before removing any technical syntax.
  // This preserves the exact source attached by the backend citation validator.
  let text = content || ''

  const citationUrl = (citation: LegalCitation): string => {
    const candidate = String(citation.viewer_url || citation.internal_url || citation.source_url || '').trim()
    if (compact && citation.article_number !== '0' && !citation.viewer_url && !citation.internal_url && /^https?:\/\//i.test(candidate)) {
      return buildOfficialArticleUrl(candidate, String(citation.article_number || '').trim())
    }
    return /^(?:https?:\/\/|\/)/i.test(candidate) ? candidate : ''
  }

  const markdownCitation = (citation: LegalCitation): string => {
    const fullLabel = formatLegalCitationLabel(citation).replace(/[\[\]]/g, '')
    const compactUnits = [
      citation.point_number ? `điểm ${citation.point_number}` : '',
      citation.clause_number ? `khoản ${citation.clause_number}` : '',
      citation.article_number && citation.article_number !== '0' ? `Điều ${citation.article_number}` : '',
      citation.law_number || '',
    ].filter(Boolean).join(', ')
    const label = compact && compactUnits ? compactUnits : fullLabel
    const url = citationUrl(citation).replace(/[<>]/g, '')
    return url ? `[${label}](<${url}>)` : label
  }

  const citationsByEvidenceId = new Map<string, LegalCitation>()
  for (const citation of citations || []) {
    for (const evidenceId of citation.evidence_ids || []) {
      const normalized = String(evidenceId || '').trim().toUpperCase()
      if (/^E\d{1,3}$/.test(normalized)) citationsByEvidenceId.set(normalized, citation)
    }
  }
  // Protect code and explicit links before resolving evidence markers. The
  // legacy cleaner below must not rewrite the direct backend answer.
  text = text.split(/(```[\s\S]*?```|`[^`\n]+`|\[[^\]\n]+\]\((?:<[^>]+>|[^\s]+)\))/g).map((part, index) => index % 2 ? part : part.replace(/^#\s+(.+)$/gm, '## $1').replace(/(?:\[[ \t]*E\d{1,3}[ \t]*\])(?:[ \t]*[,;]?[ \t]*\[[ \t]*E\d{1,3}[ \t]*\])*/gi, (full, offset: number, source: string) => {
    const rendered = [...String(full).matchAll(/\[\s*(E\d{1,3})\s*\]/gi)].map((match) => {
      const evidenceId = String(match[1] || '').toUpperCase()
      const citation = citationsByEvidenceId.get(evidenceId)
      // Keep the defect local and readable. Internal request-scoped IDs such
      // as [E4] are never useful to citizens, and guessing another source
      // would be worse than stating that this one link could not be resolved.
      return citation ? markdownCitation(citation) : 'nguồn chưa liên kết'
    })
    const uniqueRendered = [...new Set(rendered)]
    const citationText = uniqueRendered.join(', ')
    const before = String(source || '').slice(Math.max(0, Number(offset) - 80), Number(offset))
    const alreadyConnected = /(?:theo(?:\s+quy định)?(?:\s+tại)?|căn cứ(?:\s+tại)?|quy định(?:\s+tại)?|được quy định(?:\s+tại)?|sửa đổi(?:,\s*bổ sung)?\s+bởi|tại|nguồn\s*:?|\()\s*$/i.test(before)
    return alreadyConnected ? citationText : `(${citationText})`
  })).join('')
  if (citations?.some(citation => citation.evidence_ids?.length) || /\[\s*E\d{1,3}\s*\]/i.test(content)) {
    return text.trim()
  }

  const protectedParts = text.split(/(```[\s\S]*?```|`[^`\n]+`|\[[^\]\n]+\]\((?:<[^>]+>|[^\s]+)\))/g)
  const cleanLegacyProse = (text: string): string => {
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
    if (art) return `Điều ${art} ${head}`.trim()
    return head
  }

  // Remove/replace [legal:...] first
  text = text.replace(/\[\s*legal\s*:\s*([^\]]+)\]/gi, (_m, body: string) => {
    const cleanBody = String(body || '').trim()
    const natural = toNaturalCitation(cleanBody)
    if (!natural || /^\d+$/.test(natural.trim())) return ''

    return natural
  })
  // bare legal:123
  text = text.replace(/\blegal\s*:\s*\d+\b/gi, '')
  // bracket law citations like [123/2015/NĐ-CP - Điều 29]
  text = text.replace(/\[([^\]]{3,120})\]/g, (full, body: string, offset: number, source: string) => {
    const raw = String(body || '')
    // Preserve markdown links generated above and links already present in
    // the model answer. Their label is followed immediately by "(".
    if (String(source || '').slice(Number(offset) + full.length).startsWith('(')) return full
    if (/^https?:\/\//i.test(raw) || /^(source|note|source_insight)\s*:/i.test(raw)) return full
    if (/\d{1,4}\/\d{4}\//.test(raw) || /(?:điều|dieu)\s*\d+/i.test(raw)) {
      const natural = toNaturalCitation(raw)
      if (natural) {
        return natural
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
      return natural
    }
  )
  text = text.replace(
    /(\d{1,4}\/\d{4}\/[A-Za-zÀ-ỹĐđ0-9\-]+)\s*\(\s*(?:Đi[eè]u|Điều)\s*(\d+[a-zA-Z]?)\s*\)/gi,
    (_m, law: string, art: string) => {
      const prefix = /\/qh/i.test(law) ? 'Luật ' : /\/n[đd]-cp/i.test(law) ? 'Nghị định ' : /\/tt-/i.test(law) ? 'Thông tư ' : ''
      const natural = `${prefix}${law}, Điều ${art}`.replace(/\s+/g, ' ').trim()
      return natural
    }
  )
  // Drop residual long source appendix headers if model still emits them
  text = text.replace(/^##\s*Căn cứ\s*\/\s*Nguồn[^\n]*\n?/gim, '')
  text = text.replace(/^###\s*Liên kết nguồn\s*\n?/gim, '')
  // The answer lives inside the chat page hierarchy, so a model-emitted H1
  // is visually demoted without rewriting or hiding any legal content.
  text = text.replace(/^#\s+(.+)$/gm, '## $1')

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
          return match
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
  }
  return protectedParts.map((part, index) => index % 2 ? part : cleanLegacyProse(part)).join('').trim()
}

function traceDomainLabel(value?: string | null): string {
  const normalized = String(value || '').trim().toLowerCase()
  const labels: Record<string, string> = {
    ho_tich: 'Hộ tịch',
    ho_tich_chung_thuc: 'Hộ tịch - Chứng thực',
    dat_dai: 'Đất đai',
    dat_dai_xay_dung: 'Đất đai - Xây dựng',
    an_sinh: 'An sinh xã hội',
    an_sinh_y_te_giao_duc: 'An sinh - Y tế - Giáo dục',
    cu_tru: 'Cư trú',
    khieu_nai: 'Khiếu nại - Tố cáo',
  }
  return labels[normalized] || 'Lĩnh vực chung'
}

function traceScoreLabel(value: number): string {
  const percent = value <= 1 ? value * 100 : value
  return `${Math.max(0, Math.min(100, Math.round(percent)))}%`
}

function isExternalUrl(value?: string | null): boolean {
  return Boolean(value && /^https?:\/\//i.test(value))
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
  formsUnavailable?: boolean
  citations?: AskResponse['citations']
  answerSections?: AskResponse['answer_sections']
  groundingStatus?: string | null
  answerCompleteness?: AskResponse['answer_completeness'] | null
  answerMode?: AskResponse['answer_mode'] | null
  answerStatus?: AskResponse['answer_status'] | null
  fallbackTier?: AskResponse['fallback_tier'] | null
  evidenceCount?: number | null
  coverageWarning?: string | null
  blockedReason?: string | null
  presentationVersion?: AskResponse['presentation_version'] | null
  presentationSections?: AskResponse['sections'] | null
  answerRoute?: AskResponse['answer_route'] | null
  verificationLabel?: string | null
  historicalLabel?: string | null
  salutation?: string | null
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
  formsUnavailable = false,
  citations,
  answerSections,
  answerStatus,
  evidenceCount,
  presentationVersion,
  presentationSections,
  answerRoute,
  verificationLabel,
  historicalLabel,
  salutation,
  role = 'citizen',
  showRagTrace = false,
}: StreamingResponseProps) {
  const [strategyOpen, setStrategyOpen] = useState(false)
  const [answersOpen, setAnswersOpen] = useState(false)
  const [traceOpen, setTraceOpen] = useState(false)
  const [downloadingForm, setDownloadingForm] = useState<string | null>(null)
  const { openModal } = useModalManager()
  const { t } = useTranslation()
  const openLegalPreview = useLegalPreview()

  const handleDownloadForm = async (
    procId: string,
    formIndex: number,
    formName: string,
    downloadUrl?: string | null,
    fileType?: string | null,
    canDownload: boolean = true,
    formId?: string,
  ) => {
    if (!canDownload) {
      toast.error('Biểu mẫu này chưa có file chính thức đã duyệt')
      return
    }

    setDownloadingForm(formId || formName)
    try {
      if (formId && procId !== 'unknown' && /^(pdf|docx?|xlsx?)$/i.test(fileType || '')) {
        downloadUrl = `/api/procedures/forms-catalog/canonical/${encodeURIComponent(formId)}/download?procedure_id=${encodeURIComponent(procId)}`
      }
      if (isExternalUrl(downloadUrl)) {
        window.open(downloadUrl as string, '_blank', 'noopener,noreferrer')
        toast.info('Đã mở trang nguồn. Chưa xác nhận tải được tệp biểu mẫu.')
        return
      }

      // Prefer relative /api path (Next rewrite proxy). Fallback to absolute API URL if needed.
      const relativeEndpoint = downloadUrl && downloadUrl.startsWith('/')
        ? (downloadUrl.startsWith('/api/') ? downloadUrl : `/api${downloadUrl}`)
        : `/api/procedures/${procId}/forms/${formIndex}`

      const { useAuthStore } = await import('@/lib/stores/auth-store')
      const token = useAuthStore.getState().token

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
        let message = 'Biểu mẫu chưa có tệp hợp lệ, cần quản trị viên cập nhật.'
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
      if (/text\/html|application\/(?:problem\+)?json/i.test(blob.type)) {
        throw new Error('Nguồn tải trả về trang thông báo thay vì tệp biểu mẫu. Vui lòng thử lại.')
      }
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
      // Give embedded browsers time to consume the blob before revocation.
      window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
      toast.success('Đã gửi tệp biểu mẫu tới trình tải xuống của trình duyệt.')
    } catch (err) {
      console.error('Form download failed:', err)
      toast.error(formatApiError(err, 'Không thể tải biểu mẫu. Vui lòng thử lại.'))
    } finally {
      setDownloadingForm(null)
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
      openLegalPreview(`/legal-documents/${encodeURIComponent(cleanId)}${queryStr}`)
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
  const hasPresentation = presentationVersion === 'legal-answer-v1' && Boolean(presentationSections)
  if (!strategy && !answers.length && !finalAnswer && !hasAnswerSections && !hasPresentation && !isStreaming) {
    return null
  }

  return (
    <div
      className="mt-1 space-y-5 text-base"
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
                    <FinalAnswerContent
                      content={sanitizeDisplayAnswer(answer, citations)}
                      onReferenceClick={handleReferenceClick}
                      citations={citations}
                    />
                  </div>
                ))}
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      {/* Structured sections replace the aggregate prose when available. */}
      {(hasPresentation || hasAnswerSections) && salutation && !answerAlreadyHasSalutation(finalAnswer) && (
        <p className="text-[18px] md:text-[19px] font-medium leading-8 text-foreground" data-testid="answer-salutation">
          {salutation}
        </p>
      )}
      {hasPresentation && presentationSections && (
        <LegalAnswerCard
          sections={presentationSections}
          answerStatus={answerStatus}
          answerRoute={answerRoute}
          evidenceCount={evidenceCount}
          verificationLabel={verificationLabel}
          historicalLabel={historicalLabel}
          role={role}
        />
      )}
      {!hasPresentation && hasAnswerSections && answerSections && (
        <StructuredLegalAnswer
          sections={answerSections}
          role={role}
          onReferenceClick={handleReferenceClick}
        />
      )}

      {/* Legacy flat Answer Section - Always Open */}
      {!hasPresentation && !hasAnswerSections && finalAnswer && (
        <div className="space-y-4 py-2">
          {salutation && !answerAlreadyHasSalutation(finalAnswer) && (
            <p className="text-[18px] md:text-[19px] font-medium leading-8 text-foreground" data-testid="answer-salutation">
              {salutation}
            </p>
          )}
          <FinalAnswerContent
            content={sanitizeDisplayAnswer(finalAnswer, citations, role === 'citizen')}
            onReferenceClick={handleReferenceClick}
            citations={citations}
          />
        </div>
      )}

      {/* Widget Thủ tục Hành chính Cấu trúc */}

      {!hasPresentation && ((procedureDetail?.forms && procedureDetail.forms.length > 0) || (recommendedForms && recommendedForms.length > 0)) && (
        <Card data-testid="official-forms" className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2 text-primary">
              <Sparkles className="h-4 w-4" />
              Biểu mẫu gửi kèm
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Mời anh/chị xem hoặc tải biểu mẫu chính thức phù hợp với thủ tục được nêu trong câu trả lời.
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
                const procedureName = 'procedure_name' in form ? form.procedure_name : undefined
                const formCode = 'form_code' in form ? form.form_code : undefined
                const sourceUrl = 'source_url' in form ? form.source_url : undefined
                const effectiveFrom = 'effective_from' in form ? form.effective_from : undefined
                const effectiveTo = 'effective_to' in form ? form.effective_to : undefined
                return (
                  <div key={idx} className="flex flex-col gap-3 rounded-lg border bg-background p-3 hover:border-primary/50 transition-all">
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-medium text-sm leading-relaxed break-words">{displayName}</p>
                      <Badge variant={canDownload ? 'default' : 'secondary'} className="shrink-0 text-[10px] px-1.5 py-0.5">
                        {canDownload ? 'Chính thức' : 'Chưa có file hợp lệ'}
                      </Badge>
                    </div>
                    <div className="space-y-1 text-xs text-muted-foreground">
                      {formCode && <p><span className="font-medium text-foreground">Mã mẫu:</span> {formCode}</p>}
                      {(procedureName || procedureId) && (
                        <p><span className="font-medium text-foreground">Thủ tục:</span> {procedureName || procedureId}</p>
                      )}
                      {form.file_type && (
                        <p><span className="font-medium text-foreground">Định dạng:</span> {form.file_type.replace(/^\./, '').toUpperCase()}</p>
                      )}
                      {effectiveFrom && (
                        <p>
                          <span className="font-medium text-foreground">Hiệu lực:</span> từ {effectiveFrom}
                          {effectiveTo ? ` đến ${effectiveTo}` : ''}
                        </p>
                      )}
                      {sourceUrl && isExternalUrl(sourceUrl) && (
                        <a
                          href={sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex font-medium text-primary hover:underline"
                        >
                          Nguồn biểu mẫu
                        </a>
                      )}
                    </div>
                    <div className="flex justify-end border-t border-muted/50 pt-2">
                      {canDownload ? (
                        <button
                          type="button"
                          disabled={downloadingForm !== null}
                          aria-busy={downloadingForm === (('form_id' in form && form.form_id) || displayName)}
                          onClick={() => handleDownloadForm(
                            procedureId || procedureDetail?.id || 'unknown',
                            idx,
                            displayName,
                            form.download_url,
                            form.file_type,
                            true,
                            ('form_id' in form ? form.form_id : undefined) || undefined,
                          )}
                          className="min-h-11 px-2 text-sm font-semibold text-primary hover:underline flex items-center gap-1 disabled:opacity-60"
                        >
                          {downloadingForm === (('form_id' in form && form.form_id) || displayName) ? 'Đang tải…' : `Tải biểu mẫu${form.file_type ? ` (.${form.file_type.replace(/^\./, '')})` : ''}`}
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled
                          className="text-xs font-medium text-muted-foreground cursor-not-allowed"
                          title="Biểu mẫu chưa có tệp hợp lệ, cần quản trị viên cập nhật"
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

      {formsUnavailable && !(recommendedForms?.length) && !(procedureDetail?.forms?.length) && (
        <Card data-testid="forms-unavailable" className="border-amber-300/60 bg-amber-50/70 dark:bg-amber-950/20">
          <CardContent className="pt-6 text-sm">
            <p className="font-medium">Chưa có biểu mẫu chính thức đã duyệt để tải.</p>
            <p className="mt-1 text-muted-foreground">
              Vui lòng kiểm tra cổng dịch vụ công hoặc cơ quan tiếp nhận được nêu trong nguồn chính thức; hệ thống không tạo mẫu thay thế.
            </p>
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
                  Quy trình tra cứu
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
                    <Badge variant="secondary">Đã chọn: {ragTrace.selected_domain ? traceDomainLabel(ragTrace.selected_domain) : 'Tự nhận diện'}</Badge>
                    <Badge variant="outline">
                      Nhận diện: {ragTrace.detected_domain?.name || 'Chưa rõ'}
                    </Badge>
                  </div>
                </TraceBlock>
                <TraceBlock title="Mức độ bao phủ nội dung được hỏi">
                  <p data-testid="admin-evidence-coverage">
                    {Object.keys(ragTrace.evidence_coverage || {}).length
                      ? `${Object.keys(ragTrace.evidence_coverage || {}).length} nhóm nội dung đã được kiểm tra.`
                      : 'Chưa có kết quả kiểm tra mức độ bao phủ.'}
                  </p>
                </TraceBlock>
                <TraceBlock title="Nguồn gốc và trạng thái biểu mẫu">
                  <p data-testid="form-provenance">
                    {ragTrace.form_provenance?.requested
                      ? `Đã yêu cầu biểu mẫu; ${ragTrace.form_provenance.accepted?.length || 0} biểu mẫu đạt yêu cầu, ${ragTrace.form_provenance.rejected?.length || 0} biểu mẫu bị loại.`
                      : 'Câu hỏi không yêu cầu biểu mẫu.'}
                  </p>
                </TraceBlock>
                <TraceBlock title="Các đoạn văn bản được tìm thấy">
                  <div className="space-y-2 max-h-80 overflow-y-auto pr-2">
                    {(ragTrace.retrieved_chunks || []).map((chunk) => (
                      <div key={chunk.chunk_id} className="rounded-md border p-3">
                        <div className="mb-1 flex flex-wrap gap-2">
                          <Badge variant="outline">Đoạn {chunk.chunk_id}</Badge>
                          {chunk.score !== undefined && <Badge variant="secondary">Mức phù hợp {traceScoreLabel(chunk.score)}</Badge>}
                          {chunk.domain && <Badge>{traceDomainLabel(chunk.domain)}</Badge>}
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
                <TraceBlock title="Nguồn bị loại sau khi kiểm tra">
                  {Array.isArray(ragTrace.filtered_candidates) && ragTrace.filtered_candidates.length > 0 ? (
                    <p>{ragTrace.filtered_candidates.length} nguồn đã bị loại vì không đáp ứng điều kiện tra cứu.</p>
                  ) : (
                    <p className="text-muted-foreground">Không có nguồn nào bị loại ở bước lọc cuối.</p>
                  )}
                </TraceBlock>
                <TraceBlock title="Nguồn dùng để tạo câu trả lời">
                  {Array.isArray(ragTrace.llm_sources) && ragTrace.llm_sources.length > 0 ? (
                    <p>{ragTrace.llm_sources.length} nguồn hợp lệ được dùng để tạo câu trả lời.</p>
                  ) : (
                    <p className="text-muted-foreground">Chưa có nguồn hợp lệ được dùng để tạo câu trả lời.</p>
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

const CITIZEN_SECTION_ORDER = [
  'rule',
  'condition',
  'documents',
  'authority',
  'procedure',
  'deadline',
  'fee',
  'dispute',
  'form',
  'unknown',
]

const OFFICER_SECTION_ORDER = [
  'rule',
  'condition',
  'documents',
  'authority',
  'dispute',
  'procedure',
  'deadline',
  'fee',
  'form',
  'unknown',
]

const SECTION_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  rule: Scale,
  condition: ListChecks,
  authority: Landmark,
  documents: FileCheck2,
  procedure: ArrowRight,
  deadline: Clock3,
  fee: WalletCards,
  dispute: ShieldCheck,
  form: FileText,
  unknown: CircleHelp,
}

function AnswerSectionBlock({
  section,
  role,
  isSummary = false,
  onReferenceClick,
}: {
  section: LegalAnswerSection
  role: 'citizen' | 'officer' | 'admin'
  isSummary?: boolean
  onReferenceClick: (type: string, id: string) => void
}) {
  const facet = section.facet || 'unknown'
  const Icon = SECTION_ICONS[facet] || CircleHelp
  const isImmediateAction = section.claim_types?.includes('next_action') === true
  const content = section.status === 'sufficiently_evidenced'
    ? section.answer
    : section.status === 'partially_evidenced'
      ? section.guidance
      : section.limitation
  const heading = isSummary
    ? role === 'officer'
      ? 'Kết luận nghiệp vụ'
      : 'Kết luận ngắn'
    : isImmediateAction
      ? 'Việc nên làm ngay'
      : section.title

  return (
    <section
      data-testid="answer-section"
      className={[
        'px-4 py-5 sm:px-6',
        isSummary ? 'bg-primary/[0.045]' : '',
        isImmediateAction && !isSummary ? 'bg-sky-50/60 dark:bg-sky-950/15' : '',
      ].filter(Boolean).join(' ')}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className={[
            'mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl',
            isSummary || isImmediateAction ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
          ].join(' ')}>
            <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h3 className={isSummary ? 'text-lg font-semibold tracking-tight' : 'text-base font-semibold'}>
              {heading}
            </h3>
            {isSummary && section.title !== heading && (
              <p className="mt-0.5 text-xs text-muted-foreground">{section.title}</p>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-4 pl-0 sm:pl-12">
        {content && (
          <FinalAnswerContent
            content={sanitizeDisplayAnswer(content, section.citations)}
            onReferenceClick={onReferenceClick}
            citations={section.citations}
          />
        )}
        {section.limitation && section.status !== 'insufficiently_evidenced' && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <div className="flex-1 min-w-0">
              <FinalAnswerContent
                content={sanitizeDisplayAnswer(section.limitation, section.citations)}
                onReferenceClick={onReferenceClick}
                citations={section.citations}
              />
            </div>
          </div>
        )}
        {section.clarifying_question && (
          <div className="rounded-xl border border-violet-200 bg-violet-50/60 p-3 text-sm dark:border-violet-900 dark:bg-violet-950/20">
            <p className="mb-1 font-semibold text-violet-900 dark:text-violet-100">Điểm cần xác minh</p>
            <FinalAnswerContent
              content={sanitizeDisplayAnswer(section.clarifying_question, section.citations)}
              onReferenceClick={onReferenceClick}
              citations={section.citations}
            />
          </div>
        )}
      </div>
    </section>
  )
}

function StructuredLegalAnswer({
  sections,
  role,
  onReferenceClick,
}: {
  sections: LegalAnswerSection[]
  role: 'citizen' | 'officer' | 'admin'
  onReferenceClick: (type: string, id: string) => void
}) {
  const order = role === 'officer' ? OFFICER_SECTION_ORDER : CITIZEN_SECTION_ORDER
  const ordered = sections
    .map((section, index) => ({ section, index }))
    .sort((left, right) => {
      const priorityRank = { critical: 0, high: 1, normal: 2 } as const
      const leftFacet = order.indexOf(left.section.facet || 'unknown')
      const rightFacet = order.indexOf(right.section.facet || 'unknown')
      const facetDelta = (leftFacet < 0 ? order.length : leftFacet) - (rightFacet < 0 ? order.length : rightFacet)
      if (facetDelta !== 0) return facetDelta
      const leftPriority = priorityRank[left.section.priority || 'normal']
      const rightPriority = priorityRank[right.section.priority || 'normal']
      return leftPriority - rightPriority || left.index - right.index
    })
    .map(({ section }) => section)
  const summary = ordered.find((section) => (
    section.status === 'sufficiently_evidenced'
    && (section.facet === 'rule' || section.priority === 'critical')
  )) || ordered.find((section) => section.status === 'sufficiently_evidenced') || ordered[0]
  const normalizedSummaryAnswer = (summary?.answer || '')
    .replace(/^\s*(?:[-*]\s*)?(?:kết luận|điều kiện(?: áp dụng)?|hướng dẫn nghiệp vụ)\s*:\s*/iu, '')
    .replace(/\s+/g, ' ')
    .trim()
  const details = ordered
    .filter((section) => section !== summary)
    .map((section) => {
      if (section.status !== 'sufficiently_evidenced' || !section.answer || !normalizedSummaryAnswer) {
        return section
      }
      const normalizedAnswer = section.answer
        .replace(/^\s*(?:[-*]\s*)?(?:kết luận|điều kiện(?: áp dụng)?|hướng dẫn nghiệp vụ)\s*:\s*/iu, '')
        .replace(/\s+/g, ' ')
        .trim()
      if (normalizedAnswer !== normalizedSummaryAnswer) return section
      return {
        ...section,
        answer: role === 'officer'
          ? 'Nội dung này sử dụng cùng căn cứ đã nêu trong kết luận nghiệp vụ ở trên.'
          : 'Nội dung này áp dụng cùng căn cứ đã nêu trong kết luận ngắn ở trên.',
      }
    })
  const roleLabel = role === 'officer'
    ? 'Hướng dẫn nghiệp vụ cán bộ'
    : role === 'admin'
      ? 'Kết quả kiểm chứng quản trị'
      : 'Hướng dẫn dành cho người dân'

  return (
    <section
      className="overflow-hidden rounded-2xl border border-border/80 bg-card shadow-sm"
      aria-label={roleLabel}
      data-testid="structured-legal-answer"
    >
      {summary && (
        <AnswerSectionBlock
          section={summary}
          role={role}
          isSummary
          onReferenceClick={onReferenceClick}
        />
      )}
      {details.length > 0 && (
        <div className="divide-y divide-border/70">
          {details.map((section) => (
            <AnswerSectionBlock
              key={section.issue_id}
              section={section}
              role={role}
              onReferenceClick={onReferenceClick}
            />
          ))}
        </div>
      )}
    </section>
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
  const openLegalPreview = useLegalPreview()
  // Enrich legal citations and convert references to markdown links
  const enriched = enrichMarkdownWithArticleLinks(content, citations)
  const markdownWithLinks = convertReferencesToMarkdownLinks(enriched)

  // Custom link component to intercept external VBPL links and route them internally
  const LinkComponent = ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href?: string; children?: React.ReactNode }) => {
    // 0. Internal legal document routing
    if (href?.startsWith('/legal-documents/')) {
      return (
        <button
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            openLegalPreview(href)
          }}
          className="inline font-medium text-primary hover:text-primary/80 underline decoration-primary/25 hover:decoration-primary/60 underline-offset-4 cursor-pointer transition-colors text-left"
          type="button"
        >
          {children}
        </button>
      )
    }

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
          className="inline font-medium text-primary hover:text-primary/80 underline decoration-primary/25 hover:decoration-primary/60 underline-offset-4 cursor-pointer transition-colors text-left"
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
              openLegalPreview(`/legal-documents/${encodeURIComponent(cleanId)}${queryStr}`)
            }}
            className="inline font-medium text-primary hover:text-primary/80 underline decoration-primary/25 hover:decoration-primary/60 underline-offset-4 cursor-pointer transition-colors text-left"
            type="button"
          >
            {children}
          </button>
        )
      }
    }

    // Fallback standard external link
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props} className="text-primary hover:underline font-semibold inline-flex items-center gap-1">
        {children}
      </a>
    )
  }

  return (
    <div className="prose max-w-none break-words text-[18px] md:text-[19px] leading-8 text-foreground prose-a:break-all prose-p:my-3 prose-p:leading-8 dark:prose-invert">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkLegalBreaks]}
        components={{
          a: LinkComponent,
          h3: ({ children }) => <h3 className="text-lg font-bold text-foreground mt-6 mb-3 border-b pb-1.5 border-border/40">{children}</h3>,
          h4: ({ children }) => <h4 className="text-base font-semibold text-foreground mt-4 mb-2">{children}</h4>,
          p: ({ children }) => <p className="my-3 text-[18px] md:text-[19px] leading-8 text-foreground/90">{children}</p>,
          ul: ({ children }) => <ul className="my-3 list-disc space-y-2 pl-5 text-[18px] md:text-[19px] leading-8">{children}</ul>,
          ol: ({ children }) => <ol className="my-3 list-decimal space-y-2 pl-5 text-[18px] md:text-[19px] leading-8">{children}</ol>,
          li: ({ children }) => <li className="my-1 text-foreground/90">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
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
