'use client'

import { Shield, User } from 'lucide-react'
import dynamic from 'next/dynamic'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import type { AskMessage } from '@/lib/types/search'
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
}: AskMessageHistoryProps) {
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
              <div className="max-w-[90%] md:max-w-[80%] rounded-xl border bg-card px-3 py-3 rounded-tl-none space-y-3">
                <LoadingSpinner size="sm" />
                <span className="text-xs text-muted-foreground">
                  {pendingStageLabel || 'Đang xử lý câu hỏi…'}
                </span>
              </div>
            </div>
          )
        }

        return (
          <div key={message.id} className="flex gap-3 justify-start" data-testid="ask-assistant-message">
            <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-1">
              <Shield className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="max-w-[90%] md:max-w-[80%] rounded-xl border bg-card px-3 py-3 rounded-tl-none space-y-3">
              {message.status === 'error' ? (
                <p className="text-sm text-destructive">{message.content}</p>
              ) : message.status === 'cancelled' ? (
                <p className="text-sm text-muted-foreground">{message.content}</p>
              ) : (
                <StreamingResponse
                  isStreaming={false}
                  strategy={null}
                  answers={[]}
                  finalAnswer={message.content}
                  ragTrace={message.rag_trace || null}
                  procedureDetail={message.procedure_detail || null}
                  recommendedForms={message.recommended_forms || undefined}
                  citations={message.citations || undefined}
                  answerSections={message.answer_sections || undefined}
                  groundingStatus={message.grounding_status || null}
                  role={role}
                  showRagTrace={showRagTrace}
                  faqs={message.faqs || undefined}
                />
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
