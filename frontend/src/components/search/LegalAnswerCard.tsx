'use client'

import { AlertCircle, Download, FileText, ListChecks, Sparkles } from 'lucide-react'
import { useRouter } from 'next/navigation'
import type { AskResponse, LegalAnswerPresentationSections } from '@/lib/types/search'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { remarkLegalBreaks } from '@/lib/utils/remark-legal-breaks'
import { enrichMarkdownWithArticleLinks } from '@/lib/utils/legal-article-parser'

type Role = 'citizen' | 'officer' | 'admin'

export interface LegalAnswerCardProps {
  sections: LegalAnswerPresentationSections
  answerStatus?: AskResponse['answer_status'] | null
  answerRoute?: AskResponse['answer_route'] | null
  evidenceCount?: number | null
  verificationLabel?: string | null
  historicalLabel?: string | null
  role?: Role
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function procedureLines(procedure?: Record<string, unknown> | null): string[] {
  if (!procedure) return []
  const candidates = [
    ['Thủ tục', procedure.name || procedure.procedure_name || procedure.id],
    ['Nơi thực hiện', procedure.department || procedure.agency],
    ['Thời hạn', procedure.duration || procedure.processing_time],
    ['Lệ phí', procedure.fee],
  ]
  return candidates
    .map(([label, value]) => text(value) ? `${label}: ${text(value)}` : '')
    .filter(Boolean)
}

function formLabel(form: Record<string, unknown>): string {
  return text(form.name || form.form_title || form.form_code || form.form_id) || 'Biểu mẫu chính thức'
}

function formUrl(form: Record<string, unknown>): string {
  const value = text(form.download_url || form.source_url)
  return /^(?:https?:\/\/|\/)/i.test(value) ? value : ''
}

function sourcePage(value: Record<string, unknown>): string | number | null {
  const page = value.page_number ?? value.source_page ?? value.pageNumber ?? value.page
  return typeof page === 'string' || typeof page === 'number' ? page : null
}

function FormattedMarkdown({
  content,
  citations,
  className = '',
}: {
  content: string
  citations?: LegalAnswerPresentationSections['legal_bases']
  className?: string
}) {
  const router = useRouter()
  if (!content) return null

  const enriched = enrichMarkdownWithArticleLinks(
    content,
    citations as Parameters<typeof enrichMarkdownWithArticleLinks>[1]
  )

  return (
    <div className={`prose max-w-none text-foreground dark:prose-invert break-words text-pretty ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkLegalBreaks]}
        components={{
          a: ({ href, children, ...props }) => {
            const isInternal = href?.startsWith('/legal-documents/')
            if (isInternal && href) {
              return (
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    router.push(href)
                  }}
                  className="inline font-semibold text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 underline underline-offset-4 decoration-blue-500/50 hover:decoration-blue-700 transition-colors cursor-pointer text-left"
                >
                  {children}
                </button>
              )
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                {...props}
                className="inline font-semibold text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 underline underline-offset-4 decoration-blue-500/50 hover:decoration-blue-700 transition-colors"
              >
                {children}
              </a>
            )
          },
          h3: ({ children }) => (
            <h3 className="text-lg font-bold text-foreground mt-6 mb-3 flex items-center gap-2 border-b pb-1.5 border-border/40">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="text-base font-semibold text-foreground mt-4 mb-2">
              {children}
            </h4>
          ),
          p: ({ children }) => <p className="leading-relaxed my-3 text-[16px] text-foreground/90">{children}</p>,
          ul: ({ children }) => <ul className="my-3 space-y-2 list-disc pl-5 text-[15.5px] leading-relaxed">{children}</ul>,
          ol: ({ children }) => <ol className="my-3 space-y-2 list-decimal pl-5 text-[15.5px] leading-relaxed">{children}</ol>,
          li: ({ children }) => <li className="my-1 text-foreground/90">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
        }}
      >
        {enriched}
      </ReactMarkdown>
    </div>
  )
}

function TextList({ items, citations }: { items: string[]; citations?: LegalAnswerPresentationSections['legal_bases'] }) {
  const uniqueItems = Array.from(new Set(items.map(item => text(item)).filter(Boolean)))
  if (!uniqueItems.length) return null
  return (
    <ul className="space-y-2.5 text-[15.5px] leading-relaxed">
      {uniqueItems.map((item, index) => (
        <li key={`${item.slice(0, 32)}-${index}`} className="flex gap-2.5 items-start">
          <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 rounded-full bg-primary flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <FormattedMarkdown content={item} citations={citations} />
          </div>
        </li>
      ))}
    </ul>
  )
}

export function LegalAnswerCard({
  sections,
}: LegalAnswerCardProps) {
  const procedure = procedureLines(sections.procedure)
  const unverifiedExplanations = sections.unverified_explanations || []

  const combinedCaveats = Array.from(
    new Set([...(sections.caveats || []), ...(sections.clarifying_questions || [])].map(item => text(item)).filter(Boolean))
  )

  const uniqueForms = (sections.recommended_forms || []).filter(
    (form, index, self) =>
      index === self.findIndex(f => (f.form_id && f.form_id === form.form_id) || formLabel(f) === formLabel(form))
  )

  return (
    <div className="space-y-6 py-2" data-testid="legal-answer-card">
      <div className="space-y-6">
        {sections.short_answer && (
          <section className="space-y-2" data-testid="legal-answer-short">
            <span className="sr-only">Trả lời ngắn</span>
            <div className="prose-content">
              <FormattedMarkdown
                content={sections.short_answer}
                citations={sections.legal_bases}
                className="text-[16px] leading-8 text-foreground"
              />
            </div>
          </section>
        )}
        {sections.actions.length > 0 && (
          <section className="space-y-3 pt-2">
            <h3 className="flex items-center gap-2 font-bold text-base text-foreground">
              <ListChecks className="h-4 w-4 text-primary" />
              Việc cần làm
            </h3>
            <TextList items={sections.actions} citations={sections.legal_bases} />
          </section>
        )}
        {sections.dossier.length > 0 && (
          <section className="space-y-3 pt-2">
            <h3 className="flex items-center gap-2 font-bold text-base text-foreground">
              <FileText className="h-4 w-4 text-primary" />
              Hồ sơ, giấy tờ
            </h3>
            <TextList items={sections.dossier} citations={sections.legal_bases} />
          </section>
        )}
        {procedure.length > 0 && (
          <section className="space-y-3 pt-2">
            <h3 className="font-bold text-base text-foreground">Thủ tục thực hiện</h3>
            <TextList items={procedure} citations={sections.legal_bases} />
          </section>
        )}
        {uniqueForms.length > 0 && (
          <section className="space-y-3 pt-4 border-t border-border/40" data-testid="legal-answer-forms">
            <h3 className="flex items-center gap-2 font-bold text-base text-foreground">
              <Sparkles className="h-4 w-4 text-primary" />
              Biểu mẫu chính thức
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {uniqueForms.map((form, index) => {
                let url = formUrl(form) || ''
                const pageNum = sourcePage(form)
                if (url && url.toLowerCase().endsWith('.pdf') && pageNum) {
                  url += `#page=${pageNum}`
                }
                const label = formLabel(form)
                return (
                  <div
                    key={`${text(form.form_id)}-${index}`}
                    className="flex items-center justify-between p-3.5 rounded-lg border border-border/80 bg-muted/20 hover:bg-muted/40 transition-colors"
                  >
                    <div className="flex items-start gap-2.5 min-w-0">
                      <FileText className="h-4 w-4 text-primary mt-0.5 flex-shrink-0" />
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm font-medium text-foreground hover:text-primary hover:underline truncate"
                        >
                          {label}
                        </a>
                      ) : (
                        <span className="text-sm font-medium text-foreground truncate">{label}</span>
                      )}
                    </div>
                    {url ? (
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs font-semibold text-primary bg-primary/10 hover:bg-primary/20 px-2.5 py-1 rounded-md transition-colors flex-shrink-0 ml-2"
                      >
                        <Download className="h-3 w-3" />
                        Tải về / Điền
                      </a>
                    ) : (
                      <span className="text-xs text-muted-foreground">Chưa có file</span>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        )}
        {unverifiedExplanations.length > 0 && (
          <section
            className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-100"
            aria-label="Diễn giải chưa xác minh"
            data-testid="legal-answer-unverified"
          >
            <h3 className="flex items-center gap-2 font-bold text-base">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
              Diễn giải tham khảo — chưa xác minh
            </h3>
            <ul className="list-disc space-y-1 pl-5 text-sm leading-6">
              {unverifiedExplanations.map((item, index) => {
                const value = text(item.content || item.claim || item.original)
                return value ? <li key={`${value}-${index}`}>{value}</li> : null
              })}
            </ul>
          </section>
        )}
        {combinedCaveats.length > 0 && (
          <section className="space-y-3 bg-amber-500/5 p-4 rounded-lg border-l-4 border-amber-500" data-testid="legal-answer-caveats">
            <h3 className="flex items-center gap-2 font-bold text-base text-amber-800 dark:text-amber-200">
              <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
              Lưu ý và phần cần làm rõ
            </h3>
            <TextList items={combinedCaveats} citations={sections.legal_bases} />
          </section>
        )}
      </div>
    </div>
  )
}
