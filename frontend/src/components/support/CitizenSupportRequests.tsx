'use client'

import { Clock3, RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

export interface CitizenSupportTicket {
  id: string
  domain?: string | null
  canonical_domain?: string | null
  status: string
  priority?: string
  created_at: string
  position?: number | null
}

interface CitizenSupportRequestsProps {
  tickets: CitizenSupportTicket[]
  loading: boolean
  onRefresh: () => void
  onOpen: (ticketId: string) => void
  onCancel: (ticketId: string) => void
}

const statusLabels: Record<string, string> = {
  waiting: 'Đang chờ cán bộ',
  queued: 'Đang chờ cán bộ',
  assigned: 'Đã phân công',
  active: 'Đang trao đổi',
  waiting_citizen: 'Chờ bạn phản hồi',
  waiting_officer: 'Chờ cán bộ phản hồi',
  resolved: 'Đã xử lý',
  closed: 'Đã đóng',
  cancelled: 'Đã huỷ',
  expired: 'Đã hết hạn',
}

export function CitizenSupportRequests({
  tickets,
  loading,
  onRefresh,
  onOpen,
  onCancel,
}: CitizenSupportRequestsProps) {
  return (
    <section className="rounded-xl border bg-muted/10 p-3" aria-labelledby="my-support-requests-title">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 id="my-support-requests-title" className="text-sm font-semibold">Yêu cầu của tôi</h3>
          <p className="text-xs text-muted-foreground">Theo dõi hàng chờ mà không cần giữ kết nối realtime.</p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          Làm mới
        </Button>
      </div>
      {!loading && tickets.length === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">Bạn chưa có yêu cầu hỗ trợ nào.</p>
      )}
      <div className="mt-3 space-y-2">
        {tickets.map((ticket) => {
          const canCancel = ['waiting', 'queued', 'assigned'].includes(ticket.status)
          return (
            <div key={ticket.id} className="flex flex-col gap-2 rounded-lg border bg-background p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{statusLabels[ticket.status] || ticket.status}</Badge>
                  {ticket.position && (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
                      Vị trí {ticket.position}
                    </span>
                  )}
                </div>
                <p className="mt-1 truncate text-xs text-muted-foreground">
                  {ticket.canonical_domain || ticket.domain || 'Chưa xác định lĩnh vực'}
                </p>
              </div>
              <div className="flex gap-2">
                <Button type="button" size="sm" variant="outline" onClick={() => onOpen(ticket.id)}>
                  Xem yêu cầu
                </Button>
                {canCancel && (
                  <Button type="button" size="sm" variant="ghost" onClick={() => onCancel(ticket.id)}>
                    Huỷ yêu cầu
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
