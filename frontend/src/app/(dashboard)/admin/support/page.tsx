'use client'

import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { AlertTriangle, ArrowDownToLine, Bell, Clock3, RefreshCw, ShieldCheck, UserRound } from 'lucide-react'
import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { apiClient } from '@/lib/api/client'
import { formatApiError } from '@/lib/utils/error-handler'

interface SupportMetadata {
  id: string
  canonical_domain: string
  primary_organization_unit_id?: string | null
  status: string
  priority: string
  assigned_officer_id?: string | null
  assignment_generation: number
  created_at: string
  updated_at: string
  first_response_due_at?: string | null
  resolution_due_at?: string | null
  first_response_at?: string | null
  first_response_overdue?: boolean
  resolution_overdue?: boolean
  sla_overdue?: boolean
  overdue_minutes?: number
  sla_basis?: string
  version?: number
  overdue?: boolean
}

interface SensitiveTicketContent {
  grant: { expires_at: string; audit_event_id: string }
  ticket: { id: string; question?: string; status: string }
  messages: Array<{ id: string; sender_role: string; content: string; created_at: string }>
}

const DOMAINS = [
  ['ho_tich_chung_thuc', 'Hộ tịch – Chứng thực'],
  ['dat_dai_xay_dung', 'Đất đai – Xây dựng'],
  ['an_sinh_y_te_giao_duc', 'An sinh – Y tế – Giáo dục'],
  ['hanh_chinh_cong', 'Hành chính công'],
  ['trat_tu_do_thi', 'Trật tự đô thị'],
  ['cu_tru_an_ninh', 'Cư trú – An ninh'],
  ['khieu_nai_to_cao_xu_phat', 'Khiếu nại – Tố cáo – Xử phạt'],
]

function formatTime(value?: string | null) {
  if (!value) return 'Chưa xác định'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' }).format(date)
}

export default function AdminSupportPage() {
  const searchParams = useSearchParams()
  const [items, setItems] = useState<SupportMetadata[]>([])
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')
  const [slaFilter, setSlaFilter] = useState(searchParams.get('sla') === 'overdue' || searchParams.get('status') === 'overdue' ? 'overdue' : 'all')
  const [domainFilter, setDomainFilter] = useState('all')
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [content, setContent] = useState<SensitiveTicketContent | null>(null)
  const [openingContent, setOpeningContent] = useState(false)
  const [actionMode, setActionMode] = useState<'content' | 'dispatch' | 'remind' | 'escalate'>('content')
  const [officerId, setOfficerId] = useState('')
  const [targetUnit, setTargetUnit] = useState('')
  const [targetDomain, setTargetDomain] = useState('')
  const [routingOptions, setRoutingOptions] = useState<Array<{ unit_id: string; unit_name: string; domain: string }>>([])
  const [actionError, setActionError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [officers, setOfficers] = useState<Array<{ id: string; username?: string; domains?: string[]; online?: boolean; active_count?: number; capacity?: number }>>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await apiClient.get<SupportMetadata[]>('/support/admin/tickets', {
        params: {
          ...(statusFilter !== 'all' ? { status: statusFilter } : {}),
          ...(domainFilter !== 'all' ? { domain: domainFilter } : {}),
          ...(slaFilter !== 'all' ? { sla: slaFilter } : {}),
        },
      })
      setItems(response.data || [])
    } catch (error) {
      setActionError(formatApiError(error, 'Không tải được hàng hỗ trợ.'))
    } finally {
      setLoading(false)
    }
  }, [domainFilter, slaFilter, statusFilter])

  useEffect(() => {
    void load()
  }, [load])

  const openSensitiveContent = async () => {
    if (!selectedTicketId || reason.trim().length < 8) return
    setOpeningContent(true)
    try {
      const response = await apiClient.post<SensitiveTicketContent>(
        `/support/admin/tickets/${selectedTicketId}/view-content`,
        { reason: reason.trim() },
      )
      setContent(response.data)
    } catch (error) {
      setActionError(formatApiError(error, 'Không mở được nội dung hỗ trợ.'))
    } finally {
      setOpeningContent(false)
    }
  }

  const openAction = async (mode: 'dispatch' | 'remind' | 'escalate', item: SupportMetadata) => {
    setSelectedTicketId(item.id); setReason(''); setContent(null); setActionMode(mode); setOfficerId('')
    setActionError(''); setTargetUnit(item.primary_organization_unit_id || ''); setTargetDomain(item.canonical_domain); setOfficers([])
    if (mode === 'dispatch') {
      try {
        const response = await apiClient.get('/support/routing-options')
        setRoutingOptions(response.data.options || [])
      } catch (error) { setActionError(formatApiError(error, 'Không tải được phòng ban nhận.')) }
    }
  }

  useEffect(() => {
    if (!selectedTicketId || actionMode !== 'dispatch' || !targetDomain) return
    let current = true
    setOfficerId(''); setOfficers([])
    void apiClient.get('/support/admin/officers', { params: { domain: targetDomain, organization_unit_id: targetUnit || undefined } })
      .then(response => { if (current) setOfficers(response.data || []) })
      .catch(error => { if (current) setActionError(formatApiError(error, 'Không tải được cán bộ đúng phạm vi.')) })
    return () => { current = false }
  }, [selectedTicketId, actionMode, targetDomain, targetUnit])

  const submitAction = async () => {
    const item = items.find((row) => row.id === selectedTicketId)
    if (!item || reason.trim().length < 3 || submitting) return
    setSubmitting(true); setActionError('')
    try {
    const version = item.version || 1
    const key = `${actionMode}-${item.id}-${version}-${Date.now()}`
    const path = actionMode === 'dispatch'
      ? `/support/admin/tickets/${item.id}/dispatch`
      : `/support/admin/tickets/${item.id}/${actionMode}`
    const body = actionMode === 'dispatch'
      ? { officer_id: officerId || null, organization_unit_id: targetUnit || null, domain: targetDomain, reason: reason.trim(), expected_version: version }
      : { reason: reason.trim(), expected_version: version }
    if (actionMode === 'dispatch') await apiClient.post(path, body, { headers: { 'Idempotency-Key': key } })
    else await apiClient.post(path, body, { headers: { 'Idempotency-Key': key } })
    setSelectedTicketId(null); setActionMode('content'); await load()
    } catch (error) {
      setActionError(formatApiError(error, 'Không điều phối được phiên. Hãy tải lại để kiểm tra phiên bản và quyền nhận.'))
    } finally { setSubmitting(false) }
  }

  const isContentAction = actionMode === 'content'
  const actionTitle = isContentAction
    ? 'Truy cập nội dung nhạy cảm'
    : actionMode === 'dispatch'
      ? 'Điều phối phiên hỗ trợ'
      : actionMode === 'remind'
        ? 'Nhắc cán bộ phụ trách'
        : 'Đánh dấu escalated'

  return (
    <AppShell>
      <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-6xl space-y-5">
          <header>
            <h1 className="text-2xl font-bold">Vận hành hỗ trợ trực tuyến</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Danh sách này chỉ hiển thị thời hạn xử lý và thông tin phân công. Muốn xem nội dung riêng tư phải nêu lý do và được ghi vào lịch sử thao tác.
            </p>
          </header>

          <Card>
            <CardHeader className="gap-3 md:flex-row md:items-center md:justify-between">
              <CardTitle className="text-base">Bộ lọc hàng hỗ trợ</CardTitle>
              <div className="flex flex-wrap gap-2">
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                  <SelectTrigger className="w-44" aria-label="Lọc trạng thái"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Mọi trạng thái</SelectItem>
                    <SelectItem value="queued">Đang chờ</SelectItem>
                    <SelectItem value="assigned">Đã phân công</SelectItem>
                    <SelectItem value="active">Đang xử lý</SelectItem>
                    <SelectItem value="resolved">Đã xử lý</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={domainFilter} onValueChange={setDomainFilter}>
                  <SelectTrigger className="w-52" aria-label="Lọc lĩnh vực"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Mọi lĩnh vực</SelectItem>
                    {DOMAINS.map(([domain, label]) => <SelectItem key={domain} value={domain}>{label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={slaFilter} onValueChange={setSlaFilter}>
                  <SelectTrigger className="w-44" aria-label="Lọc theo thời hạn phản hồi"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Mọi thời hạn</SelectItem>
                    <SelectItem value="overdue">Chỉ yêu cầu quá hạn</SelectItem>
                  </SelectContent>
                </Select>
                <Button variant="outline" onClick={() => void load()} disabled={loading}>
                  <RefreshCw className={`mr-1 h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> Làm mới
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {actionError && !selectedTicketId && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
              {items.length === 0 && !loading && <p className="text-sm text-muted-foreground">Không có yêu cầu phù hợp.</p>}
              {items.map((item) => {
                const isOverdue = Boolean(item.sla_overdue ?? item.overdue)
                return (
                <article key={item.id} className={`rounded-lg border p-4 ${isOverdue ? 'border-red-300 bg-red-50/40' : ''}`}>
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={isOverdue ? 'destructive' : 'secondary'}>{{ queued: 'Đang chờ', assigned: 'Đã phân công', in_progress: 'Đang xử lý', resolved: 'Đã xử lý', closed: 'Đã đóng' }[item.status] || 'Đang xử lý'}</Badge>
                        <Badge variant="outline">{{ high: 'Ưu tiên cao', medium: 'Ưu tiên vừa', low: 'Ưu tiên thấp', urgent: 'Khẩn cấp' }[item.priority] || 'Ưu tiên thường'}</Badge>
                        {isOverdue && <span className="inline-flex items-center gap-1 text-xs font-medium text-red-700"><AlertTriangle className="h-3.5 w-3.5" />Quá hạn phản hồi {item.overdue_minutes ? `${item.overdue_minutes} phút` : ''}</span>}
                      </div>
                      <p className="text-sm font-medium">{DOMAINS.find(([domain]) => domain === item.canonical_domain)?.[1] || item.canonical_domain}</p>
                      <p className="text-xs text-muted-foreground">Phòng ban: {routingOptions.find(option => option.unit_id === item.primary_organization_unit_id)?.unit_name || item.primary_organization_unit_id || 'Chưa phân công'}</p>
                      <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
                        <span>Phân công: {item.assigned_officer_id || 'Chưa phân công'}</span>
                        <span>Lần phân công: {item.assignment_generation}</span>
                        <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />Phản hồi trước: {formatTime(item.first_response_due_at)}</span>
                        <span>Xử lý trước: {formatTime(item.resolution_due_at)}</span>
                      </div>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button type="button" variant="outline" onClick={() => void openAction('dispatch', item)}><UserRound className="mr-1 h-4 w-4" /> Phân công</Button>
                      <Button type="button" variant="outline" onClick={() => void openAction('remind', item)}><Bell className="mr-1 h-4 w-4" /> Nhắc việc</Button>
                      <Button type="button" variant="outline" onClick={() => void openAction('escalate', item)}><ArrowDownToLine className="mr-1 h-4 w-4" /> Chuyển cấp xử lý</Button>
                      <Button type="button" variant="ghost" onClick={() => { setSelectedTicketId(item.id); setReason(''); setContent(null); setActionMode('content') }}><ShieldCheck className="mr-1 h-4 w-4" /> Mở nội dung có kiểm soát</Button>
                    </div>
                  </div>
                </article>
                )
              })}
            </CardContent>
          </Card>
        </div>
      </main>

      <Dialog open={Boolean(selectedTicketId)} onOpenChange={(open) => { if (!open) setSelectedTicketId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{actionTitle}</DialogTitle>
            <DialogDescription>
              {isContentAction
                ? 'Nhập lý do nghiệp vụ cụ thể. Hệ thống sẽ ghi người thực hiện, đối tượng, lý do và thời gian trước khi mở nội dung.'
                : 'Nhập lý do nghiệp vụ cụ thể. Thao tác sẽ được ghi vào lịch sử mà không mở nội dung trao đổi riêng tư.'}
            </DialogDescription>
          </DialogHeader>
          {!content ? (
            <>
              {actionMode === 'dispatch' && (
                <div className="space-y-3">
                <label className="grid gap-1 text-sm">Phòng ban và lĩnh vực nhận
                  <select aria-label="Phòng ban và lĩnh vực nhận" className="h-10 rounded-md border bg-background px-3"
                    value={targetUnit ? `${targetUnit}|${targetDomain}` : ''}
                    onChange={event => { const [unit, domain] = event.target.value.split('|'); setTargetUnit(unit); setTargetDomain(domain) }}>
                    <option value="" disabled>Chọn phòng ban nhận</option>
                    {routingOptions.map(option => <option key={`${option.unit_id}|${option.domain}`} value={`${option.unit_id}|${option.domain}`}>{option.unit_name} · {DOMAINS.find(([code]) => code === option.domain)?.[1] || option.domain}</option>)}
                  </select>
                </label>
                <Select value={officerId || '__queue__'} onValueChange={(value) => setOfficerId(value === '__queue__' ? '' : value)}>
                  <SelectTrigger aria-label="Chọn cán bộ"><SelectValue placeholder="Chọn cán bộ hoặc đưa lại hàng chờ" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__queue__">Đưa lại hàng chờ</SelectItem>
                    {officers.filter((officer) => officer.online !== false && (officer.active_count || 0) < (officer.capacity || 3)).map((officer) => (
                      <SelectItem key={officer.id} value={officer.id}>{officer.username || officer.id} · {officer.active_count || 0}/{officer.capacity || 3}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                </div>
              )}
              {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
              <Input value={reason} onChange={(event) => setReason(event.target.value)} placeholder={isContentAction ? 'Ví dụ: Kiểm tra yêu cầu hỗ trợ đã quá hạn…' : 'Nêu rõ lý do nghiệp vụ…'} aria-label="Lý do truy cập" />
              <DialogFooter>
                <Button variant="outline" onClick={() => setSelectedTicketId(null)}>Huỷ</Button>
                <Button onClick={() => isContentAction ? void openSensitiveContent() : void submitAction()} disabled={reason.trim().length < (isContentAction ? 8 : 3) || openingContent || submitting || (actionMode === 'dispatch' && !targetUnit)}>
                  {openingContent || submitting ? 'Đang ghi lịch sử…' : isContentAction ? 'Ghi lịch sử và mở nội dung' : 'Ghi lịch sử và xác nhận'}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <div className="max-h-[55vh] space-y-3 overflow-y-auto">
              <p className="text-xs text-muted-foreground">Quyền xem tạm thời hết hạn: {formatTime(content.grant.expires_at)}</p>
              {content.messages.map((message) => (
                <div key={message.id} className="rounded-lg border p-3 text-sm">
                  <p className="text-xs font-medium text-muted-foreground">{{ citizen: 'Người dân', officer: 'Cán bộ', admin: 'Quản trị viên', system: 'Hệ thống' }[message.sender_role] || 'Người gửi'}</p>
                  <p className="mt-1 whitespace-pre-wrap">{message.content}</p>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}
