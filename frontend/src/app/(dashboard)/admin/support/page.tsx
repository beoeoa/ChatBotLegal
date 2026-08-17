'use client'

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Clock3, RefreshCw, ShieldCheck } from 'lucide-react'
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

interface SupportMetadata {
  id: string
  canonical_domain: string
  status: string
  priority: string
  assigned_officer_id?: string | null
  assignment_generation: number
  created_at: string
  updated_at: string
  first_response_due_at?: string | null
  resolution_due_at?: string | null
  overdue: boolean
}

interface SensitiveTicketContent {
  grant: { expires_at: string; audit_event_id: string }
  ticket: { id: string; question?: string; status: string }
  messages: Array<{ id: string; sender_role: string; content: string; created_at: string }>
}

const DOMAINS = [
  'ho_tich_chung_thuc',
  'dat_dai_xay_dung',
  'an_sinh_y_te_giao_duc',
  'hanh_chinh_cong',
  'trat_tu_do_thi',
]

function formatTime(value?: string | null) {
  if (!value) return 'Chưa xác định'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' }).format(date)
}

export default function AdminSupportPage() {
  const [items, setItems] = useState<SupportMetadata[]>([])
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')
  const [domainFilter, setDomainFilter] = useState('all')
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [content, setContent] = useState<SensitiveTicketContent | null>(null)
  const [openingContent, setOpeningContent] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await apiClient.get<SupportMetadata[]>('/support/admin/tickets', {
        params: {
          ...(statusFilter !== 'all' ? { status: statusFilter } : {}),
          ...(domainFilter !== 'all' ? { domain: domainFilter } : {}),
        },
      })
      setItems(response.data || [])
    } finally {
      setLoading(false)
    }
  }, [domainFilter, statusFilter])

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
    } finally {
      setOpeningContent(false)
    }
  }

  return (
    <AppShell>
      <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-6xl space-y-5">
          <header>
            <h1 className="text-2xl font-bold">Vận hành hỗ trợ trực tuyến</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Danh sách này chỉ hiển thị SLA và metadata phân công. Nội dung riêng tư cần lý do và audit riêng.
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
                    {DOMAINS.map((domain) => <SelectItem key={domain} value={domain}>{domain}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button variant="outline" onClick={() => void load()} disabled={loading}>
                  <RefreshCw className={`mr-1 h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> Làm mới
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {items.length === 0 && !loading && <p className="text-sm text-muted-foreground">Không có yêu cầu phù hợp.</p>}
              {items.map((item) => (
                <article key={item.id} className={`rounded-lg border p-4 ${item.overdue ? 'border-red-300 bg-red-50/40' : ''}`}>
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={item.overdue ? 'destructive' : 'secondary'}>{item.status}</Badge>
                        <Badge variant="outline">{item.priority}</Badge>
                        {item.overdue && <span className="inline-flex items-center gap-1 text-xs font-medium text-red-700"><AlertTriangle className="h-3.5 w-3.5" />Quá SLA phản hồi</span>}
                      </div>
                      <p className="text-sm font-medium">{item.canonical_domain}</p>
                      <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
                        <span>Phân công: {item.assigned_officer_id || 'Chưa phân công'}</span>
                        <span>Thế hệ phân công: {item.assignment_generation}</span>
                        <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />Phản hồi trước: {formatTime(item.first_response_due_at)}</span>
                        <span>Xử lý trước: {formatTime(item.resolution_due_at)}</span>
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => { setSelectedTicketId(item.id); setReason(''); setContent(null) }}
                    >
                      <ShieldCheck className="mr-1 h-4 w-4" /> Mở nội dung có kiểm soát
                    </Button>
                  </div>
                </article>
              ))}
            </CardContent>
          </Card>
        </div>
      </main>

      <Dialog open={Boolean(selectedTicketId)} onOpenChange={(open) => { if (!open) setSelectedTicketId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Truy cập nội dung nhạy cảm</DialogTitle>
            <DialogDescription>
              Nhập lý do nghiệp vụ cụ thể. Hệ thống sẽ ghi actor, mục tiêu, lý do và thời gian vào audit trước khi trả nội dung.
            </DialogDescription>
          </DialogHeader>
          {!content ? (
            <>
              <Input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Ví dụ: Kiểm tra khiếu nại SLA số…" aria-label="Lý do truy cập" />
              <DialogFooter>
                <Button variant="outline" onClick={() => setSelectedTicketId(null)}>Huỷ</Button>
                <Button onClick={() => void openSensitiveContent()} disabled={reason.trim().length < 8 || openingContent}>
                  {openingContent ? 'Đang ghi audit…' : 'Ghi audit và mở nội dung'}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <div className="max-h-[55vh] space-y-3 overflow-y-auto">
              <p className="text-xs text-muted-foreground">Quyền xem tạm thời hết hạn: {formatTime(content.grant.expires_at)}</p>
              {content.messages.map((message) => (
                <div key={message.id} className="rounded-lg border p-3 text-sm">
                  <p className="text-xs font-medium text-muted-foreground">{message.sender_role}</p>
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
