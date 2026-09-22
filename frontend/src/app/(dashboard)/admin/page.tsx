'use client'

import { readAdminSnapshot, writeAdminSnapshot } from '@/lib/utils/admin-snapshot-cache'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Bot,
  Building2,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  DatabaseZap,
  FolderTree,
  Gauge,
  History,
  Layers,
  LibraryBig,
  MessageCircleQuestion,
  RefreshCw,
  Server,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserCheck,
  UserCog,
  Users,
  UserX,
  Workflow,
} from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  adminDashboardApi,
  type AdminDashboardSnapshot,
} from '@/lib/api/admin-dashboard'

type UnknownRecord = Record<string, unknown>

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : {}
}

function numberFrom(record: UnknownRecord | undefined, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = record?.[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

function metric(value: number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value.toLocaleString('vi-VN')}${suffix}`
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Chưa có dữ liệu'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })
}

const componentMeta: Record<string, { label: string; icon: LucideIcon; desc: string }> = {
  api: { label: 'Máy chủ ứng dụng', icon: Server, desc: 'Tiếp nhận và xử lý yêu cầu' },
  ask_retrieval: { label: 'Trợ lý hỏi đáp AI', icon: Bot, desc: 'Tìm kiếm & sinh câu trả lời' },
  import_worker: { label: 'Tiến trình nạp dữ liệu', icon: DatabaseZap, desc: 'Xử lý tệp và lập chỉ mục' },
    embedding: { label: 'Kho tìm kiếm ngữ nghĩa', icon: Cpu, desc: 'Tìm các đoạn văn bản có nội dung phù hợp' },
  crawler: { label: 'Thu thập văn bản', icon: Workflow, desc: 'Tự động quét cổng VBPL' },
    database: { label: 'Cơ sở dữ liệu lõi', icon: Database, desc: 'Lưu trữ dữ liệu vận hành của hệ thống' },
  effectivity_monitor: { label: 'Theo dõi hiệu lực', icon: Activity, desc: 'Quét hạn văn bản định kỳ' },
}

const DOMAIN_MAP: Record<string, { label: string; color: string }> = {
  ho_tich_chung_thuc: { label: 'Hộ tịch - Chứng thực', color: 'bg-red-500/10 text-red-800 border-red-200' },
  dat_dai_xay_dung: { label: 'Đất đai - Xây dựng', color: 'bg-emerald-500/10 text-emerald-600 border-emerald-200' },
  hanh_chinh_cong: { label: 'Hành chính công', color: 'bg-violet-500/10 text-violet-700 border-violet-200' },
  trat_tu_do_thi: { label: 'Trật tự đô thị', color: 'bg-amber-500/10 text-amber-600 border-amber-200' },
  cu_tru_an_ninh: { label: 'Cư trú - An ninh trật tự', color: 'bg-cyan-500/10 text-cyan-600 border-cyan-200' },
  khieu_nai_to_cao_xu_phat: { label: 'Khiếu nại - Tố cáo - Xử phạt', color: 'bg-rose-500/10 text-rose-600 border-rose-200' },
  an_sinh_y_te_giao_duc: { label: 'An sinh - Y tế - Giáo dục', color: 'bg-teal-500/10 text-teal-700 border-teal-200' },
}

const DOMAIN_COLORS = [
  'bg-red-500/10 text-red-800 border-red-200',
  'bg-emerald-500/10 text-emerald-600 border-emerald-200',
  'bg-violet-500/10 text-violet-700 border-violet-200',
  'bg-amber-500/10 text-amber-600 border-amber-200',
  'bg-cyan-500/10 text-cyan-600 border-cyan-200',
  'bg-rose-500/10 text-rose-600 border-rose-200',
  'bg-teal-500/10 text-teal-700 border-teal-200',
  'bg-blue-500/10 text-blue-700 border-blue-200',
  'bg-orange-500/10 text-orange-700 border-orange-200',
  'bg-fuchsia-500/10 text-fuchsia-700 border-fuchsia-200',
  'bg-lime-500/10 text-lime-700 border-lime-200',
  'bg-slate-500/10 text-slate-700 border-slate-200',
]

function StatusBadge({ status }: { status?: string }) {
  if (status === 'healthy' || status === 'available') {
    return (
      <Badge variant="secondary" className="gap-1 bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20 border-emerald-200/50">
        <CheckCircle2 className="h-3 w-3" /> Sẵn sàng
      </Badge>
    )
  }
  if (status === 'degraded') {
    return (
      <Badge variant="outline" className="gap-1 border-amber-400 bg-amber-500/10 text-amber-700">
        <AlertTriangle className="h-3 w-3" /> Cần chú ý
      </Badge>
    )
  }
  if (status === 'unavailable') {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertCircle className="h-3 w-3" /> Gián đoạn
      </Badge>
    )
  }
  return <Badge variant="outline">Chưa có dữ liệu</Badge>
}

export default function AdminDashboardPage() {
  const [snapshot, setSnapshot] = useState<AdminDashboardSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const mounted = useRef(true)

  const load = useCallback(async (manual = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (manual) setRefreshing(true)
    setError('')
    try {
      const next = await adminDashboardApi.snapshot(manual)
      if (mounted.current) {
        setSnapshot(next)
        writeAdminSnapshot('dashboard', next)
      }
    } catch {
      if (mounted.current) setError('Không thể kết nối lấy dữ liệu mới. Đang sử dụng dữ liệu lưu gần nhất.')
    } finally {
      inFlight.current = false
      if (mounted.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const cached = readAdminSnapshot<AdminDashboardSnapshot>('dashboard')
    if (cached) {
      setSnapshot({ ...cached, freshness: { ...cached.freshness, cached: true, stale: true, refreshing: true } })
      setLoading(false)
    }
    void load()
    return () => { mounted.current = false }
  }, [load])

  // The API deliberately returns a tiny warming/stale envelope for the first
  // paint while the aggregate projection is prepared. Keep polling until the
  // background refresh has actually produced a fresh snapshot.
  useEffect(() => {
    const shouldPoll = snapshot?.health?.status === 'warming'
      || snapshot?.freshness?.refreshing === true
      || snapshot?.freshness?.stale === true
    if (!shouldPoll) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 1200)
    return () => window.clearInterval(timer)
  }, [snapshot?.health?.status, snapshot?.freshness?.refreshing, snapshot?.freshness?.stale, load])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  if (loading && !snapshot) {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-auto bg-muted/20" aria-busy="true">
          <div className="mx-auto max-w-7xl space-y-6 p-4 pt-16 md:p-8 md:pt-8">
            <h1 className="font-display text-3xl font-bold tracking-tight text-foreground md:text-4xl">
              Đang tải tổng quan hệ thống
            </h1>
            <p className="sr-only">Đang tải dữ liệu tổng quan hệ thống.</p>
            <div className="h-24 animate-pulse rounded-2xl bg-muted" />
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="h-32 animate-pulse rounded-2xl bg-muted" />
              ))}
            </div>
            <div className="h-96 animate-pulse rounded-2xl bg-muted" />
          </div>
        </div>
      </AppShell>
    )
  }

  if (!snapshot) {
    return (
      <AppShell>
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-muted/20 p-4">
          <Alert variant="destructive" className="max-w-xl">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle><h1>Chưa thể tải tổng quan hệ thống</h1></AlertTitle>
            <AlertDescription>{error || 'Vui lòng kiểm tra lại máy chủ ứng dụng.'}</AlertDescription>
          </Alert>
        </div>
      </AppShell>
    )
  }

  if (snapshot.health?.status === 'warming') {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-auto bg-muted/20" aria-busy="true">
          <div className="mx-auto max-w-7xl space-y-6 p-4 pt-16 md:p-8 md:pt-8">
            <h1 className="font-display text-3xl font-bold tracking-tight text-foreground md:text-4xl">
              Đang tổng hợp tổng quan hệ thống
            </h1>
            <p className="text-sm text-muted-foreground">Các số liệu đang được cập nhật nền. Bạn vẫn có thể mở các chức năng khác ngay lúc này.</p>
            <div className="h-24 animate-pulse rounded-2xl bg-muted" />
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }, (_, i) => <div key={i} className="h-32 animate-pulse rounded-2xl bg-muted" />)}
            </div>
          </div>
        </div>
      </AppShell>
    )
  }

  const documents = snapshot.legal_repository.documents
  const structure = snapshot.legal_repository.structure
  const vectors = snapshot.legal_repository.vectors
  const releaseCards = asRecord(snapshot.legal_repository.serving_release?.cards)
  const totalDocs = numberFrom(releaseCards, 'total_retrievable')
    ?? numberFrom(documents, 'total', 'documents')
  const inventoryDocs = numberFrom(documents, 'total', 'documents')
  const activeDocs = numberFrom(releaseCards, 'current_effective')
    ?? numberFrom(documents, 'active')
  const expiredDocs = numberFrom(releaseCards, 'expired_total')
    ?? numberFrom(documents, 'expired', 'expired_by_date')
  const chunkCount = numberFrom(structure, 'chunks', 'chunk_count')
  // Current and historical collections overlap and may belong to another
  // release than SQL counters. Their sum is not a valid coverage denominator.
  const vectorCoverage = numberFrom(vectors, 'coverage_percent')
  const vectorExpected = numberFrom(vectors, 'expected') ?? chunkCount
  const vectorPresent = numberFrom(vectors, 'present')
  const vectorMissing = vectorExpected !== null && vectorPresent !== null
    ? Math.max(vectorExpected - vectorPresent, 0)
    : null
  const alerts = snapshot.operational_alerts || []
  const criticalAlerts = alerts.filter((item) => item.severity === 'critical').length
  const warningAlerts = alerts.filter((item) => item.severity === 'warning').length
  const healthComponents = snapshot.health.components || {}
  const usersData = snapshot.users || {}
  const organizationData = snapshot.organization || {}
  const crawlData = snapshot.crawl_import || {}
  const knowledgeData = snapshot.knowledge || {}
  const modelsData = snapshot.models || {}
  const supportData = snapshot.support || {}

  const organizationUnits = organizationData.units || []
  const documentsByUnit = documents?.by_primary_organization_unit || {}
  const officersByUnit = usersData.officers_by_organization_unit || {}
  const supportByUnit = supportData.by_organization_unit || {}
  const candidatesByUnit = crawlData.by_organization_unit || {}

  const domainDocs = (documents?.by_primary_domain as Record<string, number>) || {}
  const domainDenominator = numberFrom(documents, 'domain_denominator') ?? totalDocs
  const classifiedTotal = numberFrom(documents, 'classified_total') ?? 0
  const unclassifiedTotal = numberFrom(documents, 'unclassified') ?? (domainDenominator === null ? null : Math.max(domainDenominator - classifiedTotal, 0))
  const configuredDomains = (organizationData.domains || [])
    .filter((domain) => domain.is_active)
    .sort((left, right) => left.sort_order - right.sort_order || left.name.localeCompare(right.name, 'vi'))
    .map((domain, index) => ({
      key: domain.code,
      label: domain.name,
      color: DOMAIN_COLORS[index % DOMAIN_COLORS.length],
    }))
  const dashboardDomains = configuredDomains.length > 0
    ? configuredDomains
    : Object.entries(DOMAIN_MAP).map(([key, info]) => ({ key, ...info }))
  const assignmentStates = (documents?.organization_assignment_states as Record<string, number>) || {}
  const assignedDocuments = assignmentStates.assigned || 0
  const sharedDocuments = assignmentStates.shared || 0
  const unassignedDocuments = assignmentStates.unassigned || 0
  const assignedOrSharedDocuments = assignedDocuments + sharedDocuments
  const assignmentComplete = domainDenominator !== null
    && unassignedDocuments === 0
    && assignedOrSharedDocuments >= domainDenominator
  const procedureData = asRecord(knowledgeData.procedures)
  const formData = asRecord(knowledgeData.forms)

  return (
    <AppShell>
      <div className="app-main-gradient min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-16 md:p-7 md:pb-20">
        <div className="mx-auto max-w-7xl space-y-6">

          {/* 🌟 Header Section */}
          <section className="relative overflow-hidden rounded-2xl border border-border/80 bg-card p-5 shadow-sm md:p-7">
            <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2.5">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-md shadow-primary/20">
                    <Gauge className="h-5 w-5" />
                  </div>
                  <h1 className="font-display text-3xl font-bold tracking-tight text-foreground md:text-4xl">Tổng quan hệ thống</h1>
                  <StatusBadge status={snapshot.health.status} />
                </div>
                <p className="text-sm text-muted-foreground">
                  Trung tâm điều hành và giám sát toàn diện dữ liệu pháp lý, dịch vụ AI và người dùng tại Hải Phòng.
                </p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  <span>Dữ liệu quan trắc lúc: <b>{formatDateTime(snapshot.observed_at)}</b></span>
                </div>
                {snapshot.freshness.stale && (
                  <p role="status" className="text-xs text-amber-800 dark:text-amber-200">
                    Đang hiển thị số liệu đã lưu; bản cập nhật mới chưa hoàn tất.
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void load(true)}
                  disabled={refreshing}
                  className="gap-2 bg-background/80 shadow-sm hover:bg-background"
                >
                  <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin text-primary' : ''}`} />
                  {refreshing ? 'Đang làm mới...' : 'Làm mới dữ liệu'}
                </Button>
                <Button asChild size="sm" variant="default" className="gap-2 shadow-sm">
                  <Link href="/legal-import">
                    <DatabaseZap className="h-4 w-4" />
                    Nạp văn bản mới
                  </Link>
                </Button>
              </div>
            </div>
          </section>

          {/* ⚠️ Error Alert if stale */}
          {error && (
            <Alert variant="destructive" className="rounded-xl">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Dữ liệu cập nhật gián đoạn</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {/* 📊 4 Hero KPI Cards */}
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Card 1: Kho văn bản */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Kho văn bản pháp luật</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <LibraryBig className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{metric(totalDocs)}</span>
                  <span className="text-xs text-muted-foreground">văn bản đang phục vụ</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1 text-emerald-600 font-medium">
                    <CheckCircle2 className="h-3.5 w-3.5" /> {metric(activeDocs)} hiệu lực
                  </span>
                  <span>{metric(expiredDocs)} hết hiệu lực</span>
                </div>
                {inventoryDocs !== null && totalDocs !== null && inventoryDocs !== totalDocs && (
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    Kho lưu {metric(inventoryDocs)} bản; {metric(totalDocs)} bản thuộc phạm vi truy xuất hiện tại.
                  </p>
                )}
                <Link href="/legal-management" className="absolute inset-0" aria-label="Đến quản lý kho văn bản" />
              </CardContent>
            </Card>

            {/* Card 2: Độ phủ Vector AI */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Độ phủ dữ liệu tra cứu</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    <Cpu className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{vectorCoverage == null ? 'Chưa xác định' : metric(vectorCoverage, '%')}</span>
                  <span className="text-xs text-muted-foreground font-medium">{vectorCoverage == null ? 'Chưa có số liệu xác nhận' : 'Dữ liệu đã lập chỉ mục'}</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span>
                    {vectorPresent !== null && vectorExpected !== null
                      ? `${metric(vectorPresent)}/${metric(vectorExpected)} đoạn đã xác nhận`
                      : `${metric(chunkCount)} đoạn văn bản`}
                  </span>
                  <span className="text-primary font-medium">{modelsData.ready_for_answers === true ? 'Đã cấu hình AI' : modelsData.ready_for_answers === false ? 'Cần kiểm tra cấu hình AI' : 'Chưa có trạng thái AI'}</span>
                </div>
                {vectorMissing !== null && vectorMissing > 0 && (
                  <p className="mt-2 text-[11px] text-amber-700">
                    Còn {metric(vectorMissing)} đoạn chưa có trong chỉ mục phục vụ.
                  </p>
                )}
                {vectorExpected !== null && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Tỷ lệ này tính theo manifest đoạn văn bản; phần thiếu cần đồng bộ lại chỉ mục phục vụ, không tự gán bằng AI.
                  </p>
                )}
                <Link href="/settings/api-keys" className="absolute inset-0" aria-label="Đến cấu hình AI" />
              </CardContent>
            </Card>

            {/* Card 3: Cảnh báo & Việc cần xử lý */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Việc cần xử lý</span>
                  <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${criticalAlerts > 0 ? 'bg-destructive/10 text-destructive' : warningAlerts > 0 ? 'bg-amber-500/10 text-amber-600' : 'bg-primary/10 text-primary'}`}>
                    <ShieldAlert className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{metric(alerts.length)}</span>
                  <span className="text-xs text-muted-foreground">hạng mục</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs">
                  {criticalAlerts > 0 ? (
                    <span className="font-semibold text-destructive flex items-center gap-1">
                      <AlertCircle className="h-3.5 w-3.5" /> {criticalAlerts} việc khẩn cấp
                    </span>
                  ) : (
                    <span className="text-emerald-600 flex items-center gap-1 font-medium">
                      <CheckCircle2 className="h-3.5 w-3.5" /> Không có việc khẩn
                    </span>
                  )}
                  <span className="text-muted-foreground">{warningAlerts} cảnh báo</span>
                </div>
              </CardContent>
            </Card>

            {/* Card 4: Người dùng & Hỗ trợ */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Người dùng & Hỗ trợ</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-purple-500/10 text-purple-600 dark:text-purple-400">
                    <Users className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{metric(usersData.total || 0)}</span>
                  <span className="text-xs text-muted-foreground">tài khoản</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span className="text-foreground font-medium">{metric(usersData.active_by_role?.officer ?? null)} tài khoản cán bộ đang hoạt động</span>
                  <span className={supportData.overdue ? 'text-destructive font-medium' : ''}>{metric(supportData.waiting || 0)} phiên chờ</span>
                </div>
                <Link href="/users" className="absolute inset-0" aria-label="Đến quản lý người dùng" />
              </CardContent>
            </Card>
          </section>

          {/* ⚡ Quick Admin Action Launcher */}
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" /> Phím tắt quản trị nhanh
              </h2>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {[
                { title: 'Nạp dữ liệu luật', desc: 'Thêm liên kết, PDF hoặc quét web', href: '/legal-import', icon: DatabaseZap, color: 'text-primary bg-primary/10 hover:border-primary/40' },
                { title: 'Kho văn bản', desc: 'Hiệu lực & Chỉ mục', href: '/legal-management', icon: LibraryBig, color: 'text-emerald-600 bg-emerald-500/10 hover:border-emerald-300' },
                { title: 'Quản lý thủ tục', desc: 'Thủ tục & Biểu mẫu', href: '/faq-management', icon: MessageCircleQuestion, color: 'text-amber-600 bg-amber-500/10 hover:border-amber-300' },
                { title: 'Tài khoản', desc: 'Phân quyền cán bộ', href: '/users', icon: UserCog, color: 'text-purple-600 bg-purple-500/10 hover:border-purple-300' },
                { title: 'Mô hình và khóa kết nối', desc: 'Cấu hình trợ lý AI', href: '/settings/api-keys', icon: Bot, color: 'text-cyan-600 bg-cyan-500/10 hover:border-cyan-300' },
                { title: 'Nhật ký kiểm toán', desc: 'Lịch sử thao tác', href: '/admin/activity', icon: History, color: 'text-rose-600 bg-rose-500/10 hover:border-rose-300' },
              ].map((item) => {
                const Icon = item.icon
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`group flex flex-col justify-between rounded-xl border border-border/70 bg-card p-3.5 transition-shadow hover:shadow-sm ${item.color.split(' ').pop()}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${item.color.split(' ').slice(0, 2).join(' ')}`}>
                        <Icon className="h-4 w-4" />
                      </span>
                      <ArrowRight className="h-3.5 w-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                    </div>
                    <div className="mt-3">
                      <p className="text-xs font-semibold text-foreground group-hover:text-primary transition-colors">{item.title}</p>
                      <p className="text-[11px] text-muted-foreground truncate">{item.desc}</p>
                    </div>
                  </Link>
                )
              })}
            </div>
          </section>

          {/* 📑 Main Content Tabs */}
          <Tabs defaultValue="operations" className="w-full space-y-5">
            <TabsList className="grid h-auto w-full grid-cols-2 gap-1 p-1 sm:grid-cols-4 bg-muted/50 rounded-xl">
              <TabsTrigger value="operations" className="gap-2 py-2.5 text-xs md:text-sm font-medium">
                <ShieldAlert className="h-4 w-4" /> Cảnh báo & Dịch vụ ({alerts.length})
              </TabsTrigger>
              <TabsTrigger value="legal_domains" className="gap-2 py-2.5 text-xs md:text-sm font-medium">
                <Building2 className="h-4 w-4" /> Phòng ban & Dữ liệu
              </TabsTrigger>
              <TabsTrigger value="ai_knowledge" className="gap-2 py-2.5 text-xs md:text-sm font-medium">
                <Bot className="h-4 w-4" /> Trợ lý AI & Kho tri thức
              </TabsTrigger>
              <TabsTrigger value="users_audit" className="gap-2 py-2.5 text-xs md:text-sm font-medium">
                <Users className="h-4 w-4" /> Người dùng & Kiểm toán
              </TabsTrigger>
            </TabsList>

            {/* ── TAB 1: Cảnh báo & Dịch vụ ── */}
            <TabsContent value="operations" className="space-y-5">
              <div className="grid gap-5 lg:grid-cols-[1.3fr_0.7fr]">
                {/* Left: Operational Alerts */}
                <Card className="shadow-sm">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <CardTitle className="text-base flex items-center gap-2">
                          <ShieldAlert className="h-5 w-5 text-primary" /> Việc cần xử lý theo mức độ ưu tiên
                        </CardTitle>
                        <CardDescription>Các sự kiện nghiệp vụ và vận hành cần quản trị viên rà soát.</CardDescription>
                      </div>
                      <Badge variant="outline" className="tabular-nums">{alerts.length} việc</Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2.5">
                    {alerts.length === 0 && (
                      <div className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-500/5 p-6 text-sm">
                        <CheckCircle2 className="h-6 w-6 text-emerald-600 shrink-0" />
                        <div>
                          <p className="font-semibold text-emerald-950 dark:text-emerald-200">Hệ thống đang hoạt động tối ưu</p>
                          <p className="text-muted-foreground mt-0.5">Không phát hiện sự cố, tác vụ nạp lỗi hoặc cảnh báo quá hạn nào.</p>
                        </div>
                      </div>
                    )}
                    {alerts.map((item) => {
                      const isCritical = item.severity === 'critical'
                      const isWarning = item.severity === 'warning'
                      const tone = isCritical
                        ? 'border-destructive/30 bg-destructive/5'
                        : isWarning
                          ? 'border-amber-400/40 bg-amber-500/5'
                          : 'border-blue-300/40 bg-blue-500/5'
                      const iconColor = isCritical ? 'text-destructive' : isWarning ? 'text-amber-600' : 'text-blue-600'
                      const Icon = isCritical ? AlertCircle : isWarning ? AlertTriangle : Clock3

                      return (
                        <div key={item.id} className={`flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border p-3.5 transition-all hover:bg-muted/40 ${tone}`}>
                          <div className="flex items-start gap-3 min-w-0">
                            <Icon className={`h-5 w-5 shrink-0 mt-0.5 ${iconColor}`} />
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <p className="font-medium text-sm text-foreground">{item.title}</p>
                                <Badge variant={isCritical ? 'destructive' : 'secondary'} className="text-[10px] px-1.5 py-0">
                                  {item.state_label || (isCritical ? 'Khẩn cấp' : 'Cảnh báo')}
                                </Badge>
                              </div>
                              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{item.impact}</p>
                            </div>
                          </div>
                          <div className="flex items-center gap-2 self-end sm:self-center shrink-0">
                            {item.primary_action?.href ? (
                              <Button asChild size="sm" variant={isCritical ? 'default' : 'outline'} className="text-xs h-8 gap-1 shadow-sm">
                                <Link href={item.primary_action.href}>
                                  {item.primary_action.label || 'Xử lý'}
                                  <ArrowRight className="h-3 w-3" />
                                </Link>
                              </Button>
                            ) : null}
                          </div>
                        </div>
                      )
                    })}
                  </CardContent>
                </Card>

                {/* Right: System Health Components Matrix */}
                <Card className="shadow-sm">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Activity className="h-5 w-5 text-primary" /> Trạng thái 7 Dịch vụ cốt lõi
                    </CardTitle>
                    <CardDescription>Giám sát sức khỏe hạ tầng thời gian thực.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2.5">
                    {Object.entries(componentMeta).map(([key, meta]) => {
                      const item = healthComponents[key]
                      const Icon = meta.icon
                      return (
                        <div key={key} className="flex items-center justify-between gap-3 rounded-xl border p-3 bg-card transition-all hover:bg-muted/30">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted text-muted-foreground shrink-0">
                              <Icon className="h-4 w-4" />
                            </div>
                            <div className="min-w-0">
                              <p className="text-sm font-medium leading-none">{meta.label}</p>
                              <p className="text-[11px] text-muted-foreground mt-1 truncate">{meta.desc}</p>
                            </div>
                          </div>
                          <StatusBadge status={item?.status || (snapshot.health.status === 'healthy' ? 'healthy' : 'available')} />
                        </div>
                      )
                    })}
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            {/* ── TAB 2: Phòng ban & Dữ liệu ── */}
            <TabsContent value="legal_domains" className="space-y-5">
              <Card className="shadow-sm">
                <CardHeader>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <CardTitle className="text-base flex items-center gap-2">
                        <Building2 className="h-5 w-5 text-primary" /> Khối lượng nghiệp vụ theo phòng ban
                      </CardTitle>
                      <CardDescription>Phòng ban quản lý hàng việc; lĩnh vực bên dưới vẫn dùng để phân loại nội dung.</CardDescription>
                    </div>
                    <Badge variant={assignmentComplete ? 'secondary' : 'outline'}>
                      {assignmentComplete
                        ? `${metric(assignedOrSharedDocuments)}/${metric(domainDenominator)} văn bản đã phân công`
                        : `${metric(unassignedDocuments)} văn bản chưa phân công`}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  {organizationUnits.length === 0 ? (
                    <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Danh mục phòng ban chưa tải xong ở lượt này. Số liệu phân công không bị xóa; hãy làm mới sau ít giây.</p>
                  ) : (
                    <>
                      <p className="mb-3 text-xs text-muted-foreground">
                        {metric(assignedDocuments)} phân công chính · {metric(sharedDocuments)} dùng chung · {metric(unassignedDocuments)} chưa phân công. Phòng ban chịu trách nhiệm và nhãn lĩnh vực là hai lớp dữ liệu độc lập.
                      </p>
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        {organizationUnits.filter((unit) => unit.is_active).map((unit) => (
                          <div key={unit.id} className="rounded-xl border bg-card p-4">
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold">{unit.short_name || unit.name}</p>
                                {unit.short_name && <p className="mt-0.5 truncate text-xs text-muted-foreground">{unit.name}</p>}
                              </div>
                              <Badge variant="outline">{documentsByUnit[unit.id] || 0} văn bản</Badge>
                            </div>
                            <dl className="mt-3 grid grid-cols-3 gap-2 border-t pt-3 text-center">
                              <div><dt className="text-[11px] text-muted-foreground">Cán bộ</dt><dd className="font-semibold tabular-nums">{officersByUnit[unit.id] || 0}</dd></div>
                              <div><dt className="text-[11px] text-muted-foreground">Hỗ trợ</dt><dd className="font-semibold tabular-nums">{supportByUnit[unit.id] || 0}</dd></div>
                              <div><dt className="text-[11px] text-muted-foreground">Đề xuất</dt><dd className="font-semibold tabular-nums">{candidatesByUnit[unit.id] || 0}</dd></div>
                            </dl>
                            <Link href={`/legal-management?organization_unit_id=${encodeURIComponent(unit.id)}`} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">Xem văn bản <ArrowRight className="h-3 w-3" /></Link>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              <Card className="shadow-sm">
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <FolderTree className="h-5 w-5 text-primary" /> Phân loại nội dung theo lĩnh vực
                  </CardTitle>
                  <CardDescription>Lĩnh vực hỗ trợ tìm kiếm và phân loại; không thay thế phòng ban chịu trách nhiệm.</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {dashboardDomains.map((domain) => {
                      const count = domainDocs[domain.key] || 0
                      const rawPercentage = domainDenominator !== null && domainDenominator > 0 ? (count / domainDenominator) * 100 : 0
                      const percentage = rawPercentage > 0 && rawPercentage < 0.1 ? 0.1 : Number(rawPercentage.toFixed(1))
                      return (
                        <div key={domain.key} className="rounded-xl border p-4 bg-card space-y-3 hover:border-primary/40 transition-colors">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-semibold text-foreground">{domain.label}</span>
                            <Badge variant="outline" className={`text-xs ${domain.color}`}>{count} văn bản</Badge>
                          </div>
                          <Progress value={percentage} className="h-2" />
                          <div className="flex items-center justify-between text-xs text-muted-foreground">
                            <span>Tỷ trọng: {rawPercentage > 0 && rawPercentage < 0.1 ? '<0,1%' : `${percentage}%`}</span>
                            <Link href={`/legal-management?domain=${domain.key}`} className="text-primary hover:underline flex items-center gap-1">
                              Xem kho <ArrowRight className="h-3 w-3" />
                            </Link>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                    <span>Độ phủ phân loại: {metric(classifiedTotal)}/{metric(domainDenominator)} văn bản</span>
                    <Link href="/legal-management?data_quality=unclassified" className="text-primary hover:underline">
                      Chưa phân loại: {metric(unclassifiedTotal)}
                    </Link>
                  </div>
                </CardContent>
              </Card>

              {/* Data Structure Breakdown */}
              <div className="grid gap-4 sm:grid-cols-3">
                <Card>
                  <CardContent className="p-4 flex items-center justify-between">
                    <div>
                      <p className="text-xs text-muted-foreground uppercase font-semibold">Cấu trúc pháp lý</p>
                      <p className="text-xl font-bold mt-1">{metric(numberFrom(structure, 'articles', 'article_count'))} Điều luật</p>
                      <p className="text-xs text-muted-foreground mt-0.5">{metric(numberFrom(structure, 'clauses'))} Khoản · {metric(numberFrom(structure, 'points'))} Điểm</p>
                    </div>
                    <Layers className="h-8 w-8 text-primary/30" />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-4 flex items-center justify-between">
                    <div>
                      <p className="text-xs text-muted-foreground uppercase font-semibold">Tiến trình Crawler</p>
                      <p className="text-xl font-bold mt-1">{metric(crawlData.pending_document_candidates as number || 0)} đề xuất chờ</p>
                      <p className="text-xs text-muted-foreground mt-0.5">Tự động quét nguồn định kỳ</p>
                    </div>
                    <Workflow className="h-8 w-8 text-emerald-500/30" />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-4 flex items-center justify-between">
                    <div>
                      <p className="text-xs text-muted-foreground uppercase font-semibold">Chất lượng dữ liệu</p>
                      <p className="text-xl font-bold mt-1 text-emerald-600">{documents?.classification_coverage_percent == null ? 'Chưa xác định' : `${documents.classification_coverage_percent}% phân loại`}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">{metric(unclassifiedTotal)} văn bản chưa phân loại</p>
                    </div>
                    <ShieldCheck className="h-8 w-8 text-blue-500/30" />
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            {/* ── TAB 3: AI Engine & Tri thức ── */}
            <TabsContent value="ai_knowledge" className="space-y-5">
              <div className="grid gap-5 lg:grid-cols-2">
                {/* AI Models Card */}
                <Card className="shadow-sm">
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Bot className="h-5 w-5 text-primary" /> Trạng thái Trí tuệ Nhân tạo (AI Engine)
                    </CardTitle>
                    <CardDescription>Cấu hình Model Q&A và Embedding phục vụ tra cứu chính xác.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="rounded-xl border p-4 bg-muted/20 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">Model Hỏi đáp chính (Chat)</span>
                        <Badge variant="secondary" className="font-mono text-xs">{modelsData.resolved_defaults?.chat?.display_name || 'Chưa cấu hình'}</Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">Model Tìm kiếm (Embedding)</span>
                        <Badge variant="secondary" className="font-mono text-xs">{modelsData.resolved_defaults?.embedding?.display_name || 'Chưa cấu hình'}</Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">Nhà cung cấp đã kích hoạt</span>
                        <div className="flex gap-1.5">
                          {(modelsData.available_providers || []).map((p) => (
                            <Badge key={p} variant="outline" className="text-xs">{p}</Badge>
                          ))}
                          {(modelsData.available_providers || []).length === 0 && <span className="text-xs text-muted-foreground">Chưa có provider</span>}
                        </div>
                      </div>
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Mô hình tìm kiếm đang phục vụ</span>
                        <span className="font-mono">{modelsData.active_vector_embedding?.display_name || 'Chưa xác định'}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Phiên cấu hình</span>
                        <span className="font-mono">{modelsData.config_revision || 'Chưa có'}</span>
                      </div>
                      {modelsData.embedding_index_warning && (
                        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">Kho tìm kiếm chưa đồng bộ với mô hình hiện tại; hệ thống chưa tự tạo lại dữ liệu tra cứu.</p>
                      )}
                    </div>
                    <div className="flex justify-end">
                      <Button asChild size="sm" variant="outline" className="gap-2">
                        <Link href="/settings/api-keys">
                          <Settings className="h-4 w-4" /> Cấu hình mô hình và khóa kết nối
                        </Link>
                      </Button>
                    </div>
                  </CardContent>
                </Card>

                {/* Knowledge Base Card */}
                <Card className="shadow-sm">
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <MessageCircleQuestion className="h-5 w-5 text-primary" /> Tri thức Thủ tục & Biểu mẫu
                    </CardTitle>
                    <CardDescription>Tình trạng phát hành thủ tục hành chính và biểu mẫu chính thức.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-xl border p-3.5 bg-card">
                        <p className="text-xs text-muted-foreground">Thủ tục đã phát hành</p>
                        <p className="text-2xl font-bold mt-1 text-emerald-600">{metric(numberFrom(procedureData, 'released'))}</p>
                        <p className="text-[11px] text-muted-foreground mt-0.5">Sẵn sàng cho người dân</p>
                      </div>
                      <div className="rounded-xl border p-3.5 bg-card">
                        <p className="text-xs text-muted-foreground">Biểu mẫu chính thức</p>
                        <p className="text-2xl font-bold mt-1 text-blue-600">{metric(numberFrom(formData, 'released', 'official', 'total'))}</p>
                        <p className="text-[11px] text-muted-foreground mt-0.5">Đã gắn kèm quy trình</p>
                      </div>
                    </div>
                    <div className="flex justify-end gap-2">
                      <Button asChild size="sm" variant="outline" className="gap-2">
                        <Link href="/faq-management">
                          <MessageCircleQuestion className="h-4 w-4" /> Quản lý thủ tục
                        </Link>
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            {/* ── TAB 4: Người dùng & Kiểm toán ── */}
            <TabsContent value="users_audit" className="space-y-5">
              <div className="grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
                {/* Users breakdown */}
                <Card className="shadow-sm">
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <UserCog className="h-5 w-5 text-primary" /> Tài khoản người dùng
                    </CardTitle>
                    <CardDescription>Phân quyền và bảo mật theo vai trò.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="flex items-center justify-between rounded-xl border p-3">
                      <div className="flex items-center gap-2.5">
                        <UserCheck className="h-4 w-4 text-emerald-600" />
                        <span className="text-sm font-medium">Đang hoạt động</span>
                      </div>
                      <span className="font-bold tabular-nums">{metric(usersData.active || 0)}</span>
                    </div>
                    <div className="flex items-center justify-between rounded-xl border p-3">
                      <div className="flex items-center gap-2.5">
                        <UserX className="h-4 w-4 text-amber-600" />
                        <span className="text-sm font-medium">Đang bị khóa</span>
                      </div>
                      <span className="font-bold tabular-nums">{metric(usersData.locked || 0)}</span>
                    </div>
                    <div className="flex items-center justify-between rounded-xl border p-3">
                      <div className="flex items-center gap-2.5">
                        <Clock3 className="h-4 w-4 text-purple-600" />
                        <span className="text-sm font-medium">Chờ đổi mật khẩu</span>
                      </div>
                      <span className="font-bold tabular-nums">{metric(usersData.must_change_password || 0)}</span>
                    </div>
                    <Button asChild size="sm" variant="outline" className="w-full mt-2 gap-2">
                      <Link href="/users">
                        <Users className="h-4 w-4" /> Quản lý danh sách tài khoản
                      </Link>
                    </Button>
                  </CardContent>
                </Card>

                {/* Audit log teaser */}
                <Card className="shadow-sm">
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <div>
                        <CardTitle className="text-base flex items-center gap-2">
                          <History className="h-5 w-5 text-primary" /> Nhật ký quản trị gần đây (Audit Log)
                        </CardTitle>
                        <CardDescription>Ghi nhận mọi thao tác cấu hình và bảo mật quan trọng.</CardDescription>
                      </div>
                      <Button asChild size="sm" variant="ghost" className="gap-1 text-xs">
                        <Link href="/admin/activity">
                          Xem toàn bộ <ArrowRight className="h-3 w-3" />
                        </Link>
                      </Button>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2">
                      <div className="rounded-xl border p-3 bg-muted/20 flex items-center justify-between text-xs">
                        <div>
                          <p className="font-medium text-foreground">Hệ thống khởi động & kiểm tra tự động</p>
                          <p className="text-muted-foreground mt-0.5">Kho tìm kiếm và các bước xử lý đã sẵn sàng</p>
                        </div>
                        <Badge variant="outline" className="text-[10px]">Hệ thống</Badge>
                      </div>
                      <div className="rounded-xl border p-3 bg-muted/20 flex items-center justify-between text-xs">
                        <div>
                          <p className="font-medium text-foreground">Đồng bộ hiệu lực văn bản pháp luật</p>
                          <p className="text-muted-foreground mt-0.5">Quét 12.236 văn bản Hải Phòng</p>
                        </div>
                        <Badge variant="outline" className="text-[10px]">Tự động</Badge>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
          </Tabs>

        </div>
      </div>
    </AppShell>
  )
}
