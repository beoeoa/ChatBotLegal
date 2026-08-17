'use client'

import { useEffect, useRef, useState, type RefObject } from 'react'
import { ArrowDown, ExternalLink, Shield, TriangleAlert, User } from 'lucide-react'
import dynamic from 'next/dynamic'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { legalValidityLabelsVi } from '@/lib/locales'
import type { AskMessage, LegalCitation, PublicValiditySync } from '@/lib/types/search'
import type { UserRole } from '@/lib/stores/auth-store'

const StreamingResponse = dynamic(
  () => import('@/components/search/StreamingResponse').then((module) => module.StreamingResponse),
  {
    ssr: false,
    loading: () => <p className="text-sm text-muted-foreground">Đang hiển thị câu trả lời…</p>,
  },
)

interface AskMessageHistoryProps {
  messages: AskMessage[]
  role: UserRole
  showRagTrace: boolean
  pendingStageLabel?: string | null
  scrollViewportRef?: RefObject<HTMLDivElement | null>
  conversationKey?: string | null
}


const BLOCKED_VALIDITY_ACTIONS = new Set(['block', 'historical_only', 'block_provisions'])


function publicCitations(citations: LegalCitation[] | undefined): LegalCitation[] | undefined {
  if (!citations) return undefined
  return citations.filter((citation) => !BLOCKED_VALIDITY_ACTIONS.has(
    String(citation.validity_sync?.serving_action || ''),
  ))
}


function validityStatusLabel(validity: PublicValiditySync): string {
  const labels = legalValidityLabelsVi.status as Record<string, string>
  return labels[String(validity.status || 'unknown')] || legalValidityLabelsVi.status.unknown
}


function verifiedDate(value?: string | null): string | null {
  if (!value) return null
  const isoDate = value.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (isoDate) return `${isoDate[3]}/${isoDate[2]}/${isoDate[1]}`
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short' }).format(parsed)
}


function CitationValidityNotice({ citations }: { citations?: LegalCitation[] }) {
  const verified = (citations || []).filter((citation) => citation.validity_sync)
  if (verified.length === 0) return null
  return (
    <div className="space-y-2" data-testid="citation-validity-notice">
      {verified.map((citation, index) => {
        const validity = citation.validity_sync as PublicValiditySync
        const warningLabels = legalValidityLabelsVi.warning as Record<string, string>
        const warning = validity.warning_code ? warningLabels[validity.warning_code] : null
        const checkedAt = verifiedDate(validity.verified_at)
        const sourceUrl = String(validity.source_url || citation.source_url || '').trim()
        const safeSource = /^https:\/\//i.test(sourceUrl)
        return (
          <div
            key={`${citation.law_number || citation.doc_id || 'citation'}-${index}`}
            className="rounded-md border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-xs text-emerald-950"
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <strong>{validityStatusLabel(validity)}</strong>
              {checkedAt && <span>{legalValidityLabelsVi.verifiedAt} {checkedAt}</span>}
              {safeSource && (
                <a
                  href={sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline"
                >
                  {legalValidityLabelsVi.source}
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
            {(warning || validity.status === 'expired_partial' || validity.status === 'suspended_partial' || validity.status === 'amended') && (
              <p className="mt-1 flex items-start gap-1 text-amber-800">
                <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />
                {warning || 'Văn bản chỉ còn hiệu lực theo phạm vi; cần đọc đúng điều, khoản được trích dẫn.'}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Single renderer for Ask history. A completed assistant turn is rendered only
 * through StreamingResponse; it must never be mirrored by a raw text bubble.
 */
export function AskMessageHistory({
  messages,
  role,
  showRagTrace,
  pendingStageLabel,
  scrollViewportRef,
  conversationKey,
}: AskMessageHistoryProps) {
  const [showLatestAction, setShowLatestAction] = useState(false)
  const nearBottomRef = useRef(true)
  const previousLastMessageIdRef = useRef<string | null>(null)

  useEffect(() => {
    nearBottomRef.current = true
    previousLastMessageIdRef.current = null
  }, [conversationKey])

  useEffect(() => {
    const viewport = scrollViewportRef?.current
    if (!viewport) return

    const updatePosition = () => {
      const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
      const isNearBottom = distance <= 96
      nearBottomRef.current = isNearBottom
      if (isNearBottom) setShowLatestAction(false)
    }

    updatePosition()
    viewport.addEventListener('scroll', updatePosition, { passive: true })
    return () => viewport.removeEventListener('scroll', updatePosition)
  }, [scrollViewportRef])

  useEffect(() => {
    const viewport = scrollViewportRef?.current
    const lastMessageId = messages.at(-1)?.id || null
    const hasNewMessage = previousLastMessageIdRef.current !== lastMessageId
    previousLastMessageIdRef.current = lastMessageId
    if (!viewport || !hasNewMessage || messages.length === 0) return

    if (nearBottomRef.current) {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' })
      setShowLatestAction(false)
    } else {
      setShowLatestAction(true)
    }
  }, [messages, scrollViewportRef])

  const goToLatestMessage = () => {
    const viewport = scrollViewportRef?.current
    if (!viewport) return
    nearBottomRef.current = true
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' })
    setShowLatestAction(false)
  }

  if (messages.length === 0) return null

  return (
    <div className="space-y-4 mb-4" data-testid="ask-message-history">
      {messages.map((message) => {
        if (message.role === 'user') {
          return (
            <div key={message.id} className="flex gap-3 justify-end" data-testid="ask-user-message">
              <div className="max-w-[85%] md:max-w-[75%] rounded-xl px-4 py-3 text-sm leading-relaxed bg-primary text-primary-foreground rounded-br-none">
                <p className="whitespace-pre-wrap break-words">{message.content}</p>
              </div>
              <div className="h-7 w-7 rounded-full bg-muted flex items-center justify-center shrink-0 mt-1">
                <User className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
            </div>
          )
        }

        if (message.status === 'pending') {
          return (
            <div key={message.id} className="flex gap-3 justify-start" data-testid="ask-assistant-pending">
              <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-1">
                <Shield className="h-3.5 w-3.5 text-primary" />
              </div>
              <div
                className="w-full max-w-[92%] rounded-2xl rounded-tl-md border bg-card px-4 py-4 shadow-sm md:max-w-[84%]"
                role="status"
                aria-live="polite"
              >
                <div className="flex items-center gap-2">
                  <LoadingSpinner size="sm" />
                  <span className="text-sm font-medium">
                    {pendingStageLabel || 'Đang xử lý câu hỏi…'}
                  </span>
                </div>
                <div className="mt-4 space-y-2.5" aria-hidden="true">
                  <div className="h-2.5 w-[88%] animate-pulse rounded-full bg-muted" />
                  <div className="h-2.5 w-[72%] animate-pulse rounded-full bg-muted" />
                  <div className="h-2.5 w-[80%] animate-pulse rounded-full bg-muted" />
                </div>
              </div>
            </div>
          )
        }

        const citations = publicCitations(message.citations)
        return (
          <div key={message.id} className="flex gap-3 justify-start" data-testid="ask-assistant-message">
            <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-1">
              <Shield className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="min-w-0 w-full max-w-[94%] space-y-3 md:max-w-[88%]">
              {message.status === 'error' ? (
                <p className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">{message.content}</p>
              ) : message.status === 'cancelled' ? (
                <p className="rounded-xl border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">{message.content}</p>
              ) : (
                <StreamingResponse
                  isStreaming={false}
                  strategy={null}
                  answers={[]}
                  finalAnswer={message.content}
                  ragTrace={role === 'admin' && showRagTrace ? message.rag_trace || null : null}
                  procedureDetail={message.procedure_detail || null}
                  recommendedForms={message.recommended_forms || undefined}
                  formsUnavailable={message.forms_unavailable === true}
                  citations={citations}
                  answerSections={message.answer_sections || undefined}
                  groundingStatus={message.grounding_status || null}
                  answerCompleteness={message.answer_completeness || null}
                  answerMode={message.answer_mode || 'normal'}
                  answerStatus={message.answer_status || null}
                  fallbackTier={message.fallback_tier || null}
                  evidenceCount={message.evidence_count ?? null}
                  coverageWarning={message.coverage_warning || null}
                  blockedReason={message.blocked_reason || null}
                  presentationVersion={message.presentation_version || null}
                  presentationSections={message.sections || null}
                  answerRoute={message.answer_route || null}
                  verificationLabel={message.verification_label || null}
                  historicalLabel={message.historical_label || null}
                  role={role}
                  showRagTrace={showRagTrace}
                  faqs={message.faqs || undefined}
                />
              )}
              {message.status !== 'error' && message.status !== 'cancelled' && (
                <CitationValidityNotice citations={citations} />
              )}
            </div>
          </div>
        )
      })}
      {showLatestAction && (
        <div className="sticky bottom-3 z-10 flex justify-center">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-full border bg-background px-3 py-1.5 text-xs font-medium shadow-md hover:bg-muted"
            onClick={goToLatestMessage}
          >
            <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
            Xuống tin mới nhất
          </button>
        </div>
      )}
    </div>
  )
}
