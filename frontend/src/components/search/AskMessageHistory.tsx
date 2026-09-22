'use client'

import { memo, useEffect, useRef, useState, type RefObject } from 'react'
import { ArrowDown, ExternalLink, Files, RotateCcw, Shield, User } from 'lucide-react'
import dynamic from 'next/dynamic'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import type { AskMessage, LegalCitation } from '@/lib/types/search'
import type { UserRole } from '@/lib/stores/auth-store'
import { type ChatViewerIdentity } from '@/lib/utils/chat-address'
import { buildOfficialArticleUrl, formatLegalCitationLabel } from '@/lib/utils/legal-article-parser'
import { SentMessageAttachment } from '@/components/search/SentMessageAttachment'

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
  pendingAnswer?: string | null
  pendingStageLabel?: string | null
  pendingCitations?: LegalCitation[] | null
  scrollViewportRef?: RefObject<HTMLDivElement | null>
  conversationKey?: string | null
  viewerIdentity?: ChatViewerIdentity | null
  onSuggestionClick?: (question: string) => void
  onRetryClick?: (question: string) => void
}


const BLOCKED_VALIDITY_ACTIONS = new Set(['block', 'historical_only', 'block_provisions'])


function publicCitations(citations: LegalCitation[] | undefined): LegalCitation[] | undefined {
  if (!citations) return undefined
  return citations.filter((citation) => !BLOCKED_VALIDITY_ACTIONS.has(
    String(citation.validity_sync?.serving_action || ''),
  ))
}


/**
 * Single renderer for Ask history. A completed assistant turn is rendered only
 * through StreamingResponse; it must never be mirrored by a raw text bubble.
 */
export const AskMessageHistory = memo(function AskMessageHistory({
  messages,
  role,
  showRagTrace,
  pendingStageLabel,
  pendingAnswer,
  pendingCitations,
  scrollViewportRef,
  conversationKey,
  onSuggestionClick,
  onRetryClick,
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

  const latestAssistantMessageId = [...messages]
    .reverse()
    .find((message) => message.role === 'assistant')?.id

  return (
    <div className="mb-4 space-y-6" data-testid="ask-message-history">
      {messages.map((message, messageIndex) => {
        if (message.role === 'user') {
          return (
            <div key={message.id} className="flex justify-end gap-3" data-testid="ask-user-message">
              <div className="flex max-w-[88%] flex-col items-end gap-2 md:max-w-[72%]">
                <SentMessageAttachment attachments={message.attachments} />
                <div className="rounded-3xl rounded-br-md bg-primary px-5 py-3 text-[18px] leading-8 text-primary-foreground md:text-[19px]">
                  <p className="whitespace-pre-wrap break-words">{message.content}</p>
                </div>
              </div>
              <div className="h-7 w-7 rounded-full bg-muted flex items-center justify-center shrink-0 mt-1">
                <User className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
            </div>
          )
        }

        if (message.status === 'pending') {
          const sourcesBeingChecked = publicCitations(pendingCitations || undefined)?.slice(0, 3) || []
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
                <div className="mt-4 min-h-20">
                  {pendingAnswer ? <p className="whitespace-pre-wrap break-words text-base leading-7">{pendingAnswer.replace(/\[E\d+\]/g, '')}</p> : sourcesBeingChecked.length > 0 ? (
                    <div className="rounded-lg border bg-muted/20 px-3 py-2 text-xs">
                      <p className="font-medium text-foreground">
                        Nguồn đang được đối chiếu ({sourcesBeingChecked.length})
                      </p>
                      <div className="mt-1.5 grid gap-1 text-muted-foreground">
                        {sourcesBeingChecked.map((citation, index) => {
                          const label = formatLegalCitationLabel(citation) || `Nguồn ${index + 1}`
                          const sourceUrl = citation.source_url
                            ? buildOfficialArticleUrl(citation.source_url, String(citation.article_number || '').trim())
                            : ''
                          return sourceUrl ? (
                            <a
                              key={`${label}:${sourceUrl}`}
                              href={sourceUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="truncate text-primary hover:underline"
                            >
                              {label}
                            </a>
                          ) : (
                            <span key={`${label}:${index}`} className="truncate">{label}</span>
                          )
                        })}
                      </div>
                      <p className="mt-1.5 text-[11px] leading-4 text-muted-foreground">
                        Chỉ được gắn làm căn cứ khi câu trả lời cuối cùng hoàn tất.
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-2.5" aria-hidden="true">
                      <div className="h-2.5 w-[88%] animate-pulse rounded-full bg-muted" />
                      <div className="h-2.5 w-[72%] animate-pulse rounded-full bg-muted" />
                      <div className="h-2.5 w-[80%] animate-pulse rounded-full bg-muted" />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        }

        const citations = publicCitations(message.citations) || []
        const fallbackUsed = message.timing_summary?.fallback_used === true
          || message.generation_provenance?.fallback_used === true
        const outcome = message.outcome
        const reasonCode = message.reason_code || ''
        const deliveryNotice = (() => {
          if (message.persistence_degraded) {
            return 'Câu trả lời đã hiển thị nhưng chưa lưu được vào lịch sử.'
          }
          if (message.scope === 'article_outline' && outcome === 'answered') {
            return 'Tổng quan toàn Điều được dựng từ toàn bộ cấu trúc nguồn; hãy chọn “Xem nguyên văn” để đọc đầy đủ.'
          }
          if (reasonCode === 'OUTPUT_SCHEMA_INVALID') {
            return 'Mô hình trả về định dạng không hợp lệ; chưa thể tạo kết luận đã kiểm chứng.'
          }
          if (reasonCode === 'CLAIM_VALIDATION_FAILED') {
            return 'Các mệnh đề chưa vượt qua kiểm định căn cứ; chưa thể tạo kết luận đã kiểm chứng.'
          }
          if (reasonCode === 'PROVIDER_TIMEOUT') {
            return Number(message.evidence_count || 0) > 0
              ? 'Mô hình không hoàn tất trong thời gian quy định; hệ thống giữ lại phần nguồn đã tìm được.'
              : 'Mô hình không hoàn tất trong thời gian quy định; lượt này không sử dụng nguồn pháp luật.'
          }
          if (reasonCode === 'PROVIDER_STREAM_INTERRUPTED' || reasonCode === 'PROVIDER_OUTPUT_TRUNCATED') {
            return 'Câu trả lời bị ngắt trước khi hoàn tất; phần đã nhận vẫn được giữ lại.'
          }
          if (reasonCode === 'PROVIDER_OUTPUT_EXHAUSTED') {
            return 'Mô hình đã dùng hết ngân sách đầu ra trước khi viết câu trả lời. Hãy thử mức trả lời khác.'
          }
          if (reasonCode === 'PROVIDER_REFUSAL') {
            return 'Mô hình đã từ chối trả lời yêu cầu này.'
          }
          if (reasonCode === 'PROVIDER_EMPTY_RESPONSE') {
            return 'Mô hình được chọn không sinh nội dung trả lời.'
          }
          if (reasonCode === 'PROVIDER_UNAVAILABLE') {
            return 'Đã giữ lại nguồn; nhà cung cấp hoặc mô hình được chọn hiện không khả dụng.'
          }
          if (message.scope === 'bounded_window' && outcome === 'partial') {
            return 'Đang hiển thị trích đoạn liên quan trong nguồn, chưa gồm toàn bộ Điều.'
          }
          if (outcome === 'source_only') {
            const hasDisplayableSource = Number(message.evidence_count || 0) > 0
              && citations.length > 0
            return hasDisplayableSource
              ? 'Đã tìm thấy nguồn nhưng chưa thể tạo kết luận pháp lý an toàn.'
              : 'Chưa tìm thấy nguồn pháp luật phù hợp trong lượt tra cứu này.'
          }
          if (outcome === 'clarification_required') {
            return 'Chưa đủ dữ kiện để kết luận. Vui lòng bổ sung thông tin được yêu cầu.'
          }
          if (outcome === 'failed') {
            if (reasonCode === 'PROVIDER_TIMEOUT') return 'Mô hình không hoàn tất trong thời gian quy định; chưa tạo kết luận mới.'
            if (reasonCode === 'PROVIDER_EMPTY_RESPONSE') return 'Mô hình được chọn không sinh nội dung; nguồn đã tìm được vẫn được giữ lại.'
            if (reasonCode === 'PROVIDER_UNAVAILABLE') return 'Nhà cung cấp hoặc mô hình được chọn hiện không khả dụng.'
            if (reasonCode === 'RETRIEVAL_UNAVAILABLE') return 'Không truy xuất được nguồn pháp luật trong lượt này.'
            return 'Chưa thể tạo câu trả lời đã kiểm chứng trong lượt này.'
          }
          if (reasonCode === 'SOFT_GROUNDING_WARNING') {
            return 'Một số mã trích dẫn chưa liên kết được với nguồn; có thể mở văn bản để đối chiếu.'
          }
          if (outcome === 'partial') {
            return 'Một số nội dung chưa được giải đáp đầy đủ; nội dung kết luận chưa được kiểm chứng độc lập.'
          }
          if (reasonCode === 'VERIFIED_SOURCE_CONDENSED' || fallbackUsed) {
            return 'Đang hiển thị bản rút gọn từ nguồn; hãy mở nguồn để đối chiếu đầy đủ.'
          }
          return null
        })()
        const primarySourceUrl = citations?.find((citation) => Boolean(citation.source_url))?.source_url
        const isArticleOverview = message.scope === 'article_outline' && outcome === 'answered'
        const isFullArticleSourceView = message.scope === 'full_article' && outcome === 'source_only'
        const retryQuestion = [...messages.slice(0, messageIndex)]
          .reverse()
          .find((candidate) => candidate.role === 'user')?.content
        return (
            <div key={message.id} className="flex justify-start gap-3" data-testid="ask-assistant-message">
            <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/15 bg-primary/10">
              <Shield className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="min-w-0 w-full max-w-[calc(100%-3rem)] space-y-3 md:max-w-[92%]">
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
                  salutation={null}
                  role={role}
                  showRagTrace={showRagTrace}
                  faqs={message.faqs || undefined}
                />
              )}
              {message.status === 'complete' && deliveryNotice && (
                <div
                  className={`flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
                    outcome === 'failed' || outcome === 'source_only' || reasonCode === 'OUTPUT_SCHEMA_INVALID' || reasonCode === 'CLAIM_VALIDATION_FAILED'
                      ? 'border-destructive/30 bg-destructive/5 text-destructive'
                      : 'border-amber-300/70 bg-amber-50/70 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100'
                  }`}
                  role={outcome === 'failed' || outcome === 'source_only' || reasonCode === 'OUTPUT_SCHEMA_INVALID' || reasonCode === 'CLAIM_VALIDATION_FAILED' ? 'alert' : undefined}
                  data-testid="answer-delivery-notice"
                >
                  <span>{deliveryNotice}</span>
                  {(isArticleOverview || isFullArticleSourceView) && primarySourceUrl && (
                    <a
                      href={primarySourceUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 rounded-md border border-current/30 px-2 py-1 font-medium hover:bg-background/60"
                      data-testid="view-full-article"
                    >
                      Xem nguyên văn <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  )}
                  {isArticleOverview && retryQuestion && onSuggestionClick && (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-md border border-current/30 px-2 py-1 font-medium hover:bg-background/60"
                      onClick={() => onSuggestionClick(`${retryQuestion} Hãy phân tích chi tiết từng nhóm nội dung của Điều này.`)}
                      data-testid="analyze-article-detail"
                    >
                      Phân tích chi tiết
                    </button>
                  )}
                  {retryQuestion && message.retryable === true && (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-md border border-current/30 px-2 py-1 font-medium hover:bg-background/60"
                      onClick={() => onRetryClick?.(retryQuestion)}
                    >
                      <RotateCcw className="h-3 w-3" aria-hidden="true" />
                      Thử lại
                    </button>
                  )}
                </div>
              )}
              {message.id === latestAssistantMessageId && message.status === 'complete' && message.suggested_questions && message.suggested_questions.length > 0 && (
                <div className="flex flex-wrap gap-2" aria-label="Câu hỏi gợi ý">
                  {message.suggested_questions.slice(0, 4).map((suggestion) => (
                    <button
                      key={suggestion.id || `${suggestion.issue_id}:${suggestion.facet}:${suggestion.text}`}
                      type="button"
                      className="rounded-full border bg-background px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:border-primary hover:bg-primary/5"
                      onClick={() => onSuggestionClick?.(suggestion.text)}
                    >
                      {suggestion.text}
                    </button>
                  ))}
                </div>
              )}
              {message.id === latestAssistantMessageId && message.status === 'complete' && message.related_documents && message.related_documents.length > 0 && (
                <details open={role !== 'citizen'} className="rounded-lg border bg-muted/20 px-3 py-2" aria-label="Văn bản liên quan" data-testid="related-documents">
                  <summary className="mb-1.5 cursor-pointer text-sm font-medium text-foreground">
                    <Files className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                    Văn bản liên quan
                  </summary>
                  <div className="grid gap-1">
                    {message.related_documents.slice(0, 3).map((document) => {
                      const articleRef = (document.article_refs || []).find((item) => /^Điều\s+[1-9]\d*[a-z]?$/i.test(item.trim())) || ''
                      const articleNumber = articleRef.replace(/^Điều\s+/i, '').trim()
                      const label = formatLegalCitationLabel({
                        document_title: document.title,
                        law_number: document.law_number || undefined,
                        article_number: articleNumber || undefined,
                      })
                      const sourceUrl = document.source_url
                        ? buildOfficialArticleUrl(document.source_url, articleNumber)
                        : ''
                      const viewerUrl = document.document_id && !/^https?:/i.test(document.document_id)
                        ? `/legal-documents/${encodeURIComponent(document.document_id)}?${new URLSearchParams({ ...(articleNumber ? { article: articleNumber } : {}), ...(document.law_number ? { law_number: document.law_number } : {}) }).toString()}`
                        : ''
                      return (
                        <div key={document.document_id} className="flex items-center justify-between gap-2 rounded-md bg-background px-2.5 py-1.5 text-xs">
                          <p className="min-w-0 truncate font-medium text-foreground">{label}</p>
                          {(viewerUrl || sourceUrl) && (
                            <a
                              href={viewerUrl || sourceUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex shrink-0 items-center gap-1 font-medium text-primary hover:underline"
                            >
                              {viewerUrl ? 'Xem căn cứ' : 'Văn bản gốc'} <ExternalLink className="h-3 w-3" aria-hidden="true" />
                            </a>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </details>
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
})
