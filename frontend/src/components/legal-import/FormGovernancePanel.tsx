'use client'

import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, RefreshCcw, Rocket, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
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
      ? 'Chưa có bản phát hành vượt qua Release Gate.'
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
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('')
  const [candidates, setCandidates] = useState<FormProcedureCandidateV18[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [title, setTitle] = useState('')
  const [sourceType, setSourceType] = useState<'official_url' | 'pdf' | 'docx' | 'eform'>('official_url')
  const [sourceUrl, setSourceUrl] = useState('')
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
      toast.error('Trình duyệt không hỗ trợ tính checksum SHA-256 cho tệp này.')
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
      checksum: (sourceType === 'pdf' || sourceType === 'docx') && !checksum
        ? 'Hãy chọn đúng tệp để hệ thống tính checksum SHA-256.'
        : '',
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
      await legalImportApi.submitFormGovernanceCase({
        procedure_id: procedure.procedure_id,
        domain: procedure.domain,
        title: title.trim(),
        source_url: sourceUrl.trim(),
        source_checksum: checksum || undefined,
        asset_kind: sourceType === 'eform' ? 'eform' : 'file',
        note: sourceType === 'pdf' || sourceType === 'docx'
          ? `Tệp ${fileName} được tính SHA-256 tại trình duyệt; chờ cổng upload an toàn.`
          : `Loại nguồn đề xuất: ${sourceType}`,
      })
      toast.success('Đã gửi đề xuất để xác minh nguồn. Dữ liệu công khai chưa thay đổi.')
      setTitle(''); setSourceUrl(''); setChecksum(''); setFileName(''); setSelectedId('')
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
      <div className="sm:col-span-2"><Label htmlFor="procedure-query">Tên hoặc mã thủ tục</Label><Input id="procedure-query" value={query} onChange={event => setQuery(event.target.value)} placeholder="Ví dụ: đăng ký thường trú hoặc 1.004222" /></div>
      <div><Label htmlFor="procedure-domain">Lĩnh vực</Label><select id="procedure-domain" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={domain} onChange={event => setDomain(event.target.value)}><option value="">Tất cả lĩnh vực được phép</option><option value="an_sinh_y_te_giao_duc">An sinh, y tế, giáo dục</option><option value="cu_tru_an_ninh">Cư trú, an ninh</option><option value="dat_dai_xay_dung">Đất đai, xây dựng</option><option value="ho_tich_chung_thuc">Hộ tịch, chứng thực</option><option value="khieu_nai_to_cao_xu_phat">Khiếu nại, tố cáo, xử phạt</option></select></div>
    </div>
    <Button type="button" variant="outline" onClick={() => void search()} disabled={busy}>Tìm thủ tục</Button>
    <div>
      <Label htmlFor="procedure-choice">Thủ tục đã chọn</Label>
      <select id="procedure-choice" aria-invalid={Boolean(errors.procedure)} className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={selectedId} onChange={event => { setSelectedId(event.target.value); setErrors(current => ({ ...current, procedure: '' })) }}><option value="">Chọn từ kết quả tìm kiếm</option>{candidates.map(item => <option key={item.procedure_id} value={item.procedure_id}>{item.name} · {item.procedure_code} · {item.domain}</option>)}</select>
      {errors.procedure && <p role="alert" className="mt-1 text-xs text-destructive">{errors.procedure}</p>}
      {candidates.find(item => item.procedure_id === selectedId) && (() => {
        const selected = candidates.find(item => item.procedure_id === selectedId)!
        return <div className="mt-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm"><p className="font-medium">{selected.name}</p><p className="mt-1 text-muted-foreground">Mã: {selected.procedure_code} · Lĩnh vực: {selected.domain}</p></div>
      })()}
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <div><Label htmlFor="proposal-title">Tên biểu mẫu</Label><Input id="proposal-title" aria-invalid={Boolean(errors.title)} value={title} onChange={event => { setTitle(event.target.value); setErrors(current => ({ ...current, title: '' })) }} />{errors.title && <p role="alert" className="mt-1 text-xs text-destructive">{errors.title}</p>}</div>
      <div><Label htmlFor="source-type">Loại nguồn</Label><select id="source-type" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={sourceType} onChange={event => { setSourceType(event.target.value as typeof sourceType); setChecksum(''); setFileName('') }}><option value="official_url">URL chính thức</option><option value="pdf">PDF</option><option value="docx">DOCX</option><option value="eform">E-form</option></select></div>
      <div className="sm:col-span-2"><Label htmlFor="source-url">URL nguồn chính thức</Label><Input id="source-url" type="url" aria-invalid={Boolean(errors.sourceUrl)} value={sourceUrl} onChange={event => { setSourceUrl(event.target.value); setErrors(current => ({ ...current, sourceUrl: '' })) }} placeholder="https://..." />{errors.sourceUrl && <p role="alert" className="mt-1 text-xs text-destructive">{errors.sourceUrl}</p>}</div>
      {(sourceType === 'pdf' || sourceType === 'docx') && <div className="sm:col-span-2"><Label htmlFor="source-file">Tệp {sourceType.toUpperCase()} để đối chiếu checksum</Label><Input id="source-file" type="file" accept={sourceType === 'pdf' ? '.pdf,application/pdf' : '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document'} onChange={event => void selectFile(event.target.files?.[0])} /><p className="mt-1 text-xs text-muted-foreground">Tệp chưa được tải lên máy chủ ở bước này; cần URL chính thức và sẽ chờ cổng upload an toàn.</p></div>}
      {checksum && <p className="break-all text-xs text-muted-foreground sm:col-span-2">SHA-256: {checksum}</p>}
      {errors.checksum && <p role="alert" className="text-xs text-destructive sm:col-span-2">{errors.checksum}</p>}
    </div>
    <Button type="button" onClick={() => void submit()} disabled={busy}>Gửi đề xuất để xác minh nguồn</Button>
    <p className="text-xs text-muted-foreground">Định dạng tệp đối chiếu: {metadata.file_contract.accepted_extensions.join(', ')} · checksum {metadata.file_contract.checksum.toUpperCase()}.</p>
    <p className="text-xs text-muted-foreground">Xác minh nguồn không đồng nghĩa công khai. Chỉ bước “Phát hành” sau Release Gate mới thay đổi danh mục người dân thấy.</p>
  </div>
}

function LegalWizard({ item, onDone }: { item: FormReviewCaseV17; onDone: () => Promise<void> }) {
  const legalAsOf = new Date().toISOString().slice(0, 10)
  const [formId, setFormId] = useState('')
  const [formName, setFormName] = useState(item.title)
  const [formCode, setFormCode] = useState('')
  const [authority, setAuthority] = useState('UBND cấp xã')
  const [instrument, setInstrument] = useState('')
  const [applicant, setApplicant] = useState('Công dân thực hiện thủ tục')
  const [effectiveFrom, setEffectiveFrom] = useState(legalAsOf)
  const [effectiveTo, setEffectiveTo] = useState('')
  const [assetKind, setAssetKind] = useState<'file' | 'eform'>('file')
  const [checksum, setChecksum] = useState(item.current_submission.source_checksum || '')
  const [condition, setCondition] = useState('')
  const [aliases, setAliases] = useState('')
  const [preview, setPreview] = useState<FormAttestationPreviewV17 | null>(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    const aliasValues = aliases.split('\n').map(value => value.trim()).filter(Boolean)
    if (!formId.trim() || !formName.trim() || !/^[a-f0-9]{64}$/i.test(checksum) || aliasValues.length === 0) {
      toast.error('Cần nhập mã định danh, tên biểu mẫu, checksum SHA-256 và ít nhất một câu hỏi mẫu.')
      return
    }
    setBusy(true)
    try {
      await legalImportApi.updateFormLegalMetadata(item.case_id, {
        procedure: { procedure_id: item.procedure_id, name: item.title, domain: item.domain, authority, jurisdiction: 'Hai Phong', applicant_description: applicant.trim() || null, official_source_url: item.current_submission.source_url, effective_from: effectiveFrom || null, effective_to: effectiveTo || null, legal_as_of: legalAsOf, coverage_status: 'unresolved' },
        asset: { form_id: formId.trim(), form_code: formCode.trim() || null, canonical_name: formName.trim(), asset_kind: assetKind, source_url: item.current_submission.source_url, source_checksum: checksum.trim().toLowerCase(), source_classification: 'official', issuing_instrument: instrument.trim() || null, effective_from: effectiveFrom || null, effective_to: effectiveTo || null, audiences: ['citizen'], coverage_status: 'unresolved' },
        bindings: [{ procedure_id: item.procedure_id, form_id: formId.trim(), requirement: condition.trim() ? 'conditional' : 'required', condition: condition.trim() || null, audience: 'citizen', coverage_status: 'unresolved' }],
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
    catch { toast.error('Preview đã cũ hoặc dữ liệu chưa đủ.') } finally { setBusy(false) }
  }

  const attest = async () => {
    if (!preview) return
    setBusy(true)
    try { await legalImportApi.attestFormCase(item.case_id, preview.fingerprint); toast.success('Đã xác nhận pháp lý. Biểu mẫu vẫn chờ Release Gate.'); setPreview(null); await onDone() }
    catch { toast.error('Fingerprint không còn khớp. Hãy tải lại preview.') } finally { setBusy(false) }
  }

  if (item.status === 'ready_for_attestation') return <div className="space-y-3 rounded-md bg-muted p-3">
    <p className="text-sm font-medium">Bước 2/2 · Xác nhận pháp lý riêng</p>
    {!preview ? <Button onClick={() => void loadPreview()} disabled={busy}>Xem bản xác nhận khóa checksum</Button> : <><p className="break-all text-xs text-muted-foreground">Fingerprint: {preview.fingerprint}</p><Button onClick={() => void attest()} disabled={busy}><ShieldCheck className="mr-2 h-4 w-4" />Xác nhận pháp lý</Button></>}
  </div>

  return <div className="space-y-3 rounded-md bg-muted/60 p-3">
    <p className="text-sm font-medium">Bước 1/2 · Hoàn thiện dữ liệu pháp lý</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <div><Label>Mã định danh biểu mẫu</Label><Input value={formId} onChange={e => setFormId(e.target.value)} placeholder="Ví dụ: ct01-68-2025-tt-bca" /></div>
      <div><Label>Mã mẫu trên văn bản</Label><Input value={formCode} onChange={e => setFormCode(e.target.value)} placeholder="Ví dụ: CT01" /></div>
      <div><Label>Tên biểu mẫu</Label><Input value={formName} onChange={e => setFormName(e.target.value)} /></div>
      <div><Label>Cơ quan có thẩm quyền</Label><Input value={authority} onChange={e => setAuthority(e.target.value)} /></div>
      <div><Label>Văn bản ban hành</Label><Input value={instrument} onChange={e => setInstrument(e.target.value)} placeholder="Số, ký hiệu văn bản" /></div>
      <div><Label>Checksum SHA-256</Label><Input value={checksum} onChange={e => setChecksum(e.target.value)} /></div>
      <div><Label>Đối tượng sử dụng</Label><Input value={applicant} onChange={e => setApplicant(e.target.value)} /></div>
      <div><Label>Loại tài nguyên</Label><select className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={assetKind} onChange={e => setAssetKind(e.target.value as 'file' | 'eform')}><option value="file">Tệp tải xuống</option><option value="eform">Biểu mẫu điện tử</option></select></div>
      <div><Label>Hiệu lực từ</Label><Input type="date" value={effectiveFrom} onChange={e => setEffectiveFrom(e.target.value)} /></div>
      <div><Label>Hiệu lực đến (nếu có)</Label><Input type="date" value={effectiveTo} onChange={e => setEffectiveTo(e.target.value)} /></div>
    </div>
    <div><Label>Điều kiện sử dụng (để trống nếu luôn bắt buộc)</Label><Textarea value={condition} onChange={e => setCondition(e.target.value)} /></div>
    <div><Label>Câu hỏi mẫu/alias (mỗi dòng một câu)</Label><Textarea value={aliases} onChange={e => setAliases(e.target.value)} /></div>
    <div className="flex flex-wrap gap-2"><Button onClick={() => void save()} disabled={busy}>Lưu dữ liệu pháp lý</Button>{item.status === 'legal_enrichment' && <Button variant="outline" onClick={() => void ready()} disabled={busy}>Chuyển sang xác nhận</Button>}</div>
  </div>
}

function ReleasePanel({ cases, onDone }: { cases: FormReviewCaseV17[]; onDone: () => Promise<void> }) {
  const attested = cases.filter(item => item.status === 'attested')
  const [selected, setSelected] = useState<string[]>([])
  const [legalAsOf, setLegalAsOf] = useState(new Date().toISOString().slice(0, 10))
  const [release, setRelease] = useState<FormReleaseV17 | null>(null)
  const [busy, setBusy] = useState(false)

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
    } catch { toast.error('Không tạo được bản phát hành thử. Hãy kiểm tra trạng thái xác nhận.') }
    finally { setBusy(false) }
  }

  const validate = async () => {
    if (!release) return
    setBusy(true)
    try {
      const next = await legalImportApi.validateFormRelease(release.release_id)
      setRelease(next)
      if (next.gate_report?.passed) toast.success('Release Gate đã đạt. Vẫn cần bấm Phát hành để công khai.')
      else toast.error(`Release Gate chưa đạt: ${(next.gate_report?.errors || []).join(', ')}`)
    } catch { toast.error('Không chạy được Release Gate.') }
    finally { setBusy(false) }
  }

  const activate = async () => {
    if (!release?.gate_report?.passed) return
    if (!window.confirm('Phát hành bản này cho người dân? Thao tác được ghi audit và có thể rollback bằng manifest trước.')) return
    setBusy(true)
    try {
      await legalImportApi.activateFormRelease(release.release_id)
      toast.success('Đã phát hành và đồng bộ trạng thái về cán bộ.')
      setRelease(null); setSelected([]); await onDone()
    } catch { toast.error('Không thể phát hành. Danh mục đang dùng không bị thay đổi.') }
    finally { setBusy(false) }
  }

  return <div className="space-y-3 rounded-lg border border-primary/30 p-4">
    <div><p className="font-medium">Phát hành biểu mẫu đã xác nhận</p><p className="text-sm text-muted-foreground">Ba bước tách biệt: tạo bản thử → kiểm tra Release Gate → phát hành.</p></div>
    {attested.length === 0 ? <p className="text-sm text-muted-foreground">Chưa có biểu mẫu nào sẵn sàng cho bản phát hành mới.</p> : <div className="space-y-2">
      {attested.map(item => <label key={item.case_id} className="flex items-start gap-2 rounded-md border p-2 text-sm">
        <input aria-label={`Chọn ${item.title}`} type="checkbox" checked={selected.includes(item.case_id)} onChange={() => toggle(item.case_id)} className="mt-1" />
        <span><b>{item.title}</b><br /><span className="text-muted-foreground">{item.procedure_id}</span></span>
      </label>)}
      <div className="flex flex-wrap items-end gap-2"><div><Label>Ngày đối chiếu pháp lý</Label><Input type="date" value={legalAsOf} onChange={event => setLegalAsOf(event.target.value)} /></div><Button onClick={() => void build()} disabled={busy}><Rocket className="mr-2 h-4 w-4" />Tạo bản phát hành thử</Button></div>
    </div>}
    {release && <div className="space-y-2 rounded-md bg-muted p-3 text-sm">
      <p><b>Bản {release.version}</b> · {release.release_id} · trạng thái: {release.status}</p>
      <p className="break-all text-xs text-muted-foreground">Manifest: {release.manifest_sha256}</p>
      {release.gate_report && <p>{release.gate_report.passed ? 'Đã đạt toàn bộ cổng kiểm tra.' : `Chưa đạt: ${release.gate_report.errors.join(', ')}`}</p>}
      <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void validate()} disabled={busy || release.status === 'validated'}>Chạy Release Gate</Button><Button onClick={() => void activate()} disabled={busy || !release.gate_report?.passed}>Phát hành</Button></div>
    </div>}
  </div>
}

export function FormGovernancePanel() {
  const [cases, setCases] = useState<FormReviewCaseV17[]>([])
  const [coverage, setCoverage] = useState<FormCoverageV17 | null>(null)
  const [metadata, setMetadata] = useState<FormSourceProposalMetadataV18 | null>(null)
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState<string | null>(null)

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
      setUnavailable('Kho quản trị PostgreSQL mới chưa được kích hoạt. Danh mục công khai hiện tại vẫn hoạt động bằng chế độ tương thích an toàn.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (kind: 'supplement' | 'approve' | 'reject', item: FormReviewCaseV17) => {
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
    } catch { toast.error('Không thể lưu quyết định. Dữ liệu công khai không bị thay đổi.') }
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
        {!unavailable && metadata && <SourceProposalPanel metadata={metadata} onDone={load} />}
        {!unavailable && <ReleasePanel cases={cases} onDone={load} />}
        {!unavailable && cases.length === 0 && !loading && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Không có đề xuất đang chờ xử lý.</p>}
        {cases.map(item => <div key={item.case_id} className="space-y-3 rounded-lg border p-4">
          <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-medium">{item.title}</p><p className="text-sm text-muted-foreground">Thủ tục: {item.procedure_id} · Lĩnh vực: {item.domain}</p></div><Badge variant="outline">{labels[item.status] || item.status}</Badge></div>
          {metadata && <WorkflowStepper item={item} metadata={metadata} />}
          <a className="block truncate text-sm text-primary underline" href={item.current_submission.source_url} target="_blank" rel="noreferrer">Mở nguồn cán bộ gửi</a>
          {['submitted', 'resubmitted'].includes(item.status) && <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void act('supplement', item)}>Yêu cầu bổ sung bằng chứng nguồn</Button>
            <Button onClick={() => void act('approve', item)}><CheckCircle2 className="mr-2 h-4 w-4" />Xác minh nguồn chính thức</Button>
            <Button variant="destructive" onClick={() => void act('reject', item)}>Từ chối nguồn không hợp lệ</Button>
          </div>}
          {['source_approved', 'legal_enrichment', 'ready_for_attestation'].includes(item.status) && <LegalWizard item={item} onDone={load} />}
          {item.status === 'attested' && <p className="rounded-md bg-muted p-3 text-sm">Đã xác nhận pháp lý. Biểu mẫu chỉ được công khai sau khi tạo release candidate, chạy Release Gate và Admin bấm Phát hành.</p>}
        </div>)}
      </CardContent>
    </Card>
  )
}
