'use client'

import { FormEvent, useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Clock3, FileSearch, Globe2, RefreshCcw, Send } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

const DOMAIN_LABELS: Record<string, string> = {
  ho_tich_chung_thuc: 'Hộ tịch - Chứng thực',
  dat_dai_xay_dung: 'Đất đai - Xây dựng',
  hanh_chinh_cong: 'Cư trú - An ninh trật tự',
  trat_tu_do_thi: 'Khiếu nại - Tố cáo - Xử phạt',
  an_sinh_y_te_giao_duc: 'An sinh - Y tế - Giáo dục',
}

interface Candidate {
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
}

interface WeeklyMonitorSource {
  id?: string
  name?: string
  scope?: string
  base_url?: string
  enabled?: boolean
  last_checked_at?: string
  last_success_at?: string
  last_status?: string
  last_error?: string
  last_run_stats?: { created?: number; discovered?: number }
}

interface WeeklyMonitor {
  enabled: boolean
  interval_minutes: number
  allowed_domains: string[]
  sources: WeeklyMonitorSource[]
  pending_candidates: Candidate[]
  pending_count: number
  warning?: string
}

function errorDetail(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object' || !('response' in error)) return fallback
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

export default function OfficerProposalsPage() {
  const role = useAuthStore((state) => state.role)
  const [domains, setDomains] = useState<string[]>([])
  const [domain, setDomain] = useState('')
  const [sourceType, setSourceType] = useState('document')
  const [title, setTitle] = useState('')
  const [reason, setReason] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [lawNumber, setLawNumber] = useState('')
  const [content, setContent] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [scanning, setScanning] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [weeklyMonitor, setWeeklyMonitor] = useState<WeeklyMonitor | null>(null)
  const [monitorLoading, setMonitorLoading] = useState(false)

  const loadCandidates = useCallback(async () => {
    if (role !== 'officer') return
    try {
      const response = await apiClient.get<{ candidates: Candidate[] }>('/legal/proposals/candidates', {
        params: { limit: 100 },
      })
      setCandidates(response.data.candidates || [])
    } catch (error) {
      toast.error(errorDetail(error, 'Không tải được danh sách đề xuất.'))
    }
  }, [role])

  const loadWeeklyMonitor = useCallback(async () => {
    if (role !== 'officer') return
    setMonitorLoading(true)
    try {
      const response = await apiClient.get<WeeklyMonitor>('/legal/proposals/weekly-monitor')
      setWeeklyMonitor(response.data)
    } catch (error) {
      toast.error(errorDetail(error, 'Không tải được trạng thái quét tự động hàng tuần.'))
    } finally {
      setMonitorLoading(false)
    }
  }, [role])

  useEffect(() => {
    if (role !== 'officer') return
    void apiClient.get<string[]>('/support/my-domains')
      .then((response) => {
        const allowed = (response.data || []).filter((item) => item in DOMAIN_LABELS)
        setDomains(allowed)
        setDomain((current) => current || allowed[0] || '')
      })
      .catch((error) => toast.error(errorDetail(error, 'Không đọc được lĩnh vực cán bộ.')))
    void loadCandidates()
    void loadWeeklyMonitor()
  }, [loadCandidates, loadWeeklyMonitor, role])

  const scanUrl = async () => {
    if (!sourceUrl.trim()) {
      toast.error('Hãy nhập URL nguồn chính thức trước khi quét.')
      return
    }
    setScanning(true)
    try {
      const response = await apiClient.post<{
        title?: string
        content?: string
        characters?: number
        crawler?: string
      }>('/legal/proposals/preview', { url: sourceUrl.trim() }, { timeout: 120_000 })
      if (response.data.title && !title.trim()) setTitle(response.data.title)
      setContent(response.data.content || '')
      toast.success(`Đã quét ${response.data.characters || 0} ký tự. Nội dung sẽ chỉ vào hàng chờ admin.`)
    } catch (error) {
      toast.error(errorDetail(error, 'Không quét được URL. Có thể gửi URL để admin kiểm tra thủ công.'))
    } finally {
      setScanning(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!domain || title.trim().length < 5 || reason.trim().length < 10) {
      toast.error('Hãy chọn lĩnh vực, nhập tên tối thiểu 5 ký tự và lý do tối thiểu 10 ký tự.')
      return
    }
    if (!sourceUrl.trim() && !file && !content.trim()) {
      toast.error('Cần có URL, file hoặc nội dung văn bản.')
      return
    }
    const body = new FormData()
    body.append('domain', domain)
    body.append('source_type', sourceType)
    body.append('title', title.trim())
    body.append('reason', reason.trim())
    body.append('source_url', sourceUrl.trim())
    body.append('law_number', lawNumber.trim())
    body.append('document_type', sourceType === 'form' ? 'Biểu mẫu đề xuất' : 'Văn bản đề xuất')
    body.append('scope', sourceUrl.toLowerCase().includes('haiphong') ? 'haiphong' : 'central')
    body.append('content', content.trim())
    if (file) body.append('file', file)

    setSubmitting(true)
    try {
      await apiClient.post('/legal/proposals', body, { timeout: 120_000 })
      toast.success('Đề xuất đã được gửi tới hàng chờ admin duyệt.')
      setTitle('')
      setReason('')
      setSourceUrl('')
      setLawNumber('')
      setContent('')
      setFile(null)
      await loadCandidates()
    } catch (error) {
      toast.error(errorDetail(error, 'Không gửi được đề xuất.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (role !== 'officer') {
    return (
      <main className="mx-auto max-w-xl p-6">
        <Card><CardHeader><CardTitle>Chức năng dành cho cán bộ</CardTitle></CardHeader><CardContent><Button asChild variant="outline"><Link href="/search">Về hỏi đáp</Link></Button></CardContent></Card>
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-5 md:p-8">
      <div>
        <h1 className="text-2xl font-bold">Đề xuất văn bản cho admin</h1>
        <p className="text-sm text-muted-foreground">Cán bộ gửi URL hoặc file trong lĩnh vực được phân công. Hệ thống quét nội dung và chỉ tạo candidate chờ admin duyệt.</p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2"><Clock3 className="h-5 w-5" />Quét tự động hàng tuần theo lĩnh vực</CardTitle>
              <CardDescription>Hệ thống quét nguồn chính thức mỗi 7 ngày, tự phân loại và chỉ hiện candidate thuộc lĩnh vực tài khoản. Admin vẫn là người duyệt cuối.</CardDescription>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={loadWeeklyMonitor} disabled={monitorLoading}>
              <RefreshCcw className={`mr-2 h-4 w-4 ${monitorLoading ? 'animate-spin' : ''}`} />Làm mới trạng thái
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {weeklyMonitor?.warning && <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{weeklyMonitor.warning}</p>}
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">Chu kỳ: 7 ngày</Badge>
            <Badge variant="outline">Nguồn phù hợp: {weeklyMonitor?.sources.length ?? 0}</Badge>
            <Badge variant="outline">Candidate chờ admin: {weeklyMonitor?.pending_count ?? 0}</Badge>
            {(weeklyMonitor?.allowed_domains || []).map((item) => <Badge key={item}>{DOMAIN_LABELS[item] || item}</Badge>)}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {(weeklyMonitor?.sources || []).map((source) => (
              <div key={source.id || source.base_url} className="rounded border p-3 text-sm">
                <div className="flex items-center gap-2 font-medium"><Globe2 className="h-4 w-4" />{source.name || source.base_url}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {source.last_checked_at ? `Lần quét: ${new Date(source.last_checked_at).toLocaleString('vi-VN')}` : 'Chưa quét'} · {source.last_status || 'chờ lịch'}
                </div>
                {source.last_error && <p className="mt-2 text-xs text-red-600">Lỗi nguồn: {source.last_error}</p>}
              </div>
            ))}
          </div>
          {(weeklyMonitor?.pending_candidates || []).length > 0 && (
            <div className="space-y-2">
              <p className="text-sm font-medium">Văn bản mới đúng lĩnh vực đang chờ admin duyệt</p>
              {weeklyMonitor!.pending_candidates.slice(0, 5).map((item) => (
                <div key={item.id} className="flex flex-wrap items-center gap-2 rounded border p-2 text-sm">
                  <span className="font-medium">{item.title || 'Chưa có tên'}</span>
                  <Badge variant="outline">{item.law_number || 'Chưa có số hiệu'}</Badge>
                  <Badge variant="secondary">{DOMAIN_LABELS[item.domain || ''] || item.domain}</Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><FileSearch className="h-5 w-5" />Tạo đề xuất</CardTitle><CardDescription>Không văn bản nào được tự động đưa vào kho trả lời.</CardDescription></CardHeader>
        <CardContent>
          <form className="grid gap-4 md:grid-cols-2" onSubmit={submit}>
            <div className="space-y-2"><Label>Lĩnh vực</Label><Select value={domain} onValueChange={setDomain}><SelectTrigger><SelectValue placeholder="Chọn lĩnh vực" /></SelectTrigger><SelectContent>{domains.map((item) => <SelectItem key={item} value={item}>{DOMAIN_LABELS[item]}</SelectItem>)}</SelectContent></Select></div>
            <div className="space-y-2"><Label>Loại nguồn</Label><Select value={sourceType} onValueChange={setSourceType}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="document">Văn bản pháp luật</SelectItem><SelectItem value="form">Biểu mẫu</SelectItem><SelectItem value="procedure">Thủ tục/hướng dẫn</SelectItem><SelectItem value="reference">Tài liệu tham khảo</SelectItem></SelectContent></Select></div>
            <div className="space-y-2 md:col-span-2"><Label>Tên văn bản</Label><Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Tên văn bản hoặc tài liệu đề xuất" /></div>
            <div className="space-y-2"><Label>Số hiệu (nếu có)</Label><Input value={lawNumber} onChange={(event) => setLawNumber(event.target.value)} placeholder="Ví dụ: 12/2026/NĐ-CP" /></div>
            <div className="space-y-2"><Label>File PDF/DOCX/TXT (nếu có)</Label><Input type="file" accept=".pdf,.docx,.txt,.md" onChange={(event) => setFile(event.target.files?.[0] || null)} /></div>
            <div className="space-y-2 md:col-span-2"><Label>URL nguồn chính thức</Label><div className="flex gap-2"><Input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://vbpl.vn/... hoặc cổng cơ quan nhà nước" /><Button type="button" variant="outline" onClick={scanUrl} disabled={scanning || !sourceUrl.trim()}><RefreshCcw className={`mr-2 h-4 w-4 ${scanning ? 'animate-spin' : ''}`} />{scanning ? 'Đang quét' : 'Quét URL'}</Button></div></div>
            <div className="space-y-2 md:col-span-2"><Label>Lý do đề xuất</Label><Textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Văn bản này bổ sung hoặc thay đổi nội dung gì trong lĩnh vực phụ trách?" /></div>
            {content && <div className="space-y-2 md:col-span-2"><Label>Nội dung đã quét (chỉ để admin kiểm tra)</Label><Textarea className="max-h-56 font-mono text-xs" value={content} onChange={(event) => setContent(event.target.value)} /></div>}
            <div className="md:col-span-2"><Button type="submit" disabled={submitting || !domain}><Send className="mr-2 h-4 w-4" />{submitting ? 'Đang gửi...' : 'Gửi tới admin duyệt'}</Button></div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Trạng thái đề xuất trong lĩnh vực của bạn</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {candidates.length === 0 && <p className="text-sm text-muted-foreground">Chưa có đề xuất nào.</p>}
          {candidates.map((item) => {
            const status = item.review_status || item.status || 'pending'
            const feedback = item.requested_changes_note || item.review_note
            return <div key={item.id} className="rounded border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <b>{item.title || 'Chưa có tên'}</b>
                <Badge variant={status === 'changes_requested' ? 'destructive' : 'secondary'}>{status}</Badge>
                <Badge variant="outline">{DOMAIN_LABELS[item.domain || ''] || item.domain}</Badge>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{item.law_number || 'Chưa có số hiệu'} · {item.source_type || 'document'}</p>
              {status === 'changes_requested' && feedback && <p className="mt-2 rounded bg-amber-50 p-2 text-sm text-amber-900"><b>Admin yêu cầu bổ sung:</b> {feedback}</p>}
            </div>
          })}
        </CardContent>
      </Card>
    </main>
  )
}
