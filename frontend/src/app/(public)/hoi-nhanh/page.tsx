'use client'

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowUp, ExternalLink, FileText, Loader2, MessageSquarePlus, Scale, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { askQuickChat, type QuickChatResponse } from '@/lib/api/public-quick-chat'

type Turn = { id: string; question: string; response?: QuickChatResponse; error?: string }

const MAX_QUESTION_CHARS = 800

const suggestions = [
  'Đăng ký kết hôn cần giấy tờ gì?',
  'Xin xác nhận cư trú nộp ở đâu?',
  'Cấp giấy phép xây dựng mất bao lâu?',
]

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function errorMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { status?: number; data?: { answer?: string; detail?: string } } }).response
    if (response?.status === 429) return response.data?.answer || 'Lượt hỏi nhanh đã tạm hết. Bạn có thể đăng nhập để tiếp tục.'
    if (response?.data?.detail === 'public_quick_chat_origin_forbidden') return 'Yêu cầu không đến từ địa chỉ được phép của hệ thống.'
  }
  return 'Chưa thể gửi câu hỏi lúc này. Bạn thử lại sau ít phút nhé.'
}

export default function QuickChatPage() {
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const viewportRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    viewportRef.current?.scrollTo({ top: viewportRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns])

  const submitQuestion = async (text: string) => {
    const normalized = text.trim()
    if (loading) return
    if (normalized.length < 2) {
      setError('Bạn vui lòng nhập câu hỏi từ 2 ký tự trở lên.')
      return
    }
    if (normalized.length > MAX_QUESTION_CHARS) {
      setError(`Câu hỏi Hỏi nhanh tối đa ${MAX_QUESTION_CHARS} ký tự.`)
      return
    }
    const id = newId()
    setTurns((current) => [...current, { id, question: normalized }].slice(-5))
    setQuestion('')
    setError('')
    setLoading(true)
    try {
      const context = turns
        .slice(-5)
        .map((turn) => ({ question: turn.question }))
      const response = await askQuickChat({ question: normalized, idempotency_key: id, context })
      setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, response } : turn).slice(-5))
    } catch (requestError) {
      setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, error: errorMessage(requestError) } : turn).slice(-5))
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void submitQuestion(question)
  }

  const onInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      if (question.trim().length >= 2 && !loading) void submitQuestion(question)
    }
  }

  const startNewChat = () => {
    setTurns([])
    setQuestion('')
    setError('')
    inputRef.current?.focus()
  }

  return (
    <main className="flex h-dvh min-h-0 bg-background text-foreground">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border/70 bg-card/70 md:flex">
        <div className="flex h-[74px] items-center gap-3 border-b border-border/70 px-5">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground"><Scale className="h-5 w-5" aria-hidden="true" /></span>
          <div className="min-w-0"><p className="truncate font-semibold">Pháp luật Hải Phòng</p><p className="text-xs text-muted-foreground">Trợ lý pháp luật</p></div>
        </div>
        <div className="p-3">
          <Button type="button" variant="ghost" className="h-11 w-full justify-start gap-3" onClick={startNewChat} disabled={loading}>
            <MessageSquarePlus className="h-4 w-4" aria-hidden="true" /> Cuộc trò chuyện mới
          </Button>
        </div>
        <div className="mt-auto border-t border-border/70 p-5 text-sm text-muted-foreground">
          <p>Hỏi nhanh không lưu lịch sử.</p>
          <Link href="/login" className="mt-3 inline-block font-semibold text-primary underline-offset-4 hover:underline">Đăng nhập</Link>
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex h-[74px] shrink-0 items-center justify-between gap-3 border-b border-border/70 bg-card/90 px-4 sm:px-6">
          <div className="min-w-0"><h1 className="truncate font-semibold">Hỏi đáp pháp luật</h1><p className="text-xs text-muted-foreground">Hỏi nhanh về thủ tục hành chính</p></div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" size="icon" className="h-11 w-11 md:hidden" onClick={startNewChat} disabled={loading} aria-label="Cuộc trò chuyện mới"><MessageSquarePlus className="h-5 w-5" aria-hidden="true" /></Button>
            <Button asChild variant="outline" className="h-11 shrink-0"><Link href="/login">Đăng nhập</Link></Button>
          </div>
        </header>

        <div ref={viewportRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-6 sm:px-6" aria-label="Nội dung cuộc trò chuyện" tabIndex={0}>
          <div className={`mx-auto w-full max-w-[860px] ${turns.length === 0 ? 'flex min-h-full flex-col items-center justify-center pb-10 text-center' : 'space-y-8'}`}>
            {turns.length === 0 ? (
              <div className="w-full max-w-2xl">
                <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary"><ShieldCheck className="h-6 w-6" aria-hidden="true" /></span>
                <h2 className="mt-5 font-display text-2xl font-semibold sm:text-3xl">Xin chào, bạn cần hỏi thủ tục gì?</h2>
                <p className="mt-3 text-base leading-7 text-muted-foreground">Cứ hỏi bằng cách nói tự nhiên. Mình sẽ trả lời theo thông tin thủ tục đã được công bố.</p>
                <div className="mt-7 flex flex-wrap justify-center gap-2" aria-label="Câu hỏi gợi ý">
                  {suggestions.map((suggestion) => (
                    <button key={suggestion} type="button" onClick={() => { setQuestion(suggestion); inputRef.current?.focus() }} className="min-h-11 rounded-full border border-border bg-card px-4 py-2 text-sm transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{suggestion}</button>
                  ))}
                </div>
              </div>
            ) : turns.map((turn) => (
              <div key={turn.id} className="space-y-5">
                <div className="flex justify-end"><div className="max-w-[88%] rounded-[24px] rounded-br-md bg-primary px-5 py-3 text-base leading-7 text-primary-foreground sm:max-w-[75%]">{turn.question}</div></div>
                <div className="flex items-start gap-3" aria-live="polite">
                  <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/5 text-primary"><ShieldCheck className="h-4 w-4" aria-hidden="true" /></span>
                  <div className="min-w-0 flex-1 pt-1 text-base leading-8">
                    {turn.response ? (
                      <>
                        <div className="whitespace-pre-wrap break-words" data-testid="quick-chat-answer">{turn.response.answer}</div>
                        {turn.response.quick_facts?.length ? (
                          <div className="mt-5 grid gap-2 sm:grid-cols-3" aria-label="Thông tin chính của thủ tục">
                            {turn.response.quick_facts.map((fact) => (
                              <div key={fact.id} className="min-w-0 rounded-2xl border border-border bg-card p-3.5 leading-6 shadow-sm">
                                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{fact.label}</p>
                                <p className="mt-1 break-words text-sm font-medium">{fact.value}</p>
                              </div>
                            ))}
                          </div>
                        ) : null}
                        {turn.response.answer_sections?.length ? (
                          <div className="mt-4 space-y-2" aria-label="Chi tiết thủ tục">
                            {turn.response.answer_sections.map((section) => (
                              <details key={section.id} className="group rounded-2xl border border-border bg-card px-4 py-3 open:shadow-sm">
                                <summary className="cursor-pointer font-semibold marker:text-primary focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{section.title}</summary>
                                {section.body ? <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">{section.body}</p> : null}
                                {section.items?.length ? (
                                  <ol className={`mt-3 space-y-2 pl-5 text-sm leading-7 ${section.kind === 'steps' ? 'list-decimal' : 'list-disc'}`}>
                                    {section.items.map((item, index) => <li key={`${index}-${item}`} className="break-words pl-1">{item}</li>)}
                                  </ol>
                                ) : null}
                              </details>
                            ))}
                          </div>
                        ) : null}
                        {turn.response.data_quality_notice ? (
                          <p className="mt-3 text-sm leading-6 text-muted-foreground">{turn.response.data_quality_notice}</p>
                        ) : null}
                        {turn.response.citations?.some((citation) => citation.source_url) && (
                          <div className="mt-4 border-t border-border/70 pt-3 text-sm leading-6">
                            <p className="flex items-center gap-2 font-semibold"><FileText className="h-4 w-4" aria-hidden="true" /> Nguồn công bố</p>
                            {turn.response.citations.filter((citation) => citation.source_url).map((citation) => (
                              <a key={`${citation.document_title}-${citation.source_url}`} href={citation.source_url!} target="_blank" rel="noopener noreferrer" className="mt-1 flex items-start gap-2 break-all text-primary underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring"><ExternalLink className="mt-1 h-4 w-4 shrink-0" aria-hidden="true" />{citation.document_title || citation.label || citation.source_url}</a>
                            ))}
                          </div>
                        )}
                        {turn.response.action_chips?.length || turn.response.suggested_questions?.length ? (
                          <div className="mt-5 flex flex-wrap gap-2" aria-label="Câu hỏi tiếp theo">
                            {turn.response.action_chips?.map((chip) => (
                              <button key={chip.id} type="button" onClick={() => void submitQuestion(chip.question)} disabled={loading} className="min-h-11 cursor-pointer rounded-full border border-primary/30 bg-primary/5 px-4 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50">{chip.label}</button>
                            ))}
                            {turn.response.suggested_questions?.map((suggestion) => (
                              <button key={suggestion} type="button" onClick={() => void submitQuestion(suggestion)} disabled={loading} className="min-h-11 cursor-pointer rounded-full border border-border bg-card px-4 py-2 text-left text-sm transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50">{suggestion}</button>
                            ))}
                          </div>
                        ) : null}
                        {turn.response.source_gap?.length ? (
                          <p className="mt-3 text-sm leading-6 text-muted-foreground">{turn.response.source_gap.join(' ')}</p>
                        ) : null}
                      </>
                    ) : turn.error ? <p role="alert" className="text-destructive">{turn.error}</p> : <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Đang tra cứu…</p>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="shrink-0 border-t border-border/60 bg-card px-3 py-3 sm:px-6">
          <form onSubmit={submit} className="mx-auto w-full max-w-[860px]">
            <label htmlFor="quick-chat-question" className="mb-2 block text-sm font-medium">Câu hỏi của bạn</label>
            <div className="rounded-[26px] border border-border bg-card p-2 shadow-[0_2px_12px_rgba(47,35,22,0.06)] transition-shadow focus-within:border-primary/50 focus-within:shadow-[0_4px_20px_rgba(143,29,44,0.12)]">
              <Textarea ref={inputRef} id="quick-chat-question" data-testid="quick-chat-question" value={question} onChange={(event) => { setQuestion(event.target.value); setError('') }} onKeyDown={onInputKeyDown} placeholder="Nhập câu hỏi pháp lý..." rows={1} maxLength={MAX_QUESTION_CHARS} disabled={loading} className="min-h-11 max-h-[180px] resize-none overflow-y-auto border-0 bg-transparent px-3 py-2.5 text-base leading-7 shadow-none focus-visible:ring-0" aria-describedby="quick-chat-hint" />
              <div className="flex min-h-11 items-center justify-between gap-2 border-t border-border/50 px-2 pt-1">
                <span className="rounded-full bg-muted px-3 py-1 text-sm font-medium">Nhanh</span>
                <Button type="submit" size="icon" className="h-11 w-11 rounded-full" disabled={loading || question.trim().length < 2} aria-label="Gửi câu hỏi">{loading ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> : <ArrowUp className="h-5 w-5" aria-hidden="true" />}</Button>
              </div>
            </div>
            {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
            <p id="quick-chat-hint" className="mt-2 text-xs text-muted-foreground"><span className="sm:hidden">Tối đa {MAX_QUESTION_CHARS} ký tự · Enter để gửi</span><span className="hidden sm:inline">Tối đa {MAX_QUESTION_CHARS} ký tự · Enter để gửi · Shift + Enter để xuống dòng · Không lưu lịch sử hỏi nhanh</span></p>
          </form>
        </div>
      </section>
    </main>
  )
}
