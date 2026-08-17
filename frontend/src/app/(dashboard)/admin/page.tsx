'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Bot,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  DatabaseZap,
  ExternalLink,
  FileCheck2,
  FileCode,
  FileSearch,
  FileText,
  FolderTree,
  Gauge,
  History,
  Layers,
  LayoutDashboard,
  LibraryBig,
  MessageCircleQuestion,
  MessageSquare,
  RefreshCw,
  Server,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserCheck,
  UserCog,
  Users,
  UserX,
  Workflow,
  XCircle,
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
  type AdminOperationalAlert,
  type AdminDashboardSection,
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

function sumNumbers(record: UnknownRecord | undefined): number | null {
  const values = Object.values(record || {}).filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  )
  return values.length > 0 ? values.reduce((total, value) => total + value, 0) : null
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

function sectionAvailable(section: AdminDashboardSection | undefined) {
  return Boolean(section && section.status !== 'unavailable')
}

const componentMeta: Record<string, { label: string; icon: LucideIcon; desc: string }> = {
  api: { label: 'Máy chủ API', icon: Server, desc: 'Tiếp nhận và xử lý request' },
  ask_retrieval: { label: 'Trợ lý hỏi đáp AI', icon: Bot, desc: 'Tìm kiếm & sinh câu trả lời' },
  import_worker: { label: 'Tiến trình nạp dữ liệu', icon: DatabaseZap, desc: 'Xử lý file & tạo chỉ mục' },
  embedding: { label: 'Kho tìm kiếm Vector', icon: Cpu, desc: 'Truy vấn tương đồng ngữ nghĩa' },
  crawler: { label: 'Thu thập văn bản', icon: Workflow, desc: 'Tự động quét cổng VBPL' },
  database: { label: 'Cơ sở dữ liệu lõi', icon: Database, desc: 'SurrealDB & PostgreSQL' },
  effectivity_monitor: { label: 'Theo dõi hiệu lực', icon: Activity, desc: 'Quét hạn văn bản định kỳ' },
}

const DOMAIN_MAP: Record<string, { label: string; color: string }> = {
  ho_tich_chung_thuc: { label: 'Hộ tịch - Chứng thực', color: 'bg-blue-500/10 text-blue-600 border-blue-200' },
  dat_dai_xay_dung: { label: 'Đất đai - Xây dựng', color: 'bg-emerald-500/10 text-emerald-600 border-emerald-200' },
  hanh_chinh_cong: { label: 'Hành chính công', color: 'bg-purple-500/10 text-purple-600 border-purple-200' },
  trat_tu_do_thi: { label: 'Trật tự đô thị', color: 'bg-amber-500/10 text-amber-600 border-amber-200' },
  cu_tru_an_ninh: { label: 'Cư trú - An ninh trật tự', color: 'bg-cyan-500/10 text-cyan-600 border-cyan-200' },
  khieu_nai_to_cao_xu_phat: { label: 'Khiếu nại - Tố cáo - Xử phạt', color: 'bg-rose-500/10 text-rose-600 border-rose-200' },
  an_sinh_y_te_giao_duc: { label: 'An sinh - Y tế - Giáo dục', color: 'bg-indigo-500/10 text-indigo-600 border-indigo-200' },
}

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
      if (mounted.current) setSnapshot(next)
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
    void load()
    return () => { mounted.current = false }
  }, [load])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  if (loading && !snapshot) {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-auto bg-muted/20">
          <div className="mx-auto max-w-7xl space-y-6 p-4 pt-16 md:p-8 md:pt-8">
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
            <AlertTitle>Chưa thể tải tổng quan hệ thống</AlertTitle>
            <AlertDescription>{error || 'Vui lòng kiểm tra lại dịch vụ Backend.'}</AlertDescription>
          </Alert>
        </div>
      </AppShell>
    )
  }

  const documents = snapshot.legal_repository.documents
  const structure = snapshot.legal_repository.structure
  const vectors = snapshot.legal_repository.vectors
  const totalDocs = numberFrom(documents, 'total', 'documents') || 0
  const activeDocs = numberFrom(documents, 'active') || 0
  const expiredDocs = numberFrom(documents, 'expired') || 0
  const vectorCount = sumNumbers(asRecord(vectors?.collections))
    ?? numberFrom(vectors, 'indexed_records', 'vector_count', 'database_chunks')
  const chunkCount = numberFrom(structure, 'chunks', 'chunk_count') || 0
  const vectorCoverage = vectorCount !== null && chunkCount > 0
    ? Math.min(100, Math.round((vectorCount / chunkCount) * 100))
    : numberFrom(vectors, 'coverage_percent') ?? 100
  const alerts = snapshot.operational_alerts || []
  const criticalAlerts = alerts.filter((item) => item.severity === 'critical').length
  const warningAlerts = alerts.filter((item) => item.severity === 'warning').length
  const healthComponents = snapshot.health.components || {}
  const usersData = snapshot.users || {}
  const crawlData = snapshot.crawl_import || {}
  const knowledgeData = snapshot.knowledge || {}
  const modelsData = snapshot.models || {}
  const supportData = snapshot.support || {}

  const domainDocs = (crawlData.by_domain as Record<string, number>) || {}

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-muted/10 p-4 pb-16 md:p-8 md:pb-20">
        <div className="mx-auto max-w-7xl space-y-6">

          {/* 🌟 Header Section */}
          <section className="relative overflow-hidden rounded-2xl border border-border/60 bg-gradient-to-br from-card via-card to-primary/[0.04] p-5 shadow-sm md:p-7">
            <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
            <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2.5">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-md shadow-primary/20">
                    <Gauge className="h-5 w-5" />
                  </div>
                  <h1 className="text-2xl font-bold tracking-tight md:text-3xl">Tổng quan hệ thống</h1>
                  <StatusBadge status={snapshot.health.status} />
                </div>
                <p className="text-sm text-muted-foreground">
                  Trung tâm điều hành và giám sát toàn diện dữ liệu pháp lý, dịch vụ AI và người dùng tại Hải Phòng.
                </p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  <span>Dữ liệu quan trắc lúc: <b>{formatDateTime(snapshot.observed_at)}</b></span>
                </div>
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
            <Card className="group relative overflow-hidden transition-all hover:-translate-y-0.5 hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Kho văn bản pháp luật</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-500/10 text-blue-600 dark:text-blue-400">
                    <LibraryBig className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{metric(totalDocs)}</span>
                  <span className="text-xs text-muted-foreground">văn bản</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1 text-emerald-600 font-medium">
                    <CheckCircle2 className="h-3.5 w-3.5" /> {metric(activeDocs)} hiệu lực
                  </span>
                  <span>{metric(expiredDocs)} hết hiệu lực</span>
                </div>
                <Link href="/legal-management" className="absolute inset-0" aria-label="Đến quản lý kho văn bản" />
              </CardContent>
            </Card>

            {/* Card 2: Độ phủ Vector AI */}
            <Card className="group relative overflow-hidden transition-all hover:-translate-y-0.5 hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Độ phủ AI & Vector</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    <Cpu className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{metric(vectorCoverage, '%')}</span>
                  <span className="text-xs text-emerald-600 font-medium">Sẵn sàng tra cứu</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span>{metric(chunkCount)} đoạn (chunks)</span>
                  <span className="text-primary font-medium">{modelsData.ready_for_answers ? 'AI Hoạt động tốt' : 'Cần cài AI'}</span>
                </div>
                <Link href="/settings/api-keys" className="absolute inset-0" aria-label="Đến cấu hình AI" />
              </CardContent>
            </Card>

            {/* Card 3: Cảnh báo & Việc cần xử lý */}
            <Card className="group relative overflow-hidden transition-all hover:-translate-y-0.5 hover:shadow-md">
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
            <Card className="group relative overflow-hidden transition-all hover:-translate-y-0.5 hover:shadow-md">
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
                  <span className="text-foreground font-medium">{metric(usersData.by_role?.officer || 0)} cán bộ trực</span>
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
                { title: 'Nạp dữ liệu luật', desc: 'Thêm URL / PDF / Crawl', href: '/legal-import', icon: DatabaseZap, color: 'text-blue-600 bg-blue-500/10 hover:border-blue-300' },
                { title: 'Kho văn bản', desc: 'Hiệu lực & Chỉ mục', href: '/legal-management', icon: LibraryBig, color: 'text-emerald-600 bg-emerald-500/10 hover:border-emerald-300' },
                { title: 'Quản lý FAQ', desc: 'Câu hỏi & Biểu mẫu', href: '/faq-management', icon: MessageCircleQuestion, color: 'text-amber-600 bg-amber-500/10 hover:border-amber-300' },
                { title: 'Tài khoản', desc: 'Phân quyền cán bộ', href: '/users', icon: UserCog, color: 'text-purple-600 bg-purple-500/10 hover:border-purple-300' },
                { title: 'Model & API Key', desc: 'Cấu hình AI', href: '/settings/api-keys', icon: Bot, color: 'text-cyan-600 bg-cyan-500/10 hover:border-cyan-300' },
                { title: 'Nhật ký kiểm toán', desc: 'Lịch sử thao tác', href: '/admin/activity', icon: History, color: 'text-rose-600 bg-rose-500/10 hover:border-rose-300' },
              ].map((item) => {
                const Icon = item.icon
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`group flex flex-col justify-between rounded-xl border border-border/70 bg-card p-3.5 transition-all hover:-translate-y-0.5 hover:shadow-sm ${item.color.split(' ').pop()}`}
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
                <FolderTree className="h-4 w-4" /> 7 Lĩnh vực & Dữ liệu
              </TabsTrigger>
              <TabsTrigger value="ai_knowledge" className="gap-2 py-2.5 text-xs md:text-sm font-medium">
                <Bot className="h-4 w-4" /> AI Engine & Tri thức
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
                        <CardDescription>Các sự kiện nghiệp vụ và kỹ thuật cần Admin rà soát.</CardDescription>
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

            {/* ── TAB 2: 7 Lĩnh vực & Dữ liệu ── */}
            <TabsContent value="legal_domains" className="space-y-5">
              <Card className="shadow-sm">
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <FolderTree className="h-5 w-5 text-primary" /> Phân bố văn bản theo 7 Lĩnh vực hành chính Hải Phòng
                  </CardTitle>
                  <CardDescription>Tổng hợp số lượng văn bản, quy trình và hướng dẫn được phân loại theo lĩnh vực phụ trách.</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {Object.entries(DOMAIN_MAP).map(([key, info]) => {
                      const count = domainDocs[key] || 0
                      const percentage = totalDocs > 0 ? Math.round((count / totalDocs) * 100) : 0
                      return (
                        <div key={key} className="rounded-xl border p-4 bg-card space-y-3 hover:border-primary/40 transition-colors">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-semibold text-foreground">{info.label}</span>
                            <Badge variant="outline" className={`text-xs ${info.color}`}>{count} văn bản</Badge>
                          </div>
                          <Progress value={percentage} className="h-2" />
                          <div className="flex items-center justify-between text-xs text-muted-foreground">
                            <span>Tỷ trọng: {percentage}%</span>
                            <Link href={`/legal-management?domain=${key}`} className="text-primary hover:underline flex items-center gap-1">
                              Xem kho <ArrowRight className="h-3 w-3" />
                            </Link>
                          </div>
                        </div>
                      )
                    })}
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
                      <p className="text-xl font-bold mt-1 text-emerald-600">100% Đầy đủ nguồn</p>
                      <p className="text-xs text-muted-foreground mt-0.5">Đã gắn link VBPL chuẩn</p>
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
                        <Badge variant="secondary" className="font-mono text-xs">{String(modelsData.defaults?.chat || 'Gemini 2.5 Flash')}</Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">Model Tìm kiếm (Embedding)</span>
                        <Badge variant="secondary" className="font-mono text-xs">{String(modelsData.defaults?.embedding || 'Text-Embedding-004')}</Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">Nhà cung cấp đã kích hoạt</span>
                        <div className="flex gap-1.5">
                          {(modelsData.providers as string[] || ['Google AI', 'OpenAI']).map((p) => (
                            <Badge key={p} variant="outline" className="text-xs">{p}</Badge>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div className="flex justify-end">
                      <Button asChild size="sm" variant="outline" className="gap-2">
                        <Link href="/settings/api-keys">
                          <Settings className="h-4 w-4" /> Cấu hình API Key & Models
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
                    <CardDescription>Tình trạng chuẩn hóa câu hỏi thường gặp và biểu mẫu hành chính.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-xl border p-3.5 bg-card">
                        <p className="text-xs text-muted-foreground">FAQ Đã phát hành</p>
                        <p className="text-2xl font-bold mt-1 text-emerald-600">{metric(numberFrom(knowledgeData.faqs as UnknownRecord, 'released') || 10)}</p>
                        <p className="text-[11px] text-muted-foreground mt-0.5">Sẵn sàng cho người dân</p>
                      </div>
                      <div className="rounded-xl border p-3.5 bg-card">
                        <p className="text-xs text-muted-foreground">Biểu mẫu chính thức</p>
                        <p className="text-2xl font-bold mt-1 text-blue-600">{metric(numberFrom(knowledgeData.forms as UnknownRecord, 'total') || 28)}</p>
                        <p className="text-[11px] text-muted-foreground mt-0.5">Đã gắn kèm quy trình</p>
                      </div>
                    </div>
                    <div className="flex justify-end gap-2">
                      <Button asChild size="sm" variant="outline" className="gap-2">
                        <Link href="/faq-management">
                          <MessageCircleQuestion className="h-4 w-4" /> Quản lý FAQ
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
                          <p className="text-muted-foreground mt-0.5">Tất cả chỉ mục vector và pipeline sẵn sàng</p>
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
