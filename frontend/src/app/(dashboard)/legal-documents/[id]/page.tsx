"use client"

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import { ArrowLeft, BookOpen, Download, FileText } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { getApiUrl } from '@/lib/config'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  buildArticleAnchor,
  buildInternalLegalUrl,
  getChunkHighlightState,
  isTargetArticle,
} from '@/components/legal/legal-document-viewer-utils'

interface LegalChunk {
  chunk_id?: string | number
  chunk_index?: number
  content?: string
}

interface LegalArticle {
  article_id?: string | number
  article_number?: string
  article_title?: string
  chunks?: LegalChunk[]
}

interface LegalArticleIndex {
  article_number?: string
  article_title?: string
  has_content?: boolean
}

interface LegalDocumentDetail {
  doc_id?: string
  document_title?: string
  law_number?: string
  document_type?: string
  issuing_agency?: string
  scope?: string
  effective_status?: string
  effective_date?: string | null
  field_name?: string
  article_index?: LegalArticleIndex[]
  articles?: LegalArticle[]
  content?: string
  source_file_available?: boolean
}

function getAuthToken(): string {
  if (typeof window === 'undefined') return ''
  try {
    return String(JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.token || '')
  } catch {
    return ''
  }
}

export default function LegalDocumentViewerPage() {
  const params = useParams<{ id: string }>()
  const searchParams = useSearchParams()
  const docId = decodeURIComponent(String(params?.id || '')).replace(/^legal:/, '')
  const article = (searchParams.get('article') || '').trim()
  const clause = (searchParams.get('clause') || '').trim()
  const point = (searchParams.get('point') || '').trim()
  const [document, setDocument] = useState<LegalDocumentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tocOpen, setTocOpen] = useState(true)
  const [viewLatencyMs, setViewLatencyMs] = useState<number | null>(null)
  const [downloadLatencyMs, setDownloadLatencyMs] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!docId) {
        setError('Thiếu mã văn bản.')
        setLoading(false)
        return
      }
      try {
        setLoading(true)
        setError(null)
        const query = new URLSearchParams()
        if (article) query.set('article', article)
        const viewStartedAt = performance.now()
        const response = await apiClient.get<LegalDocumentDetail>(
          `/legal/docs/${encodeURIComponent(docId)}?${query.toString()}`,
        )
        if (!cancelled) {
          setDocument(response.data)
          setViewLatencyMs(Math.round(performance.now() - viewStartedAt))
        }
      } catch (caught: unknown) {
        if (!cancelled) {
          const detail = (caught as { response?: { data?: { detail?: string } }; message?: string })
          setError(detail.response?.data?.detail || detail.message || 'Không tải được văn bản.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [article, docId])

  useEffect(() => {
    if (!article || !document) return
    const target = window.document.getElementById(buildArticleAnchor(article))
    if (target) window.setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80)
  }, [article, document])

  const articleIndex = useMemo(
    () => document?.article_index || document?.articles || [],
    [document],
  )

  const hasLoadedArticle = Boolean((document?.articles || []).some((item) => (item.chunks || []).length > 0))

  const downloadPdf = async () => {
    const downloadStartedAt = performance.now()
    try {
      const apiUrl = await getApiUrl()
      const query = article ? `?article=${encodeURIComponent(article)}` : ''
      const response = await fetch(`${apiUrl}/api/legal/docs/${encodeURIComponent(docId)}/download.pdf${query}`, {
        headers: getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {},
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(payload?.detail || 'Không tải được PDF.')
      }
      setDownloadLatencyMs(Math.round(performance.now() - downloadStartedAt))
      const href = URL.createObjectURL(await response.blob())
      const anchor = window.document.createElement('a')
      anchor.href = href
      anchor.download = `${document?.law_number || docId}.pdf`
      anchor.click()
      URL.revokeObjectURL(href)
      toast.success(document?.source_file_available ? 'Đã tải file PDF gốc.' : 'Đã tải bản trích xuất từ kho hệ thống.')
    } catch (caught: unknown) {
      toast.error(caught instanceof Error ? caught.message : 'Không tải được PDF.')
    }
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden bg-background">
      <aside className={`hidden shrink-0 border-r bg-card md:flex md:flex-col ${tocOpen ? 'w-72' : 'w-0 overflow-hidden'}`}>
        <div className="flex items-center justify-between border-b p-4">
          <span className="flex items-center gap-2 text-sm font-semibold"><BookOpen className="h-4 w-4" />Mục lục</span>
          <Button variant="ghost" size="sm" onClick={() => setTocOpen(false)}>‹</Button>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-2" aria-label="Mục lục điều khoản">
          {articleIndex.map((item) => {
            const itemArticle = String(item.article_number || '').trim()
            return (
              <Link
                key={itemArticle}
                href={buildInternalLegalUrl(docId, itemArticle)}
                className={`block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-accent ${isTargetArticle(itemArticle, article) ? 'bg-primary/10 font-medium text-primary' : ''}`}
              >
                Điều {itemArticle}{item.article_title ? ` - ${item.article_title}` : ''}
              </Link>
            )
          })}
        </nav>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-5 p-6 pb-20">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex gap-2">
              <Button asChild variant="outline" size="sm"><Link href="/search"><ArrowLeft className="mr-2 h-4 w-4" />Về hỏi đáp</Link></Button>
              {!tocOpen && <Button variant="ghost" size="sm" onClick={() => setTocOpen(true)}>Mục lục</Button>}
            </div>
            <Button size="sm" onClick={() => void downloadPdf()} disabled={!document}><Download className="mr-2 h-4 w-4" />{document?.source_file_available ? 'Tải PDF gốc' : 'Tải PDF trích xuất'}</Button>
          </div>

          {loading && <Card aria-busy="true"><CardContent className="space-y-4 p-8"><div className="h-5 w-1/3 animate-pulse rounded bg-muted" /><div className="h-4 w-full animate-pulse rounded bg-muted" /><div className="h-4 w-4/5 animate-pulse rounded bg-muted" /><p className="pt-2 text-sm text-muted-foreground">Đang tải văn bản pháp lý...</p></CardContent></Card>}
          {error && <Card><CardContent className="space-y-2 p-8"><p className="font-medium text-destructive">Không mở được văn bản</p><p className="text-sm text-muted-foreground">{error}</p></CardContent></Card>}
          {document && !loading && !error && <>
            <Card>
              <CardHeader className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {document.law_number && <Badge variant="outline">{document.law_number}</Badge>}
                  {document.effective_status && <Badge>{document.effective_status}</Badge>}
                  <Badge variant="outline" className={document.source_file_available ? 'border-green-200 bg-green-50 text-green-700' : 'border-amber-200 bg-amber-50 text-amber-700'}>{document.source_file_available ? 'Có file PDF gốc' : 'Bản trích xuất hệ thống'}</Badge>
                  {article && <Badge className="bg-primary/10 text-primary">Điều {article}{clause ? `, Khoản ${clause}` : ''}{point ? `, Điểm ${point}` : ''}</Badge>}
                </div>
                <CardTitle className="text-2xl leading-snug">{document.document_title || document.law_number || `Văn bản ${docId}`}</CardTitle>
                <div className="grid gap-1 text-sm text-muted-foreground">
                  {viewLatencyMs !== null && <div>Thời gian mở: {viewLatencyMs} ms</div>}
                  {downloadLatencyMs !== null && <div>Thời gian chuẩn bị PDF: {downloadLatencyMs} ms</div>}
                  {document.issuing_agency && <div>Cơ quan ban hành: {document.issuing_agency}</div>}
                  {document.document_type && <div>Loại văn bản: {document.document_type}</div>}
                  {document.effective_date && <div>Hiệu lực: {document.effective_date}</div>}
                  {document.scope && <div>Phạm vi: {document.scope}</div>}
                </div>
              </CardHeader>
            </Card>

            <section className="space-y-4" aria-label="Nội dung văn bản">
              {hasLoadedArticle ? document.articles?.map((item) => {
                const itemArticle = String(item.article_number || '').trim()
                const articleHighlighted = isTargetArticle(itemArticle, article)
                return <Card key={String(item.article_id || itemArticle)} id={buildArticleAnchor(itemArticle)} className={articleHighlighted ? 'border-primary ring-2 ring-primary/30' : ''}>
                  <CardHeader className="pb-2"><CardTitle className={articleHighlighted ? 'text-lg text-primary' : 'text-lg'}>Điều {itemArticle}{item.article_title ? `. ${item.article_title}` : ''}</CardTitle></CardHeader>
                  <CardContent className="space-y-3 text-sm leading-7">
                    {(item.chunks || []).map((chunk, index) => {
                      const state = getChunkHighlightState(String(chunk.content || ''), articleHighlighted, clause, point)
                      return <p key={String(chunk.chunk_id || index)} className={`whitespace-pre-wrap rounded-md p-2 ${state.highlight ? 'border border-primary/25 bg-primary/5 outline outline-1 outline-primary/15' : ''}`}>{chunk.content}</p>
                    })}
                  </CardContent>
                </Card>
              }) : <Card><CardHeader><CardTitle className="flex items-center gap-2 text-lg"><FileText className="h-5 w-5" />Chọn điều để xem nội dung</CardTitle></CardHeader><CardContent className="text-sm text-muted-foreground">Mục lục đang hiển thị ở bên trái. Chọn một điều để tải riêng nội dung của điều đó, giúp mở văn bản nhanh hơn.</CardContent></Card>}
            </section>
          </>}
        </div>
      </main>
    </div>
  )
}
