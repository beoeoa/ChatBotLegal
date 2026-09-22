'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  ArrowRight,
  Book,
  BookOpen,
  FilePlus2,
  FileSearch,
  FileText,
  FolderTree,
  Headphones,
  MessageCircleQuestion,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  User,
} from 'lucide-react'

import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { useOperatingScope } from '@/lib/hooks/use-operating-scope'
import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface ProposalCandidate {
  id: string
  title?: string
  law_number?: string
  domain?: string
  source_type?: string
  status?: string
  review_status?: string
  proposal_reason?: string
  review_note?: string
  requested_changes_note?: string
  created_at?: string
}

interface NotebookItem {
  id: string
  name: string
  description?: string
  source_count?: number
  updated_at?: string
}

export default function OfficerDashboardPage() {
  const { username } = useAuthStore()
  const { scope: operatingScope, error: scopeError } = useOperatingScope()
  const [proposalSummary, setProposalSummary] = useState<{ total: number; by_status: Record<string, number> } | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [proposals, setProposals] = useState<ProposalCandidate[]>([])
  const [notebooks, setNotebooks] = useState<NotebookItem[]>([])
  const [faqCount, setFaqCount] = useState<number | null>(null)
  const [notebookCount, setNotebookCount] = useState<number | null>(null)
  const [loadErrors, setLoadErrors] = useState<string[]>([])
  const inFlight = useRef(false)
  const domainLabels = operatingScope?.domain_labels || {}
  const domainLabel = (code?: string) =>
    (code && domainLabels[code]) || code || 'Lĩnh vực khác'

  const loadData = useCallback(async (manual = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (manual) setRefreshing(true)
    try {
      const [proposalsRes, notebooksRes, faqRes, proposalCountsRes] = await Promise.allSettled([
        apiClient.get<{ candidates?: ProposalCandidate[] }>('/legal/proposals/candidates'),
        apiClient.get<NotebookItem[] | { items?: NotebookItem[] }>('/notebooks'),
        apiClient.get<{ total?: number }>('/faq?limit=1'),
        apiClient.get<{ total: number; by_status: Record<string, number> }>('/legal/proposals/summary'),
      ])

      if (proposalsRes.status === 'fulfilled') {
        setProposals(proposalsRes.value.data?.candidates || [])
      }
      if (proposalCountsRes.status === 'fulfilled') setProposalSummary(proposalCountsRes.value.data)
      if (notebooksRes.status === 'fulfilled') {
        const raw = notebooksRes.value.data
        const items = Array.isArray(raw) ? raw : raw?.items || []
        setNotebooks(items.slice(0, 5))
        setNotebookCount(items.length)
      }
      if (faqRes.status === 'fulfilled') {
        setFaqCount(faqRes.value.data?.total ?? null)
      }
      setLoadErrors([
        ...(proposalsRes.status === 'rejected' || proposalCountsRes.status === 'rejected' ? ['đề xuất văn bản'] : []),
        ...(notebooksRes.status === 'rejected' ? ['hồ sơ pháp lý'] : []),
        ...(faqRes.status === 'rejected' ? ['câu hỏi thường gặp'] : []),
      ])
    } catch {
      setLoadErrors(['số liệu tổng quan'])
    } finally {
      inFlight.current = false
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const pendingProposals = proposalSummary?.by_status.pending || 0
  const changesRequestedProposals = proposalSummary?.by_status.changes_requested || 0
  const approvedProposals = ['approved', 'import_queued', 'imported'].reduce((sum, status) => sum + (proposalSummary?.by_status[status] || 0), 0)

  return (
    <AppShell>
      <div aria-busy={loading || refreshing} className="app-main-gradient min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-16 md:p-7 md:pb-20">
        <div className="mx-auto max-w-7xl space-y-6">

          {/* 🌟 Officer Welcome Hero Banner */}
          <section className="relative overflow-hidden rounded-2xl border border-border/80 bg-card p-5 shadow-sm md:p-7">
            <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2.5">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-md shadow-primary/20">
                    <User className="h-5 w-5" />
                  </div>
                  <div>
                    <h1 className="font-display text-3xl font-bold tracking-tight text-foreground md:text-4xl">
                      Xin chào, {username || 'Cán bộ nghiệp vụ'}!
                    </h1>
                  </div>
                  <Badge variant="secondary" className="bg-primary/10 text-primary border-primary/20 font-medium">
                    Cán bộ nghiệp vụ
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  Bàn làm việc nghiệp vụ pháp lý theo phòng ban được phân công.
                </p>
                <p role="status" className="text-sm text-muted-foreground">{scopeError || (operatingScope
                  ? `Phòng ban chính: ${operatingScope.primary_organization_unit_name || 'Chưa phân công'}. ${operatingScope.can_manage_content ? 'Quyền nghiệp vụ được xác nhận theo phòng ban và hỗ trợ còn hạn.' : 'Chưa có quyền nhận việc; vẫn được tra cứu pháp luật dùng chung.'}`
                  : 'Đang xác nhận phạm vi phòng ban…')}</p>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                    Theo dõi công việc của bạn
                  </span>
                  <span>·</span>
                  <span>Tra cứu tài liệu và tiếp nhận yêu cầu hỗ trợ</span>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void loadData(true)}
                  disabled={loading || refreshing}
                  className="gap-2 bg-background/80 shadow-sm hover:bg-background"
                >
                  <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin text-primary' : ''}`} />
                  {refreshing ? 'Đang cập nhật...' : 'Làm mới'}
                </Button>
                <Button asChild size="sm" variant="default" className="gap-2 shadow-sm">
                  <Link href="/search">
                    <Sparkles className="h-4 w-4" />
                    Hỏi đáp AI ngay
                  </Link>
                </Button>
              </div>
            </div>
          </section>

          {loadErrors.length > 0 && (
            <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:bg-amber-950/30 dark:text-amber-100">
              Chưa tải được {loadErrors.join(', ')}. Số liệu đã tải trước đó được giữ lại; hãy bấm Làm mới để thử lại.
            </div>
          )}

          {/* 📊 4 KPI Metric Cards */}
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Card 1: Hồ sơ pháp lý */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Hồ sơ pháp lý</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Book className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{notebookCount ?? '—'}</span>
                  <span className="text-xs text-muted-foreground">hồ sơ được truy cập</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span>Nghiên cứu & Ghi chú</span>
                  <span className="text-primary font-medium flex items-center gap-0.5">
                    Xem danh sách <ArrowRight className="h-3 w-3" />
                  </span>
                </div>
                <Link href="/notebooks" className="absolute inset-0" aria-label="Đến Hồ sơ pháp lý" />
              </CardContent>
            </Card>

            {/* Card 2: Đề xuất văn bản */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Đề xuất văn bản</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    <FileSearch className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{loading || loadErrors.includes('đề xuất văn bản') ? '—' : proposalSummary?.total ?? '—'}</span>
                  <span className="text-xs text-muted-foreground">tổng đề xuất</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs">
                  {changesRequestedProposals > 0 ? (
                    <span className="font-semibold text-amber-600 flex items-center gap-1">
                      <ShieldAlert className="h-3.5 w-3.5" /> {changesRequestedProposals} cần bổ sung
                    </span>
                  ) : (
                    <span className="text-emerald-600 font-medium">{approvedProposals} đã duyệt</span>
                  )}
                  <span className="text-muted-foreground">{pendingProposals} chờ duyệt</span>
                </div>
                <Link href="/officer-proposals" className="absolute inset-0" aria-label="Đến Đề xuất văn bản" />
              </CardContent>
            </Card>

            {/* Card 3: Hỗ trợ người dân */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Hỗ trợ công dân</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-purple-500/10 text-purple-600 dark:text-purple-400">
                    <Headphones className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">Tiếp nhận</span>
                  <span className="text-xs text-muted-foreground">yêu cầu hỗ trợ</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span>Hàng chờ phòng ban</span>
                  <span className="text-primary font-medium flex items-center gap-0.5">
                    Vào ca trực <ArrowRight className="h-3 w-3" />
                  </span>
                </div>
                <Link href="/live-support" className="absolute inset-0" aria-label="Đến Hỗ trợ trực tuyến" />
              </CardContent>
            </Card>

            {/* Card 4: Kho thủ tục & FAQ */}
            <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Câu hỏi thường gặp</span>
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
                    <FileText className="h-5 w-5" />
                  </span>
                </div>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-bold tracking-tight tabular-nums text-foreground">{faqCount ?? '—'}</span>
                  <span className="text-xs text-muted-foreground">hướng dẫn đã công bố</span>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-2.5 text-xs text-muted-foreground">
                  <span>Tra cứu theo nhu cầu</span>
                  <span className="text-primary font-medium flex items-center gap-0.5">
                    Tra cứu ngay <ArrowRight className="h-3 w-3" />
                  </span>
                </div>
                <Link href="/procedures" className="absolute inset-0" aria-label="Đến Thủ tục hành chính" />
              </CardContent>
            </Card>
          </section>

          {/* ⚡ Officer Action Launcher */}
          <section className="space-y-2.5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" /> Lối tắt tác vụ nghiệp vụ
              </h2>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {[
                { title: 'Hỏi đáp pháp luật', desc: 'Trợ lý AI tra cứu nhanh', href: '/search', icon: MessageCircleQuestion, color: 'text-primary bg-primary/10 hover:border-primary/40' },
                { title: 'Đề xuất văn bản', desc: 'Gửi đường dẫn hoặc tệp cho quản trị viên', href: '/officer-proposals', icon: FilePlus2, color: 'text-emerald-600 bg-emerald-500/10 hover:border-emerald-300' },
                { title: 'Hỗ trợ công dân', desc: 'Tiếp nhận phiên chat', href: '/live-support', icon: Headphones, color: 'text-purple-600 bg-purple-500/10 hover:border-purple-300' },
                { title: 'Hồ sơ pháp lý', desc: 'Ghi chú & Quản lý vụ việc', href: '/notebooks', icon: Book, color: 'text-amber-600 bg-amber-500/10 hover:border-amber-300' },
                { title: 'Thủ tục hành chính', desc: 'Các bước & Biểu mẫu', href: '/procedures', icon: FileSearch, color: 'text-cyan-600 bg-cyan-500/10 hover:border-cyan-300' },
                { title: 'Nguồn tài liệu', desc: 'Văn bản đính kèm', href: '/sources', icon: FileText, color: 'text-rose-600 bg-rose-500/10 hover:border-rose-300' },
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

          {/* 📑 Main 2-Column Work Center */}
          <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">

            {/* ── Left Column: Đề xuất & Hồ sơ ── */}
            <div className="space-y-6">
              {/* Proposals Tracking Card */}
              <Card className="shadow-sm">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle className="text-base flex items-center gap-2">
                        <FileSearch className="h-5 w-5 text-primary" /> Tiến độ đề xuất văn bản của bạn
                      </CardTitle>
                      <CardDescription>Theo dõi trạng thái quản trị viên kiểm tra và duyệt nguồn.</CardDescription>
                    </div>
                    <Button asChild size="sm" variant="outline" className="gap-1 text-xs">
                      <Link href="/officer-proposals">
                        <Plus className="h-3.5 w-3.5" /> Tạo đề xuất
                      </Link>
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {proposals.length === 0 && !loading && !loadErrors.length && (
                    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center">
                      <FileSearch className="h-8 w-8 text-muted-foreground/50 mb-2" />
                      <p className="text-sm font-medium text-foreground">Bạn chưa gửi đề xuất văn bản nào</p>
                      <p className="text-xs text-muted-foreground mt-1 max-w-sm">
                        Khi phát hiện văn bản mới hoặc biểu mẫu thay đổi, hãy gửi đề xuất để quản trị viên phê duyệt vào kho chung.
                      </p>
                      <Button asChild size="sm" className="mt-4 gap-1">
                        <Link href="/officer-proposals">
                          <Plus className="h-3.5 w-3.5" /> Gửi đề xuất đầu tiên
                        </Link>
                      </Button>
                    </div>
                  )}

                  {proposals.slice(0, 5).map((item) => {
                    const status = item.review_status || item.status || 'pending'
                    const isNeedsChange = status === 'changes_requested'
                    const isApproved = status === 'approved' || status === 'imported'
                    const feedback = item.requested_changes_note || item.review_note

                    return (
                      <div key={item.id} className="rounded-xl border p-3.5 bg-card transition-colors hover:border-primary/40">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold text-sm text-foreground">{item.title || 'Văn bản đề xuất'}</span>
                            <Badge variant={isNeedsChange ? 'destructive' : isApproved ? 'secondary' : 'outline'}>
                              {isNeedsChange ? 'Quản trị viên yêu cầu bổ sung' : isApproved ? 'Đã duyệt' : 'Đang chờ duyệt'}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                              {domainLabel(item.domain)}
                            </Badge>
                          </div>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                          Số hiệu: {item.law_number || 'Chưa có'}
                        </p>
                        {isNeedsChange && feedback && (
                          <div className="mt-2.5 rounded-lg bg-amber-500/10 border border-amber-300/40 p-2.5 text-xs text-amber-900 dark:text-amber-200">
                            <b>Quản trị viên phản hồi:</b> {feedback}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </CardContent>
              </Card>

              {/* Recent Notebooks Card */}
              <Card className="shadow-sm">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle className="text-base flex items-center gap-2">
                        <BookOpen className="h-5 w-5 text-primary" /> Hồ sơ pháp lý gần đây
                      </CardTitle>
                      <CardDescription>Các chuyên đề và hồ sơ vụ việc đang lưu trữ.</CardDescription>
                    </div>
                    <Button asChild size="sm" variant="ghost" className="gap-1 text-xs">
                      <Link href="/notebooks">
                        Xem tất cả <ArrowRight className="h-3 w-3" />
                      </Link>
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2.5">
                  {notebooks.length === 0 && !loading && !loadErrors.length && (
                    <p className="text-xs text-muted-foreground py-4 text-center">Chưa có hồ sơ pháp lý nào được tạo.</p>
                  )}
                  {notebooks.map((nb) => (
                    <Link
                      key={nb.id}
                      href={`/notebooks/${nb.id}`}
                      className="group flex items-center justify-between rounded-xl border p-3 bg-card transition-all hover:bg-muted/40 hover:border-primary/40"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary shrink-0">
                          <Book className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium group-hover:text-primary transition-colors truncate">{nb.name}</p>
                          <p className="text-[11px] text-muted-foreground truncate">{nb.description || 'Hồ sơ tài liệu nghiệp vụ'}</p>
                        </div>
                      </div>
                      <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                    </Link>
                  ))}
                </CardContent>
              </Card>
            </div>

            {/* ── Right Column: Kênh hỗ trợ & Thủ tục nhanh ── */}
            <div className="space-y-6">
              {/* Live Support Channel Card */}
              <Card className="shadow-sm border-primary/20">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Headphones className="h-5 w-5 text-primary" /> Ca trực hỗ trợ công dân
                  </CardTitle>
                  <CardDescription>Kênh trao đổi trực tiếp giữa cán bộ và người dân.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3.5">
                  <div className="rounded-xl border p-4 bg-primary/[0.03] space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">Phạm vi tiếp nhận:</span>
                      <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-600 border-emerald-200">
                        Phòng ban của bạn
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Yêu cầu được chuyển đến phòng ban phụ trách. Mở bàn làm việc để kiểm tra hàng chờ và nhận xử lý.
                    </p>
                  </div>
                  <Button asChild className="w-full gap-2 shadow-sm">
                    <Link href="/live-support">
                      <Headphones className="h-4 w-4" /> Mở bàn làm việc hỗ trợ
                    </Link>
                  </Button>
                </CardContent>
              </Card>

              {/* Quick procedures follow the officer's persisted assignments. */}
              <Card className="shadow-sm">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <FolderTree className="h-5 w-5 text-primary" /> Lĩnh vực được phân công
                  </CardTitle>
                  <CardDescription>Tra cứu nhanh quy trình và biểu mẫu theo lĩnh vực.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {(operatingScope?.domains || []).map((code) => (
                    <Link
                      key={code}
                      href={`/procedures?domain=${encodeURIComponent(code)}`}
                      className="flex items-center justify-between rounded-xl border p-2.5 bg-card hover:bg-muted/40 hover:border-primary/40 transition-colors text-xs"
                    >
                      <span className="font-medium text-foreground">{domainLabel(code)}</span>
                      <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                    </Link>
                  ))}
                  {operatingScope && operatingScope.domains.length === 0 && (
                    <p className="text-xs text-muted-foreground">
                      Chưa có lĩnh vực đang hoạt động được phân công cho tài khoản này.
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>

          </div>

        </div>
      </div>
    </AppShell>
  )
}
