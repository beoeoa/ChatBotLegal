'use client'

import Link from 'next/link'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Database,
  FileSearch,
  Layers3,
  RefreshCw,
  Search,
  ShieldCheck,
} from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { LifecycleReadinessPanel } from '@/components/legal-management/LifecycleReadinessPanel'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  legalManagementApi,
  type AvailabilitySection,
  type LegalManagementListParams,
  type LegalManagementListResponse,
  type LegalManagementSummary,
} from '@/lib/api/legal-management'

const PAGE_SIZE = 30

function availabilityMessage(section?: AvailabilitySection) {
  if (!section || section.status === 'available') return null
  return section.message || 'Không thể đọc dữ liệu tại thời điểm này.'
}

function statusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    active: 'Đang phục vụ',
    inactive: 'Không phục vụ',
    expired: 'Hết hiệu lực',
    not_yet_effective: 'Chưa hiệu lực',
    blocked: 'Đã chặn',
    archived: 'Lưu trữ',
    staging: 'Đang chuẩn bị',
    draft: 'Bản nháp',
    unknown: 'Chưa xác minh',
  }
  return labels[String(status || '').toLowerCase()] || status || 'Chưa xác định'
}

function SummaryCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string
  value: number | string
  hint?: string
  icon: typeof Database
}) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3 p-5">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="rounded-xl bg-primary/10 p-2 text-primary">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </span>
      </CardContent>
    </Card>
  )
}

export default function LegalManagementPage() {
  const [summary, setSummary] = useState<LegalManagementSummary | null>(null)
  const [inventory, setInventory] = useState<LegalManagementListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [draftQuery, setDraftQuery] = useState('')
  const [draftStatus, setDraftStatus] = useState<'' | 'active' | 'not_yet_effective' | 'expired' | 'unknown'>('')
  const [filters, setFilters] = useState<LegalManagementListParams>({
    q: '',
    tier: 'all',
    source_presence: 'all',
    limit: PAGE_SIZE,
    offset: 0,
    sort_by: 'effective_date',
    sort_order: 'desc',
  })

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const dataQuality = params.get('data_quality')
    const validityStatus = params.get('validity_status')
    if (
      !['missing_source', 'missing_metadata', 'zero_chunks', 'unknown_status'].includes(dataQuality || '')
      && !['active', 'not_yet_effective', 'expired', 'unknown'].includes(validityStatus || '')
    ) return
    setFilters((current) => ({
      ...current,
      data_quality: ['missing_source', 'missing_metadata', 'zero_chunks', 'unknown_status'].includes(dataQuality || '')
        ? dataQuality as LegalManagementListParams['data_quality']
        : undefined,
      validity_status: ['active', 'not_yet_effective', 'expired', 'unknown'].includes(validityStatus || '')
        ? validityStatus as LegalManagementListParams['validity_status']
        : undefined,
      offset: 0,
    }))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [summaryResult, listResult] = await Promise.all([
        legalManagementApi.summary(),
        legalManagementApi.list(filters),
      ])
      setSummary(summaryResult)
      setInventory(listResult)
    } catch {
      setError('Không tải được tổng quan kho văn bản. Vui lòng thử lại.')
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    void load()
  }, [load])

  const page = Math.floor((inventory?.offset || 0) / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil((inventory?.total || 0) / PAGE_SIZE))
  const observed = useMemo(() => {
    if (!summary?.observed_at) return ''
    const parsed = new Date(summary.observed_at)
    return Number.isNaN(parsed.getTime()) ? summary.observed_at : parsed.toLocaleString('vi-VN')
  }, [summary?.observed_at])

  const applyFilters = (event: FormEvent) => {
    event.preventDefault()
    setFilters((current) => ({
      ...current,
      q: draftQuery.trim(),
      validity_status: draftStatus || undefined,
      offset: 0,
    }))
  }

  const clearFilters = () => {
    setDraftQuery('')
    setDraftStatus('')
    setFilters({
      q: '',
      tier: 'all',
      source_presence: 'all',
      limit: PAGE_SIZE,
      offset: 0,
      sort_by: 'effective_date',
      sort_order: 'desc',
    })
  }

  const vectorMessage = availabilityMessage(summary?.vectors)
  const faqMessage = availabilityMessage(summary?.faq_impacts)

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-auto bg-muted/20">
        <div className="mx-auto max-w-[1500px] space-y-6 p-4 pt-16 md:p-8 md:pt-8">
          <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-primary">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                Chỉ đọc · dành cho Admin
              </div>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight">Kho văn bản pháp luật</h1>
              <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
                Xem văn bản đang phục vụ tra cứu, tình trạng hiệu lực và các lỗi dữ liệu cần xử lý.
              </p>
              {observed && <p className="mt-2 text-xs text-muted-foreground">Cập nhật: {observed}</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link href="/legal-management/validity"><ShieldCheck className="mr-2 h-4 w-4" />Văn bản cần kiểm tra hiệu lực</Link>
              </Button>
              <Button variant="outline" onClick={() => void load()} disabled={loading}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                Làm mới
              </Button>
            </div>
          </header>

          {error && (
            <Alert variant="destructive" role="alert">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Không tải được tổng quan</AlertTitle>
              <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
                <span>{error}</span>
                <Button size="sm" variant="outline" onClick={() => void load()}>Thử lại</Button>
              </AlertDescription>
            </Alert>
          )}

          <section aria-label="Tổng quan kho văn bản" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryCard
              label="Đang phục vụ tra cứu"
              value={summary ? `${summary.documents.active.toLocaleString('vi-VN')} / ${summary.documents.total.toLocaleString('vi-VN')}` : '—'}
              hint="Số văn bản đang sử dụng trên tổng số trong kho"
              icon={Database}
            />
            <SummaryCard
              label="Cần kiểm tra hiệu lực"
              value={summary?.validity.counts?.open_events ?? '—'}
              icon={ShieldCheck}
            />
            <SummaryCard
              label="Chờ kiểm duyệt"
              value={summary?.operations.pending_document_candidates ?? '—'}
              hint={summary?.operations.status === 'unavailable' ? summary.operations.message || undefined : undefined}
              icon={FileSearch}
            />
            <SummaryCard label="Lỗi xử lý dữ liệu" value={summary?.operations.import_queue?.failed ?? '—'} icon={AlertTriangle} />
          </section>

          <Card>
            <CardHeader>
              <CardTitle>Toàn bộ văn bản</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <form onSubmit={applyFilters} className="grid gap-3 lg:grid-cols-[minmax(280px,1fr)_240px_auto]">
                <label className="space-y-1 text-sm">
                  <span className="font-medium">Tìm văn bản</span>
                  <div className="relative">
                    <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                      aria-label="Tìm văn bản"
                      value={draftQuery}
                      onChange={(event) => setDraftQuery(event.target.value)}
                      placeholder="Số, tên, cơ quan, loại văn bản"
                      className="pl-9"
                    />
                  </div>
                </label>
                <label className="space-y-1 text-sm">
                  <span className="font-medium">Tình trạng sử dụng</span>
                  <select
                    aria-label="Tình trạng sử dụng"
                    value={draftStatus}
                    onChange={(event) => setDraftStatus(event.target.value as typeof draftStatus)}
                    className="h-10 w-full rounded-md border bg-background px-3"
                  >
                    <option value="">Tất cả</option>
                    <option value="active">Đang có hiệu lực</option>
                    <option value="not_yet_effective">Chưa có hiệu lực</option>
                    <option value="expired">Hết hiệu lực</option>
                    <option value="unknown">Chưa xác minh</option>
                  </select>
                </label>
                <div className="flex items-end gap-2">
                  <Button type="submit">Áp dụng bộ lọc</Button>
                  <Button type="button" variant="ghost" onClick={clearFilters}>Xóa lọc</Button>
                </div>
              </form>

              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full min-w-[980px] text-sm">
                  <thead className="bg-muted/70 text-left text-xs uppercase text-muted-foreground">
                    <tr>
                      <th className="px-4 py-3">Văn bản</th>
                      <th className="px-4 py-3">Cơ quan ban hành</th>
                      <th className="px-4 py-3">Hiệu lực</th>
                      <th className="px-4 py-3">Tình trạng dữ liệu</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {(inventory?.items || []).map((document) => (
                      <tr key={String(document.doc_id)} className="bg-background hover:bg-muted/30">
                        <td className="max-w-md px-4 py-3 align-top">
                          <Link
                            href={`/legal-management/${encodeURIComponent(String(document.doc_id))}`}
                            className="font-medium text-primary hover:underline"
                          >
                            {document.document_title || 'Chưa có tên'}
                          </Link>
                          <div className="mt-1 text-xs text-muted-foreground">{document.law_number || 'Chưa có số, ký hiệu'}</div>
                          {document.source_url && <div className="mt-1 truncate text-xs text-muted-foreground">Có nguồn chính thức</div>}
                        </td>
                        <td className="px-4 py-3 align-top">
                          <div>{document.issuing_agency || 'Chưa rõ cơ quan'}</div>
                          <div className="mt-1 text-xs text-muted-foreground">{document.document_type || 'Chưa rõ loại'}</div>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <Badge variant={document.current_answer_eligible === false ? 'destructive' : document.validity_status === 'active' ? 'default' : 'secondary'}>
                            {document.validity_sync?.display_label || statusLabel(document.validity_status || document.as_of_status || document.stored_status)}
                          </Badge>
                          {document.current_answer_eligible === false && (
                            <div className="mt-1 text-xs font-medium text-destructive">
                              Chỉ dùng để tra cứu lịch sử
                            </div>
                          )}
                          <div className="mt-1 text-xs text-muted-foreground">{document.effective_date || 'Chưa có ngày'}</div>
                        </td>
                        <td className="px-4 py-3 align-top">
                          {document.quality_flags.length === 0 ? (
                            <span className="text-emerald-700">Dữ liệu đầy đủ</span>
                          ) : (
                            <span className="text-amber-700">{document.quality_flags.length} cảnh báo</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!loading && inventory?.items.length === 0 && (
                  <div className="p-10 text-center text-sm text-muted-foreground">Không có văn bản phù hợp</div>
                )}
                {loading && !inventory && (
                  <div className="p-10 text-center text-sm text-muted-foreground">Đang đọc metadata kho văn bản…</div>
                )}
              </div>

              <div className="flex flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <span className="text-muted-foreground">{inventory?.total ?? 0} văn bản · Trang {page}/{totalPages}</span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label="Trang trước"
                    disabled={page <= 1 || loading}
                    onClick={() => setFilters((current) => ({ ...current, offset: Math.max(0, (current.offset || 0) - PAGE_SIZE) }))}
                  >
                    <ChevronLeft className="h-4 w-4" /> Trước
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label="Trang sau"
                    disabled={page >= totalPages || loading}
                    onClick={() => setFilters((current) => ({ ...current, offset: (current.offset || 0) + PAGE_SIZE }))}
                  >
                    Sau <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <details className="rounded-xl border bg-background p-4">
            <summary className="cursor-pointer text-sm font-medium">Quản trị vòng đời nâng cao</summary>
            <div className="mt-4 space-y-4">
              {(vectorMessage || faqMessage) && (
                <div className="grid gap-3 lg:grid-cols-2">
                  {vectorMessage && <Alert><Layers3 className="h-4 w-4" /><AlertTitle>Kho tìm kiếm cần kiểm tra</AlertTitle><AlertDescription>Dữ liệu chỉ mục tìm kiếm chưa sẵn sàng để kiểm tra.</AlertDescription></Alert>}
                  {faqMessage && <Alert><FileSearch className="h-4 w-4" /><AlertTitle>Dữ liệu FAQ liên quan</AlertTitle><AlertDescription>Chưa thể xác nhận tình trạng liên kết FAQ trong lần tải này.</AlertDescription></Alert>}
                </div>
              )}
              <LifecycleReadinessPanel />
            </div>
          </details>
        </div>
      </div>
    </AppShell>
  )
}
