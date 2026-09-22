'use client'

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FileSearch, History, Pencil, Send, X } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { useOperatingScope } from '@/lib/hooks/use-operating-scope'
import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type { LegalDomainConfig } from '@/lib/types/api'
import { formatApiError } from '@/lib/utils/error-handler'
import { systemStatusLabel } from '@/lib/utils/system-labels'

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
  revision?: number
  proposal_revision?: number
  proposal_thread_id?: string
  revision_history?: Array<{ id: string; revision?: number; status?: string; review_status?: string; updated_at?: string }>
  uploaded_file?: { filename?: string; size?: number; sha256?: string; content_type?: string }
  content?: string
  source_url?: string
  document_type?: string
  issuing_agency?: string
  issued_date?: string
  effective_date?: string
}

function errorDetail(error: unknown, fallback: string): string {
  return formatApiError(error, fallback)
}

export default function OfficerProposalsPage() {
  const role = useAuthStore((state) => state.role)
  const { scope: operatingScope, error: scopeError } = useOperatingScope()
  const [adminDomains, setAdminDomains] = useState<LegalDomainConfig[]>([])
  const domains = useMemo(
    () =>
      role === 'admin'
        ? adminDomains.filter((item) => item.is_active).map((item) => item.code)
        : operatingScope?.domains || [],
    [adminDomains, operatingScope?.domains, role],
  )
  const domainLabels = useMemo(
    () =>
      role === 'admin'
        ? Object.fromEntries(adminDomains.map((item) => [item.code, item.name]))
        : operatingScope?.domain_labels || {},
    [adminDomains, operatingScope?.domain_labels, role],
  )
  const domainLabel = (code?: string) =>
    (code && domainLabels[code]) || code || 'Lĩnh vực khác'
  const canCreateProposal = role === 'officer' && Boolean(operatingScope?.can_manage_content)
  const [responsibleUnit, setResponsibleUnit] = useState('')
  const [domain, setDomain] = useState('')
  const eligibleUnits = (operatingScope?.proposal_units || []).filter(unit => unit.domains.includes(domain))
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
  const [listError, setListError] = useState('')
  const [listLoading, setListLoading] = useState(true)
  const submitInFlight = useRef(false)
  const [editing, setEditing] = useState<Candidate | null>(null)
  const [loadingEdit, setLoadingEdit] = useState(false)
  const [fileInputKey, setFileInputKey] = useState(0)
  const formRef = useRef<HTMLFormElement | null>(null)
  useEffect(() => {
    if (!domains.includes(domain)) setDomain(domains[0] || '')
  }, [domain, domains])
  useEffect(() => {
    const choices = (operatingScope?.proposal_units || []).filter(unit => unit.domains.includes(domain))
    setResponsibleUnit(current => choices.some(unit => unit.id === current) ? current : choices[0]?.id || '')
  }, [operatingScope, domain])

  useEffect(() => {
    if (role !== 'admin') {
      setAdminDomains([])
      return
    }
    let active = true
    void apiClient
      .get<LegalDomainConfig[]>('/settings/legal-domains')
      .then((response) => {
        if (active) setAdminDomains(response.data || [])
      })
      .catch(() => {
        if (active) setAdminDomains([])
      })
    return () => {
      active = false
    }
  }, [role])

  const loadCandidates = useCallback(async () => {
    setListLoading(true)
    try {
      const response = await apiClient.get<{ candidates?: Candidate[] }>('/legal/proposals/candidates')
      setCandidates(response.data.candidates || [])
      setListError('')
    } catch {
      setListError('Chưa tải được danh sách đề xuất. Dữ liệu trước đó được giữ lại; hãy thử lại.')
    } finally { setListLoading(false) }
  }, [])

  useEffect(() => {
    void loadCandidates()
  }, [loadCandidates])

  const resetForm = () => {
    setEditing(null)
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
    setFileInputKey((value) => value + 1)
  }

  const beginSupplement = async (candidate: Candidate) => {
    try {
      setLoadingEdit(true)
      const response = await apiClient.get<{ candidate?: Candidate }>(`/legal/proposals/candidates/${candidate.id}`)
      const item = response.data.candidate
      if (!item) throw new Error('proposal_candidate_not_found')
      setEditing(item)
      setDomain(item.domain || domains[0] || '')
      setSourceType(item.source_type || 'document')
      setTitle(item.title || '')
      setReason(item.proposal_reason || '')
      setSourceUrl(item.source_url || '')
      setLawNumber(item.law_number || '')
      setDocumentType(item.document_type || '')
      setIssuingAgency(item.issuing_agency || '')
      setIssuedDate(String(item.issued_date || '').slice(0, 10))
      setEffectiveDate(String(item.effective_date || '').slice(0, 10))
      setContent(item.content || '')
      setFile(null)
      setFileInputKey((value) => value + 1)
      window.requestAnimationFrame(() => formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    } catch (error) {
      toast.error(errorDetail(error, 'Không tải được dữ liệu đề xuất để bổ sung.'))
    } finally {
      setLoadingEdit(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (submitInFlight.current) return
    if (!canCreateProposal) { toast.error(scopeError || 'Chưa có phòng ban hoặc quyền hỗ trợ còn hạn.'); return }
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
      submitInFlight.current = true
      setSubmitting(true)
      const formPayload = new FormData()
      formPayload.append('domain', domain)
      formPayload.append('source_type', sourceType)
      formPayload.append('title', title.trim())
      formPayload.append('reason', reason.trim())
      if (!editing && responsibleUnit) formPayload.append('metadata_json', JSON.stringify({ primary_organization_unit_id: responsibleUnit }))
      if (sourceUrl.trim()) formPayload.append('source_url', sourceUrl.trim())
      if (lawNumber.trim()) formPayload.append('law_number', lawNumber.trim())
      if (documentType.trim()) formPayload.append('document_type', documentType.trim())
      if (issuingAgency.trim()) formPayload.append('issuing_agency', issuingAgency.trim())
      if (issuedDate) formPayload.append('issued_date', issuedDate)
      if (effectiveDate) formPayload.append('effective_date', effectiveDate)
      if (content.trim()) formPayload.append('content', content.trim())
      if (file) formPayload.append('file', file)

      if (editing) {
        formPayload.append('expected_revision', String(editing.proposal_revision || editing.revision || 1))
        await apiClient.post(`/legal/proposals/${editing.id}/resubmit`, formPayload, {
          headers: {
            'Content-Type': 'multipart/form-data',
            'Idempotency-Key': crypto.randomUUID(),
          },
        })
        toast.success('Đã nộp lại đề xuất. Đề xuất cũ được giữ trong lịch sử đối chiếu.')
      } else {
        await apiClient.post('/legal/proposals', formPayload, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
        toast.success('Đã gửi đề xuất tới quản trị viên để kiểm tra và duyệt nguồn.')
      }
      resetForm()
      await loadCandidates()
    } catch (error) {
      toast.error(errorDetail(error, 'Không gửi được đề xuất.'))
    } finally {
      submitInFlight.current = false
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
            <h1 className="text-xl md:text-2xl font-bold">Đề xuất văn bản cho quản trị viên</h1>
            <p className="text-sm text-muted-foreground">Cán bộ gửi đường dẫn, tệp hoặc nội dung trong lĩnh vực được phân công. Quản trị viên kiểm tra nguồn, duyệt và quyết định nhập vào kho pháp luật.</p>
          </div>

          {role === 'officer' && <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><FileSearch className="h-5 w-5" />{editing ? 'Bổ sung và nộp lại đề xuất' : 'Tạo đề xuất'}</CardTitle>
                <CardDescription>{editing ? 'Thông tin cũ đã được điền lại. Chỉ chọn tệp mới nếu muốn thay thế tệp hiện tại.' : 'Không văn bản nào được tự động đưa vào kho trả lời trước khi quản trị viên duyệt.'}</CardDescription>
              </CardHeader>
            <CardContent>
              {!canCreateProposal && <p role="status" className="mb-4 text-sm text-muted-foreground">{scopeError || 'Chưa có phòng ban hoạt động hoặc quyền hỗ trợ còn hạn. Bạn vẫn được tra cứu văn bản dùng chung.'}</p>}
              <form ref={formRef} className="grid gap-4 md:grid-cols-2" onSubmit={submit}>
                {editing && (
                  <div className="md:col-span-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                    <div className="flex items-start justify-between gap-3">
                      <div><b>Quản trị viên yêu cầu bổ sung:</b> {editing.requested_changes_note || editing.review_note || 'Vui lòng kiểm tra và bổ sung thông tin.'}<p className="mt-1 text-xs">Lần gửi {editing.proposal_revision || editing.revision || 1} · bản mới sẽ được tạo riêng, không ghi đè lịch sử.</p></div>
                      <Button type="button" variant="ghost" size="sm" onClick={resetForm}><X className="mr-1 h-4 w-4" />Hủy chỉnh sửa</Button>
                    </div>
                  </div>
                )}
                <div className="space-y-2">
                  <Label>Lĩnh vực *</Label>
                  <Select value={domain} onValueChange={setDomain}>
                    <SelectTrigger aria-label="Lĩnh vực đề xuất">
                      <SelectValue placeholder="Chọn lĩnh vực" />
                    </SelectTrigger>
                    <SelectContent>
                      {domains.map((item) => (
                        <SelectItem key={item} value={item}>
                          {domainLabel(item)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {eligibleUnits.length > 0 && !editing && <label className="grid gap-2 text-sm">Phòng ban chịu trách nhiệm
                  <select aria-label="Phòng ban chịu trách nhiệm" className="h-10 rounded-md border bg-background px-3" value={responsibleUnit} onChange={event => setResponsibleUnit(event.target.value)}>
                    {eligibleUnits.map(unit => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
                  </select>
                </label>}
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
                  <Input key={fileInputKey} type="file" accept=".pdf,.docx,.txt,.md" onChange={(event) => setFile(event.target.files?.[0] || null)} />
                  {editing?.uploaded_file && <p className="text-xs text-muted-foreground">Tệp hiện tại: <b>{editing.uploaded_file.filename || 'tệp đã tải'}</b>. Không chọn tệp mới để kế thừa.</p>}
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
                  <Label>Đường dẫn nguồn chính thức (nếu có)</Label>
                  <Input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://vbpl.vn/... hoặc cổng cơ quan nhà nước" />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Lý do đề xuất *</Label>
                  <Textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Văn bản này bổ sung hoặc thay đổi nội dung gì trong lĩnh vực phụ trách? (tối thiểu 10 ký tự)" required />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Nội dung văn bản (nếu có)</Label>
                  <Textarea className="max-h-56 font-mono text-xs" value={content} onChange={(event) => setContent(event.target.value)} placeholder="Có thể dán nội dung để quản trị viên đối chiếu; hệ thống không tự quét nguồn từ màn hình cán bộ." />
                </div>
                <div className="md:col-span-2">
                  <Button type="submit" disabled={submitting || !canCreateProposal || !domain || !title.trim() || !reason.trim()}>
                    <Send className="mr-2 h-4 w-4" />
                    {submitting ? 'Đang gửi...' : editing ? 'Nộp lại cho quản trị viên' : 'Gửi duyệt'}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>}

          <Card>
            <CardHeader>
              <CardTitle>{role === 'admin' ? 'Danh sách đề xuất của cán bộ' : 'Trạng thái đề xuất trong lĩnh vực của bạn'}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {listError && <div role="alert" className="text-sm text-destructive">{listError} <Button variant="outline" size="sm" disabled={listLoading} onClick={() => void loadCandidates()}>Thử lại</Button></div>}
              {listLoading && <p role="status" className="text-sm text-muted-foreground">Đang tải đề xuất…</p>}
              {!listLoading && !listError && candidates.length === 0 && <p className="text-sm text-muted-foreground">Chưa có đề xuất nào.</p>}
              {candidates.map((item) => {
                const status = item.review_status || item.status || 'pending'
                const feedback = item.requested_changes_note || item.review_note
                return (
                  <div key={item.id} className="rounded border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <b>{item.title || 'Chưa có tên'}</b>
                      <Badge variant={status === 'changes_requested' ? 'destructive' : 'secondary'}>{systemStatusLabel(status)}</Badge>
                      <Badge variant="outline">{domainLabel(item.domain)}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{item.law_number || 'Chưa có số hiệu'} · {item.source_type === 'document' ? 'Văn bản' : item.source_type === 'form' ? 'Biểu mẫu' : 'Nguồn tài liệu'}</p>
                    {status === 'changes_requested' && feedback && (
                      <p className="mt-2 rounded bg-amber-50 p-2 text-sm text-amber-900"><b>Quản trị viên yêu cầu bổ sung:</b> {feedback}</p>
                    )}
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <span className="text-xs text-muted-foreground">Lần gửi {item.proposal_revision || item.revision || 1}</span>
                      {role === 'officer' && status === 'changes_requested' && <Button type="button" size="sm" variant="outline" disabled={loadingEdit} onClick={() => void beginSupplement(item)}><Pencil className="mr-1 h-4 w-4" />Bổ sung thông tin</Button>}
                      {item.revision_history && item.revision_history.length > 1 && <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><History className="h-3 w-3" />{item.revision_history.length} lần gửi</span>}
                    </div>
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
