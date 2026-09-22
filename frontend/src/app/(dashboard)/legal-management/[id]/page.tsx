'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowLeft, BookOpen, ExternalLink, FileClock, History, Layers3, Pencil, RefreshCw, Scale, ShieldCheck } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { DocumentActions } from '@/components/legal-management/DocumentActions'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { legalManagementApi, type AvailabilitySection, type LegalManagementDetail } from '@/lib/api/legal-management'
import { useSettings } from '@/lib/hooks/use-settings'
import { formatApiError } from '@/lib/utils/error-handler'
import { activeDirectoryUnits } from '@/lib/utils/organization-directory'
import { systemStatusLabel } from '@/lib/utils/system-labels'

function dateLabel(value?: string | null) {
  if (!value) return 'Chưa có dữ liệu'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('vi-VN')
}

function Metadata({ label, value, className = '' }: { label: string; value?: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border bg-background p-3 ${className}`}>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 break-words text-sm">{value || 'Chưa có dữ liệu'}</div>
    </div>
  )
}

function AvailabilityNotice({ section }: { section: AvailabilitySection }) {
  if (section.status === 'available') return null
  return <Alert><AlertTriangle className="h-4 w-4" /><AlertTitle>{section.status === 'degraded' ? 'Dữ liệu chưa đầy đủ' : 'Chưa có dữ liệu'}</AlertTitle><AlertDescription>{section.message || 'Kho dữ liệu này hiện chưa sẵn sàng.'}</AlertDescription></Alert>
}

function SummaryBox({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <Card><CardContent className="p-5"><p className="text-sm text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>{hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}</CardContent></Card>
}

type EditorMode = 'edit'
type MetadataFormState = {
  issued_date: string
  effective_date: string
  expired_date: string
  source_url: string
  gazette_date: string
  signer_title: string
  signer_name: string
  applicability_info: string
  version: string
  reason: string
}

const EMPTY_FORM: MetadataFormState = {
  issued_date: '', effective_date: '', expired_date: '', source_url: '', gazette_date: '',
  signer_title: '', signer_name: '', applicability_info: '', version: '', reason: '',
}

export default function LegalManagementDetailPage() {
  const params = useParams<{ id: string }>()
  const documentId = useMemo(() => decodeURIComponent(String(params?.id || '')), [params?.id])
  const { data: settings } = useSettings()
  const [detail, setDetail] = useState<LegalManagementDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null)
  const [form, setForm] = useState<MetadataFormState>(EMPTY_FORM)
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setDetail(await legalManagementApi.detail(documentId))
    } catch {
      setError('Không thể đọc thông tin văn bản này.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (documentId) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId])

  const document = detail?.document
  const organizationNames = useMemo(
    () => new Map((settings?.organization_units || []).map(unit => [unit.id, unit.short_name || unit.name])),
    [settings?.organization_units],
  )
  const assignableOrganizationUnits = useMemo(
    () => activeDirectoryUnits(settings).map(unit => ({
      id: unit.id,
      name: unit.short_name || unit.name,
    })),
    [settings],
  )
  const organizationLabel = useMemo(() => {
    if (document?.organization_assignment_state === 'shared') return 'Dùng chung toàn hệ thống'
    const names = (document?.organization_unit_ids || []).map(id => organizationNames.get(id) || id)
    return names.length ? names.join(', ') : 'Chưa gắn phòng ban'
  }, [document?.organization_assignment_state, document?.organization_unit_ids, organizationNames])

  function openEditor() {
    if (!document) return
    setForm({
      issued_date: document.issued_date?.slice(0, 10) || '',
      effective_date: document.effective_date?.slice(0, 10) || '',
      expired_date: document.expired_date?.slice(0, 10) || '',
      source_url: document.source_url || '',
      gazette_date: document.gazette_date?.slice(0, 10) || '',
      signer_title: document.signer_title || '',
      signer_name: document.signer_name || '',
      applicability_info: document.applicability_info || '',
      version: document.version || '',
      reason: '',
    })
    setMessage('')
    setFormError('')
    setEditorMode('edit')
  }

  async function submitMetadata(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!document?.metadata_revision) {
      setFormError('Thiếu phiên bản thông tin hiện tại. Hãy tải lại trang.')
      return
    }
    if (form.reason.trim().length < 10) {
      setFormError('Vui lòng ghi lý do hoặc căn cứ đối chiếu, tối thiểu 10 ký tự.')
      return
    }
    if (form.effective_date && form.expired_date && form.expired_date < form.effective_date) {
      setFormError('Ngày hết hiệu lực không được trước ngày có hiệu lực.')
      return
    }
    const confirmValidity = false

    setSaving(true)
    setFormError('')
    try {
      const nullable = (value: string) => value.trim() || null
      const result = await legalManagementApi.updateMetadata(documentId, {
        issued_date: nullable(form.issued_date), effective_date: nullable(form.effective_date),
        expired_date: nullable(form.expired_date), source_url: nullable(form.source_url),
        gazette_date: nullable(form.gazette_date), signer_title: nullable(form.signer_title),
        signer_name: nullable(form.signer_name), applicability_info: nullable(form.applicability_info),
        version: nullable(form.version), reason: form.reason.trim(),
        expected_revision: document.metadata_revision, confirm_validity: confirmValidity,
      })
      setEditorMode(null)
      setMessage(result.message || 'Đã lưu thông tin văn bản.')
      await load()
    } catch (caught) {
      setFormError(formatApiError(caught, 'Không thể lưu thông tin văn bản.'))
    } finally {
      setSaving(false)
    }
  }

  const validityLabel = document?.validity_sync?.display_label
    || (document?.validity_status === 'active' ? 'Đang có hiệu lực'
      : document?.validity_status === 'expired' ? 'Đã hết hiệu lực'
        : document?.validity_status === 'not_yet_effective' ? 'Chưa có hiệu lực'
          : 'Chưa xác minh hiệu lực')

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-auto bg-muted/20">
        <main className="mx-auto max-w-[1280px] space-y-6 p-4 pt-16 md:p-8 md:pt-8">
          <header className="space-y-4">
            <Link href="/legal-management" className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
              <ArrowLeft className="mr-2 h-4 w-4" /> Quay lại kho văn bản
            </Link>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={document?.current_answer_eligible === false ? 'destructive' : 'default'}>{validityLabel}</Badge>
                  {document?.stored_status && <Badge variant="outline">Kho: {systemStatusLabel(document.stored_status)}</Badge>}
                </div>
                <h1 className="mt-3 max-w-4xl text-2xl font-semibold tracking-tight md:text-3xl">
                  {document?.document_title || (loading ? 'Đang đọc văn bản…' : 'Chi tiết văn bản')}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">{document?.law_number || `ID ${documentId}`}</p>
              </div>
              <Button variant="outline" onClick={() => void load()} disabled={loading}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> Làm mới
              </Button>
            </div>
          </header>

          {error && <Alert variant="destructive" role="alert"><AlertTriangle className="h-4 w-4" /><AlertTitle>Không tải được văn bản</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          {message && <Alert role="status"><ShieldCheck className="h-4 w-4" /><AlertDescription>{message}</AlertDescription></Alert>}

          {document && (
            <Card className="border-primary/30 shadow-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg"><Scale className="h-5 w-5 text-primary" />Thao tác kho văn bản</CardTitle>
                <p className="text-sm text-muted-foreground">Quản lý thông tin, thay thế văn bản, phân công phòng ban và trạng thái tra cứu.</p>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {document.metadata_editable !== false && <Button type="button" variant="outline" onClick={openEditor} disabled={!document.metadata_revision}><Pencil className="mr-2 h-4 w-4" />Sửa thông tin</Button>}
                <DocumentActions document={document} units={assignableOrganizationUnits} onComplete={async result => { setMessage(result); await load() }} />
              </CardContent>
            </Card>
          )}

          {document && detail && (
            <Tabs defaultValue="general" className="space-y-4">
              <TabsList className="h-auto w-full justify-start overflow-x-auto bg-background p-1">
                <TabsTrigger value="general"><BookOpen className="mr-2 h-4 w-4" />Thông tin chung</TabsTrigger>
                <TabsTrigger value="validity"><Scale className="mr-2 h-4 w-4" />Hiệu lực &amp; quan hệ</TabsTrigger>
                <TabsTrigger value="vectors"><Layers3 className="mr-2 h-4 w-4" />Dữ liệu tra cứu</TabsTrigger>
                <TabsTrigger value="faq"><ShieldCheck className="mr-2 h-4 w-4" />Câu hỏi thường gặp ảnh hưởng</TabsTrigger>
                <TabsTrigger value="versions"><FileClock className="mr-2 h-4 w-4" />Phiên bản</TabsTrigger>
                <TabsTrigger value="audit"><History className="mr-2 h-4 w-4" />Lịch sử thao tác</TabsTrigger>
              </TabsList>
              <TabsContent value="general">
            <Card>
              <CardHeader className="gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div><CardTitle>Thông tin chung</CardTitle><p className="mt-1 text-sm text-muted-foreground">Thông tin quản lý và trạng thái hiệu lực của văn bản.</p></div>
              </CardHeader>
              <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Metadata label="Số, ký hiệu" value={document.law_number} />
                <Metadata label="Loại văn bản" value={document.document_type} />
                <Metadata label="Cơ quan ban hành" value={document.issuing_agency} />
                <Metadata label="Ngày ban hành" value={dateLabel(document.issued_date)} />
                <Metadata label="Ngày có hiệu lực" value={dateLabel(document.effective_date)} />
                <Metadata label="Ngày hết hiệu lực" value={dateLabel(document.expired_date)} />
                <Metadata label="Trạng thái hiệu lực" value={validityLabel} />
                <Metadata label="Xác minh gần nhất" value={dateLabel(document.validity_sync?.verified_at)} />
                <Metadata label="Ngày đăng công báo" value={dateLabel(document.gazette_date)} />
                <Metadata label="Phạm vi" value={document.scope} />
                <Metadata label="Lĩnh vực" value={document.field_name || document.sector} />
                <Metadata label="Phòng ban quản lý" value={organizationLabel} />
                <Metadata label="Người ký" value={document.signer_name} />
                <Metadata label="Chức danh" value={document.signer_title} />
                <Metadata label="Phiên bản" value={document.version} />
                <Metadata label="Nguồn chính thức" value={document.source_url ? <a href={document.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center text-primary hover:underline">Mở nguồn <ExternalLink className="ml-1 h-3.5 w-3.5" /></a> : undefined} />
                <Metadata label="Thông tin áp dụng" value={document.applicability_info} className="sm:col-span-2" />
              </CardContent>
            </Card>
              </TabsContent>
              <TabsContent value="validity" className="space-y-4">
                <AvailabilityNotice section={detail.validity} />
                <AvailabilityNotice section={detail.relationships} />
                {detail.validity.status === 'available' && <div className="grid gap-4 lg:grid-cols-3"><SummaryBox label="Quan sát" value={detail.validity.observations?.length || 0} /><SummaryBox label="Sự kiện" value={detail.validity.events?.length || 0} /><SummaryBox label="Quyết định" value={detail.validity.decisions?.length || 0} /></div>}
              </TabsContent>
              <TabsContent value="vectors" className="space-y-4">
                <AvailabilityNotice section={detail.vectors} />
                <div className="grid gap-4 md:grid-cols-3"><SummaryBox label="Chunk dự kiến" value={detail.vectors.expected ?? detail.structure.chunk_count} /><SummaryBox label="Tra cứu hiện hành" value={detail.vectors.current_retrieval_ready ? 'Sẵn sàng' : 'Chưa sẵn sàng'} /><SummaryBox label="Sẵn sàng tra cứu" value={detail.vectors.retrieval_ready ? 'Đã sẵn sàng' : 'Chưa sẵn sàng'} /></div>
              </TabsContent>
              <TabsContent value="faq" className="space-y-4"><AvailabilityNotice section={detail.faq_impacts} />{detail.faq_impacts.status === 'available' && <p className="text-sm text-muted-foreground">{(detail.faq_impacts.items || []).length ? `Có ${(detail.faq_impacts.items || []).length} FAQ bị ảnh hưởng.` : 'Không có câu hỏi thường gặp bị ảnh hưởng.'}</p>}</TabsContent>
              <TabsContent value="versions" className="space-y-4"><AvailabilityNotice section={detail.versions} /><Card><CardHeader><CardTitle>Phiên bản hiện có</CardTitle></CardHeader><CardContent className="text-sm">{detail.versions.legacy_version || document.version || 'Chưa có thông tin phiên bản'}</CardContent></Card></TabsContent>
              <TabsContent value="audit" className="space-y-4"><AvailabilityNotice section={detail.audit} />{detail.audit.status === 'available' && !(detail.audit.items || []).length && <p className="text-sm text-muted-foreground">Chưa có lịch sử thao tác gắn trực tiếp với văn bản này.</p>}{(detail.audit.items || []).map(item => <Card key={item.id || `${item.action}-${item.created}`}><CardContent className="p-4 text-sm"><div className="font-medium">{item.action || 'Sự kiện quản trị'}</div><div className="text-muted-foreground">{dateLabel(item.created)} · {item.reason || ''}</div></CardContent></Card>)}</TabsContent>
            </Tabs>
          )}
        </main>
      </div>

      <Dialog open={editorMode !== null} onOpenChange={open => { if (!open && !saving) setEditorMode(null) }}>
        <DialogContent className="max-w-3xl overflow-y-auto" aria-busy={saving} onOpenAutoFocus={event => event.preventDefault()} style={{ maxHeight: 'min(760px, calc(100dvh - 2rem))' }}>
          <DialogHeader>
            <DialogTitle>Sửa thông tin văn bản</DialogTitle>
            <DialogDescription>Cập nhật các thông tin đang hiển thị trong phần Thông tin chung.</DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={event => void submitMetadata(event)}>
            {formError && <Alert variant="destructive" role="alert"><AlertTriangle className="h-4 w-4" /><AlertTitle>Chưa thể lưu thông tin</AlertTitle><AlertDescription>{formError}</AlertDescription></Alert>}
            <div className="grid gap-4 sm:grid-cols-3">
              {([['issued_date', 'Ngày ban hành'], ['effective_date', 'Ngày có hiệu lực'], ['expired_date', 'Ngày hết hiệu lực']] as const).map(([field, label]) => (
                <div key={field} className="space-y-2"><Label htmlFor={`metadata-${field}`}>{label}</Label><Input id={`metadata-${field}`} type="date" value={form[field]} onChange={event => setForm(current => ({ ...current, [field]: event.target.value }))} disabled={saving} /></div>
              ))}
            </div>
            <div className="space-y-2"><Label htmlFor="metadata-source-url">URL nguồn chính thức</Label><Input id="metadata-source-url" type="url" value={form.source_url} onChange={event => setForm(current => ({ ...current, source_url: event.target.value }))} placeholder="https://vbpl.vn/..." disabled={saving} /></div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2"><Label htmlFor="metadata-signer-name">Người ký</Label><Input id="metadata-signer-name" value={form.signer_name} onChange={event => setForm(current => ({ ...current, signer_name: event.target.value }))} disabled={saving} /></div>
              <div className="space-y-2"><Label htmlFor="metadata-signer-title">Chức danh</Label><Input id="metadata-signer-title" value={form.signer_title} onChange={event => setForm(current => ({ ...current, signer_title: event.target.value }))} disabled={saving} /></div>
              <div className="space-y-2"><Label htmlFor="metadata-gazette-date">Ngày đăng công báo</Label><Input id="metadata-gazette-date" type="date" value={form.gazette_date} onChange={event => setForm(current => ({ ...current, gazette_date: event.target.value }))} disabled={saving} /></div>
              <div className="space-y-2"><Label htmlFor="metadata-version">Phiên bản</Label><Input id="metadata-version" value={form.version} onChange={event => setForm(current => ({ ...current, version: event.target.value }))} disabled={saving} /></div>
            </div>
            <div className="space-y-2"><Label htmlFor="metadata-applicability">Thông tin áp dụng</Label><Textarea id="metadata-applicability" className="min-h-20" value={form.applicability_info} onChange={event => setForm(current => ({ ...current, applicability_info: event.target.value }))} disabled={saving} /></div>
            <div className="space-y-2"><Label htmlFor="metadata-reason">Lý do cập nhật hoặc căn cứ đối chiếu</Label><Textarea id="metadata-reason" className="min-h-20" value={form.reason} onChange={event => setForm(current => ({ ...current, reason: event.target.value }))} placeholder="Ghi nội dung đã chỉnh sửa…" minLength={10} disabled={saving} /></div>
            <DialogFooter><Button type="button" variant="outline" onClick={() => setEditorMode(null)} disabled={saving}>Hủy</Button><Button type="submit" disabled={saving || form.reason.trim().length < 10}>{saving ? 'Đang lưu…' : 'Lưu thông tin'}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}
