'use client'

import { FormEvent, useCallback, useEffect, useState } from 'react'
import { FileSearch, Send } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { AppShell } from '@/components/layout/AppShell'
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
  hanh_chinh_cong: 'Hành chính công',
  trat_tu_do_thi: 'Trật tự đô thị',
  cu_tru_an_ninh: 'Cư trú - An ninh trật tự',
  khieu_nai_to_cao_xu_phat: 'Khiếu nại - Tố cáo - Xử phạt',
  an_sinh_y_te_giao_duc: 'An sinh - Y tế - Giáo dục',
}

const DEFAULT_DOMAINS = Object.keys(DOMAIN_LABELS)

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

function errorDetail(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object' || !('response' in error)) return fallback
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

export default function OfficerProposalsPage() {
  const role = useAuthStore((state) => state.role)
  const [domains, setDomains] = useState<string[]>(DEFAULT_DOMAINS)
  const [domain, setDomain] = useState(DEFAULT_DOMAINS[0])
  const [sourceType, setSourceType] = useState('document')
  const [title, setTitle] = useState('')
  const [reason, setReason] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [lawNumber, setLawNumber] = useState('')
  const [documentType, setDocumentType] = useState('')
  const [issuingAgency, setIssuingAgency] = useState('')
  const [issuedDate, setIssuedDate] = useState('')
  const [effectiveDate, setEffectiveDate] = useState('')
  const [content, setContent] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [candidates, setCandidates] = useState<Candidate[]>([])

  const loadCandidates = useCallback(async () => {
    try {
      const response = await apiClient.get<{ candidates?: Candidate[] }>('/legal/proposals/candidates')
      setCandidates(response.data.candidates || [])
    } catch {
      setCandidates([])
    }
  }, [])

  useEffect(() => {
    void loadCandidates()
  }, [loadCandidates])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!domain) {
      toast.error('Vui lòng chọn lĩnh vực.')
      return
    }
    if (!title.trim() || title.trim().length < 5) {
      toast.error('Tên văn bản cần có ít nhất 5 ký tự.')
      return
    }
    if (!reason.trim() || reason.trim().length < 10) {
      toast.error('Lý do đề xuất cần có ít nhất 10 ký tự.')
      return
    }
    try {
      setSubmitting(true)
      const formPayload = new FormData()
      formPayload.append('domain', domain)
      formPayload.append('source_type', sourceType)
      formPayload.append('title', title.trim())
      formPayload.append('reason', reason.trim())
      if (sourceUrl.trim()) formPayload.append('source_url', sourceUrl.trim())
      if (lawNumber.trim()) formPayload.append('law_number', lawNumber.trim())
      if (documentType.trim()) formPayload.append('document_type', documentType.trim())
      if (issuingAgency.trim()) formPayload.append('issuing_agency', issuingAgency.trim())
      if (issuedDate) formPayload.append('issued_date', issuedDate)
      if (effectiveDate) formPayload.append('effective_date', effectiveDate)
      if (content.trim()) formPayload.append('content', content.trim())
      if (file) formPayload.append('file', file)

      await apiClient.post('/legal/proposals', formPayload, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      toast.success('Đã gửi đề xuất tới Admin để kiểm tra và duyệt nguồn.')
      setTitle('')
      setReason('')
      setSourceUrl('')
      setLawNumber('')
      setDocumentType('')
      setIssuingAgency('')
      setIssuedDate('')
      setEffectiveDate('')
      setContent('')
      setFile(null)
      await loadCandidates()
    } catch (error) {
      toast.error(errorDetail(error, 'Không gửi được đề xuất.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (role !== 'officer' && role !== 'admin') {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
          <div className="mx-auto max-w-xl">
            <Card>
              <CardHeader>
                <CardTitle>Chức năng dành cho cán bộ</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">Vui lòng đăng nhập với tài khoản Cán bộ để sử dụng chức năng đề xuất văn bản.</p>
              </CardContent>
            </Card>
          </div>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <div className="mx-auto max-w-5xl space-y-6">
          <div>
            <h1 className="text-xl md:text-2xl font-bold">Đề xuất văn bản cho admin</h1>
            <p className="text-sm text-muted-foreground">Cán bộ gửi URL, file hoặc nội dung trong lĩnh vực được phân công. Admin kiểm tra nguồn, duyệt và quyết định nhập vào kho pháp luật.</p>
          </div>

          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><FileSearch className="h-5 w-5" />Tạo đề xuất</CardTitle><CardDescription>Không văn bản nào được tự động đưa vào kho trả lời trước khi Admin duyệt.</CardDescription></CardHeader>
            <CardContent>
              <form className="grid gap-4 md:grid-cols-2" onSubmit={submit}>
                <div className="space-y-2">
                  <Label>Lĩnh vực *</Label>
                  <Select value={domain} onValueChange={setDomain}>
                    <SelectTrigger>
                      <SelectValue placeholder="Chọn lĩnh vực" />
                    </SelectTrigger>
                    <SelectContent>
                      {domains.map((item) => (
                        <SelectItem key={item} value={item}>
                          {DOMAIN_LABELS[item] || item}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Loại nguồn *</Label>
                  <Select value={sourceType} onValueChange={setSourceType}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="document">Văn bản pháp luật</SelectItem>
                      <SelectItem value="form">Biểu mẫu</SelectItem>
                      <SelectItem value="procedure">Thủ tục/hướng dẫn</SelectItem>
                      <SelectItem value="reference">Tài liệu tham khảo</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Tên văn bản / tài liệu *</Label>
                  <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Tên văn bản hoặc tài liệu đề xuất (tối thiểu 5 ký tự)" required />
                </div>
                <div className="space-y-2">
                  <Label>Số hiệu (nếu có)</Label>
                  <Input value={lawNumber} onChange={(event) => setLawNumber(event.target.value)} placeholder="Ví dụ: 12/2026/NĐ-CP" />
                </div>
                <div className="space-y-2">
                  <Label>File PDF/DOCX/TXT (nếu có)</Label>
                  <Input type="file" accept=".pdf,.docx,.txt,.md" onChange={(event) => setFile(event.target.files?.[0] || null)} />
                </div>
                <div className="space-y-2">
                  <Label>Loại văn bản</Label>
                  <Input value={documentType} onChange={(event) => setDocumentType(event.target.value)} placeholder="Thông tư, Nghị định, Quyết định..." />
                </div>
                <div className="space-y-2">
                  <Label>Cơ quan ban hành</Label>
                  <Input value={issuingAgency} onChange={(event) => setIssuingAgency(event.target.value)} placeholder="Ví dụ: Bộ Công an, UBND Hải Phòng" />
                </div>
                <div className="space-y-2">
                  <Label>Ngày ban hành</Label>
                  <Input type="date" value={issuedDate} onChange={(event) => setIssuedDate(event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Ngày có hiệu lực</Label>
                  <Input type="date" value={effectiveDate} onChange={(event) => setEffectiveDate(event.target.value)} />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>URL nguồn chính thức (nếu có)</Label>
                  <Input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://vbpl.vn/... hoặc cổng cơ quan nhà nước" />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Lý do đề xuất *</Label>
                  <Textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Văn bản này bổ sung hoặc thay đổi nội dung gì trong lĩnh vực phụ trách? (tối thiểu 10 ký tự)" required />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Nội dung văn bản (nếu có)</Label>
                  <Textarea className="max-h-56 font-mono text-xs" value={content} onChange={(event) => setContent(event.target.value)} placeholder="Có thể dán nội dung để Admin đối chiếu; hệ thống không tự crawl từ màn hình cán bộ." />
                </div>
                <div className="md:col-span-2">
                  <Button type="submit" disabled={submitting || !domain || !title.trim() || !reason.trim()}>
                    <Send className="mr-2 h-4 w-4" />
                    {submitting ? 'Đang gửi...' : 'Gửi tới admin duyệt'}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Trạng thái đề xuất trong lĩnh vực của bạn</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {candidates.length === 0 && <p className="text-sm text-muted-foreground">Chưa có đề xuất nào.</p>}
              {candidates.map((item) => {
                const status = item.review_status || item.status || 'pending'
                const feedback = item.requested_changes_note || item.review_note
                return (
                  <div key={item.id} className="rounded border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <b>{item.title || 'Chưa có tên'}</b>
                      <Badge variant={status === 'changes_requested' ? 'destructive' : 'secondary'}>{status}</Badge>
                      <Badge variant="outline">{DOMAIN_LABELS[item.domain || ''] || item.domain}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{item.law_number || 'Chưa có số hiệu'} · {item.source_type || 'document'}</p>
                    {status === 'changes_requested' && feedback && (
                      <p className="mt-2 rounded bg-amber-50 p-2 text-sm text-amber-900"><b>Admin yêu cầu bổ sung:</b> {feedback}</p>
                    )}
                  </div>
                )
              })}
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}
