'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BookOpen,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Download,
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
  LegalDocumentFilterParams,
  LegalDocumentListItem,
  LegalDomain,
  LegalRetrievalTier,
  legalDocumentsApi,
} from '@/lib/api/legal-documents'

const PAGE_SIZE = 20

type DateField = 'effective' | 'issued' | 'expired'
type PaginationItem = number | 'ellipsis-start' | 'ellipsis-end'

function paginationItems(page: number, pageCount: number): PaginationItem[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1)
  const pages = new Set([1, pageCount, page - 1, page, page + 1])
  const visible = [...pages].filter((item) => item >= 1 && item <= pageCount).sort((a, b) => a - b)
  const result: PaginationItem[] = []
  visible.forEach((item, index) => {
    const previous = visible[index - 1]
    if (previous && item - previous > 1) {
      result.push(previous === 1 ? 'ellipsis-start' : 'ellipsis-end')
    }
    result.push(item)
  })
  return result
}

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
  const [documents, setDocuments] = useState<LegalDocumentListItem[]>([])
  const [domains, setDomains] = useState<LegalDomain[]>([])
  const [searchText, setSearchText] = useState('')
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('')
  const [tier, setTier] = useState<LegalRetrievalTier>('all')
  const [dateField, setDateField] = useState<DateField>('effective')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [asOf, setAsOf] = useState('')
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const requestSequence = useRef(0)
  const listTopRef = useRef<HTMLDivElement | null>(null)
  const scrollAfterLoadRef = useRef(false)

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      const nextQuery = searchText.trim()
      if (nextQuery !== query) {
        setQuery(nextQuery)
        setPage(1)
      }
    }, 350)
    return () => window.clearTimeout(timeout)
  }, [query, searchText])

  useEffect(() => {
    legalDocumentsApi.domains()
      .then(setDomains)
      .catch(() => setDomains([]))
  }, [])

  const dateError = dateFrom && dateTo && dateFrom > dateTo
    ? 'Ngày bắt đầu không được sau ngày kết thúc.'
    : null

  const filterParams = useMemo<LegalDocumentFilterParams>(() => {
    const params: LegalDocumentFilterParams = {
      q: query || undefined,
      domain: domain || undefined,
      tier,
      sort_by: 'effective_date',
      sort_order: 'desc',
    }
    if (dateFrom) params[`${dateField}_from`] = dateFrom
    if (dateTo) params[`${dateField}_to`] = dateTo
    return params
  }, [dateField, dateFrom, dateTo, domain, query, tier])

  useEffect(() => {
    if (dateError) {
      requestSequence.current += 1
      setDocuments([])
      setTotal(0)
      setError(dateError)
      setLoading(false)
      return
    }

    const controller = new AbortController()
    const requestId = requestSequence.current + 1
    requestSequence.current = requestId
    setLoading(true)
    setError(null)

    void legalDocumentsApi.list({
      ...filterParams,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }, controller.signal).then((result) => {
      if (requestSequence.current !== requestId) return
      const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE))
      if (page > lastPage) {
        setPage(lastPage)
        return
      }
      setDocuments(result.items)
      setTotal(result.total)
      setAsOf(result.as_of)
      if (scrollAfterLoadRef.current) {
        scrollAfterLoadRef.current = false
        window.requestAnimationFrame(() => listTopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
      }
    }).catch((caught: unknown) => {
      if (requestSequence.current !== requestId) return
      if ((caught as { code?: string }).code === 'ERR_CANCELED') return
      const message = getErrorMessage(caught)
      setError(message)
      toast.error(message)
    }).finally(() => {
      if (requestSequence.current === requestId) setLoading(false)
    })

    return () => controller.abort()
  }, [dateError, filterParams, page, refreshVersion])

  const handleExport = async () => {
    if (dateError) {
      toast.error(dateError)
      return
    }
    try {
      setExporting(true)
      const exported = await legalDocumentsApi.exportXlsx(filterParams)
      const url = window.URL.createObjectURL(exported.blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = exported.filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(url)
      toast.success(`Đã xuất ${total.toLocaleString('vi-VN')} văn bản.`)
    } catch (caught) {
      toast.error(getErrorMessage(caught))
    } finally {
      setExporting(false)
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const firstVisible = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const lastVisible = Math.min(page * PAGE_SIZE, total)

  const goToPage = (nextPage: number) => {
    const bounded = Math.max(1, Math.min(pageCount, nextPage))
    if (bounded === page) return
    scrollAfterLoadRef.current = true
    setPage(bounded)
  }

  const selectedDomainName = useMemo(
    () => domains.find((item) => item.slug === domain)?.name,
    [domain, domains],
  )

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
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => void handleExport()} disabled={exporting || loading || Boolean(dateError)}>
                {exporting
                  ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                  : <Download className="mr-2 h-4 w-4" />}
                {exporting ? 'Đang xuất...' : 'Xuất Excel'}
              </Button>
              <Button variant="outline" onClick={() => setRefreshVersion((value) => value + 1)} disabled={loading}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                Làm mới
              </Button>
            </div>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl space-y-5 p-6">
            <section className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
              <div className="grid gap-3 lg:grid-cols-[1fr_260px_220px]">
                <label className="relative block">
                  <span className="sr-only">Tìm kiếm văn bản</span>
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
                  onChange={(event) => {
                    setDomain(event.target.value)
                    setPage(1)
                  }}
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
                  onChange={(event) => {
                    setTier(event.target.value as LegalRetrievalTier)
                    setPage(1)
                  }}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  aria-label="Lọc theo tầng truy xuất"
                >
                  <option value="all">Toàn bộ kho tra cứu</option>
                  <option value="core">Kho nhanh</option>
                  <option value="expanded">Kho mở rộng</option>
                </select>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <label className="space-y-1 text-xs font-medium text-muted-foreground">
                  Mốc thời gian
                  <select
                    value={dateField}
                    onChange={(event) => {
                      setDateField(event.target.value as DateField)
                      setPage(1)
                    }}
                    className="block h-9 w-full rounded-md border border-input bg-background px-3 text-sm font-normal text-foreground outline-none focus:ring-2 focus:ring-ring"
                    aria-label="Chọn loại ngày"
                  >
                    <option value="effective">Ngày hiệu lực</option>
                    <option value="issued">Ngày ban hành</option>
                    <option value="expired">Ngày hết hiệu lực</option>
                  </select>
                </label>
                <label className="space-y-1 text-xs font-medium text-muted-foreground">
                  Từ ngày
                  <Input
                    type="date"
                    value={dateFrom}
                    onChange={(event) => {
                      setDateFrom(event.target.value)
                      setPage(1)
                    }}
                    aria-label="Từ ngày"
                    className="block font-normal text-foreground"
                  />
                </label>
                <label className="space-y-1 text-xs font-medium text-muted-foreground">
                  Đến ngày
                  <Input
                    type="date"
                    value={dateTo}
                    onChange={(event) => {
                      setDateTo(event.target.value)
                      setPage(1)
                    }}
                    aria-label="Đến ngày"
                    className="block font-normal text-foreground"
                  />
                </label>
              </div>
            </section>

            <div ref={listTopRef} className="scroll-mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
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
                    className="rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/30"
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
                        <h2 className="text-base font-semibold leading-6">
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
                        <Button
                          variant="default"
                          size="sm"
                          asChild
                        >
                          <a
                            href={
                              document.source_url && /^https?:\/\//i.test(document.source_url)
                                ? document.source_url
                                : `https://vbpl.vn`
                            }
                            target="_blank"
                            rel="noreferrer"
                          >
                            Xem văn bản <ExternalLink className="ml-2 h-4 w-4" />
                          </a>
                        </Button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}

            {!loading && total > 0 && (
              <div className="flex flex-col items-center justify-between gap-3 border-t pb-6 pt-4 sm:flex-row">
                <p className="text-sm text-muted-foreground">
                  Trang <span className="font-medium text-foreground">{page}/{pageCount}</span>
                  {' · '}Đang xem {firstVisible.toLocaleString('vi-VN')}–{lastVisible.toLocaleString('vi-VN')}
                  {' '}trong tổng số {total.toLocaleString('vi-VN')} văn bản
                </p>
                <nav className="flex flex-wrap items-center justify-center gap-1" aria-label="Phân trang văn bản">
                  <Button variant="outline" size="sm" onClick={() => goToPage(1)} disabled={page === 1}>
                    Trang đầu
                  </Button>
                  <Button variant="outline" size="icon" onClick={() => goToPage(page - 1)} disabled={page === 1} aria-label="Trang trước">
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  {paginationItems(page, pageCount).map((item) => (
                    typeof item === 'number' ? (
                      <Button
                        key={item}
                        variant={item === page ? 'default' : 'outline'}
                        size="icon"
                        onClick={() => goToPage(item)}
                        aria-label={`Trang ${item}`}
                        aria-current={item === page ? 'page' : undefined}
                      >
                        {item}
                      </Button>
                    ) : (
                      <span key={item} className="px-1 text-sm text-muted-foreground" aria-hidden="true">…</span>
                    )
                  ))}
                  <Button variant="outline" size="icon" onClick={() => goToPage(page + 1)} disabled={page === pageCount} aria-label="Trang sau">
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => goToPage(pageCount)} disabled={page === pageCount}>
                    Trang cuối
                  </Button>
                </nav>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}
