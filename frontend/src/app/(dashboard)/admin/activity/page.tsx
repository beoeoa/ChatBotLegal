'use client'

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  CheckCircle2,
  Download,
  Filter,
  Loader2,
  Search,
  ShieldAlert,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'

import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  adminActivityApi,
  type AdminActivityFilters,
  type AdminActivityItem,
} from '@/lib/api/admin-activity'

type ActivityDetail = AdminActivityItem & {
  details?: Record<string, unknown>
  ip_address?: string | null
  user_agent?: string | null
  access_reason?: string
  detail_rows?: Array<{ label: string; value: string }>
}

const activityTypes = {
  accounts: 'Tài khoản và đăng nhập',
  legal_documents: 'Văn bản pháp luật',
  data_ingestion: 'Nạp và thu thập dữ liệu',
  governance: 'FAQ, thủ tục và biểu mẫu',
  configuration: 'Mô hình và khóa kết nối',
  sensitive_access: 'Truy cập nhạy cảm và xuất báo cáo',
  support: 'Hỗ trợ người dân',
  system: 'Hệ thống',
}

const activityModules = {
  legal_data: 'Dữ liệu pháp lý',
  model_setup: 'Model và kết nối',
  security: 'Tài khoản và bảo mật',
  configuration: 'Cấu hình hệ thống',
  support: 'Hỗ trợ người dân',
  system: 'Tác vụ hệ thống',
}

const detailLabels: Record<string, string> = {
  access_reason: 'Lý do truy cập',
  ask_sessions: 'Phiên hỏi đáp',
  conversations: 'Cuộc hội thoại',
  count: 'Số lượng',
  generated_at: 'Thời điểm tạo',
  legal_cases: 'Hồ sơ pháp lý',
  reason: 'Lý do',
  record_count: 'Số bản ghi',
  result: 'Kết quả',
  status: 'Trạng thái',
  support_tickets: 'Phiên hỗ trợ',
  updated_fields: 'Trường đã thay đổi',
  within_days: 'Phạm vi ngày',
}

const legacyActionLabels: Record<string, string> = {
  'retention.purge.completed': 'Đã hoàn tất xóa dữ liệu hết hạn',
  'auth.login.failed': 'Đăng nhập thất bại',
  'user.create': 'Đã tạo tài khoản',
  'user.update': 'Đã cập nhật tài khoản',
  'user.deactivate': 'Đã khóa tài khoản',
  'user.reactivate': 'Đã mở khóa tài khoản',
  'user.soft_delete': 'Đã xóa tài khoản',
  'admin.legal.import': 'Đã nhập văn bản pháp luật',
  'legal.proposal.create': 'Đã tạo đề xuất văn bản',
  'legal.crawl.run.manual': 'Đã chạy thu thập văn bản',
  'legal.candidate.import.enqueue': 'Đã đưa văn bản vào hàng chờ nhập',
  'legal.candidate.import.duplicate_conflict': 'Nhập văn bản thất bại do trùng dữ liệu',
  'faq.revision.create': 'Đã tạo bản sửa FAQ',
  'faq.revision.confirm': 'Đã duyệt FAQ',
  'faq.release.activate': 'Đã phát hành FAQ',
  'form.source.approve': 'Đã duyệt nguồn biểu mẫu',
  'form.attest': 'Đã xác nhận biểu mẫu',
  'form.release.activate': 'Đã phát hành biểu mẫu',
  'admin.model.create': 'Đã thêm mô hình trả lời',
  'admin.model.delete': 'Đã xóa mô hình trả lời',
  'admin.credential.create': 'Đã thêm khóa kết nối',
  'admin.credential.update': 'Đã cập nhật khóa kết nối',
  'admin.credential.delete': 'Đã xóa khóa kết nối',
  'admin.configuration.update': 'Đã cập nhật cấu hình hệ thống',
  'admin.activity.sensitive_view': 'Đã xem dữ liệu nhạy cảm',
  'admin.activity.export': 'Đã xuất nhật ký quản trị',
  'support.ticket.assign_failed': 'Phân công hỗ trợ thất bại',
  'admin.config_history.restore': 'Đã khôi phục cấu hình',
  'admin.knowledge.candidate.review': 'Đã rà soát đề xuất tri thức',
  'admin.legal_case.detail.view': 'Đã xem hồ sơ pháp lý',
  'admin.legal_effectivity.run': 'Đã chạy kiểm tra hiệu lực văn bản',
  'admin.retention.run': 'Đã chạy tác vụ lưu trữ',
  'admin.support.detail.view': 'Đã xem chi tiết hỗ trợ',
  'admin.legal_validity.run': 'Đã chạy kiểm tra tính hợp lệ văn bản',
  'admin.legal_validity.vector_cleanup': 'Đã dọn dữ liệu tìm kiếm theo quyết định hợp lệ',
  'admin.legal_validity.decision': 'Đã ghi nhận quyết định tính hợp lệ',
  'legal.candidate.assess': 'Đã đánh giá văn bản chờ nhập',
  'legal.candidate.extract.queue': 'Đã đưa văn bản vào hàng chờ trích xuất',
  'legal.candidate.metadata.update': 'Đã cập nhật thông tin mô tả văn bản',
  'legal.candidate.review': 'Đã rà soát văn bản chờ nhập',
  'legal.crawl.source.create': 'Đã thêm nguồn thu thập',
  'legal.crawl.source.update': 'Đã cập nhật nguồn thu thập',
  'legal.crawl.source.delete': 'Đã xóa nguồn thu thập',
  'legal.lifecycle.change_candidate_created': 'Đã tạo sự kiện vòng đời văn bản',
  'legal.lifecycle.change_event_confirmed': 'Đã xác nhận sự kiện vòng đời văn bản',
  'legal.lifecycle.impact_decided': 'Đã ghi nhận đánh giá tác động văn bản',
  'support.attachment.download': 'Đã tải tệp hỗ trợ',
  'support.chat.messages.view': 'Đã xem tin nhắn hỗ trợ',
  'support.chat.view': 'Đã xem phiên hỗ trợ',
  'support.ticket.reassign': 'Đã chuyển phân công hỗ trợ',
  'user.password.change': 'Đã đổi mật khẩu',
  'user.password.reset_by_admin': 'Quản trị viên đã đặt lại mật khẩu',
  'user.password.reset_requested': 'Đã yêu cầu đặt lại mật khẩu',
}

const legacyResourceLabels: Record<string, string> = {
  audit_event: 'Sự kiện trong lịch sử thao tác',
  credential: 'Khóa kết nối',
  faq_release: 'Bản phát hành câu hỏi thường gặp',
  faq_revision: 'Câu hỏi thường gặp',
  form_release: 'Bản phát hành biểu mẫu',
  form_review_case: 'Hồ sơ biểu mẫu',
  legal_crawl_candidate: 'Văn bản chờ nhập',
  legal_crawl_source: 'Nguồn thu thập',
  legal_document: 'Văn bản pháp luật',
  model: 'Mô hình AI',
  retention_job: 'Tác vụ lưu trữ',
  support_ticket: 'Phiên hỗ trợ',
  system_settings: 'Cấu hình hệ thống',
  user_account: 'Tài khoản',
}

function actorLabel(item: AdminActivityItem): string {
  if (item.actor_label?.trim()) return item.actor_label
  const actor = item.actor?.trim() || ''
  const role = item.actor_role?.trim().toLocaleLowerCase('vi-VN') || 'system'
  if (actor.toLocaleLowerCase('vi-VN') === 'system' || role === 'system') return 'Hệ thống'
  const roleLabels: Record<string, string> = { admin: 'Quản trị viên', officer: 'Cán bộ', citizen: 'Người dân' }
  const shortActor = actor.split(':').at(-1)?.trim()
  if (shortActor && shortActor.toLocaleLowerCase('vi-VN') in roleLabels) return roleLabels[role] || shortActor
  if (shortActor && shortActor !== actor) return 'Tài khoản chưa xác định'
  return shortActor || roleLabels[role] || 'Người dùng'
}

function actionLabel(item: AdminActivityItem): string {
  if (item.action_label?.trim()) return item.action_label
  if (legacyActionLabels[item.action]) return legacyActionLabels[item.action]
  return item.result === 'failed' ? 'Thao tác không thành công' : 'Hoạt động hệ thống'
}

function resourceLabel(item: AdminActivityItem): string {
  if (item.resource_label?.trim()) return item.resource_label
  return legacyResourceLabels[item.resource_type] || 'Dữ liệu hệ thống'
}

function activityTypeOf(item: AdminActivityItem): string {
  if (item.activity_type?.trim()) return item.activity_type
  const value = `${item.action || ''} ${item.resource_type || ''}`.toLocaleLowerCase('vi-VN')
  if (value.includes('support')) return 'support'
  if (['sensitive_view', 'activity.export', 'audit_event'].some(term => value.includes(term))) return 'sensitive_access'
  if (['auth', 'user', 'session', 'password', 'mfa', 'account'].some(term => value.includes(term))) return 'accounts'
  if (['faq', 'form', 'procedure'].some(term => value.includes(term))) return 'governance'
  if (['crawl', 'import', 'ocr', 'extract', 'source'].some(term => value.includes(term))) return 'data_ingestion'
  if (['model', 'credential', 'provider', 'config', 'setting', 'api_key'].some(term => value.includes(term))) return 'configuration'
  if (['legal', 'document', 'lifecycle', 'validity'].some(term => value.includes(term))) return 'legal_documents'
  return 'system'
}

function searchable(value: string): string {
  return value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase('vi-VN')
}

function matchesFilters(item: AdminActivityItem, filters: AdminActivityFilters): boolean {
  if (filters.role && item.actor_role !== filters.role) return false
  if (filters.module && item.module !== filters.module) return false
  if (filters.result && item.result !== filters.result) return false
  if (filters.status && (item.workflow_status || (item.result === 'failed' ? 'failed' : 'succeeded')) !== filters.status) return false
  if (filters.activity_type && activityTypeOf(item) !== filters.activity_type) return false
  if (filters.search) {
    const needle = searchable(filters.search.trim())
    const haystack = searchable([
      actorLabel(item), actionLabel(item), resourceLabel(item), item.actor,
      item.action, item.resource_type, item.resource_id,
    ].filter(Boolean).join(' '))
    if (!haystack.includes(needle)) return false
  }
  if (filters.date_from) {
    const occurredAt = new Date(item.occurred_at).getTime()
    const from = new Date(`${filters.date_from}T00:00:00+07:00`).getTime()
    if (Number.isFinite(occurredAt) && occurredAt < from) return false
  }
  if (filters.date_to) {
    const occurredAt = new Date(item.occurred_at).getTime()
    const to = new Date(`${filters.date_to}T23:59:59.999+07:00`).getTime()
    if (Number.isFinite(occurredAt) && occurredAt > to) return false
  }
  return true
}

function dateFromPeriod(period: string): string | undefined {
  const days = Number(period)
  if (!Number.isFinite(days) || days <= 0) return undefined
  const value = new Date()
  value.setHours(0, 0, 0, 0)
  value.setDate(value.getDate() - (days - 1))
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function meaningfulReason(value: string): boolean {
  const normalized = value.trim().replace(/\s+/g, ' ')
  const tokens = normalized.toLocaleLowerCase('vi-VN').match(/[\p{L}\p{N}]+/gu) || []
  const characters = [...normalized.toLocaleLowerCase('vi-VN')].filter(character => /[\p{L}\p{N}]/u.test(character))
  return normalized.length >= 12
    && tokens.length >= 3
    && new Set(tokens).size >= 3
    && new Set(characters).size >= 4
}

function formatTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value || '—'
  return parsed.toLocaleTimeString('vi-VN', {
    hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Ho_Chi_Minh',
  })
}

function formatFullTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value || '—'
  return parsed.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })
}

function resultLabel(result: string): string {
  return result === 'failed' ? 'Thất bại' : 'Thành công'
}

function workflowStatusLabel(item: AdminActivityItem): string {
  if (item.workflow_status_label?.trim()) return item.workflow_status_label
  const labels: Record<string, string> = {
    queued: 'Đang chờ', running: 'Đang xử lý', succeeded: 'Đã hoàn tất',
    partial: 'Hoàn tất một phần', failed: 'Thất bại', blocked: 'Bị chặn',
    cancelled: 'Đã hủy', retrying: 'Đang thử lại',
  }
  return labels[item.workflow_status || (item.result === 'failed' ? 'failed' : 'succeeded')] || 'Đã hoàn tất'
}

function formatDetailValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Không có'
  if (typeof value === 'boolean') return value ? 'Có' : 'Không'
  if (Array.isArray(value)) {
    if (value.every(item => ['string', 'number'].includes(typeof item))) return value.join(', ')
    return `${value.length.toLocaleString('vi-VN')} mục`
  }
  if (typeof value === 'object') return `${Object.keys(value as Record<string, unknown>).length.toLocaleString('vi-VN')} mục`
  if (typeof value === 'number') return value.toLocaleString('vi-VN')
  const text = String(value)
  const parsed = /^\d{4}-\d{2}-\d{2}T/.test(text) ? new Date(text) : null
  return parsed && !Number.isNaN(parsed.getTime())
    ? parsed.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })
    : text
}

function plainBusinessText(value: string | null | undefined, fallback: string): string {
  const text = value?.trim()
  if (!text) return fallback
  return text
    .replace(/\bmetadata\b/gi, 'thông tin mô tả')
    .replace(/\bvector(?:s)?\b/gi, 'dữ liệu tìm kiếm')
    .replace(/\bindex(?:ed|ing)?\b/gi, 'tạo dữ liệu tìm kiếm')
    .replace(/\bAPI key\b/gi, 'khóa kết nối')
    .replace(/\baudit\b/gi, 'lịch sử thao tác')
    .replace(/\bworkflow\b/gi, 'quy trình')
    .replace(/\bmigration\b/gi, 'nâng cấp dữ liệu')
    .replace(/\bstaging\b/gi, 'khu vực chuẩn bị')
}

function detailEntries(detail: ActivityDetail): Array<[string, string]> {
  if (detail.detail_rows?.length) {
    return detail.detail_rows.map(row => [
      plainBusinessText(row.label, 'Thông tin bổ sung'),
      plainBusinessText(row.value, 'Không có'),
    ])
  }
  const source: Record<string, unknown> = {
    ...(detail.details || {}),
    access_reason: detail.access_reason,
  }
  return Object.entries(source)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .filter(([key]) => Boolean(detailLabels[key]))
    .map(([key, value]) => [detailLabels[key], plainBusinessText(formatDetailValue(value), 'Không có')])
}

export default function AdminActivityPage() {
  const [search, setSearch] = useState('')
  const [role, setRole] = useState('')
  const [module, setModule] = useState('')
  const [activityType, setActivityType] = useState('')
  const [result, setResult] = useState('')
  const [workflowStatus, setWorkflowStatus] = useState('')
  const [period, setPeriod] = useState('30')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [appliedFilters, setAppliedFilters] = useState<AdminActivityFilters>({
    date_from: dateFromPeriod('30'), limit: 50,
  })
  const [items, setItems] = useState<AdminActivityItem[]>([])
  const [total, setTotal] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<AdminActivityItem | null>(null)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState('')
  const [detail, setDetail] = useState<ActivityDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [exporting, setExporting] = useState(false)

  const load = useCallback(async (filters: AdminActivityFilters, cursor?: string) => {
    setLoading(true)
    try {
      const page = await adminActivityApi.list({ ...filters, cursor })
      setItems(current => cursor ? [...current, ...page.items] : page.items)
      setTotal(page.total)
      setNextCursor(page.next_cursor)
    } catch {
      toast.error('Không tải được nhật ký quản trị.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(appliedFilters) }, [appliedFilters, load])

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAppliedFilters({
      search: search.trim() || undefined,
      role: role || undefined,
      module: module || undefined,
      activity_type: activityType || undefined,
      result: result || undefined,
      status: workflowStatus || undefined,
      date_from: period === 'custom' ? dateFrom || undefined : dateFromPeriod(period),
      date_to: period === 'custom' ? dateTo || undefined : undefined,
      limit: 50,
    })
  }

  const openItem = (item: AdminActivityItem) => {
    setSelected(item)
    setReason('')
    setReasonError('')
    setDetail(null)
  }

  const viewDetail = async () => {
    if (!selected) return
    const normalized = reason.trim().replace(/\s+/g, ' ')
    if (!meaningfulReason(normalized)) {
      setReasonError('Lý do phải có ít nhất 3 từ có nghĩa và không được lặp ký tự hoặc từ.')
      return
    }
    setReasonError('')
    setDetailLoading(true)
    try {
      setDetail(await adminActivityApi.sensitiveView(selected.id, normalized) as ActivityDetail)
    } catch {
      toast.error('Không thể mở chi tiết; yêu cầu truy cập đã bị từ chối.')
    } finally {
      setDetailLoading(false)
    }
  }

  const visibleItems = useMemo(
    () => items.filter(item => matchesFilters(item, appliedFilters)),
    [appliedFilters, items],
  )
  const visibleTotal = visibleItems.length === items.length ? total : visibleItems.length

  const exportExcel = async () => {
    setExporting(true)
    try {
      const exported = await adminActivityApi.exportXlsx(appliedFilters)
      const url = URL.createObjectURL(exported.blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = exported.filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      // Do not revoke synchronously: Chromium may cancel the pending download
      // before it has consumed the object URL, especially for an XLSX blob.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast.success('Đã xuất nhật ký Excel theo bộ lọc hiện tại.')
    } catch {
      toast.error('Không thể xuất Excel. Vui lòng thử lại.')
    } finally {
      setExporting(false)
    }
  }

  return (
    <AppShell>
      <main className="min-h-0 flex-1 overflow-auto bg-muted/20 p-4 pt-16 md:p-8 md:pt-8">
        <div className="mx-auto max-w-6xl space-y-5">
          <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-bold">
                <Activity className="h-6 w-6" /> Nhật ký quản trị
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Theo dõi các thay đổi quan trọng và thao tác không thành công trong hệ thống.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void exportExcel()} disabled={exporting}>
              {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
              Xuất Excel
            </Button>
          </header>

          <Card>
            <CardContent className="p-4">
              <form className="grid gap-3 md:grid-cols-[minmax(220px,1.4fr)_1fr_1fr_0.8fr_0.8fr_auto] md:items-end" onSubmit={applyFilters}>
                <div>
                  <Label htmlFor="activity-search">Tìm kiếm</Label>
                  <div className="relative mt-1.5">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input id="activity-search" className="pl-9" placeholder="Người thực hiện hoặc hoạt động" value={search} onChange={event => setSearch(event.target.value)} />
                  </div>
                </div>
                <div>
                  <Label htmlFor="activity-role">Vai trò</Label>
                  <select id="activity-role" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm" value={role} onChange={event => setRole(event.target.value)}>
                    <option value="">Tất cả</option>
                    <option value="admin">Quản trị viên</option>
                    <option value="officer">Cán bộ</option>
                    <option value="citizen">Người dân</option>
                    <option value="system">Hệ thống</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="activity-module">Module</Label>
                  <select id="activity-module" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm" value={module} onChange={event => setModule(event.target.value)}>
                    <option value="">Tất cả</option>
                    {Object.entries(activityModules).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </div>
                <div>
                  <Label htmlFor="activity-type">Loại hoạt động</Label>
                  <select id="activity-type" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm" value={activityType} onChange={event => setActivityType(event.target.value)}>
                    <option value="">Tất cả</option>
                    {Object.entries(activityTypes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </div>
                <div>
                  <Label htmlFor="activity-status">Trạng thái</Label>
                  <select id="activity-status" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm" value={workflowStatus} onChange={event => setWorkflowStatus(event.target.value)}>
                    <option value="">Tất cả</option>
                    <option value="queued">Đang chờ</option>
                    <option value="running">Đang xử lý</option>
                    <option value="succeeded">Đã hoàn tất</option>
                    <option value="partial">Hoàn tất một phần</option>
                    <option value="failed">Thất bại</option>
                    <option value="blocked">Bị chặn</option>
                    <option value="cancelled">Đã hủy</option>
                    <option value="retrying">Đang thử lại</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="activity-result">Kết quả</Label>
                  <select id="activity-result" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm" value={result} onChange={event => setResult(event.target.value)}>
                    <option value="">Tất cả</option>
                    <option value="success">Thành công</option>
                    <option value="failed">Thất bại</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="activity-period">Thời gian</Label>
                  <select id="activity-period" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm" value={period} onChange={event => setPeriod(event.target.value)}>
                    <option value="1">Hôm nay</option>
                    <option value="7">7 ngày</option>
                    <option value="30">30 ngày</option>
                    <option value="">Tất cả</option>
                    <option value="custom">Khoảng ngày tùy chọn</option>
                  </select>
                </div>
                {period === 'custom' && (
                  <>
                    <div>
                      <Label htmlFor="activity-date-from">Từ ngày</Label>
                      <Input id="activity-date-from" className="mt-1.5" type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} />
                    </div>
                    <div>
                      <Label htmlFor="activity-date-to">Đến ngày</Label>
                      <Input id="activity-date-to" className="mt-1.5" type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} />
                    </div>
                  </>
                )}
                <Button type="submit" disabled={loading} aria-label="Lọc nhật ký">
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Filter className="h-4 w-4" />}
                  <span>Áp dụng</span>
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-0">
              <div className="flex items-center justify-between border-b px-4 py-3">
                <p className="text-sm font-semibold">{visibleTotal.toLocaleString('vi-VN')} sự kiện</p>
                <p className="text-xs text-muted-foreground">Chọn một hoạt động để xem chi tiết</p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-sm" aria-label="Danh sách sự kiện quản trị">
                  <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="w-28 px-4 py-3 font-medium">Thời gian</th>
                      <th className="w-44 px-4 py-3 font-medium">Người thực hiện</th>
                      <th className="px-4 py-3 font-medium">Hoạt động</th>
                      <th className="w-36 px-4 py-3 font-medium">Trạng thái</th>
                      <th className="w-32 px-4 py-3 font-medium">Kết quả</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleItems.map(item => (
                      <tr key={item.id} className="border-t hover:bg-muted/30">
                        <td className="px-4 py-3 font-medium tabular-nums">{formatTime(item.occurred_at)}</td>
                        <td className="px-4 py-3">
                          <span>{actorLabel(item)}</span>
                          {item.actor_job_title && <span className="mt-0.5 block text-xs text-muted-foreground">{item.actor_job_title}</span>}
                        </td>
                        <td className="px-4 py-3">
                          <button type="button" className="block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => openItem(item)} aria-label={`Xem chi tiết: ${actionLabel(item)}`}>
                            <span className="font-medium text-foreground hover:text-primary">{actionLabel(item)}</span>
                            <span className="mt-0.5 block text-xs text-muted-foreground">{resourceLabel(item)}</span>
                          </button>
                        </td>
                        <td className="px-4 py-3">
                          <Badge variant="outline">{workflowStatusLabel(item)}</Badge>
                        </td>
                        <td className="px-4 py-3">
                          <Badge variant={item.result === 'failed' ? 'destructive' : 'secondary'} className="gap-1">
                            {item.result === 'failed' ? <XCircle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                            {resultLabel(item.result)}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {loading && items.length === 0 && (
                <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Đang tải nhật ký…</div>
              )}
              {!loading && visibleItems.length === 0 && (
                <div className="py-12 text-center"><p className="font-medium">Không có sự kiện phù hợp</p><p className="mt-1 text-sm text-muted-foreground">Hãy đổi từ khóa hoặc phạm vi thời gian.</p></div>
              )}
              {nextCursor && (
                <div className="border-t p-3 text-center"><Button variant="outline" size="sm" onClick={() => void load(appliedFilters, nextCursor)} disabled={loading}>Tải thêm</Button></div>
              )}
            </CardContent>
          </Card>
        </div>
      </main>

      <Dialog open={Boolean(selected)} onOpenChange={open => { if (!open) { setSelected(null); setDetail(null) } }}>
        <DialogContent className="!bottom-0 !left-auto !right-0 !top-0 !h-dvh !max-w-xl !translate-x-0 !translate-y-0 !rounded-none p-0 sm:!max-w-xl" showCloseButton>
          {selected && (
            <div className="flex min-h-0 flex-1 flex-col">
              <DialogHeader className="border-b p-6 pr-12">
                <DialogTitle>Chi tiết sự kiện</DialogTitle>
                <DialogDescription>{actionLabel(selected)}</DialogDescription>
              </DialogHeader>

              <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-6">
                <dl className="grid grid-cols-2 gap-4 rounded-lg border bg-muted/20 p-4 text-sm">
                  <div><dt className="text-xs text-muted-foreground">Thời gian</dt><dd className="mt-1 font-medium">{formatFullTime(selected.occurred_at)}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Kết quả</dt><dd className="mt-1 font-medium">{resultLabel(selected.result)}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Người thực hiện</dt><dd className="mt-1 font-medium">{actorLabel(selected)}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Vai trò/đơn vị</dt><dd className="mt-1 font-medium">{[selected.actor_job_title, selected.actor_department].filter(Boolean).join(' · ') || selected.actor_role_label || 'Không có'}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Dữ liệu liên quan</dt><dd className="mt-1 font-medium">{resourceLabel(selected)}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Trạng thái</dt><dd className="mt-1 font-medium">{workflowStatusLabel(selected)}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Mức độ</dt><dd className="mt-1 font-medium">{selected.severity_label || 'Thông tin'}</dd></div>
                </dl>

                <section className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/60 p-4">
                  <h2 className="font-semibold text-blue-950">Diễn giải và xử lý</h2>
                  <div className="space-y-2 text-sm">
                    <p><span className="font-medium text-blue-950">Đã xảy ra:</span> {plainBusinessText(detail?.explanation || selected.explanation, actionLabel(selected))}</p>
                    <p><span className="font-medium text-blue-950">Ảnh hưởng:</span> {plainBusinessText(detail?.impact || selected.impact, 'Chưa có mô tả ảnh hưởng.')}</p>
                    <p><span className="font-medium text-blue-950">Việc cần làm:</span> {plainBusinessText(detail?.next_action || selected.next_action, 'Không có hướng xử lý tiếp theo.')}</p>
                  </div>
                </section>

                {selected.sensitive_detail_available && !detail && (
                  <section className="space-y-3">
                    <div>
                      <h2 className="flex items-center gap-2 font-semibold"><ShieldAlert className="h-4 w-4 text-amber-700" />Chi tiết có kiểm soát</h2>
                      <p className="mt-1 text-sm text-muted-foreground">Nhập lý do nghiệp vụ cụ thể. Lần truy cập này sẽ được ghi vào nhật ký.</p>
                    </div>
                    <div>
                      <Label htmlFor="activity-reason">Lý do truy cập</Label>
                      <Input id="activity-reason" className="mt-1.5" placeholder="Ví dụ: Rà soát sự cố nhập văn bản ngày 16/08" value={reason} onChange={event => { setReason(event.target.value); setReasonError('') }} aria-invalid={Boolean(reasonError)} />
                      {reasonError && <p className="mt-1.5 text-sm text-destructive" role="alert">{reasonError}</p>}
                    </div>
                    <Button onClick={() => void viewDetail()} disabled={detailLoading}>
                      {detailLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Xem chi tiết
                    </Button>
                  </section>
                )}

                {!selected.sensitive_detail_available && (
                  <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Sự kiện này không có dữ liệu chi tiết bổ sung.</p>
                )}

                {detail && (
                  <section className="space-y-4">
                    <h2 className="font-semibold">Thông tin chi tiết</h2>
                    <dl className="divide-y rounded-lg border">
                      {detailEntries(detail).map(([label, value]) => (
                        <div key={label} className="grid gap-1 px-4 py-3 sm:grid-cols-[160px_1fr]">
                          <dt className="text-sm text-muted-foreground">{label}</dt>
                          <dd className="break-words text-sm font-medium">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </section>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}
