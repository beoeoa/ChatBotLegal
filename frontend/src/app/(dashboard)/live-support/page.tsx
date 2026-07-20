'use client'

import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { AlertCircle, ArrowLeft, Check, FileUp, MessageSquare, Send, Star, UserCheck, Wifi, WifiOff } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { getApiUrl } from '@/lib/config'
import { useAuthStore } from '@/lib/stores/auth-store'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

const DOMAINS: Record<string, string> = {
  ho_tich_chung_thuc: 'Hộ tịch - Chứng thực',
  dat_dai_xay_dung: 'Đất đai - Xây dựng',
  an_sinh_y_te_giao_duc: 'An sinh - Y tế - Giáo dục',
  hanh_chinh_cong: 'Cư trú - An ninh trật tự',
  trat_tu_do_thi: 'Khiếu nại - Tố cáo - Xử phạt',
}

type TicketStatus = 'waiting' | 'assigned' | 'active' | 'closed'

interface Attachment {
  id: string
  name: string
  size: number
  download_url: string
}

interface Message {
  id: string
  sender_id: string
  sender_role: string
  content: string
  attachments?: Attachment[]
  created_at: string
}

interface Ticket {
  id: string
  citizen_id: string
  question: string
  domain: string
  status: TicketStatus
  priority: string
  assigned_officer_id?: string | null
  message_count: number
  unread_count?: number
  created_at: string
  updated_at: string
  messages?: Message[]
  rating?: number | null
}

function apiErrorDetail(error: unknown, fallback: string): string {
  if (typeof error !== 'object' || error === null || !('response' in error)) return fallback
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
}

function authState() {
  try {
    return JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state || {}
  } catch {
    return {}
  }
}

async function supportWebSocketUrl(ticketId?: string, domain?: string) {
  const state = authState()
  let apiUrl = ''
  try {
    apiUrl = await getApiUrl()
  } catch {
    // Realtime must connect to FastAPI directly; Next.js rewrites do not
    // reliably proxy WebSocket upgrades in local development.
    apiUrl = `${window.location.protocol}//${window.location.hostname}:5055`
  }
  const origin = (apiUrl || `${window.location.protocol}//${window.location.hostname}:5055`)
    .replace(/\/$/, '')
    .replace(/^http/, 'ws')
  const params = new URLSearchParams({
    ...(ticketId ? { ticket_id: ticketId } : {}),
    ...(domain ? { domain } : {}),
    ...(state.token ? { token: state.token } : {}),
  })
  return `${origin}/api/support/ws?${params}`
}

export default function LiveSupportPage() {
  const { role, userId, username } = useAuthStore()
  const searchParams = useSearchParams()
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [active, setActive] = useState<Ticket | null>(null)
  const [ticketId, setTicketId] = useState<string | null>(searchParams?.get('ticket') || null)
  const [domain, setDomain] = useState('ho_tich_chung_thuc')
  const [question, setQuestion] = useState('')
  const [input, setInput] = useState('')
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([])
  const [typing, setTyping] = useState(false)
  const [connected, setConnected] = useState(false)
  const [rating, setRating] = useState(0)
  const [transferDomain, setTransferDomain] = useState('')
  const [officerDomains, setOfficerDomains] = useState<string[]>([])
  const socketRef = useRef<WebSocket | null>(null)
  const queueSocketsRef = useRef<WebSocket[]>([])
  const currentUser = userId || username || `legacy:${role || 'citizen'}`

  const refreshList = useCallback(async () => {
    try {
      const response = await apiClient.get<Ticket[]>('/support/tickets')
      setTickets(response.data)
    } catch {
      // Polling is best effort. The page remains usable while a service restarts.
    }
  }, [])

  const refreshTicket = useCallback(async (id: string) => {
    try {
      const response = await apiClient.get<Ticket>(`/support/tickets/${id}`)
      setActive(response.data)
    } catch {
      setActive(null)
    }
  }, [])

  useEffect(() => {
    void refreshList()
    const interval = window.setInterval(() => void refreshList(), 8000)
    return () => window.clearInterval(interval)
  }, [refreshList])

  useEffect(() => {
    if (role !== 'officer') {
      setOfficerDomains([])
      return
    }
    let cancelled = false
    void apiClient.get<string[]>('/support/my-domains')
      .then((response) => {
        if (!cancelled) setOfficerDomains(response.data)
      })
      .catch(() => {
        if (!cancelled) setOfficerDomains([])
      })
    return () => { cancelled = true }
  }, [role])

  useEffect(() => {
    if (role !== 'officer' || officerDomains.length === 0) return
    let cancelled = false
    void Promise.all(officerDomains.map(async (watchDomain) => {
      const socket = new WebSocket(await supportWebSocketUrl(undefined, watchDomain))
      if (cancelled) {
        socket.close()
        return null
      }
      socket.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            if (['queue.created', 'ticket.transferred', 'ticket.reassigned'].includes(data.type)) {
              void refreshList()
              toast.info(`Có yêu cầu hỗ trợ mới thuộc lĩnh vực ${DOMAINS[watchDomain] || watchDomain}.`)
            }
          } catch {
            // Polling is the fallback for malformed or missed events.
          }
        }
      return socket
    })).then((sockets) => {
      if (!cancelled) queueSocketsRef.current = sockets.filter((item): item is WebSocket => item !== null)
    })
    return () => {
      cancelled = true
      queueSocketsRef.current.forEach((socket) => socket.close())
      queueSocketsRef.current = []
    }
  }, [officerDomains, refreshList, role])

  useEffect(() => {
    if (ticketId) void refreshTicket(ticketId)
  }, [refreshTicket, ticketId])

  useEffect(() => {
    if (!ticketId) return
    const interval = window.setInterval(() => void refreshTicket(ticketId), 5000)
    return () => window.clearInterval(interval)
  }, [refreshTicket, ticketId])

  useEffect(() => {
    if (!ticketId || !role || role === 'admin') return
    let socket: WebSocket | null = null
    let cancelled = false
    void supportWebSocketUrl(ticketId).then((url) => {
      if (cancelled) return
      socket = new WebSocket(url)
      socketRef.current = socket
      socket.onopen = () => setConnected(true)
      socket.onclose = () => setConnected(false)
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'typing' && data.sender_id !== currentUser) setTyping(Boolean(data.is_typing))
          if (data.type === 'message.created' || data.type?.startsWith('ticket.')) {
            void refreshTicket(ticketId)
            void refreshList()
          }
        } catch {
          // Polling remains available if a WebSocket event cannot be read.
        }
      }
    })
    return () => {
      cancelled = true
      socket?.close()
      if (socketRef.current === socket) socketRef.current = null
    }
  }, [currentUser, refreshList, refreshTicket, role, ticketId])

  const createTicket = async (event: FormEvent) => {
    event.preventDefault()
    if (question.trim().length < 5) {
      toast.error('Nội dung yêu cầu cần ít nhất 5 ký tự.')
      return
    }
    try {
      const response = await apiClient.post<Ticket>('/support/tickets', { question, domain })
      setTicketId(response.data.id)
      setActive(response.data)
      setQuestion('')
      toast.success('Đã gửi yêu cầu đến hàng chờ cán bộ phụ trách.')
      void refreshList()
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Không thể tạo yêu cầu hỗ trợ.'))
    }
  }

  const claim = async (ticketIdOverride?: string) => {
    const id = ticketIdOverride || ticketId
    if (!id) return
    try {
      setTicketId(id)
      await apiClient.post(`/support/tickets/${id}/claim`, {})
      await refreshTicket(id)
      void refreshList()
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Không thể tiếp nhận phiên.'))
    }
  }

  const closeTicket = async () => {
    if (!ticketId) return
    try {
      await apiClient.patch(`/support/tickets/${ticketId}/close`, { resolution_note: '' })
      await refreshTicket(ticketId)
      void refreshList()
    } catch {
      toast.error('Không thể đóng phiên.')
    }
  }

  const transfer = async () => {
    if (!ticketId || !transferDomain) return
    const reason = window.prompt('Nêu lý do chuyển đúng lĩnh vực:')
    if (!reason?.trim()) return
    try {
      await apiClient.post(`/support/tickets/${ticketId}/decline`, {
        transfer_domain: transferDomain,
        reason,
      })
      await refreshTicket(ticketId)
      void refreshList()
      toast.success('Đã chuyển phiên đến hàng chờ lĩnh vực phù hợp.')
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Không thể chuyển phiên.'))
    }
  }

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault()
    if (!ticketId || !input.trim()) return
    try {
      await apiClient.post(`/support/tickets/${ticketId}/messages`, {
        content: input.trim(),
        attachment_ids: pendingAttachments.map((item) => item.id),
      })
      setInput('')
      setPendingAttachments([])
      socketRef.current?.send(JSON.stringify({ type: 'typing', is_typing: false }))
      await refreshTicket(ticketId)
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Không thể gửi tin nhắn.'))
    }
  }

  const downloadAttachment = async (file: Attachment) => {
    try {
      const apiUrl = await getApiUrl()
      const token = String(authState().token || '')
      const response = await fetch(`${apiUrl}${file.download_url}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(payload?.detail || 'Không thể tải tệp đính kèm.')
      }
      const href = URL.createObjectURL(await response.blob())
      const anchor = window.document.createElement('a')
      anchor.href = href
      anchor.download = file.name || 'tep-dinh-kem'
      anchor.click()
      URL.revokeObjectURL(href)
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : 'Không thể tải tệp đính kèm.')
    }
  }

  const uploadAttachment = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !ticketId) return
    const body = new FormData()
    body.append('file', file)
    try {
      const response = await apiClient.post<Attachment>(`/support/tickets/${ticketId}/attachments`, body)
      setPendingAttachments((old) => [...old, response.data])
      toast.success('Đã đính kèm tệp.')
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Không thể tải tệp.'))
    } finally {
      event.target.value = ''
    }
  }

  const submitRating = async () => {
    if (!ticketId || !rating) return
    try {
      await apiClient.post(`/support/tickets/${ticketId}/rating`, { rating, feedback: '' })
      await refreshTicket(ticketId)
      toast.success('Cảm ơn bạn đã đánh giá.')
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Chưa thể ghi nhận đánh giá.'))
    }
  }

  if (role === 'admin') {
    return (
      <main className="mx-auto max-w-xl space-y-4 p-6">
        <Card>
          <CardHeader><CardTitle>Hỗ trợ trực tuyến dành cho người dân và cán bộ</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>Admin không tham gia nhắn tin trong các phiên hỗ trợ. Người dân được kết nối trực tiếp với cán bộ phụ trách một trong năm lĩnh vực.</p>
            <Button asChild variant="outline"><Link href="/legal-import">Đến hàng duyệt văn bản</Link></Button>
          </CardContent>
        </Card>
      </main>
    )
  }

  if (role === 'citizen' && !ticketId) {
    return (
      <main className="mx-auto max-w-2xl space-y-5 p-5 md:p-8">
        <Link className="text-sm text-primary hover:underline" href="/search">← Về Hỏi đáp</Link>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><MessageSquare className="h-5 w-5" />Hỗ trợ trực tuyến với cán bộ</CardTitle>
            <p className="text-sm text-muted-foreground">Đây là kênh trao đổi với cán bộ phụ trách, không phải chatbot. Hãy chọn đúng lĩnh vực và mô tả rõ thắc mắc.</p>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={createTicket}>
              <Select value={domain} onValueChange={setDomain}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{Object.entries(DOMAINS).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}</SelectContent>
              </Select>
              <Textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={7} placeholder="Mô tả nội dung cần cán bộ hỗ trợ..." />
              <Button type="submit"><Send className="mr-2 h-4 w-4" />Gửi yêu cầu hỗ trợ</Button>
            </form>
          </CardContent>
        </Card>
        {tickets.length > 0 && (
          <Card>
            <CardHeader><CardTitle className="text-base">Các phiên hỗ trợ của bạn</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {tickets.map((ticket) => <button className="block w-full rounded border p-3 text-left hover:bg-muted" key={ticket.id} onClick={() => setTicketId(ticket.id)}><b>{DOMAINS[ticket.domain] || ticket.domain}</b><p className="line-clamp-1 text-sm text-muted-foreground">{ticket.question}</p><Badge>{ticket.status}</Badge></button>)}
            </CardContent>
          </Card>
        )}
      </main>
    )
  }

  return (
    <main className="flex h-[calc(100vh-4rem)] min-h-[580px] overflow-hidden bg-background">
      <aside className="w-80 shrink-0 overflow-y-auto border-r bg-card p-3">
        {role === 'citizen' && (
          <Button asChild variant="ghost" size="sm" className="mb-3 w-full justify-start">
            <Link href="/search?mode=ask"><ArrowLeft className="mr-2 h-4 w-4" />Về giao diện chính</Link>
          </Button>
        )}
        <div className="mb-3 flex items-center justify-between"><b>{role === 'officer' ? 'Hàng chờ lĩnh vực' : 'Phiên hỗ trợ'}</b>{connected ? <Wifi className="h-4 w-4 text-emerald-600" /> : <WifiOff className="h-4 w-4 text-amber-600" />}</div>
        {tickets.map((ticket) => <div key={ticket.id} className={`mb-2 rounded border p-3 ${ticketId === ticket.id ? 'border-primary bg-primary/5' : ''}`}>
          <button onClick={() => setTicketId(ticket.id)} className="w-full text-left hover:bg-muted/50">
            <div className="flex justify-between gap-2"><Badge>{ticket.status}</Badge>{ticket.unread_count ? <Badge variant="destructive">{ticket.unread_count}</Badge> : null}</div>
            <p className="mt-2 line-clamp-2 text-sm">{ticket.question}</p>
            <small className="text-muted-foreground">{DOMAINS[ticket.domain] || ticket.domain}</small>
          </button>
          {role === 'officer' && ticket.status === 'waiting' && !ticket.assigned_officer_id && <Button className="mt-2 w-full" size="sm" onClick={() => void claim(ticket.id)}><UserCheck className="mr-2 h-4 w-4" />Tiếp nhận</Button>}
        </div>)}
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        {!active ? (
          <div className="m-auto text-center text-muted-foreground"><AlertCircle className="mx-auto mb-2 h-9 w-9" />Chọn một phiên hỗ trợ để bắt đầu.</div>
        ) : (
          <>
            <header className="flex flex-wrap items-center justify-between gap-3 border-b p-4"><div><b>{DOMAINS[active.domain] || active.domain}</b><p className="text-sm text-muted-foreground">{active.status} · {connected ? 'Kết nối thời gian thực' : 'Đang dùng polling dự phòng'}</p></div><div className="flex flex-wrap gap-2">{role === 'officer' && !active.assigned_officer_id && <Button onClick={() => void claim()}><UserCheck className="mr-2 h-4 w-4" />Tiếp nhận</Button>}{role === 'officer' && active.status !== 'closed' && <><Select value={transferDomain} onValueChange={setTransferDomain}><SelectTrigger className="w-44"><SelectValue placeholder="Chuyển lĩnh vực" /></SelectTrigger><SelectContent>{Object.entries(DOMAINS).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={!transferDomain} onClick={transfer}>Chuyển phiên</Button></>}{active.status !== 'closed' && <Button variant="outline" onClick={closeTicket}><Check className="mr-2 h-4 w-4" />Đóng phiên</Button>}</div></header>
            <div className="flex-1 space-y-3 overflow-y-auto p-4">{active.messages?.map((message) => <article key={message.id} className={`max-w-[78%] rounded-lg border p-3 ${message.sender_id === currentUser ? 'ml-auto bg-primary text-primary-foreground' : 'bg-card'}`}><div className="mb-1 text-xs opacity-75">{message.sender_role} · {new Date(message.created_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}</div><p className="whitespace-pre-wrap">{message.content}</p>{message.attachments?.map((file) => <button type="button" className="mt-2 block text-left text-xs underline" key={file.id} onClick={() => void downloadAttachment(file)}>↳ {file.name}</button>)}</article>)}{typing && <p className="text-xs text-muted-foreground">Cán bộ/Người dân đang nhập...</p>}</div>
            {active.status === 'closed' && role === 'citizen' ? <div className="border-t p-4"><p className="mb-2 text-sm">Đánh giá hỗ trợ của cán bộ:</p><div className="flex gap-1">{[1, 2, 3, 4, 5].map((value) => <button type="button" onClick={() => setRating(value)} key={value}><Star className={`h-6 w-6 ${value <= rating ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground'}`} /></button>)}<Button size="sm" disabled={!rating} onClick={submitRating}>Gửi</Button></div></div> : active.status !== 'closed' ? <form onSubmit={sendMessage} className="border-t p-3"><div className="mb-2 flex gap-2">{pendingAttachments.map((file) => <Badge key={file.id}>{file.name}</Badge>)}<label className="cursor-pointer"><FileUp className="h-5 w-5" /><input className="hidden" type="file" onChange={uploadAttachment} /></label></div><div className="flex gap-2"><Input value={input} onChange={(event) => { setInput(event.target.value); socketRef.current?.send(JSON.stringify({ type: 'typing', is_typing: true })) }} placeholder="Nhập tin nhắn..." /><Button type="submit"><Send className="h-4 w-4" /></Button></div></form> : null}
          </>
        )}
      </section>
    </main>
  )
}
