'use client'

import { readAdminSnapshot, writeAdminSnapshot } from '@/lib/utils/admin-snapshot-cache'

import Link from 'next/link'
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Database,
  FileSearch,
  RefreshCw,
  Search,
  ShieldCheck,
} from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useSettings } from '@/lib/hooks/use-settings'
import {
  legalManagementApi,
  type AvailabilitySection,
  type LegalManagementListParams,
  type LegalManagementListResponse,
  type LegalManagementSummary,
} from '@/lib/api/legal-management'
import { formatApiError } from '@/lib/utils/error-handler'
import { systemStatusLabel } from '@/lib/utils/system-labels'

const PAGE_SIZE = 30
const METADATA_TIMEOUT_MS = 10000

function isTimeoutError(error: unknown): boolean {
  return Boolean(
    error && typeof error === 'object' && 'code' in error
      && (error as { code?: string }).code === 'ECONNABORTED',
  )
}

function availabilityMessage(section?: AvailabilitySection) {
  if (!section || section.status === 'available') return null
  return formatApiError(section.message, 'Không thể đọc dữ liệu tại thời điểm này.')
}

function servingStateLabel(servingState?: string | null, eligible?: boolean) {
  if (servingState === 'historical_only') return 'Chỉ tra cứu lịch sử'
  if (servingState === 'quarantined') return 'Cách ly khỏi mọi tra cứu'
  if (servingState === 'future_effective') return 'Chưa phục vụ; sắp có hiệu lực'
  if (eligible === false) return 'Tạm ngừng trả lời hiện hành'
  return ''
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
    <Card className="h-full">
      <CardContent className="flex h-full flex-col items-center p-5 text-center">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </span>
        <p className="mt-3 flex min-h-10 items-center justify-center text-sm leading-5 text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="mt-2 min-h-10 text-xs leading-5 text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  )
}

export default function LegalManagementPage() {
  const { data: settings } = useSettings()
  const [summary, setSummary] = useState<LegalManagementSummary | null>(null)
  const [inventory, setInventory] = useState<LegalManagementListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filtersReady, setFiltersReady] = useState(false)
  const loadRequestRef = useRef(0)
  const lastAutomaticLoadKeyRef = useRef('')
  const [draftQuery, setDraftQuery] = useState('')
  const [draftStatus, setDraftStatus] = useState<'' | 'active' | 'not_yet_effective' | 'expired' | 'unknown'>('')
  const [draftHistory, setDraftHistory] = useState<'recent' | 'all'>('recent')
  const [draftOrganizationUnit, setDraftOrganizationUnit] = useState('')
  const [draftResponsibility, setDraftResponsibility] = useState<'all' | 'primary' | 'support'>('all')
  const [filters, setFilters] = useState<LegalManagementListParams>({
    q: '',
    tier: 'all',
    source_presence: 'all',
    include_expired_history: false,
    include_organization_units: true,
    limit: PAGE_SIZE,
    offset: 0,
    sort_by: 'effective_date',
    sort_order: 'desc',
  })

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const query = params.get('q') || ''
    const dataQuality = params.get('data_quality')
    const validityStatus = params.get('validity_status')
    const includeExpiredHistory = params.get('include_expired_history') === 'true'
    const organizationUnitId = params.get('organization_unit_id') || ''
    const responsibility = params.get('organization_responsibility')
    const nextResponsibility = responsibility === 'primary' || responsibility === 'support' ? responsibility : 'all'
    if (
      !['missing_source', 'missing_metadata', 'zero_chunks', 'unknown_status', 'unclassified'].includes(dataQuality || '')
      && !['active', 'not_yet_effective', 'expired', 'unknown'].includes(validityStatus || '')
      && !query
      && !includeExpiredHistory
      && !organizationUnitId
    ) {
      setFiltersReady(true)
      return
    }
    const nextValidityStatus = ['active', 'not_yet_effective', 'expired', 'unknown'].includes(validityStatus || '')
      ? validityStatus as LegalManagementListParams['validity_status']
      : undefined
    setDraftQuery(query)
    setDraftStatus(nextValidityStatus || '')
    setDraftHistory(includeExpiredHistory ? 'all' : 'recent')
    setDraftOrganizationUnit(organizationUnitId)
    setDraftResponsibility(nextResponsibility)
    setFilters((current) => ({
      ...current,
      q: query,
      data_quality: ['missing_source', 'missing_metadata', 'zero_chunks', 'unknown_status', 'unclassified'].includes(dataQuality || '')
        ? dataQuality as LegalManagementListParams['data_quality']
        : undefined,
      validity_status: nextValidityStatus,
      include_expired_history: includeExpiredHistory,
      organization_unit_id: organizationUnitId || undefined,
      organization_responsibility: nextResponsibility,
      offset: 0,
    }))
    setFiltersReady(true)
  }, [])

  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current
    setLoading(true)
    setError('')
    const cacheKey = `inventory:${JSON.stringify(filters)}`
    const cachedSummary = readAdminSnapshot<LegalManagementSummary>('legal-summary')
    const cachedList = readAdminSnapshot<LegalManagementListResponse>(cacheKey)
    if (cachedSummary) setSummary(cachedSummary)
    setInventory(cachedList)
    try {
      const [summaryResult, listResult] = await Promise.allSettled([
        legalManagementApi.summary({ timeout: METADATA_TIMEOUT_MS }).then(value => {
          if (requestId === loadRequestRef.current) {
            setSummary(value)
            writeAdminSnapshot('legal-summary', value)
          }
          return value
        }),
        legalManagementApi.list(filters, { timeout: METADATA_TIMEOUT_MS }).then(value => {
          if (requestId === loadRequestRef.current) {
            setInventory(value)
            writeAdminSnapshot(cacheKey, value)
          }
          return value
        }),
      ])
      if (requestId !== loadRequestRef.current) return
      const failures = [summaryResult, listResult].filter(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      )
      if (failures.length > 0) {
        const timedOut = failures.some((result) => isTimeoutError(result.reason))
        setError(timedOut
          ? 'Máy chủ chưa trả thông tin văn bản trong 10 giây. Dữ liệu chưa được cập nhật; hãy thử lại.'
          : 'Không tải được một phần thông tin kho văn bản. Vui lòng thử lại.')
      }
    } catch {
      if (requestId !== loadRequestRef.current) return
      setError('Không tải được tổng quan kho văn bản. Vui lòng thử lại.')
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    if (!filtersReady) return
    const automaticLoadKey = JSON.stringify(filters)
    if (lastAutomaticLoadKeyRef.current === automaticLoadKey) return
    lastAutomaticLoadKeyRef.current = automaticLoadKey
    void load()
  }, [filters, filtersReady, load])

  const page = Math.floor((inventory?.offset || 0) / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil((inventory?.total || 0) / PAGE_SIZE))
  const observed = useMemo(() => {
    if (!summary?.observed_at) return ''
    const parsed = new Date(summary.observed_at)
    return Number.isNaN(parsed.getTime()) ? summary.observed_at : parsed.toLocaleString('vi-VN')
  }, [summary?.observed_at])
  const organizationUnits = useMemo(
    () => (settings?.organization_units || [])
      .filter((unit) => unit.is_active)
      .sort((left, right) => left.sort_order - right.sort_order || left.name.localeCompare(right.name, 'vi')),
    [settings?.organization_units],
  )
  const organizationUnitNames = useMemo(
    () => new Map(organizationUnits.map((unit) => [unit.id, unit.short_name || unit.name])),
    [organizationUnits],
  )

  const applyFilters = (event: FormEvent) => {
    event.preventDefault()
    const nextFilters: LegalManagementListParams = {
      ...filters,
      q: draftQuery.trim(),
      validity_status: draftStatus || undefined,
      include_expired_history: draftHistory === 'all',
      organization_unit_id: draftOrganizationUnit || undefined,
      organization_responsibility: draftResponsibility,
      offset: 0,
    }
    const params = new URLSearchParams()
    if (nextFilters.q) params.set('q', nextFilters.q)
    if (nextFilters.validity_status) params.set('validity_status', nextFilters.validity_status)
    if (nextFilters.include_expired_history) params.set('include_expired_history', 'true')
    if (nextFilters.organization_unit_id) params.set('organization_unit_id', nextFilters.organization_unit_id)
    if (draftResponsibility !== 'all') params.set('organization_responsibility', draftResponsibility)
    window.history.replaceState(null, '', `${window.location.pathname}${params.size ? `?${params.toString()}` : ''}`)
    setFilters((current) => ({
      ...current,
      q: nextFilters.q,
      validity_status: nextFilters.validity_status,
      include_expired_history: nextFilters.include_expired_history,
      organization_unit_id: nextFilters.organization_unit_id,
      organization_responsibility: draftResponsibility,
      offset: 0,
    }))
  }

  const clearFilters = () => {
    setDraftResponsibility('all')
    setDraftQuery('')
    setDraftStatus('')
    setDraftHistory('recent')
    setDraftOrganizationUnit('')
    window.history.replaceState(null, '', window.location.pathname)
    setFilters({
      q: '',
      tier: 'all',
      source_presence: 'all',
      include_expired_history: false,
      include_organization_units: true,
      limit: PAGE_SIZE,
      offset: 0,
      sort_by: 'effective_date',
      sort_order: 'desc',
    })
  }

  const vectorMessage = availabilityMessage(summary?.vectors)
  const release = summary?.serving_release
  const cards = release?.cards

  return (
    <AppShell>
      <div className="min-h-0 min-w-0 flex-1 overflow-auto bg-muted/20">
        <div className="mx-auto w-full min-w-0 max-w-[1500px] space-y-6 p-4 pt-8 sm:p-6 md:p-8 md:pt-8">
          <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-primary">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                Dành cho quản trị viên · chỉ đọc
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

          <section aria-label="Tổng quan kho văn bản" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
            <SummaryCard
              label="Tổng văn bản tra cứu"
              value={cards?.total_retrievable == null ? 'Chưa xác định' : cards.total_retrievable.toLocaleString('vi-VN')}
              hint="Gồm văn bản hiện hành và văn bản tra cứu lịch sử"
              icon={Database}
            />
            <SummaryCard
              label="Văn bản còn hiệu lực"
              value={cards?.current_effective == null ? 'Chưa xác định' : cards.current_effective.toLocaleString('vi-VN')}
              hint="Còn hiệu lực toàn bộ hoặc một phần"
              icon={ShieldCheck}
            />
            <SummaryCard
              label="Văn bản hết hiệu lực"
              value={cards?.expired_total == null ? 'Chưa xác định' : cards.expired_total.toLocaleString('vi-VN')}
              hint="Đã xác nhận chỉ dùng cho tra cứu lịch sử"
              icon={FileSearch}
            />
            <SummaryCard
              label="Chưa xác minh hiệu lực"
              value={cards?.unknown_total == null ? 'Chưa xác định' : cards.unknown_total.toLocaleString('vi-VN')}
              hint="Cần quản trị viên đối chiếu nguồn và xác nhận"
              icon={AlertTriangle}
            />
            <SummaryCard
              label="Sắp hết hiệu lực 30 ngày"
              value={cards?.expiring_30 == null ? 'Chưa xác định' : cards.expiring_30.toLocaleString('vi-VN')}
              hint="Tính theo ngày hiệu lực đã xác minh"
              icon={AlertTriangle}
            />
            <SummaryCard
              label="Sắp có hiệu lực 30 ngày"
              value={cards?.effective_30 == null ? 'Chưa xác định' : cards.effective_30.toLocaleString('vi-VN')}
              hint="Chưa dùng cho tra cứu trước ngày có hiệu lực"
              icon={ShieldCheck}
            />
          </section>

          {release?.release_id && (
            <div className="rounded-lg border bg-background px-4 py-3 text-xs text-muted-foreground">
              Đang hiển thị dữ liệu từ bản phát hành hiện hành
              {summary?.as_of ? ` · trạng thái hiệu lực tại ${summary.as_of}` : ''}
            </div>
          )}

          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>Hồ sơ văn bản trong kho</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <form onSubmit={applyFilters} className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(280px,1fr)_220px_220px_240px_auto]">
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
                  <span className="font-medium">Lịch sử hết hiệu lực</span>
                  <select
                    aria-label="Lịch sử hết hiệu lực"
                    value={draftHistory}
                    onChange={(event) => setDraftHistory(event.target.value as typeof draftHistory)}
                    className="h-10 w-full rounded-md border bg-background px-3"
                  >
                    <option value="recent">Gần đây (mặc định)</option>
                    <option value="all">Xem toàn bộ lịch sử</option>
                  </select>
                  <span className="text-xs text-muted-foreground">Ẩn hồ sơ hết hiệu lực quá 365 ngày khi xem mặc định.</span>
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
                <label className="space-y-1 text-sm">
                  <span className="font-medium">Phòng ban phụ trách</span>
                  <select
                    aria-label="Phòng ban phụ trách"
                    value={draftOrganizationUnit}
                    onChange={(event) => setDraftOrganizationUnit(event.target.value)}
                    className="h-10 w-full rounded-md border bg-background px-3"
                  >
                    <option value="">Tất cả phòng ban</option>
                    {organizationUnits.map((unit) => (
                      <option key={unit.id} value={unit.id}>{unit.short_name || unit.name}</option>
                    ))}
                    <option value="__shared__">Dùng chung toàn hệ thống</option>
                    <option value="__unassigned__">Chưa gắn phòng ban</option>
                  </select>
                  <span className="text-xs text-muted-foreground">Chỉ thay đổi cách quản lý, không giới hạn căn cứ tra cứu.</span>
                </label>
                <label className="space-y-1 text-sm">
                  <span className="font-medium">Vai trò phòng ban</span>
                  <select aria-label="Vai trò phòng ban" value={draftResponsibility} onChange={event => setDraftResponsibility(event.target.value as 'all' | 'primary' | 'support')} disabled={!draftOrganizationUnit || draftOrganizationUnit.startsWith('__')} className="h-10 w-full rounded-md border bg-background px-3">
                    <option value="all">Chủ trì hoặc phối hợp</option><option value="primary">Chủ trì</option><option value="support">Phối hợp</option>
                  </select>
                </label>
                <div className="flex flex-wrap items-end gap-2">
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
                    {(inventory?.items || []).map((document) => {
                      const servingLabel = servingStateLabel(
                        document.serving_state || document.validity_sync?.serving_action || document.serving_status,
                        document.current_answer_eligible,
                      )
                      return (
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
                          <div className="mt-2 flex flex-wrap gap-1">
                            {(document.organization_unit_ids || []).map((unitId) => (
                              <Badge key={unitId} variant="outline">
                                {organizationUnitNames.get(unitId) || 'Phòng ban đã đổi tên'}
                              </Badge>
                            ))}
                            {inventory?.organization_units_available === false && (
                              <Badge variant="secondary">Chưa tải thông tin phòng ban</Badge>
                            )}
                            {inventory?.organization_units_available !== false
                              && document.organization_assignment_state === 'shared' && (
                              <Badge variant="outline">Dùng chung toàn hệ thống</Badge>
                            )}
                            {inventory?.organization_units_available !== false
                              && document.organization_assignment_state !== 'shared'
                              && (!document.organization_unit_ids || document.organization_unit_ids.length === 0) && (
                              <Badge variant="secondary">Chưa gắn phòng ban</Badge>
                            )}
                            {document.organization_assignment_status === 'needs_confirmation' && (
                              <Badge variant="destructive">Cần xác nhận lại</Badge>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <div>{document.issuing_agency || 'Chưa rõ cơ quan'}</div>
                          <div className="mt-1 text-xs text-muted-foreground">{document.document_type || 'Chưa rõ loại'}</div>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <Badge variant={document.current_answer_eligible === false ? 'destructive' : document.validity_status === 'active' ? 'default' : 'secondary'}>
                            {document.validity_sync?.display_label || systemStatusLabel(document.validity_status || document.as_of_status || document.stored_status)}
                          </Badge>
                          {servingLabel && (
                            <div className="mt-1 text-xs font-medium text-destructive">
                              {servingLabel}
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
                      )
                    })}
                  </tbody>
                </table>
                {!loading && inventory?.items.length === 0 && (
                  <div className="p-10 text-center text-sm text-muted-foreground">Không có văn bản phù hợp</div>
                )}
                {loading && !inventory && (
                  <div className="p-10 text-center text-sm text-muted-foreground">Đang tải thông tin kho văn bản…</div>
                )}
              </div>

              <div className="flex flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <span className="text-muted-foreground">
                  {inventory?.total ?? '—'} hồ sơ trong kho
                  {cards?.total_retrievable != null ? ` · ${cards.total_retrievable.toLocaleString('vi-VN')} văn bản phục vụ tra cứu` : ''}
                  {` · Trang ${page}/${totalPages}`}
                  {loading && inventory ? ' · Đang làm mới dữ liệu đã lưu' : ''}
                </span>
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

          <Card>
            <CardHeader><CardTitle>Việc cần xử lý</CardTitle></CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Link href="/legal-management/validity" className="rounded-lg border p-3 text-sm hover:bg-muted/40">
                <div className="font-medium">Văn bản hết hiệu lực cần xử lý</div>
                <div className="mt-1 text-muted-foreground">{cards?.expired_total ?? 'Chưa xác định'}</div>
              </Link>
              <div className="rounded-lg border p-3 text-sm"><div className="font-medium">Chờ duyệt nguồn</div><div className="mt-1 text-muted-foreground">{summary?.operations.pending_document_candidates ?? 'Chưa xác định'}</div></div>
              <div className="rounded-lg border p-3 text-sm"><div className="font-medium">Nguồn quét hoặc nhập kho bị lỗi</div><div className="mt-1 text-muted-foreground">{summary?.operations.import_queue?.failed ?? 'Chưa xác định'}</div></div>
              <div className="rounded-lg border p-3 text-sm"><div className="font-medium">Nguồn thay thế cần xử lý</div><div className="mt-1 text-muted-foreground">Chưa xác định</div></div>
            </CardContent>
          </Card>

          <details className="rounded-xl border bg-background p-4">
            <summary className="cursor-pointer text-sm font-medium">Tình trạng kho tra cứu</summary>
            <div className="mt-3 space-y-2 text-xs text-muted-foreground">
              <div>Tra cứu hiện hành: {release?.current_collection ? 'Đã kết nối' : 'Chưa kết nối'}</div>
              <div>Tra cứu lịch sử: {release?.temporal_collection ? 'Đã kết nối' : 'Chưa kết nối'}</div>
              <div>Tìm chính xác số, ký hiệu: {release?.exact_lexical_index ? 'Đã sẵn sàng' : 'Chưa sẵn sàng'}</div>
              {vectorMessage && <div className="text-amber-700">{vectorMessage}</div>}
              <div>Kho dự phòng cũ chỉ được dùng khi cần khôi phục hệ thống.</div>
            </div>
          </details>
        </div>
      </div>
    </AppShell>
  )
}
