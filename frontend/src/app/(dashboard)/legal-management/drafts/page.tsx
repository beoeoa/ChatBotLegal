'use client'

import Link from 'next/link'
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

const emptyForm: LifecycleDraftInput = {
  reason: '', title: '', law_number: '', document_type: '', issuing_agency: '',
  scope: '', sector: '', issued_date: '', effective_date: '', expired_date: '',
  source_url: '', content: '',
}

const stateLabels: Record<string, string> = {
  draft: 'Bản nháp', duplicate_review: 'Cần rà soát trùng', submitted: 'Chờ duyệt',
  changes_requested: 'Yêu cầu sửa', rejected: 'Đã từ chối', approved: 'Đã duyệt',
}

function errorMessage(error: unknown): string {
  const value = error as { response?: { data?: { detail?: { message?: string } | string } } }
  const detail = value.response?.data?.detail
  if (typeof detail === 'object' && detail?.message) return detail.message
  if (typeof detail === 'string') return detail
  return 'Không thể hoàn thành thao tác. Dữ liệu chưa bị thay đổi.'
}

export default function LegalLifecycleDraftPage() {
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
        const result = await legalLifecycleApi.list(caps.reviewer ? 'submitted' : undefined)
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
      setNotice(selected ? 'Đã lưu revision mới.' : 'Đã tạo bản nháp trong khu vực staging.')
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
      setNotice('Đã ghi nhận quyết định; chưa kích hoạt corpus hoặc vector thật.')
      await load()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

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
              <p className="mt-2 text-sm text-muted-foreground">Phase 2 · staging, ETag và phê duyệt tách người. Chưa kích hoạt dữ liệu thật.</p>
            </div>
            <Button variant="outline" onClick={() => void load()} disabled={loading || busy}>
              <RefreshCw className="mr-2 h-4 w-4" />Tải lại
            </Button>
          </header>

          {!loading && capabilities && !capabilities.writes_enabled && (
            <Alert data-testid="lifecycle-write-gate"><ShieldAlert className="h-4 w-4" /><AlertTitle>Cổng ghi Phase 2 đang đóng</AlertTitle><AlertDescription>Workflow được cài đặt nhưng không thể tạo, duyệt hoặc kích hoạt cho đến khi migration cô lập và ánh xạ editor/reviewer được phê duyệt.</AlertDescription></Alert>
          )}
          {!loading && capabilities && !capabilities.editor && !capabilities.reviewer && (
            <Alert><ShieldAlert className="h-4 w-4" /><AlertTitle>Tài khoản chưa được ánh xạ</AlertTitle><AlertDescription>Quyền Admin không tự động cấp quyền biên tập hoặc kiểm duyệt pháp lý.</AlertDescription></Alert>
          )}
          {error && <Alert variant="destructive"><AlertTitle>Không thể thực hiện</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          {notice && <Alert><FileCheck2 className="h-4 w-4" /><AlertTitle>Đã ghi nhận</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}

          {loading ? <div className="flex min-h-48 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" aria-label="Đang tải workflow" /></div> : (
            <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
              <Card>
                <CardHeader className="flex-row items-center justify-between"><CardTitle>Hàng công việc</CardTitle>{capabilities?.editor && <Button size="sm" variant="outline" onClick={() => { setSelected(null); setForm(emptyForm) }}><Plus className="mr-2 h-4 w-4" />Bản nháp mới</Button>}</CardHeader>
                <CardContent className="space-y-3">
                  {drafts.length === 0 ? <p className="text-sm text-muted-foreground">Không có bản nháp phù hợp.</p> : drafts.map((draft) => (
                    <button key={draft.id} type="button" onClick={() => void openDraft(draft)} className="w-full rounded-lg border p-3 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <div className="flex items-center justify-between gap-2"><span className="font-medium">{draft.title || 'Chưa có tiêu đề'}</span><Badge variant="outline">{stateLabels[draft.state] || draft.state}</Badge></div>
                      <p className="mt-1 text-xs text-muted-foreground">{draft.law_number || 'Chưa có số'} · revision {draft.revision}</p>
                    </button>
                  ))}
                </CardContent>
              </Card>

              <div className="space-y-6">
                {capabilities?.editor && (
                  <Card><CardHeader><CardTitle>{selected ? `Chỉnh sửa revision ${selected.revision}` : 'Tạo bản nháp'}</CardTitle></CardHeader><CardContent>
                    <form className="grid gap-4 md:grid-cols-2" onSubmit={save}>
                      <Label className="space-y-2">Tiêu đề<Input aria-label="Tiêu đề bản nháp" value={form.title || ''} onChange={(event) => setForm({ ...form, title: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2">Số/ký hiệu<Input aria-label="Số ký hiệu bản nháp" value={form.law_number || ''} onChange={(event) => setForm({ ...form, law_number: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2">Loại văn bản<Input value={form.document_type || ''} onChange={(event) => setForm({ ...form, document_type: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2">Cơ quan ban hành<Input value={form.issuing_agency || ''} onChange={(event) => setForm({ ...form, issuing_agency: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2 md:col-span-2">URL nguồn chính thức<Input aria-label="URL nguồn chính thức" value={form.source_url || ''} onChange={(event) => setForm({ ...form, source_url: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2 md:col-span-2">Nội dung staging<textarea aria-label="Nội dung staging" className="min-h-40 w-full rounded-md border bg-background p-3 text-sm" value={form.content || ''} onChange={(event) => setForm({ ...form, content: event.target.value })} disabled={!canEdit} /></Label>
                      <Label className="space-y-2 md:col-span-2">Lý do thay đổi<Input aria-label="Lý do thay đổi" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} disabled={!canEdit} /></Label>
                      <div className="md:col-span-2"><Button type="submit" disabled={!canEdit || busy || form.reason.trim().length < 10}>Lưu bản nháp</Button></div>
                    </form>
                  </CardContent></Card>
                )}

                {selected && (
                  <Card><CardHeader><CardTitle>Kiểm tra và quyết định</CardTitle></CardHeader><CardContent className="space-y-4">
                    <div className="flex flex-wrap gap-2"><Badge>{stateLabels[selected.state] || selected.state}</Badge><Badge variant="outline">revision {selected.revision}</Badge>{selected.validation?.blocking?.length ? <Badge variant="destructive">{selected.validation.blocking.length} lỗi chặn</Badge> : selected.validation ? <Badge variant="outline">Không có lỗi chặn</Badge> : null}</div>
                    {selected.validation?.blocking?.length ? <ul className="list-disc pl-5 text-sm text-destructive">{selected.validation.blocking.map((item) => <li key={item}>{item}</li>)}</ul> : null}
                    <Label className="space-y-2">Lý do kiểm tra/quyết định<Input aria-label="Lý do kiểm tra hoặc quyết định" value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></Label>
                    <div className="flex flex-wrap gap-2">
                      {capabilities?.editor && <><Button variant="outline" onClick={() => void transition('validate')} disabled={!canEdit || busy || reviewReason.trim().length < 10}>Kiểm tra</Button><Button onClick={() => void transition('submit')} disabled={!canEdit || busy || reviewReason.trim().length < 10 || Boolean(selected.validation?.blocking?.length)}>Gửi duyệt</Button></>}
                      {capabilities?.reviewer && <><Button onClick={() => void review('approved')} disabled={!canReview || busy || reviewReason.trim().length < 10}>Phê duyệt</Button><Button variant="outline" onClick={() => void review('changes_requested')} disabled={!canReview || busy || reviewReason.trim().length < 10}>Yêu cầu sửa</Button><Button variant="destructive" onClick={() => void review('rejected')} disabled={!canReview || busy || reviewReason.trim().length < 10}>Từ chối</Button></>}
                    </div>
                    <p className="text-xs text-muted-foreground">Không có nút kích hoạt live. Phiên bản đang phục vụ chỉ thay đổi trong một lát cắt được phê duyệt riêng.</p>
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
