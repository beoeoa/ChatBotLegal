'use client'

import { AlertCircle, BookOpen, CheckCircle2, FileText, ListChecks, Scale } from 'lucide-react'
import type { AskResponse, LegalAnswerPresentationSections } from '@/lib/types/search'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

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

const STATUS_LABELS: Record<string, string> = {
  grounded: 'Đã xác minh',
  partial_grounded: 'Đã xác minh một phần',
  broad_grounded: 'Đã xác minh quy định khung',
  clarifying: 'Cần làm rõ',
  source_gap: 'Cần bổ sung nguồn',
  provider_error: 'Tạm thời chưa thể tổng hợp',
}

function citationLabel(citation: LegalAnswerPresentationSections['legal_bases'][number]): string {
  const identity = citation.law_number || citation.document_title || 'Nguồn pháp lý'
  const provision = [
    citation.article_number ? `Điều ${citation.article_number}` : '',
    citation.clause_number ? `Khoản ${citation.clause_number}` : '',
    citation.point_number ? `Điểm ${citation.point_number}` : '',
  ].filter(Boolean).join(', ')
  return [identity, provision].filter(Boolean).join(' · ')
}

function FormattedMarkdown({ content, className = '' }: { content: string; className?: string }) {
  if (!content) return null
  return (
    <div className={`prose prose-sm max-w-none dark:prose-invert break-words prose-p:leading-relaxed prose-p:my-1 prose-headings:my-2 ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props} className="font-medium text-primary hover:underline">
              {children}
            </a>
          ),
          p: ({ children }) => <p className="leading-relaxed">{children}</p>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function TextList({ items }: { items: string[] }) {
  const uniqueItems = Array.from(new Set(items.map(item => text(item)).filter(Boolean)))
  if (!uniqueItems.length) return null
  return (
    <ul className="space-y-2 text-sm leading-6">
      {uniqueItems.map((item, index) => (
        <li key={`${item.slice(0, 32)}-${index}`} className="flex gap-2 items-start">
          <span aria-hidden="true" className="mt-1 text-muted-foreground">•</span>
          <div className="flex-1 min-w-0">
            <FormattedMarkdown content={item} />
          </div>
        </li>
      ))}
    </ul>
  )
}

export function LegalAnswerCard({
  sections,
  answerStatus,
  answerRoute,
  evidenceCount,
  verificationLabel,
  historicalLabel,
  role = 'citizen',
}: LegalAnswerCardProps) {
  const procedure = procedureLines(sections.procedure)
  const verified = Boolean(verificationLabel && (evidenceCount || 0) > 0)
  const title = role === 'officer' ? 'Tra cứu nghiệp vụ' : role === 'admin' ? 'Kết quả kiểm chứng' : 'Trợ lý pháp luật'

  const combinedCaveats = Array.from(
    new Set([...(sections.caveats || []), ...(sections.clarifying_questions || [])].map(item => text(item)).filter(Boolean))
  )

  const uniqueForms = (sections.recommended_forms || []).filter(
    (form, index, self) =>
      index === self.findIndex(f => (f.form_id && f.form_id === form.form_id) || formLabel(f) === formLabel(form))
  )

  const uniqueLegalBases = (sections.legal_bases || []).filter(
    (citation, index, self) =>
      index === self.findIndex(c => citationLabel(c) === citationLabel(citation))
  )

  return (
    <Card className="overflow-hidden border-primary/25 shadow-sm" data-testid="legal-answer-card">
      <CardHeader className="border-b bg-primary/[0.035]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base"><Scale className="h-4 w-4 text-primary" />{title}</CardTitle>
          <div className="flex flex-wrap gap-2">
            {verified && <Badge data-testid="verified-label"><CheckCircle2 className="mr-1 h-3 w-3" />{verificationLabel}</Badge>}
            {answerRoute === 'historical' && historicalLabel && <Badge variant="outline" data-testid="historical-label">{historicalLabel}</Badge>}
            {answerStatus && <Badge variant="secondary">{STATUS_LABELS[answerStatus] || answerStatus}</Badge>}
          </div>
        </div>
      </CardHeader>
      <CardContent className="divide-y p-0">
        {sections.short_answer && (
          <section className="space-y-2 px-4 py-5 sm:px-6" data-testid="legal-answer-short">
            <h3 className="font-semibold text-foreground">Trả lời ngắn</h3>
            <FormattedMarkdown content={sections.short_answer} className="text-sm leading-6" />
          </section>
        )}
        {sections.actions.length > 0 && <section className="space-y-3 px-4 py-5 sm:px-6"><h3 className="flex items-center gap-2 font-semibold"><ListChecks className="h-4 w-4" />Việc cần làm</h3><TextList items={sections.actions} /></section>}
        {sections.dossier.length > 0 && <section className="space-y-3 px-4 py-5 sm:px-6"><h3 className="flex items-center gap-2 font-semibold"><FileText className="h-4 w-4" />Hồ sơ, giấy tờ</h3><TextList items={sections.dossier} /></section>}
        {procedure.length > 0 && <section className="space-y-3 px-4 py-5 sm:px-6"><h3 className="font-semibold">Thủ tục thực hiện</h3><TextList items={procedure} /></section>}
        {uniqueForms.length > 0 && (
          <section className="space-y-3 px-4 py-5 sm:px-6" data-testid="legal-answer-forms"><h3 className="font-semibold">Biểu mẫu chính thức</h3>
            <ul className="space-y-2 text-sm">{uniqueForms.map((form, index) => { const url = formUrl(form); const label = formLabel(form); return <li key={`${text(form.form_id)}-${index}`}>{url ? <a href={url} target="_blank" rel="noopener noreferrer" className="font-medium text-primary hover:underline">{label}</a> : <span>{label}</span>}</li> })}</ul>
          </section>
        )}
        {uniqueLegalBases.length > 0 && (
          <section className="space-y-3 px-4 py-5 sm:px-6" data-testid="legal-answer-bases"><h3 className="flex items-center gap-2 font-semibold"><BookOpen className="h-4 w-4" />Căn cứ pháp lý</h3>
            <ul className="space-y-2 text-sm">{uniqueLegalBases.map((citation, index) => <li key={`${citation.law_number || citation.document_title}-${index}`}>{citation.source_url ? <a href={citation.source_url} target="_blank" rel="noopener noreferrer" className="font-medium text-primary hover:underline">{citationLabel(citation)}</a> : citationLabel(citation)}</li>)}</ul>
          </section>
        )}
        {combinedCaveats.length > 0 && (
          <section className="space-y-3 bg-amber-50/40 px-4 py-5 sm:px-6 dark:bg-amber-950/10" data-testid="legal-answer-caveats"><h3 className="flex items-center gap-2 font-semibold"><AlertCircle className="h-4 w-4" />Lưu ý và phần cần làm rõ</h3><TextList items={combinedCaveats} /></section>
        )}
      </CardContent>
    </Card>
  )
}
