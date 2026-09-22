'use client'

import Link from 'next/link'
import { useSettings } from '@/lib/hooks/use-settings'
import { legalImportApi, type LegalField } from '@/lib/api/legal-import'
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { ArrowLeft, FileCheck2, Loader2, Plus, RefreshCw, ShieldAlert } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  legalLifecycleApi,
  lifecycleIdempotencyKey,
  type LifecycleCapabilities,
  type LifecycleDraft,
  type LifecycleDraftInput,
} from '@/lib/api/legal-lifecycle'
import { formatApiError } from '@/lib/utils/error-handler'

const emptyForm: LifecycleDraftInput = {
  reason: '', title: '', law_number: '', document_type: '', issuing_agency: '',
  scope: 'Trung ương - toàn quốc', sector: '', issued_date: '', effective_date: '', expired_date: '',
  source_url: '', content: '',
}

const stateLabels: Record<string, string> = {
  draft: 'Bản nháp', duplicate_review: 'Cần rà soát trùng', submitted: 'Chờ duyệt',
  changes_requested: 'Yêu cầu sửa', rejected: 'Đã từ chối', approved: 'Đã duyệt',
}

function errorMessage(error: unknown): string {
  return formatApiError(error, 'Không thể hoàn thành thao tác. Dữ liệu chưa bị thay đổi.')
}

function foldRoutingLabel(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/gi, 'd')
    .replace(/[^a-z0-9]+/gi, ' ')
    .trim()
    .toLocaleLowerCase('vi')
}

export default function LegalLifecycleDraftPage() {
  const { data: settings } = useSettings()
  const [fields, setFields] = useState<LegalField[]>([])
  useEffect(() => { void legalImportApi.fields(10000).then(setFields).catch(() => setFields([])) }, [])
  const [capabilities, setCapabilities] = useState<LifecycleCapabilities | null>(null)
  const [drafts, setDrafts] = useState<LifecycleDraft[]>([])
  const [selected, setSelected] = useState<LifecycleDraft | null>(null)
  const [form, setForm] = useState<LifecycleDraftInput>(emptyForm)
  const [reviewReason, setReviewReason] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const caps = await legalLifecycleApi.capabilities()
      setCapabilities(caps)
      if (caps.editor || caps.reviewer) {
        const result = await legalLifecycleApi.list()
        setDrafts(result.items)
      } else {
        setDrafts([])
      }
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const openDraft = async (draft: LifecycleDraft) => {
    setBusy(true)
    setError('')
    try {
      const detail = await legalLifecycleApi.detail(draft.id)
      setSelected(detail)
      setForm({
        reason: '', logical_document_id: detail.logical_document_id,
        field_id: detail.field_id, domain_slug: detail.domain_slug, primary_organization_unit_id: detail.primary_organization_unit_id,
        title: detail.title || '', law_number: detail.law_number || '',
        document_type: detail.document_type || '', issuing_agency: detail.issuing_agency || '',
        scope: detail.scope || '', sector: detail.sector || '', issued_date: detail.issued_date || '',
        effective_date: detail.effective_date || '', expired_date: detail.expired_date || '',
        source_url: detail.source_url || '', content: detail.content || '',
      })
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true); setError(''); setNotice('')
    try {
      const result = selected
        ? await legalLifecycleApi.update(selected.id, form, selected.revision, lifecycleIdempotencyKey('update'))
        : await legalLifecycleApi.create(form, lifecycleIdempotencyKey('create'))
      setSelected(result)
      setNotice(selected ? 'Đã lưu phiên bản mới.' : 'Đã tạo bản nháp trong khu vực chuẩn bị.')
      setForm((current) => ({ ...current, reason: '' }))
      await load()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const transition = async (action: 'validate' | 'submit') => {
    if (!selected) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await legalLifecycleApi.transition(
        selected.id, action, reviewReason, selected.revision, lifecycleIdempotencyKey(action),
      )
      setSelected(result)
      setReviewReason('')
      setNotice(action === 'validate' ? 'Đã kiểm tra bản nháp.' : 'Đã gửi sang hàng chờ duyệt.')
      await load()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const review = async (decision: 'approved' | 'rejected' | 'changes_requested') => {
    if (!selected) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await legalLifecycleApi.review(
        selected.id, decision, reviewReason, selected.revision, lifecycleIdempotencyKey(`review-${decision}`),
      )
      setSelected(result)
      setReviewReason('')
      setNotice(result.activation_state === 'active' ? 'Đã lập chỉ mục và xác minh chatbot tìm được văn bản.' : result.activation_error || 'Đã ghi nhận quyết định.')
      await load()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const activate = async () => {
    if (!selected) return
    setBusy(true); setError('')
    try {
      const result = await legalLifecycleApi.activate(selected.id, reviewReason, selected.revision)
      setSelected(result); setNotice('Đã lập chỉ mục và xác minh văn bản trong kho tra cứu.')
      await load()
    } catch (caught) { setError(errorMessage(caught)) } finally { setBusy(false) }
  }
  const unit = settings?.organization_units?.find(item => item.id === form.primary_organization_unit_id)
  const selectedDomain = settings?.legal_domains?.find(
    (domain) => domain.code === form.domain_slug
  )
  const allowedFieldLabels = new Set(
    [selectedDomain?.name, ...(selectedDomain?.aliases || [])]
      .filter(Boolean)
      .map((label) => foldRoutingLabel(String(label)))
  )
  const allowedFields = fields.filter(
    (field) =>
      field.id === form.field_id ||
      !form.domain_slug ||
      !settings?.legal_domains?.length ||
      allowedFieldLabels.has(foldRoutingLabel(field.name))
  )

  const canEdit = Boolean(capabilities?.editor && capabilities.writes_enabled && (!selected || ['draft', 'duplicate_review', 'changes_requested'].includes(selected.state)))
  const canReview = Boolean(capabilities?.reviewer && capabilities.writes_enabled && selected?.state === 'submitted' && selected.submitted_by !== capabilities.actor_id)

  return (
    <AppShell>
      <main className="min-h-0 flex-1 overflow-auto bg-muted/20">
        <div className="mx-auto max-w-7xl space-y-6 p-4 pt-16 md:p-8 md:pt-8">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <Button variant="ghost" size="sm" asChild className="-ml-3 mb-2">
                <Link href="/legal-management"><ArrowLeft className="mr-2 h-4 w-4" />Quay lại kho văn bản</Link>
              </Button>
              <h1 className="text-3xl font-semibold tracking-tight">Bản nháp và phiên bản</h1>
              <p className="mt-2 text-sm text-muted-foreground">Bản nháp được lưu riêng và phải qua người kiểm duyệt khác trước khi đưa vào dữ liệu thật.</p>
            </div>
            <Button variant="outline" onClick={() => void load()} disabled={loading || busy}>
              <RefreshCw className="mr-2 h-4 w-4" />Tải lại
            </Button>
          </header>

          {!loading && capabilities && !capabilities.writes_enabled && (
            <Alert data-testid="lifecycle-write-gate"><ShieldAlert className="h-4 w-4" /><AlertTitle>Chức năng biên tập đang tạm khóa</AlertTitle><AlertDescription>Chưa thể tạo, duyệt hoặc kích hoạt bản nháp cho đến khi tài khoản được phân quyền biên tập hoặc kiểm duyệt.</AlertDescription></Alert>
          )}
          {!loading && capabilities && !capabilities.editor && !capabilities.reviewer && (
            <Alert><ShieldAlert className="h-4 w-4" /><AlertTitle>Tài khoản chưa được phân quyền</AlertTitle><AlertDescription>Quyền quản trị viên không tự động bao gồm quyền biên tập hoặc kiểm duyệt pháp lý.</AlertDescription></Alert>
          )}
          {error && <Alert variant="destructive"><AlertTitle>Không thể thực hiện</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          {notice && <Alert><FileCheck2 className="h-4 w-4" /><AlertTitle>Đã ghi nhận</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}

          {loading ? <div className="flex min-h-48 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" aria-label="Đang tải quy trình" /></div> : (
            <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
              <Card>
                <CardHeader className="flex-row items-center justify-between"><CardTitle>Hàng công việc</CardTitle>{capabilities?.editor && <Button size="sm" variant="outline" onClick={() => { setSelected(null); setForm(emptyForm) }}><Plus className="mr-2 h-4 w-4" />Bản nháp mới</Button>}</CardHeader>
                <CardContent className="space-y-3">
                  {drafts.length === 0 ? <p className="text-sm text-muted-foreground">Không có bản nháp phù hợp.</p> : drafts.map((draft) => (
                    <button key={draft.id} type="button" onClick={() => void openDraft(draft)} className="w-full rounded-lg border p-3 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <div className="flex items-center justify-between gap-2"><span className="font-medium">{draft.title || 'Chưa có tiêu đề'}</span><Badge variant="outline">{stateLabels[draft.state] || draft.state}</Badge></div>
                      <p className="mt-1 text-xs text-muted-foreground">{draft.law_number || 'Chưa có số'} · phiên bản {draft.revision}</p>
                    </button>
                  ))}
                </CardContent>
              </Card>

              <div className="space-y-6">
                {capabilities?.editor && (
                  <Card><CardHeader><CardTitle>{selected ? `Chỉnh sửa phiên bản ${selected.revision}` : 'Tạo bản nháp'}</CardTitle></CardHeader><CardContent>
                    <form className="grid gap-4 md:grid-cols-2" onSubmit={save}>
                      <Label className="space-y-2">Phòng ban
                        <select className="h-11 w-full rounded-md border bg-background px-3" value={form.primary_organization_unit_id || ''} disabled={!canEdit} onChange={event => setForm({ ...form, primary_organization_unit_id: event.target.value, domain_slug: '', field_id: null })}>
                          <option value="">Chọn phòng ban</option>{settings?.organization_units?.filter(item => item.is_active).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                        </select>
                      </Label>
                      <Label className="space-y-2">Nhóm lĩnh vực
                        <select className="h-11 w-full rounded-md border bg-background px-3" value={form.domain_slug || ''} disabled={!canEdit || !unit} onChange={event => setForm({ ...form, domain_slug: event.target.value, field_id: null })}>
                          <option value="">Chọn nhóm lĩnh vực</option>{(unit?.domain_codes || []).map(domain => <option key={domain} value={domain}>{settings?.legal_domains?.find(item => item.code === domain)?.name || domain}</option>)}
                        </select>
                      </Label>
                      <Label className="space-y-2">Lĩnh vực văn bản
                        <select className="h-11 w-full rounded-md border bg-background px-3" value={form.field_id || ''} disabled={!canEdit || !form.domain_slug} onChange={event => setForm({ ...form, field_id: Number(event.target.value) || null })}>
                          <option value="">Chọn lĩnh vực</option>{allowedFields.map(field => <option key={field.id} value={field.id}>{field.name}</option>)}
                        </select>
                      </Label>
                      <Label className="space-y-2">Phạm vi<Input value={form.scope || ''} disabled={!canEdit} onChange={event => setForm({ ...form, scope: event.target.value })} /></Label>
                      <Label className="space-y-2">Ngày ban hành<Input type="date" value={form.issued_date || ''} disabled={!canEdit} onChange={event => setForm({ ...form, issued_date: event.target.value })} /></Label>
                      <Label className="space-y-2">Ngày có hiệu lực<Input type="date" value={form.effective_date || ''} disabled={!canEdit} onChange={event => setForm({ ...form, effective_date: event.target.value })} /></Label>
                      <Label className="space-y-2">Ngày hết hiệu lực (nếu có)<Input type="date" value={form.expired_date || ''} disabled={!canEdit} onChange={event => setForm({ ...form, expired_date: event.target.value })} /></Label>
                      <Label className="space-y-2">Tiêu đề<Input aria-label="Tiêu đề bản nháp" value={form.title || ''} onChange={(event) => setForm({ ...form, title: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2">Số/ký hiệu<Input aria-label="Số ký hiệu bản nháp" value={form.law_number || ''} onChange={(event) => setForm({ ...form, law_number: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2">Loại văn bản<Input value={form.document_type || ''} onChange={(event) => setForm({ ...form, document_type: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2">Cơ quan ban hành<Input value={form.issuing_agency || ''} onChange={(event) => setForm({ ...form, issuing_agency: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2 md:col-span-2">URL nguồn chính thức<Input aria-label="URL nguồn chính thức" value={form.source_url || ''} onChange={(event) => setForm({ ...form, source_url: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2 md:col-span-2">Nội dung bản nháp<textarea aria-label="Nội dung bản nháp" className="min-h-40 w-full rounded-md border bg-background p-3 text-sm" value={form.content || ''} onChange={(event) => setForm({ ...form, content: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2 md:col-span-2">Lý do thay đổi<Input aria-label="Lý do thay đổi" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} disabled={!canEdit} /></Label>
                      <div className="md:col-span-2"><Button type="submit" disabled={!canEdit || busy || form.reason.trim().length < 10}>Lưu bản nháp</Button></div>
                    </form>
                  </CardContent></Card>
                )}

                {selected && (
                  <Card><CardHeader><CardTitle>Kiểm tra và quyết định</CardTitle></CardHeader><CardContent className="space-y-4">
                    <div className="flex flex-wrap gap-2"><Badge>{stateLabels[selected.state] || 'Chưa xác định'}</Badge><Badge variant="outline">phiên bản {selected.revision}</Badge>{selected.validation?.blocking?.length ? <Badge variant="destructive">{selected.validation.blocking.length} lỗi cần xử lý</Badge> : selected.validation ? <Badge variant="outline">Không có lỗi cản trở</Badge> : null}</div>
                    {selected.validation?.blocking?.length ? <ul className="list-disc pl-5 text-sm text-destructive">{selected.validation.blocking.map((item) => <li key={item}>{item}</li>)}</ul> : null}
                    <Label className="space-y-2">Lý do kiểm tra/quyết định<Input aria-label="Lý do kiểm tra hoặc quyết định" value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></Label>
                    <div className="flex flex-wrap gap-2">
                      {capabilities?.editor && <><Button variant="outline" onClick={() => void transition('validate')} disabled={!canEdit || busy || reviewReason.trim().length < 10}>Kiểm tra</Button><Button onClick={() => void transition('submit')} disabled={!canEdit || busy || reviewReason.trim().length < 10 || Boolean(selected.validation?.blocking?.length)}>Gửi duyệt</Button></>}
                      {capabilities?.reviewer && <><Button onClick={() => void review('approved')} disabled={!canReview || busy || reviewReason.trim().length < 10}>Phê duyệt</Button><Button variant="outline" onClick={() => void review('changes_requested')} disabled={!canReview || busy || reviewReason.trim().length < 10}>Yêu cầu sửa</Button><Button variant="destructive" onClick={() => void review('rejected')} disabled={!canReview || busy || reviewReason.trim().length < 10}>Từ chối</Button></>}
                    </div>
                    {selected.activation_state === 'active' && <Badge>Đã đưa vào kho tra cứu</Badge>}
                    {capabilities?.reviewer && capabilities.activation_enabled && selected.state === 'approved' && selected.activation_state !== 'active' && <Button onClick={() => void activate()} disabled={busy || reviewReason.trim().length < 10}>Lập chỉ mục / Thử lại</Button>}
                    <p className="text-xs text-muted-foreground">Sau khi duyệt, hệ thống chia đoạn, lập chỉ mục và kiểm tra khả năng tra cứu. Nếu gián đoạn, dùng nút thử lại để tiếp tục cùng phiên bản.</p>
                  </CardContent></Card>
                )}
              </div>
            </div>
          )}
        </div>
      </main>
    </AppShell>
  )
}
