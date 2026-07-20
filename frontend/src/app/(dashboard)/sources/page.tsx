'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  BookOpen,
  CalendarDays,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
} from 'lucide-react'
import { toast } from 'sonner'

import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  LegalDocumentListItem,
  LegalDomain,
  LegalRetrievalTier,
  legalDocumentsApi,
} from '@/lib/api/legal-documents'

const PAGE_SIZE = 30

function formatDate(value?: string | null): string {
  if (!value) return 'Chưa cập nhật'
  const parsed = new Date(`${value}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('vi-VN').format(parsed)
}

function getErrorMessage(error: unknown): string {
  const candidate = error as {
    response?: { data?: { detail?: string | { message?: string } } }
    message?: string
  }
  const detail = candidate.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && detail.message) return detail.message
  return candidate.message || 'Không tải được kho văn bản pháp luật.'
}

export default function SourcesPage() {
  const router = useRouter()
  const [documents, setDocuments] = useState<LegalDocumentListItem[]>([])
  const [domains, setDomains] = useState<LegalDomain[]>([])
  const [searchText, setSearchText] = useState('')
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('')
  const [tier, setTier] = useState<LegalRetrievalTier>('all')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [asOf, setAsOf] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timeout = window.setTimeout(() => setQuery(searchText.trim()), 350)
    return () => window.clearTimeout(timeout)
  }, [searchText])

  useEffect(() => {
    legalDocumentsApi.domains()
      .then(setDomains)
      .catch(() => setDomains([]))
  }, [])

  const loadDocuments = useCallback(async (nextOffset = 0, append = false) => {
    try {
      if (append) setLoadingMore(true)
      else setLoading(true)
      setError(null)
      const result = await legalDocumentsApi.list({
        q: query || undefined,
        domain: domain || undefined,
        tier,
        limit: PAGE_SIZE,
        offset: nextOffset,
        sort_by: 'effective_date',
        sort_order: 'desc',
      })
      setDocuments((current) => append ? [...current, ...result.items] : result.items)
      setOffset(nextOffset + result.items.length)
      setTotal(result.total)
      setAsOf(result.as_of)
    } catch (caught) {
      const message = getErrorMessage(caught)
      setError(message)
      toast.error(message)
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }, [domain, query, tier])

  useEffect(() => {
    void loadDocuments(0, false)
  }, [loadDocuments])

  const selectedDomainName = useMemo(
    () => domains.find((item) => item.slug === domain)?.name,
    [domain, domains],
  )

  const openDocument = (document: LegalDocumentListItem) => {
    router.push(`/legal-documents/${encodeURIComponent(String(document.doc_id))}`)
  }

  return (
    <AppShell>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
        <header className="border-b bg-card px-6 py-5">
          <div className="mx-auto flex max-w-7xl flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <BookOpen className="h-6 w-6 text-primary" />
                <h1 className="text-2xl font-semibold">Văn bản pháp luật</h1>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                Kho văn bản đang được hệ thống Legal Retrieval dùng để tra cứu và tạo căn cứ trả lời.
              </p>
            </div>
            <Button variant="outline" onClick={() => void loadDocuments(0, false)} disabled={loading}>
              <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Làm mới
            </Button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl space-y-5 p-6">
            <section className="grid gap-3 rounded-xl border bg-card p-4 shadow-sm lg:grid-cols-[1fr_260px_220px]">
              <label className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="Tìm theo số hiệu, tên văn bản, cơ quan ban hành..."
                  className="pl-9"
                />
              </label>
              <select
                value={domain}
                onChange={(event) => setDomain(event.target.value)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                aria-label="Lọc theo lĩnh vực"
              >
                <option value="">Tất cả lĩnh vực</option>
                {domains.map((item) => (
                  <option key={item.slug} value={item.slug}>{item.name}</option>
                ))}
              </select>
              <select
                value={tier}
                onChange={(event) => setTier(event.target.value as LegalRetrievalTier)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                aria-label="Lọc theo tầng truy xuất"
              >
                <option value="all">Toàn bộ kho tra cứu</option>
                <option value="core">Kho nhanh</option>
                <option value="expanded">Kho mở rộng</option>
              </select>
            </section>

            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">{total.toLocaleString('vi-VN')} văn bản</Badge>
                {selectedDomainName && <Badge variant="outline">{selectedDomainName}</Badge>}
                {asOf && (
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <CalendarDays className="h-4 w-4" />
                    Hiệu lực tại {formatDate(asOf)}
                  </span>
                )}
              </div>
              <span className="text-muted-foreground">
                Kho nhanh phục vụ câu hỏi phổ biến; kho mở rộng bổ sung căn cứ khi cần.
              </span>
            </div>

            {error && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
                <p className="font-medium">Không tải được danh sách văn bản</p>
                <p className="mt-1">{error}</p>
              </div>
            )}

            {loading ? (
              <div className="space-y-3" aria-busy="true" aria-label="Đang tải văn bản">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div key={index} className="h-28 animate-pulse rounded-xl border bg-muted/40" />
                ))}
              </div>
            ) : documents.length === 0 && !error ? (
              <div className="flex min-h-72 flex-col items-center justify-center rounded-xl border bg-card text-center">
                <FileText className="mb-3 h-10 w-10 text-muted-foreground" />
                <h2 className="font-semibold">Không tìm thấy văn bản phù hợp</h2>
                <p className="mt-1 text-sm text-muted-foreground">Hãy đổi từ khóa hoặc bỏ bớt bộ lọc.</p>
              </div>
            ) : (
              <div className="space-y-3">
                {documents.map((document) => (
                  <article
                    key={String(document.doc_id)}
                    className="group cursor-pointer rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/40 hover:shadow-md"
                    onClick={() => openDocument(document)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') openDocument(document)
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0 flex-1">
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          {document.law_number && <Badge>{document.law_number}</Badge>}
                          {document.document_type && <Badge variant="outline">{document.document_type}</Badge>}
                          <Badge variant={document.retrieval_tier === 'core' ? 'secondary' : 'outline'}>
                            {document.retrieval_tier === 'core' ? 'Kho nhanh' : 'Kho mở rộng'}
                          </Badge>
                          {document.domain_name && <Badge variant="outline">{document.domain_name}</Badge>}
                        </div>
                        <h2 className="text-base font-semibold leading-6 group-hover:text-primary">
                          {document.document_title || document.law_number || `Văn bản ${document.doc_id}`}
                        </h2>
                        <div className="mt-2 grid gap-1 text-sm text-muted-foreground sm:grid-cols-2">
                          <span>Cơ quan: {document.issuing_agency || 'Chưa cập nhật'}</span>
                          <span>Hiệu lực từ: {formatDate(document.effective_date)}</span>
                          <span>Lĩnh vực: {document.field_name || document.domain_name || 'Chưa phân loại'}</span>
                          <span>{Number(document.article_count || 0).toLocaleString('vi-VN')} điều/mục đã lập chỉ mục</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        {document.source_url && /^https?:\/\//i.test(document.source_url) && (
                          <Button
                            variant="ghost"
                            size="sm"
                            asChild
                            onClick={(event) => event.stopPropagation()}
                          >
                            <a href={document.source_url} target="_blank" rel="noreferrer">
                              Nguồn gốc <ExternalLink className="ml-2 h-4 w-4" />
                            </a>
                          </Button>
                        )}
                        <Button size="sm" onClick={(event) => { event.stopPropagation(); openDocument(document) }}>
                          Xem văn bản
                        </Button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}

            {!loading && documents.length < total && (
              <div className="flex justify-center pb-6">
                <Button
                  variant="outline"
                  onClick={() => void loadDocuments(offset, true)}
                  disabled={loadingMore}
                >
                  {loadingMore ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {loadingMore ? 'Đang tải...' : 'Tải thêm văn bản'}
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}
