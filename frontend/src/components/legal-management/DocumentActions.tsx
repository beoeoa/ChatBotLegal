'use client'

import { useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Archive, CheckCircle2, FileClock, FileUp, Link2, LoaderCircle, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { legalManagementApi, type ManagedLegalDocument, type LegalManagementHardDeletePreview } from '@/lib/api/legal-management'
import { legalImportApi } from '@/lib/api/legal-import'
import { formatApiError } from '@/lib/utils/error-handler'
import { detectLegalDocumentMetadata } from '@/lib/utils/legal-document-metadata'

type Action = 'replace' | 'assign' | 'historical' | 'exclude' | 'restore' | 'delete'
const titles: Record<Action, string> = { replace: 'Thay thế văn bản', assign: 'Phân công phòng ban', historical: 'Đưa vào tra cứu lịch sử', exclude: 'Loại khỏi mọi tìm kiếm', restore: 'Khôi phục vào tra cứu', delete: 'Xóa vĩnh viễn văn bản' }
const descriptions: Record<Action, string> = {
  replace: 'Hệ thống nạp và kiểm tra đầy đủ bản mới trước, sau đó mới chuyển bản cũ khỏi tra cứu hiện hành.',
  assign: 'Chọn phòng ban chủ trì và các phòng ban phối hợp quản lý văn bản.',
  historical: 'Văn bản chỉ còn dùng để tra cứu lịch sử, không dùng làm căn cứ trả lời hiện hành.',
  exclude: 'Ngừng sử dụng văn bản trong mọi tìm kiếm. Dữ liệu gốc vẫn được giữ lại.',
  restore: 'Đưa văn bản trở lại tra cứu theo trạng thái hiệu lực và các điều kiện hiện có.',
  delete: 'Xóa dữ liệu gốc và dữ liệu tra cứu của văn bản. Thao tác không thể hoàn tác; lịch sử quản trị được giữ lại.',
}

type ReplacementStage = 'idle' | 'extracting' | 'ready' | 'replacing' | 'completed'

interface ReplacementMetadata {
  title: string
  law_number: string
  document_type: string
  issuing_agency: string
  issued_date: string
  effective_date: string
  expired_date: string
  scope: string
  sector: string
  applicability_info: string
}

function asInputDate(value?: string | null): string {
  return value ? String(value).slice(0, 10) : ''
}

function compactMetadataPatch(values: object): Partial<ReplacementMetadata> {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== ''),
  ) as Partial<ReplacementMetadata>
}

export function DocumentActions({ document, units, onComplete }: {
  document: ManagedLegalDocument
  units: Array<{ id: string; name: string }>
  onComplete: (message: string) => Promise<void>
}) {
  const router = useRouter()
  const [action, setAction] = useState<Action | null>(null)
  const [busy, setBusy] = useState(false)
  const lock = useRef(false)
  const [error, setError] = useState('')
  const [reason, setReason] = useState('')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const replacementReadSequence = useRef(0)
  const [preparingReplacement, setPreparingReplacement] = useState(false)
  const [replacementContent, setReplacementContent] = useState('')
  const [replacementStage, setReplacementStage] = useState<ReplacementStage>('idle')
  const [replacementStatus, setReplacementStatus] = useState('Chưa lấy nội dung bản thay thế.')
  const [replacementProgress, setReplacementProgress] = useState<number | null>(0)
  const [replacementWorkflowSteps, setReplacementWorkflowSteps] = useState<Record<string, string>>({})
  const replacementDefaults = (): ReplacementMetadata => ({
    title: document.document_title || '',
    law_number: document.law_number || '',
    document_type: document.document_type || '',
    issuing_agency: document.issuing_agency || '',
    issued_date: asInputDate(document.issued_date),
    effective_date: asInputDate(document.effective_date),
    expired_date: asInputDate(document.expired_date),
    scope: document.scope || 'central',
    sector: document.sector || '',
    applicability_info: document.applicability_info || '',
  })
  const [replacementMetadata, setReplacementMetadata] = useState<ReplacementMetadata>(replacementDefaults)
  const [assignment, setAssignment] = useState<'assigned' | 'shared' | 'unassigned'>('assigned')
  const [selected, setSelected] = useState<string[]>([])
  const [primary, setPrimary] = useState('')
  const [preview, setPreview] = useState<LegalManagementHardDeletePreview | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const isHistorical = document.serving_state === 'historical_only'
    || (document.current_answer_eligible === false && document.historical_lookup_allowed === true)
  const isExcluded = document.serving_state === 'excluded'
    || document.serving_state === 'quarantined'
    || (document.search_included === false && document.historical_lookup_allowed !== true && !isHistorical)
  const canRestore = isHistorical || isExcluded
  const deleteBlocked = action === 'delete' && Boolean(preview && !preview.eligible)
  const immutableDeleteBlocked = deleteBlocked
    && preview?.reason_code === 'immutable_release_document_cannot_be_hard_deleted'
  const deleteBlockMessage = deleteBlocked
    ? formatApiError(
      { code: preview?.reason_code },
      'Văn bản này chưa đủ điều kiện để xóa vĩnh viễn. Dữ liệu hiện tại vẫn được giữ nguyên.',
    )
    : ''

  async function open(next: Action) {
    if ((next === 'exclude' && isExcluded) || (next === 'historical' && isHistorical)) return
    setAction(next); setError(''); setReason(''); setUrl(''); setFile(null); setPreview(null); setConfirmation('')
    replacementReadSequence.current += 1
    setPreparingReplacement(false)
    setReplacementContent('')
    setReplacementMetadata(replacementDefaults())
    setReplacementStage('idle')
    setReplacementStatus('Chưa lấy nội dung bản thay thế.')
    setReplacementProgress(0)
    setReplacementWorkflowSteps({})
    setAssignment(document.organization_assignment_state === 'shared' ? 'shared' : document.organization_assignment_state === 'unassigned' ? 'unassigned' : 'assigned')
    const activeIds = new Set(units.map(unit => unit.id))
    const activeSelection = (document.organization_unit_ids || []).filter(id => activeIds.has(id))
    const activePrimary = document.primary_organization_unit_id && activeIds.has(document.primary_organization_unit_id)
      ? document.primary_organization_unit_id
      : ''
    setSelected(activeSelection)
    setPrimary(activeSelection.includes(activePrimary) ? activePrimary : '')
    if (next === 'delete') {
      lock.current = true; setBusy(true)
      try { setPreview(await legalManagementApi.previewHardDelete(document.doc_id)) }
      catch (caught) { setError(formatApiError(caught, 'Không thể kiểm tra điều kiện xóa. Hãy đóng và mở lại.')) }
      finally { lock.current = false; setBusy(false) }
    }
  }

  function updateReplacementMetadata<K extends keyof ReplacementMetadata>(
    key: K,
    value: ReplacementMetadata[K],
  ) {
    setReplacementMetadata(current => ({ ...current, [key]: value }))
  }

  async function prepareReplacementFromUrl() {
    if (!url.trim() || preparingReplacement || busy) return
    const sequence = ++replacementReadSequence.current
    setPreparingReplacement(true)
    setError('')
    setReplacementStage('extracting')
    setReplacementProgress(null)
    setReplacementStatus('Đang lấy và chuẩn hóa nội dung từ nguồn chính thức…')
    try {
      const source = await legalImportApi.crawlPreview(url.trim())
      if (sequence !== replacementReadSequence.current) return
      if (source.content.trim().length < 20) {
        throw new Error('Nguồn chưa cung cấp đủ nội dung để thay thế.')
      }
      setReplacementContent(source.content)
      setFile(null)
      setReplacementMetadata(current => ({
        ...current,
        ...compactMetadataPatch({
          title: source.title,
          law_number: source.law_number,
          document_type: source.document_type,
          issuing_agency: source.issuing_agency,
          issued_date: asInputDate(source.issued_date),
          effective_date: asInputDate(source.effective_date),
          expired_date: asInputDate(source.expired_date),
          scope: source.scope,
        }),
      }))
      setReplacementStage('ready')
      setReplacementProgress(45)
      setReplacementStatus(`Đã lấy ${source.content.length.toLocaleString('vi-VN')} ký tự. Hãy kiểm tra thông tin trước khi thay thế.`)
    } catch (caught) {
      if (sequence !== replacementReadSequence.current) return
      setReplacementContent('')
      setReplacementStage('idle')
      setReplacementProgress(0)
      setReplacementStatus('Chưa lấy được nội dung từ liên kết.')
      setError(formatApiError(caught, 'Không thể lấy nội dung từ liên kết này.'))
    } finally {
      if (sequence === replacementReadSequence.current) setPreparingReplacement(false)
    }
  }

  async function prepareReplacementFile(nextFile: File | null) {
    setFile(nextFile)
    setReplacementContent('')
    if (!nextFile) {
      setReplacementStage('idle')
      setReplacementProgress(0)
      setReplacementStatus('Chưa lấy nội dung bản thay thế.')
      return
    }
    const sequence = ++replacementReadSequence.current
    setPreparingReplacement(true)
    setError('')
    setReplacementStage('extracting')
    setReplacementProgress(5)
    setReplacementStatus(`Đang đọc ${nextFile.name}…`)
    try {
      const extracted = await legalImportApi.extractFile(nextFile, 'auto')
      if (sequence !== replacementReadSequence.current) return
      let content = extracted.content || ''
      let complete = extracted.complete !== false
      const jobId = extracted.extraction_job_id
      if (extracted.extraction_status === 'processing' && jobId) {
        complete = false
        for (let attempt = 0; attempt < 360; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 1000))
          if (sequence !== replacementReadSequence.current) return
          const job = await legalImportApi.extractionJob(jobId)
          const total = Number(job.total_pages || job.page_count || 0)
          const processed = Number(job.processed_pages || 0)
          setReplacementProgress(total > 0 ? Math.min(40, 5 + Math.round((processed / total) * 35)) : null)
          setReplacementStatus(total > 0
            ? `Đang đọc tài liệu: ${processed}/${total} trang…`
            : 'Đang nhận dạng nội dung tài liệu…')
          if (job.extraction_status === 'processing') continue
          if (job.extraction_status === 'error') throw new Error(job.error || 'Không đọc được tệp.')
          content = job.extracted_text?.trim() || content
          complete = job.extraction_status === 'complete' && job.complete !== false
          break
        }
      }
      if (!complete || extracted.truncated || (extracted.failed_pages?.length || 0) > 0 || content.trim().length < 100) {
        throw new Error('Tệp chưa được đọc đầy đủ. Hãy chọn tệp rõ hơn hoặc dùng nguồn chính thức.')
      }
      const detected = detectLegalDocumentMetadata(content, nextFile.name)
      setReplacementContent(content)
      setReplacementMetadata(current => ({
        ...current,
        ...compactMetadataPatch(detected),
      }))
      setReplacementStage('ready')
      setReplacementProgress(45)
      setReplacementStatus(`Đã đọc ${content.length.toLocaleString('vi-VN')} ký tự từ ${nextFile.name}. Hãy kiểm tra thông tin trước khi thay thế.`)
    } catch (caught) {
      if (sequence !== replacementReadSequence.current) return
      setReplacementContent('')
      setReplacementStage('idle')
      setReplacementProgress(0)
      setReplacementStatus('Tệp chưa sẵn sàng để thay thế.')
      setError(formatApiError(caught, 'Không thể đọc đầy đủ tệp văn bản.'))
    } finally {
      if (sequence === replacementReadSequence.current) setPreparingReplacement(false)
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!action || lock.current) return
    if (reason.trim().length < 10) { setError('Nhập lý do tối thiểu 10 ký tự.'); return }
    if (action === 'assign' && assignment === 'assigned' && (!primary || !selected.includes(primary))) { setError('Chọn phòng ban chủ trì trong danh sách phòng ban quản lý.'); return }
    if (action === 'replace') {
      if (!url.trim() && !file) { setError('Chọn tệp hoặc nhập URL nguồn chính thức.'); return }
      if (preparingReplacement) { setError('Hãy chờ hệ thống đọc xong nội dung bản thay thế.'); return }
      if (replacementStage !== 'ready' || replacementContent.trim().length < 20) {
        setError('Hãy lấy và kiểm tra nội dung bản thay thế trước khi xác nhận.'); return
      }
      const missing = [
        ['Tên văn bản', replacementMetadata.title],
        ['Số, ký hiệu', replacementMetadata.law_number],
        ['Loại văn bản', replacementMetadata.document_type],
        ['Cơ quan ban hành', replacementMetadata.issuing_agency],
        ['Ngày ban hành', replacementMetadata.issued_date],
        ['Ngày có hiệu lực', replacementMetadata.effective_date],
      ].filter(([, value]) => !value.trim()).map(([label]) => label)
      if (missing.length) { setError(`Cần kiểm tra đủ: ${missing.join(', ')}.`); return }
    }
    if (action === 'delete' && (!preview?.eligible || confirmation !== preview.confirmation_text)) return
    if (['historical', 'exclude', 'restore'].includes(action) && !document.state_revision) { setError('Thiếu phiên bản trạng thái. Hãy tải lại trang.'); return }
    lock.current = true; setBusy(true); setError('')
    try {
      let message = 'Đã cập nhật văn bản.'
      if (action === 'replace') {
        setReplacementStage('replacing')
        setReplacementProgress(50)
        setReplacementStatus('Đang tạo bản mới, tách đoạn, embedding và kiểm tra truy xuất…')
        const idempotencyKey = `management-replacement-${document.doc_id}-${globalThis.crypto?.randomUUID?.() || Date.now()}`
        let keepPolling = true
        const pollWorkflow = async () => {
          if (typeof legalManagementApi.replacementWorkflowByKey !== 'function') return
          while (keepPolling) {
            await new Promise(resolve => window.setTimeout(resolve, 750))
            if (!keepPolling) return
            try {
              const workflow = await legalManagementApi.replacementWorkflowByKey(idempotencyKey)
              if (!keepPolling) return
              const steps = workflow.steps || {}
              setReplacementWorkflowSteps(steps)
              const passed = ['normalize', 'chunk', 'embedding', 'retrieval_smoke', 'publish']
                .filter(key => ['passed', 'replacement_active', 'overlay_active_manifest_unchanged'].includes(steps[key])).length
              setReplacementProgress(Math.min(95, 50 + passed * 9))
              setReplacementStatus(workflow.status === 'pending_retry'
                ? 'Bản cũ vẫn an toàn; tác vụ đang chờ thử lại bước chưa hoàn tất.'
                : workflow.status === 'failed'
                  ? 'Thay thế chưa hoàn tất; bản cũ chưa bị loại khỏi tra cứu.'
                  : 'Đang xử lý bản mới và xác minh khả năng trả lời của chatbot…')
            } catch {
              // The workflow may not exist during the first few milliseconds.
              // Keep the primary request authoritative and try the receipt again.
            }
          }
        }
        void pollWorkflow()
        const payload = {
          source_url: url.trim(),
          reason: reason.trim(),
          uploaded_content: replacementContent,
          uploaded_filename: file?.name,
          title: replacementMetadata.title.trim(),
          law_number: replacementMetadata.law_number.trim(),
          document_type: replacementMetadata.document_type.trim(),
          issuing_agency: replacementMetadata.issuing_agency.trim(),
          issued_date: replacementMetadata.issued_date,
          effective_date: replacementMetadata.effective_date,
          ...(replacementMetadata.expired_date ? { expired_date: replacementMetadata.expired_date } : {}),
          scope: replacementMetadata.scope.trim(),
          sector: replacementMetadata.sector.trim(),
          applicability_info: replacementMetadata.applicability_info.trim(),
        }
        let result
        try {
          result = await legalManagementApi.replaceDocument(
            document.doc_id,
            payload,
            { idempotencyKey },
          )
        } finally {
          keepPolling = false
        }
        setReplacementWorkflowSteps({ chunk: 'passed', embedding: 'passed', retrieval_smoke: 'passed', publish: 'replacement_active' })
        setReplacementStage('completed')
        setReplacementProgress(100)
        setReplacementStatus('Đã thay thế, embedding và kiểm tra truy xuất thành công.')
        message = result.message || 'Đã thay thế văn bản.'
        const newId = result.new_document?.doc_id ?? result.new_document?.document_id ?? result.document_id
        if (newId && String(newId) !== String(document.doc_id)) {
          setAction(null)
          router.push(`/legal-management/${encodeURIComponent(String(newId))}`)
          return
        }
      } else if (action === 'assign') {
        const result = await legalManagementApi.setOrganizationAssignment(document.doc_id, {
          assignment_state: assignment, organization_unit_ids: assignment === 'assigned' ? selected : [],
          primary_organization_unit_id: assignment === 'assigned' ? primary : null,
          expected_fingerprint: document.organization_assignment_fingerprint, reason: reason.trim(),
        })
        message = result.projection_updated ? 'Đã lưu phân công phòng ban.' : 'Đã lưu phân công phòng ban; dữ liệu tra cứu chưa đồng bộ.'
      } else if (action === 'delete') {
        await legalManagementApi.hardDelete(document.doc_id, { reason: reason.trim(), expected_revision: preview!.state_revision, confirmation_text: confirmation })
        setAction(null); router.push('/legal-management'); return
      } else {
        const result = await legalManagementApi.setSearchState(document.doc_id, { action, reason: reason.trim(), expected_revision: document.state_revision! })
        message = result.message || 'Đã cập nhật trạng thái tra cứu.'
        if (result.operation_verification?.status === 'degraded') message += ' Dữ liệu tra cứu chưa sẵn sàng đầy đủ.'
      }
      setAction(null)
      await onComplete(message)
    } catch (caught) { setError(formatApiError(caught, 'Không thể hoàn tất thao tác.')) }
    finally { lock.current = false; setBusy(false) }
  }

  return <>
    <Button onClick={() => void open('replace')} disabled={busy}><RefreshCw className="mr-2 h-4 w-4" />Thay thế văn bản</Button>
    <Button variant="outline" onClick={() => void open('assign')} disabled={busy}><ShieldCheck className="mr-2 h-4 w-4" />Phân công phòng ban</Button>
    <Button variant="outline" onClick={() => void open('historical')} disabled={busy || isHistorical} aria-disabled={isHistorical}>
      <Archive className="mr-2 h-4 w-4" />{isHistorical ? 'Đang ở tra cứu lịch sử' : 'Đưa vào tra cứu lịch sử'}
    </Button>
    <Button variant="destructive" onClick={() => void open('exclude')} disabled={busy || isExcluded} aria-disabled={isExcluded}>
      <Trash2 className="mr-2 h-4 w-4" />{isExcluded ? 'Đã loại khỏi mọi tìm kiếm' : 'Loại khỏi mọi tìm kiếm'}
    </Button>
    {canRestore && <Button variant="outline" onClick={() => void open('restore')} disabled={busy}>Khôi phục vào tra cứu</Button>}
    <Button variant="outline" onClick={() => void open('delete')} disabled={busy}><FileClock className="mr-2 h-4 w-4" />Xóa vĩnh viễn…</Button>
    <Dialog open={action !== null} onOpenChange={value => {
      if (!value && !lock.current) {
        replacementReadSequence.current += 1
        setPreparingReplacement(false)
        setAction(null)
      }
    }}>
      <DialogContent style={{ width: 'min(720px, calc(100vw - 2rem))', maxWidth: '720px', maxHeight: 'calc(100dvh - 3rem)' }} className="overflow-y-auto" aria-busy={busy}>
        <DialogHeader><DialogTitle>{action ? titles[action] : ''}</DialogTitle><DialogDescription>{action ? descriptions[action] : ''}</DialogDescription></DialogHeader>
        <form onSubmit={event => void submit(event)} className="space-y-4">
          <p className="text-sm font-medium break-words">{document.law_number} · {document.document_title}</p>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {action === 'replace' && <>
            <div role="status" className="rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
              Bản cũ chỉ được chuyển khỏi tra cứu hiện hành sau khi bản mới đã được tách đoạn, tạo vector và kiểm tra truy xuất thành công. Nếu một bước lỗi, bản cũ được giữ hoặc phục hồi tự động.
            </div>
            <section className="space-y-3 rounded-lg border p-3" aria-labelledby="replacement-source-heading">
              <div>
                <h3 id="replacement-source-heading" className="font-semibold">1. Lấy nội dung bản thay thế</h3>
                <p className="text-xs text-muted-foreground">Dùng liên kết chính thức hoặc tệp PDF, DOC, DOCX, bảng tính và ảnh rõ nét.</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="replacement-url">URL nguồn chính thức</Label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input id="replacement-url" type="url" value={url} onChange={e => {
                    setUrl(e.target.value)
                    setReplacementContent('')
                    setReplacementStage('idle')
                    setReplacementProgress(0)
                    setReplacementStatus('Liên kết đã thay đổi; hãy lấy lại nội dung.')
                  }} disabled={busy || preparingReplacement} placeholder="https://..." />
                  <Button type="button" variant="outline" onClick={() => void prepareReplacementFromUrl()} disabled={busy || preparingReplacement || !url.trim()}>
                    <Link2 className="mr-2 h-4 w-4" />Lấy nội dung từ link
                  </Button>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="replacement-file">Tệp văn bản thay thế</Label>
                <Input
                  id="replacement-file"
                  type="file"
                  accept=".txt,.md,.json,.pdf,.doc,.docx,.rtf,.xls,.xlsx,.csv,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff"
                  onChange={e => void prepareReplacementFile(e.target.files?.[0] || null)}
                  disabled={busy || preparingReplacement}
                />
              </div>
              <div className="rounded-md border bg-background p-3" role="status" aria-live="polite">
                <div className="flex items-start gap-2">
                  {replacementStage === 'ready' || replacementStage === 'completed'
                    ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                    : replacementStage === 'extracting' || replacementStage === 'replacing'
                      ? <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
                      : <FileUp className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{replacementStatus}</p>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className={`h-full rounded-full bg-primary transition-all ${replacementProgress === null ? 'w-1/3 animate-pulse' : ''}`}
                        style={replacementProgress === null ? undefined : { width: `${replacementProgress}%` }}
                      />
                    </div>
                    {Object.keys(replacementWorkflowSteps).length > 0 && <p className="mt-2 text-xs text-muted-foreground">
                      Tách đoạn: {replacementWorkflowSteps.chunk || 'đang chờ'} · Embedding: {replacementWorkflowSteps.embedding || 'đang chờ'} · Kiểm tra chatbot: {replacementWorkflowSteps.retrieval_smoke || 'đang chờ'}
                    </p>}
                  </div>
                </div>
              </div>
            </section>
            <section className="space-y-3 rounded-lg border p-3" aria-labelledby="replacement-metadata-heading">
              <div>
                <h3 id="replacement-metadata-heading" className="font-semibold">2. Kiểm tra thông tin văn bản</h3>
                <p className="text-xs text-muted-foreground">Thông tin nhận diện chỉ được dùng sau khi Admin kiểm tra; hệ thống không tự đoán làm căn cứ pháp lý.</p>
              </div>
              <div className="space-y-2"><Label htmlFor="replacement-title">Tên văn bản *</Label><Input id="replacement-title" value={replacementMetadata.title} onChange={e => updateReplacementMetadata('title', e.target.value)} disabled={busy} /></div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2"><Label htmlFor="replacement-law-number">Số, ký hiệu *</Label><Input id="replacement-law-number" value={replacementMetadata.law_number} onChange={e => updateReplacementMetadata('law_number', e.target.value)} disabled={busy} /></div>
                <div className="space-y-2"><Label htmlFor="replacement-document-type">Loại văn bản *</Label><Input id="replacement-document-type" value={replacementMetadata.document_type} onChange={e => updateReplacementMetadata('document_type', e.target.value)} disabled={busy} /></div>
              </div>
              <div className="space-y-2"><Label htmlFor="replacement-agency">Cơ quan ban hành *</Label><Input id="replacement-agency" value={replacementMetadata.issuing_agency} onChange={e => updateReplacementMetadata('issuing_agency', e.target.value)} disabled={busy} /></div>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-2"><Label htmlFor="replacement-issued-date">Ngày ban hành *</Label><Input id="replacement-issued-date" type="date" value={replacementMetadata.issued_date} onChange={e => updateReplacementMetadata('issued_date', e.target.value)} disabled={busy} /></div>
                <div className="space-y-2"><Label htmlFor="replacement-effective-date">Ngày có hiệu lực *</Label><Input id="replacement-effective-date" type="date" value={replacementMetadata.effective_date} onChange={e => updateReplacementMetadata('effective_date', e.target.value)} disabled={busy} /></div>
                <div className="space-y-2"><Label htmlFor="replacement-expired-date">Ngày hết hiệu lực</Label><Input id="replacement-expired-date" type="date" value={replacementMetadata.expired_date} onChange={e => updateReplacementMetadata('expired_date', e.target.value)} disabled={busy} /></div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2"><Label htmlFor="replacement-scope">Phạm vi áp dụng</Label><Input id="replacement-scope" value={replacementMetadata.scope} onChange={e => updateReplacementMetadata('scope', e.target.value)} disabled={busy} /></div>
                <div className="space-y-2"><Label htmlFor="replacement-sector">Ngành/chủ đề</Label><Input id="replacement-sector" value={replacementMetadata.sector} onChange={e => updateReplacementMetadata('sector', e.target.value)} disabled={busy} /></div>
              </div>
              <div className="space-y-2"><Label htmlFor="replacement-applicability">Thông tin áp dụng/sửa đổi</Label><Textarea id="replacement-applicability" value={replacementMetadata.applicability_info} onChange={e => updateReplacementMetadata('applicability_info', e.target.value)} disabled={busy} /></div>
            </section>
          </>}
          {action === 'assign' && <>
            <Label htmlFor="assignment-mode">Cách phân công</Label>
            <select id="assignment-mode" className="w-full rounded border p-2" value={assignment} onChange={e => setAssignment(e.target.value as typeof assignment)} disabled={busy}>
              <option value="assigned">Phân công phòng ban</option><option value="shared">Dùng chung toàn hệ thống</option><option value="unassigned">Chưa phân công</option>
            </select>
            {assignment === 'assigned' && <>
              <fieldset disabled={busy} className="space-y-2"><legend>Phòng ban quản lý</legend>
                {units.map(unit => <label key={unit.id} className="flex items-center gap-2"><input type="checkbox" checked={selected.includes(unit.id)} onChange={e => {
                  setSelected(current => e.target.checked ? [...current, unit.id] : current.filter(id => id !== unit.id))
                  if (!e.target.checked && primary === unit.id) setPrimary('')
                }} />{unit.name}</label>)}
                {!units.length && <p>Chưa tải được danh sách phòng ban.</p>}
              </fieldset>
              <Label htmlFor="assignment-primary">Phòng ban chủ trì</Label><select id="assignment-primary" className="w-full rounded border p-2" value={primary} onChange={e => setPrimary(e.target.value)} disabled={busy}><option value="">Chọn phòng ban chủ trì</option>{units.filter(unit => selected.includes(unit.id)).map(unit => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select>
            </>}
          </>}
          {action === 'delete' && <>
            {busy && !preview && <p role="status">Đang kiểm tra điều kiện xóa…</p>}
            {preview && <><p role={preview.eligible ? 'status' : 'alert'}>{preview.eligible ? `Sẽ xóa ${preview.database.articles} điều và ${preview.database.chunks} đoạn dữ liệu cùng các liên kết của văn bản.` : deleteBlockMessage}</p>
              {preview.eligible && <div className="space-y-2"><Label htmlFor="delete-confirmation">Nhập chính xác: {preview.confirmation_text}</Label><Input id="delete-confirmation" value={confirmation} onChange={e => setConfirmation(e.target.value)} disabled={busy} autoComplete="off" /></div>}
            </>}
          </>}
          {(action !== 'delete' || preview?.eligible) && <div className="space-y-2"><Label htmlFor="document-action-reason">Lý do thao tác</Label><Textarea id="document-action-reason" value={reason} onChange={e => setReason(e.target.value)} minLength={10} maxLength={2000} required disabled={busy} /></div>}
          {deleteBlocked ? (
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setAction(null)}>Đóng</Button>
              {immutableDeleteBlocked && <Button type="button" onClick={() => void open('replace')}>Thay thế văn bản</Button>}
            </DialogFooter>
          ) : (
            <DialogFooter>
              <Button type="button" variant="outline" disabled={busy || preparingReplacement} onClick={() => setAction(null)}>Hủy</Button>
              {!(action === 'delete' && !preview) && <Button type="submit" variant={action === 'delete' || action === 'exclude' ? 'destructive' : 'default'} disabled={busy || preparingReplacement || reason.trim().length < 10 || (action === 'replace' && replacementStage !== 'ready') || (action === 'delete' && (!preview?.eligible || confirmation !== preview.confirmation_text))}>{busy ? (action === 'replace' ? 'Đang thay thế…' : 'Đang xử lý…') : action === 'replace' ? 'Bắt đầu thay thế' : 'Xác nhận'}</Button>}
            </DialogFooter>
          )}
        </form>
      </DialogContent>
    </Dialog>
  </>
}
