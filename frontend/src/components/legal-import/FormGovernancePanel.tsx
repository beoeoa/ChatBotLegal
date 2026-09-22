'use client'

import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, RefreshCcw, Rocket, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  FormAttestationPreviewV17,
  FormCoverageV17,
  FormProcedureCandidateV18,
  FormReleaseV17,
  FormReviewCaseV17,
  FormSourceProposalMetadataV18,
  legalImportApi,
} from '@/lib/api/legal-import'
import { formatApiError, getApiErrorCode } from '@/lib/utils/error-handler'
import { systemStatusLabel } from '@/lib/utils/system-labels'
import { FormManagementList, formDomainName } from './FormManagementList'
import { useSettings } from '@/lib/hooks/use-settings'
import { activeDirectoryUnits, domainsForUnit, domainLabel } from '@/lib/utils/organization-directory'

const labels: Record<string, string> = {
  submitted: 'Mới gửi', resubmitted: 'Đã bổ sung', needs_supplement: 'Chờ cán bộ bổ sung',
  source_approved: 'Đã duyệt nguồn · chưa công khai', legal_enrichment: 'Đang hoàn thiện pháp lý',
  ready_for_attestation: 'Chờ xác nhận pháp lý', attested: 'Đã xác nhận · chờ phát hành',
  release_candidate: 'Đang ở bản phát hành thử',
  released: 'Đã phát hành', rejected: 'Đã từ chối', quarantined: 'Đang cách ly',
}

const workflowStepByStatus: Record<string, number> = {
  draft: 0,
  submitted: 1,
  resubmitted: 1,
  needs_supplement: 1,
  source_approved: 2,
  legal_enrichment: 2,
  ready_for_attestation: 3,
  attested: 4,
  release_candidate: 4,
  released: 5,
}

function governanceErrorMessage(error: unknown): string {
  const code = String(getApiErrorCode(error) || '').toUpperCase()

  if (code === 'FORM_SOURCE_UNAVAILABLE') {
    return 'Không truy cập được đường dẫn nguồn chính thức. Hãy yêu cầu bổ sung một đường dẫn còn hoạt động hoặc tệp PDF có mã kiểm tra tệp.'
  }
  if (code === 'FORM_SOURCE_REQUIRED') {
    return 'Chưa có đường dẫn thuộc danh sách nguồn chính thức được phép. Không thể xác minh nguồn này.'
  }
  if (code === 'FORM_CHECKSUM_REQUIRED' || code === 'FORM_CHECKSUM_INVALID') {
    return 'Thiếu mã kiểm tra hợp lệ của tệp nguồn. Hãy yêu cầu cán bộ gửi lại đúng tệp PDF hoặc DOCX.'
  }
  if (code === 'FORM_TRANSITION_INVALID') {
    return 'Trạng thái hồ sơ đã thay đổi. Hãy bấm Làm mới trước khi xử lý tiếp.'
  }
  const releaseMessages: Record<string, string> = {
    FORM_RELEASE_ATTESTATION_REQUIRED: 'Chỉ biểu mẫu đã xác nhận pháp lý mới được chọn để tạo bản phát hành thử.',
    FORM_SOURCE_SNAPSHOT_INVALID: 'Ảnh chụp nguồn không hợp lệ. Hãy tải lại dữ liệu rồi thử lại.',
    FORM_RELEASE_GATE_FAILED: 'Các điều kiện kiểm tra trước khi phát hành chưa đạt nên chưa thể công khai.',
    FORM_COVERAGE_MANIFEST_MISMATCH: 'Dữ liệu phạm vi biểu mẫu chưa đồng bộ. Hãy bấm Làm mới và tạo lại bản phát hành thử.',
    FORM_CHECKSUM_MISMATCH: 'Mã kiểm tra nguồn không khớp. Hãy chọn lại đúng tệp hoặc chọn loại biểu mẫu điện tử cho trang chính thức.',
  }
  if (releaseMessages[code]) return releaseMessages[code]
  return formatApiError(error, 'Không thể lưu quyết định. Dữ liệu công khai không bị thay đổi.')
}

function WorkflowStepper({
  item,
  metadata,
}: {
  item: FormReviewCaseV17
  metadata: FormSourceProposalMetadataV18
}) {
  const current = workflowStepByStatus[item.status] ?? 1
  const next = metadata.steps[current + 1]
  const blockedReason = item.status === 'needs_supplement'
    ? 'Đang chờ cán bộ bổ sung bằng chứng nguồn.'
    : item.status === 'attested'
      ? 'Chưa có bản phát hành vượt qua bước kiểm tra cuối.'
      : item.status === 'released'
        ? 'Không bị chặn; biểu mẫu đã được phát hành.'
        : `Chưa hoàn tất bước ${metadata.steps[current]?.label.toLocaleLowerCase('vi') || 'hiện tại'}.`

  return <div className="space-y-2 rounded-md border bg-muted/30 p-3">
    <ol aria-label={`Tiến độ ${item.title}`} className="grid gap-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
      {metadata.steps.map((step, index) => <li
        key={step.id}
        className={`rounded-md border p-2 ${index < current ? 'bg-emerald-50 text-emerald-800' : index === current ? 'border-primary bg-primary/5 font-medium' : 'text-muted-foreground'}`}
      >
        <span className="block">{index < current ? '✓' : index + 1}</span>
        {step.label}
      </li>)}
    </ol>
    <p className="text-sm"><b>Bước hiện tại:</b> {metadata.steps[current]?.label}</p>
    <p className="text-sm"><b>Bước kế tiếp:</b> {next?.label || 'Đã hoàn tất quy trình'}</p>
    <p className="text-xs text-muted-foreground"><b>Lý do bị chặn:</b> {blockedReason}</p>
  </div>
}

function SourceProposalPanel({
  metadata,
  onDone,
}: {
  metadata: FormSourceProposalMetadataV18
  onDone: () => Promise<void>
}) {
  const { data: settings } = useSettings()
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('')
  const [department, setDepartment] = useState('')
  const departments = activeDirectoryUnits(settings).map(unit => [unit.id, unit.name] as const)
  const proposalDomains = domainsForUnit(settings, department).map(value => value.code)
  const [candidates, setCandidates] = useState<FormProcedureCandidateV18[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [title, setTitle] = useState('')
  const [sourceType, setSourceType] = useState<'official_url' | 'pdf' | 'docx' | 'eform'>('official_url')
  const [sourceUrl, setSourceUrl] = useState('')
  const [pageNumber, setPageNumber] = useState('')
  const [checksum, setChecksum] = useState('')
  const [fileName, setFileName] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<'procedure' | 'title' | 'sourceUrl' | 'checksum', string>>({
    procedure: '', title: '', sourceUrl: '', checksum: '',
  })

  const search = async () => {
    setBusy(true)
    try {
      const result = await legalImportApi.formProcedureCandidates({
        q: query.trim() || undefined,
        domain: domain || undefined,
        organization_unit_id: department || undefined,
        limit: 20,
      })
      setCandidates(result.items)
      setSelectedId(current => result.items.some(item => item.procedure_id === current) ? current : '')
    } catch {
      toast.error('Không tải được danh sách thủ tục trong phạm vi được phân quyền.')
    } finally {
      setBusy(false)
    }
  }

  const selectFile = async (file: File | undefined) => {
    if (!file) return
    const expectedExtension = sourceType === 'pdf' ? '.pdf' : '.docx'
    if (!file.name.toLocaleLowerCase().endsWith(expectedExtension)) {
      toast.error(`Tệp phải có định dạng ${expectedExtension}.`)
      return
    }
    setFileName(file.name)
    if (!title.trim()) setTitle(file.name.replace(/\.(pdf|docx)$/i, ''))
    if (!globalThis.crypto?.subtle) {
      toast.error('Trình duyệt không hỗ trợ tính mã kiểm tra cho tệp này.')
      return
    }
    const digest = await globalThis.crypto.subtle.digest('SHA-256', await file.arrayBuffer())
    setChecksum(Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join(''))
  }

  const submit = async () => {
    const procedure = candidates.find(item => item.procedure_id === selectedId)
    const nextErrors = {
      procedure: procedure ? '' : 'Hãy tìm và chọn một thủ tục trong danh sách kết quả.',
      title: title.trim() ? '' : 'Tên biểu mẫu không được để trống.',
      sourceUrl: sourceUrl.trim() ? '' : 'URL nguồn chính thức không được để trống.',
      checksum: '',
    }
    setErrors(nextErrors)
    if (Object.values(nextErrors).some(Boolean)) {
      return
    }
    if (!procedure) {
      return
    }
    setBusy(true)
    try {
      const payload: Parameters<typeof legalImportApi.submitFormGovernanceCase>[0] = {
        procedure_id: procedure.procedure_id,
        domain: procedure.domain,
        title: title.trim(),
        source_url: sourceUrl.trim(),
        source_checksum: checksum || undefined,
        // A URL-only proposal points to an official procedure/e-form page,
        // not to downloadable file bytes. File assets must use PDF/DOCX with
        // a checksum so Release Gate can verify the exact bytes.
        asset_kind: sourceType === 'official_url' || sourceType === 'eform' ? 'eform' : 'file',
        note: sourceType === 'pdf' || sourceType === 'docx'
          ? (fileName ? `Đối chiếu tệp ${fileName} với nguồn tải chính thức.` : 'Hệ thống kiểm tra tệp từ đường dẫn chính thức khi duyệt nguồn.')
          : `Loại nguồn đề xuất: ${sourceType}`,
      }
      if (sourceType === 'pdf' && pageNumber.trim()) {
        payload.page_number = parseInt(pageNumber.trim(), 10)
      }
      await legalImportApi.submitFormGovernanceCase(payload)
      toast.success('Đã gửi đề xuất để xác minh nguồn. Dữ liệu công khai chưa thay đổi.')
      setTitle(''); setSourceUrl(''); setChecksum(''); setFileName(''); setSelectedId(''); setPageNumber('')
      setErrors({ procedure: '', title: '', sourceUrl: '', checksum: '' })
      await onDone()
    } catch {
      toast.error('Không gửi được đề xuất. Hãy kiểm tra phạm vi thủ tục và nguồn chính thức.')
    } finally {
      setBusy(false)
    }
  }

  return <div className="space-y-3 rounded-lg border border-dashed p-4">
    <div>
      <p className="font-medium">Đề xuất nguồn biểu mẫu</p>
      <p className="text-sm text-muted-foreground">Tìm thủ tục theo tên, mã hoặc lĩnh vực; không cần nhập ID nội bộ.</p>
    </div>
    <div className="grid gap-3 sm:grid-cols-3">
      <div><Label htmlFor="proposal-department">Phòng ban</Label><select id="proposal-department" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={department} onChange={event => { setDepartment(event.target.value); setDomain(''); setCandidates([]); setSelectedId('') }}><option value="">Tất cả phòng ban</option>{departments.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></div>
      <div className="sm:col-span-2"><Label htmlFor="procedure-query">Tên hoặc mã thủ tục</Label><Input id="procedure-query" value={query} onChange={event => setQuery(event.target.value)} placeholder="Ví dụ: đăng ký thường trú hoặc 1.004222" /></div>
      <div><Label htmlFor="procedure-domain">Lĩnh vực</Label><select id="procedure-domain" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={domain} onChange={event => { setDomain(event.target.value); setCandidates([]); setSelectedId('') }}><option value="">Tất cả lĩnh vực được phép</option>{proposalDomains.map(value => <option key={value} value={value}>{domainLabel(value, settings) || formDomainName(value)}</option>)}</select></div>
    </div>
    <Button type="button" variant="outline" onClick={() => void search()} disabled={busy}>Tìm thủ tục</Button>
    <div>
      <Label htmlFor="procedure-choice">Thủ tục đã chọn</Label>
      <select id="procedure-choice" aria-invalid={Boolean(errors.procedure)} className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={selectedId} onChange={event => { setSelectedId(event.target.value); setErrors(current => ({ ...current, procedure: '' })) }}><option value="">Chọn từ kết quả tìm kiếm</option>{candidates.map(item => <option key={item.procedure_id} value={item.procedure_id}>{item.name} · {item.procedure_code} · {item.domain}</option>)}</select>
      {errors.procedure && <p role="alert" className="mt-1 text-xs text-destructive">{errors.procedure}</p>}
      {candidates.find(item => item.procedure_id === selectedId) && (() => {
        const selected = candidates.find(item => item.procedure_id === selectedId)!
        return <div className="mt-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm"><p className="font-medium">{selected.name}</p><p className="mt-1 text-muted-foreground">Mã: {selected.procedure_code} · Lĩnh vực: {selected.domain}</p><p className="mt-1 text-muted-foreground">Phòng ban chủ trì: {selected.primary_organization_unit_name || 'Chưa phân công'}</p></div>
      })()}
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <div><Label htmlFor="proposal-title">Tên biểu mẫu</Label><Input id="proposal-title" aria-invalid={Boolean(errors.title)} value={title} onChange={event => { setTitle(event.target.value); setErrors(current => ({ ...current, title: '' })) }} />{errors.title && <p role="alert" className="mt-1 text-xs text-destructive">{errors.title}</p>}</div>
      <div><Label htmlFor="source-type">Loại nguồn</Label><select id="source-type" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={sourceType} onChange={event => { setSourceType(event.target.value as typeof sourceType); setChecksum(''); setFileName('') }}><option value="official_url">Đường dẫn chính thức</option><option value="pdf">Tệp PDF</option><option value="docx">Tệp DOCX</option><option value="eform">Biểu mẫu điện tử</option></select></div>
      <div className={sourceType === 'pdf' ? "sm:col-span-1" : "sm:col-span-2"}><Label htmlFor="source-url">Đường dẫn nguồn chính thức</Label><Input id="source-url" type="url" aria-invalid={Boolean(errors.sourceUrl)} value={sourceUrl} onChange={event => { setSourceUrl(event.target.value); setErrors(current => ({ ...current, sourceUrl: '' })) }} placeholder="https://..." />{errors.sourceUrl && <p role="alert" className="mt-1 text-xs text-destructive">{errors.sourceUrl}</p>}</div>
      {sourceType === 'pdf' && <div><Label htmlFor="page-number">Trang PDF (nếu là một phần của file lớn)</Label><Input id="page-number" type="number" min="1" value={pageNumber} onChange={event => setPageNumber(event.target.value)} placeholder="Ví dụ: 12" /></div>}
      {(sourceType === 'pdf' || sourceType === 'docx') && <div className="sm:col-span-2"><Label htmlFor="source-file">Tệp {sourceType.toUpperCase()} để đối chiếu</Label><Input id="source-file" type="file" accept={sourceType === 'pdf' ? '.pdf,application/pdf' : '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document'} onChange={event => void selectFile(event.target.files?.[0])} /><p className="mt-1 text-xs text-muted-foreground">Đường dẫn phải trỏ trực tiếp tới đúng tệp; với trang văn bản trên web, hãy chọn “Đường dẫn chính thức” hoặc “Biểu mẫu điện tử”.</p></div>}
      {checksum && <p className="break-all text-xs text-muted-foreground sm:col-span-2">Mã kiểm tra tệp: {checksum}</p>}
      {errors.checksum && <p role="alert" className="text-xs text-destructive sm:col-span-2">{errors.checksum}</p>}
    </div>
    <Button type="button" onClick={() => void submit()} disabled={busy}>Gửi đề xuất để xác minh nguồn</Button>
    <p className="text-xs text-muted-foreground">Định dạng tệp được chấp nhận: {metadata.file_contract.accepted_extensions.join(', ')}. Hệ thống tự kiểm tra tệp trước khi duyệt.</p>
    <p className="text-xs text-muted-foreground">Xác minh nguồn không đồng nghĩa với công khai. Danh mục của người dân chỉ thay đổi sau bước “Phát hành”.</p>
  </div>
}

function LegalWizard({ item, onDone }: { item: FormReviewCaseV17; onDone: () => Promise<void> }) {
  const legalAsOf = new Date().toISOString().slice(0, 10)
  const sourceUrl = item.current_submission.source_url || ''
  // Older submissions stored an official HTML/VBPL page as a generic file.
  // Treat URL-only pages as e-forms by default; only direct PDF/DOCX URLs
  // should remain file assets and be checked byte-for-byte by Release Gate.
  const isDirectFileUrl = /\.(pdf|docx)(?:[?#].*)?$/i.test(sourceUrl)
  const existingMetadata = item.legal_metadata || {}
  const existingProcedure = (existingMetadata.procedure || {}) as Record<string, unknown>
  const existingAsset = (existingMetadata.asset || {}) as Record<string, unknown>
  const existingBinding = Array.isArray(existingMetadata.bindings) && existingMetadata.bindings.length > 0
    ? (existingMetadata.bindings[0] || {}) as Record<string, unknown>
    : {}
  const existingAliases = Array.isArray(existingMetadata.aliases)
    ? existingMetadata.aliases.map(value => String(value)).filter(Boolean)
    : []
  const textValue = (value: unknown, fallback = '') => value === null || value === undefined ? fallback : String(value)
  const initialAssetKind: 'file' | 'eform' = item.current_submission.asset_kind === 'eform'
    || existingAsset.asset_kind === 'eform'
    || !isDirectFileUrl ? 'eform' : 'file'
  const [formId] = useState(textValue(existingAsset.form_id, `form-${item.case_id}`))
  const [procedureInfo, setProcedureInfo] = useState<FormProcedureCandidateV18 | null>(null)
  const [audience, setAudience] = useState(textValue(existingBinding.audience, 'citizen'))
  useEffect(() => {
    let cancelled = false
    void legalImportApi.formProcedureCandidates({ q: item.procedure_id, limit: 1000 }).then(result => {
      if (!cancelled) setProcedureInfo(result.items.find(p => p.procedure_id === item.procedure_id) || null)
    }).catch(() => { if (!cancelled) toast.error('Chưa đọc được thông tin thủ tục gốc. Hãy làm mới trước khi lưu.') })
    return () => { cancelled = true }
  }, [item.procedure_id])
  const [formName, setFormName] = useState(textValue(existingAsset.canonical_name, item.title))
  const [formCode, setFormCode] = useState(textValue(existingAsset.form_code))
  const [authority, setAuthority] = useState(textValue(existingProcedure.authority))
  const [instrument, setInstrument] = useState(textValue(existingAsset.issuing_instrument))
  const [applicant, setApplicant] = useState(textValue(existingProcedure.applicant_description))
  const [effectiveFrom, setEffectiveFrom] = useState(textValue(existingAsset.effective_from))
  const [effectiveTo, setEffectiveTo] = useState(textValue(existingAsset.effective_to || existingProcedure.effective_to))
  const [assetKind, setAssetKind] = useState<'file' | 'eform'>(initialAssetKind)
  const [checksum] = useState(item.current_submission.source_checksum || textValue(existingAsset.source_checksum))
  const [pageNumber, setPageNumber] = useState(item.current_submission.page_number?.toString() || textValue(existingAsset.page_number))
  const [condition, setCondition] = useState(textValue(existingBinding.condition))
  const [aliases, setAliases] = useState((existingAliases.length ? existingAliases : [item.title]).join('\n'))
  const [preview, setPreview] = useState<FormAttestationPreviewV17 | null>(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    const aliasValues = aliases.split('\n').map(value => value.trim()).filter(Boolean)
    if (!formId.trim() || !formName.trim() || !/^[a-f0-9]{64}$/i.test(checksum) || aliasValues.length === 0) {
      toast.error('Cần nhập mã định danh, tên biểu mẫu, mã kiểm tra tệp và ít nhất một câu hỏi mẫu.')
      return
    }
    if (!procedureInfo || !authority.trim() || !instrument.trim() || !applicant.trim() || !effectiveFrom || (effectiveTo && effectiveTo < effectiveFrom)) {
      toast.error('Cần có thủ tục gốc, cơ quan, văn bản ban hành, đối tượng và ngày hiệu lực đúng theo nguồn. Ngày kết thúc không được trước ngày bắt đầu.')
      return
    }
    setBusy(true)
    try {
      const parsedPageNum = pageNumber.trim() ? parseInt(pageNumber.trim(), 10) : null
      await legalImportApi.updateFormLegalMetadata(item.case_id, {
        procedure: { ...existingProcedure, procedure_id: item.procedure_id, name: procedureInfo.name, domain: item.domain, authority, applicant_description: applicant.trim(), official_source_url: procedureInfo.official_source_url || textValue(existingProcedure.official_source_url), legal_as_of: legalAsOf, coverage_status: 'unresolved' },
        asset: { form_id: formId.trim(), form_code: formCode.trim() || null, canonical_name: formName.trim(), asset_kind: assetKind, source_url: item.current_submission.source_url, source_checksum: checksum.trim().toLowerCase(), source_classification: 'official', issuing_instrument: instrument.trim(), effective_from: effectiveFrom, effective_to: effectiveTo || null, audiences: [audience], coverage_status: 'unresolved', page_number: parsedPageNum },
        bindings: [{ procedure_id: item.procedure_id, form_id: formId.trim(), requirement: condition.trim() ? 'conditional' : 'required', condition: condition.trim() || null, audience, coverage_status: 'unresolved' }],
        aliases: aliasValues,
      })
      toast.success('Đã lưu dữ liệu pháp lý. Chưa xác nhận và chưa công khai.')
      await onDone()
    } catch { toast.error('Dữ liệu pháp lý chưa hợp lệ hoặc trạng thái đã thay đổi.') } finally { setBusy(false) }
  }

  const ready = async () => {
    setBusy(true)
    try { await legalImportApi.readyFormForAttestation(item.case_id); toast.success('Đã tạo bước xác nhận riêng.'); await onDone() }
    catch { toast.error('Chưa thể chuyển sang bước xác nhận pháp lý.') } finally { setBusy(false) }
  }

  const loadPreview = async () => {
    setBusy(true)
    try { setPreview(await legalImportApi.formAttestationPreview(item.case_id)) }
    catch { toast.error('Bản xem trước đã cũ hoặc dữ liệu chưa đủ.') } finally { setBusy(false) }
  }

  const attest = async () => {
    if (!preview) return
    setBusy(true)
    try { await legalImportApi.attestFormCase(item.case_id, preview.fingerprint); toast.success('Đã xác nhận pháp lý. Biểu mẫu vẫn chờ bước kiểm tra trước khi phát hành.'); setPreview(null); await onDone() }
    catch { toast.error('Dữ liệu đã thay đổi. Hãy tải lại bản xem trước trước khi xác nhận.') } finally { setBusy(false) }
  }

  if (item.status === 'ready_for_attestation') return <div className="space-y-3 rounded-md bg-muted p-3">
    <p className="text-sm font-medium">Bước 2/2 · Xác nhận pháp lý riêng</p>
    {!preview ? <Button onClick={() => void loadPreview()} disabled={busy}>Xem bản xác nhận dữ liệu</Button> : <><p>Thủ tục: {textValue(existingProcedure.name)}</p><p>Biểu mẫu: {formName} · {formCode || 'Không ghi mã mẫu'}</p><p>Hiệu lực: {effectiveFrom || 'Chưa ghi'} → {effectiveTo || 'Không ghi ngày kết thúc'}</p><p>Đối tượng: {applicant}</p><p>Cách gọi: {existingAliases.join('; ')}</p><p className="text-sm">Chỉ xác nhận sau khi đã đối chiếu mẫu, nguồn và thủ tục. Bản này chưa công khai.</p><Button onClick={() => void attest()} disabled={busy}><ShieldCheck className="mr-2 h-4 w-4" />Xác nhận pháp lý</Button></>}
  </div>

  return <div className="space-y-3 rounded-md bg-muted/60 p-3" onChange={() => setDirty(true)}>
    <p className="text-sm font-medium">Bước 1/2 · Hoàn thiện dữ liệu pháp lý</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <div><Label htmlFor="form-governance-code">Mã mẫu trên văn bản</Label><Input id="form-governance-code" value={formCode} onChange={e => setFormCode(e.target.value)} placeholder="Ví dụ: CT01" /></div>
      <div><Label htmlFor="form-governance-name">Tên biểu mẫu</Label><Input id="form-governance-name" value={formName} onChange={e => setFormName(e.target.value)} /></div>
      <div><Label htmlFor="form-governance-authority">Cơ quan có thẩm quyền</Label><Input id="form-governance-authority" value={authority} onChange={e => setAuthority(e.target.value)} /></div>
      <div><Label htmlFor="form-governance-instrument">Văn bản ban hành</Label><Input id="form-governance-instrument" value={instrument} onChange={e => setInstrument(e.target.value)} placeholder="Số, ký hiệu văn bản" /></div>
      <div><Label htmlFor="form-governance-applicant">Đối tượng sử dụng</Label><Input id="form-governance-applicant" value={applicant} onChange={e => setApplicant(e.target.value)} /></div>
      <div><Label htmlFor="form-governance-audience">Cho phép cung cấp mẫu cho</Label><select id="form-governance-audience" className="h-10 w-full rounded-md border bg-background px-3" value={audience} onChange={e => setAudience(e.target.value)}><option value="citizen">Người dân</option><option value="officer">Cán bộ</option><option value="both">Người dân và cán bộ</option></select></div>
      <div><Label htmlFor="form-governance-kind">Loại tài nguyên</Label><select id="form-governance-kind" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={assetKind} onChange={e => setAssetKind(e.target.value as 'file' | 'eform')}><option value="file">Tệp tải xuống</option><option value="eform">Biểu mẫu điện tử</option></select><p className="mt-1 text-xs text-muted-foreground">Với trang văn bản trên web, chọn “Biểu mẫu điện tử”; chỉ chọn “Tệp tải xuống” khi đường dẫn mở trực tiếp tệp PDF hoặc DOCX.</p></div>
      <div><Label htmlFor="form-governance-page">Trang PDF (tuỳ chọn)</Label><Input id="form-governance-page" type="number" min="1" value={pageNumber} onChange={e => setPageNumber(e.target.value)} placeholder="Ví dụ: 12" /></div>
      <div><Label htmlFor="form-governance-effective-from">Hiệu lực từ</Label><Input id="form-governance-effective-from" type="date" value={effectiveFrom} onChange={e => setEffectiveFrom(e.target.value)} /></div>
      <div><Label htmlFor="form-governance-effective-to">Hiệu lực đến (nếu có)</Label><Input id="form-governance-effective-to" type="date" value={effectiveTo} onChange={e => setEffectiveTo(e.target.value)} /></div>
    </div>
    <details className="rounded border p-3 text-xs text-muted-foreground"><summary className="cursor-pointer">Thông tin đối chiếu tự động</summary><p className="mt-2 break-all">Mã quản lý: {formId}</p><p className="mt-2 break-all">Mã kiểm tra nguồn: {checksum}</p><p>Muốn thay nguồn, hãy chọn Sửa thông tin để hệ thống kiểm tra lại.</p></details>
    <div><Label htmlFor="form-governance-condition">Điều kiện sử dụng (để trống nếu luôn bắt buộc)</Label><Textarea id="form-governance-condition" value={condition} onChange={e => setCondition(e.target.value)} /></div>
    <div><Label htmlFor="form-governance-aliases">Tên gọi khác và câu hỏi người dùng (mỗi dòng một cách gọi)</Label><Textarea id="form-governance-aliases" value={aliases} onChange={e => setAliases(e.target.value)} /><p className="text-xs text-muted-foreground">Ghi rõ tên thủ tục trong câu hỏi để tránh nhầm với mẫu của thủ tục khác.</p></div>
    {dirty && <p className="text-sm text-muted-foreground">Có thay đổi chưa lưu. Hãy lưu trước khi chuyển sang xác nhận.</p>}
    <div className="flex flex-wrap gap-2"><Button onClick={() => void save()} disabled={busy}>Lưu dữ liệu pháp lý</Button>{item.status === 'legal_enrichment' && <Button variant="outline" onClick={() => void ready()} disabled={busy || dirty}>Chuyển sang xác nhận</Button>}</div>
  </div>
}

function ReleasePanel({ cases, onDone }: { cases: FormReviewCaseV17[]; onDone: () => Promise<void> }) {
  const attested = cases.filter(item => item.status === 'attested')
  const [selected, setSelected] = useState<string[]>([])
  const [legalAsOf, setLegalAsOf] = useState(new Date().toISOString().slice(0, 10))
  const [release, setRelease] = useState<FormReleaseV17 | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  useEffect(() => {
    if (release || !cases.some(item => item.status === 'release_candidate')) return
    let cancelled = false
    void legalImportApi.pendingFormRelease()
      .then(value => {
        if (!cancelled && value) setRelease(value)
      })
      .catch(() => {
        if (!cancelled) toast.error('Không thể tiếp tục bản phát hành đang chờ. Hãy bấm Làm mới và thử lại.')
      })
    return () => { cancelled = true }
  }, [cases, release])

  const toggle = (caseId: string) => {
    setSelected(current => current.includes(caseId) ? current.filter(value => value !== caseId) : [...current, caseId])
  }

  const build = async () => {
    if (selected.length === 0) return toast.error('Hãy chọn ít nhất một biểu mẫu đã xác nhận.')
    setBusy(true)
    try {
      setRelease(await legalImportApi.previewFormRelease(selected, legalAsOf))
      toast.success('Đã tạo bản phát hành thử. Dữ liệu công khai chưa thay đổi.')
      await onDone()
    } catch (error) { toast.error(governanceErrorMessage(error)) }
    finally { setBusy(false) }
  }

  const validate = async () => {
    if (!release) return
    setBusy(true)
    try {
      const next = await legalImportApi.validateFormRelease(release.release_id)
      setRelease(next)
      if (next.gate_report?.passed) toast.success('Đã đạt các điều kiện kiểm tra. Vẫn cần bấm Phát hành để công khai.')
      else toast.error(`Chưa đạt điều kiện phát hành: ${(next.gate_report?.errors || []).map(error => governanceErrorMessage({ detail: error })).join(' ')}`)
    } catch (error) { toast.error(governanceErrorMessage(error)) }
    finally { setBusy(false) }
  }

  const activate = async () => {
    if (!release?.gate_report?.passed) return
    setBusy(true)
    try {
      await legalImportApi.activateFormRelease(release.release_id)
      toast.success('Đã phát hành và đồng bộ trạng thái về cán bộ.')
      setConfirmOpen(false); setRelease(null); setSelected([]); await onDone()
    } catch (error) { toast.error(governanceErrorMessage(error)) }
    finally { setBusy(false) }
  }

  return <div className="space-y-3 rounded-lg border border-primary/30 p-4">
    <div><p className="font-medium">Phát hành biểu mẫu đã xác nhận</p><p className="text-sm text-muted-foreground">Ba bước tách biệt: tạo bản thử → kiểm tra điều kiện → phát hành.</p></div>
    {attested.length === 0 ? <p className="text-sm text-muted-foreground">Chưa có biểu mẫu nào sẵn sàng cho bản phát hành mới.</p> : <div className="space-y-2">
      {attested.map(item => <label key={item.case_id} className="flex items-start gap-2 rounded-md border p-2 text-sm">
        <input aria-label={`Chọn ${item.title}`} type="checkbox" checked={selected.includes(item.case_id)} onChange={() => toggle(item.case_id)} className="mt-1" />
        <span><b>{item.title}</b><br /><span className="text-muted-foreground">{item.procedure_id}</span></span>
      </label>)}
      <div className="flex flex-wrap items-end gap-2"><div><Label>Ngày đối chiếu pháp lý</Label><Input type="date" value={legalAsOf} onChange={event => setLegalAsOf(event.target.value)} /></div><Button onClick={() => void build()} disabled={busy}><Rocket className="mr-2 h-4 w-4" />Tạo bản phát hành thử</Button></div>
    </div>}
    {release && <div className="space-y-2 rounded-md bg-muted p-3 text-sm">
      <p><b>Bản {release.version}</b> · trạng thái: {systemStatusLabel(release.status)}</p>
      {release.gate_report && <p>{release.gate_report.passed ? 'Đã đạt toàn bộ điều kiện kiểm tra.' : `Chưa đạt: ${release.gate_report.errors.map(error => governanceErrorMessage({ detail: error })).join(' ')}`}</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => void validate()} disabled={busy || release.status === 'validated'}>Kiểm tra điều kiện phát hành</Button>
        <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <AlertDialogTrigger asChild><Button disabled={busy || !release.gate_report?.passed}>Phát hành</Button></AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Xác nhận phát hành cho người dân?</AlertDialogTitle>
              <AlertDialogDescription>Danh mục thủ tục, biểu mẫu và FAQ liên quan sẽ được đồng bộ trong cùng một giao dịch. Thao tác được ghi vào lịch sử và có thể khôi phục về bản trước.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={busy}>Hủy</AlertDialogCancel>
              <AlertDialogAction disabled={busy} onClick={() => void activate()}>Xác nhận phát hành</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>}
  </div>
}

export function FormGovernancePanel() {
  const [cases, setCases] = useState<FormReviewCaseV17[]>([])
  const [coverage, setCoverage] = useState<FormCoverageV17 | null>(null)
  const [metadata, setMetadata] = useState<FormSourceProposalMetadataV18 | null>(null)
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [actionBusyCase, setActionBusyCase] = useState<string | null>(null)
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [nextCases, nextCoverage, nextMetadata] = await Promise.all([
        legalImportApi.formGovernanceCases(),
        legalImportApi.formGovernanceCoverage(),
        legalImportApi.formSourceProposalMetadata(),
      ])
      setCases(nextCases); setCoverage(nextCoverage); setMetadata(nextMetadata); setUnavailable(null)
    } catch {
      setUnavailable('Không tải được dữ liệu quản lý biểu mẫu. Hãy bấm Làm mới; dữ liệu công khai chưa bị thay đổi.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (kind: 'supplement' | 'approve' | 'reject', item: FormReviewCaseV17) => {
    if (actionBusyCase) return
    setActionBusyCase(item.case_id)
    setActionErrors(current => {
      const next = { ...current }
      delete next[item.case_id]
      return next
    })
    try {
      if (kind === 'approve') await legalImportApi.approveFormSource(item.case_id)
      else {
        const reason = window.prompt(kind === 'supplement' ? 'Ghi rõ bằng chứng nguồn cần bổ sung:' : 'Ghi lý do nguồn không hợp lệ:')?.trim()
        if (!reason) return
        if (kind === 'supplement') await legalImportApi.requestFormSupplement(item.case_id, reason)
        else await legalImportApi.rejectFormSource(item.case_id, reason)
      }
      toast.success(kind === 'approve' ? 'Đã xác minh nguồn chính thức. Biểu mẫu vẫn chưa được công khai.' : 'Đã lưu quyết định và lịch sử xử lý.')
      await load()
    } catch (error) {
      const message = governanceErrorMessage(error)
      setActionErrors(current => ({ ...current, [item.case_id]: message }))
      toast.error(message)
    } finally {
      setActionBusyCase(null)
    }
  }

  const reopenForCorrection = async (item: FormReviewCaseV17) => {
    if (actionBusyCase) return
    setActionBusyCase(item.case_id)
    setActionErrors(current => {
      const next = { ...current }
      delete next[item.case_id]
      return next
    })
    try {
      await legalImportApi.reopenFormForCorrection(item.case_id)
      toast.success('Đã mở lại hồ sơ để sửa nguồn hoặc mã kiểm tra. Hồ sơ cần được xác nhận pháp lý lại trước khi phát hành.')
      await load()
    } catch (error) {
      const message = governanceErrorMessage(error)
      setActionErrors(current => ({ ...current, [item.case_id]: message }))
      toast.error(message)
    } finally {
      setActionBusyCase(null)
    }
  }

  return (
    <Card className="border-primary/20">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-primary" />Quản trị thủ tục và biểu mẫu</CardTitle>
            <CardDescription className="mt-2">Duyệt nguồn trước; hoàn thiện và xác nhận pháp lý sau; chỉ bước Phát hành mới đưa biểu mẫu đến người dân.</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}><RefreshCcw className="mr-2 h-4 w-4" />Làm mới</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {unavailable && <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"><CircleAlert className="mr-2 inline h-4 w-4" />{unavailable}</div>}
        {coverage && <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Thủ tục đã có quyết định</p><p className="text-xl font-semibold">{coverage.procedure_decided}/{coverage.procedure_total}</p></div>
          <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Biểu mẫu đã có quyết định</p><p className="text-xl font-semibold">{coverage.identity_decided}/{coverage.identity_total}</p></div>
          <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Quan hệ thủ tục–mẫu</p><p className="text-xl font-semibold">{coverage.binding_decided}/{coverage.binding_total}</p></div>
        </div>}
        {!unavailable && metadata && <details className="rounded-lg border p-4"><summary className="cursor-pointer font-medium">Thêm biểu mẫu</summary><div className="mt-4"><SourceProposalPanel metadata={metadata} onDone={load} /></div></details>}
        {!unavailable && <ReleasePanel cases={cases} onDone={load} />}
        {!unavailable && cases.length === 0 && !loading && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Không có đề xuất đang chờ xử lý.</p>}
        {!unavailable && <FormManagementList cases={cases} onDone={load} renderCase={item => <div key={item.case_id} className="space-y-3 rounded-lg border p-4">
          <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-medium">{item.title}</p><p className="text-sm text-muted-foreground">Thủ tục: {item.procedure_id} · Lĩnh vực: {item.domain}</p></div><Badge variant="outline">{labels[item.status] || systemStatusLabel(item.status)}</Badge></div>
          {metadata && <WorkflowStepper item={item} metadata={metadata} />}
          {item.current_submission.source_url?.trim() ? <a className="block truncate text-sm text-primary underline" href={item.current_submission.source_url} target="_blank" rel="noreferrer">Mở nguồn cán bộ gửi</a> : <p className="text-sm text-destructive">Chưa có đường dẫn nguồn chính thức; hồ sơ cần được bổ sung trước khi xác minh.</p>}
          <p className="text-xs text-muted-foreground">Mã kiểm tra tệp: {item.current_submission.source_checksum || 'chưa có — hệ thống sẽ tự tính khi xác minh nguồn'}</p>
          {actionErrors[item.case_id] && <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{actionErrors[item.case_id]}</div>}
          {item.status === 'release_candidate' && <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"><span>Bản phát hành thử đang chờ hoặc chưa đạt điều kiện kiểm tra. Sửa nguồn hoặc mã kiểm tra sẽ hủy xác nhận cũ để xác nhận lại.</span><Button size="sm" variant="outline" onClick={() => void reopenForCorrection(item)} disabled={Boolean(actionBusyCase)}>Sửa dữ liệu nguồn</Button></div>}
          {['submitted', 'resubmitted'].includes(item.status) && <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void act('supplement', item)} disabled={Boolean(actionBusyCase)}>Yêu cầu bổ sung bằng chứng nguồn</Button>
            <Button onClick={() => void act('approve', item)} disabled={Boolean(actionBusyCase)}><CheckCircle2 className="mr-2 h-4 w-4" />{actionBusyCase === item.case_id ? 'Đang xác minh…' : 'Xác minh nguồn chính thức'}</Button>
            <Button variant="destructive" onClick={() => void act('reject', item)} disabled={Boolean(actionBusyCase)}>Từ chối nguồn không hợp lệ</Button>
          </div>}
          {['source_approved', 'legal_enrichment', 'ready_for_attestation'].includes(item.status) && <LegalWizard key={`${item.case_id}-${item.version}`} item={item} onDone={load} />}
          {item.status === 'attested' && <p className="rounded-md bg-muted p-3 text-sm">Đã xác nhận pháp lý. Biểu mẫu chỉ được công khai sau khi tạo bản thử, đạt điều kiện kiểm tra và được quản trị viên phát hành.</p>}
        </div>} />}
      </CardContent>
    </Card>
  )
}
