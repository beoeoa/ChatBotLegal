'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { legalImportApi, type FormReviewCaseV17, type FormManagementCatalog, type FormManagementEvent, type FormManagementAsset, type FormProcedureCandidateV18 } from '@/lib/api/legal-import'
import { formatApiError } from '@/lib/utils/error-handler'
import { useSettings } from '@/lib/hooks/use-settings'
import { activeDirectoryUnits, domainsForUnit, domainLabel } from '@/lib/utils/organization-directory'

const domainNames: Record<string, string> = {
  cu_tru_an_ninh: 'Cư trú, an ninh', cu_tru: 'Cư trú', ho_tich_chung_thuc: 'Hộ tịch, chứng thực',
  dat_dai_xay_dung: 'Đất đai, xây dựng', an_sinh_y_te_giao_duc: 'An sinh, y tế, giáo dục',
  khieu_nai_to_cao_xu_phat: 'Khiếu nại, tố cáo, xử phạt', quoc_phong_quan_su: 'Quốc phòng, quân sự',
}
export const formDomainName = (value: string) => domainNames[value] || value.replaceAll('_', ' ')
const stateGroup = (value: string) => value === 'released' ? 'public' : ['attested', 'release_candidate'].includes(value) ? 'approved' : ['needs_supplement', 'rejected'].includes(value) ? 'supplement' : ['withdrawn', 'superseded', 'expired', 'quarantined'].includes(value) ? 'archived' : 'pending'
const groups: Record<string, string> = { pending: 'Chờ kiểm tra', supplement: 'Cần bổ sung / bị từ chối', approved: 'Đã duyệt · chờ công khai', public: 'Đã công khai', archived: 'Đã ngừng / đã xóa' }
const historyLabels: Record<string, string> = { submit: 'Thêm biểu mẫu', edit_draft: 'Sửa thông tin, chờ kiểm tra lại', delete_draft: 'Xóa bản nháp', replacement_draft: 'Tạo bản thay thế', legal_enrichment: 'Lưu thông tin pháp lý', attest: 'Duyệt pháp lý', release: 'Phát hành', withdraw_published: 'Gỡ mẫu công khai' }
const safeUrl = (url?: string) => { try { const parsed = new URL(url || ''); return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : undefined } catch { return undefined } }
const selectClass = 'h-11 w-full min-w-0 rounded-md border bg-background px-3 text-sm'

export function FormManagementList({ cases, onDone, renderCase }: {
  cases: FormReviewCaseV17[]; onDone: () => Promise<void>; renderCase: (item: FormReviewCaseV17) => ReactNode
}) {
  const { data: settings } = useSettings()
  const [catalog, setCatalog] = useState<FormManagementCatalog>({ assets: [], procedures: [], bindings: [], aliases: [] })
  const [procedures, setProcedures] = useState<FormProcedureCandidateV18[]>([])
  const [error, setError] = useState('')
  const [unit, setUnit] = useState(''); const [domain, setDomain] = useState(''); const [procedure, setProcedure] = useState('')
  const [query, setQuery] = useState(''); const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [edit, setEdit] = useState<FormReviewCaseV17 | null>(null)
  const [title, setTitle] = useState(''); const [url, setUrl] = useState(''); const [editProcedure, setEditProcedure] = useState('')
  const [kind, setKind] = useState<'file' | 'eform'>('file')
  const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false)
  const [remove, setRemove] = useState<{ item?: FormReviewCaseV17; asset?: FormManagementAsset } | null>(null)
  const [replacement, setReplacement] = useState<FormManagementAsset | null>(null)
  const [history, setHistory] = useState<FormManagementEvent[] | null>(null)
  const [preview, setPreview] = useState<{ url: string; title: string; pdf: boolean } | null>(null)
  const [modalError, setModalError] = useState('')

  useEffect(() => {
    let cancelled = false
    Promise.all([legalImportApi.formManagementCatalog(), legalImportApi.formProcedureCandidates({ limit: 1000 })])
      .then(([next, candidates]) => { if (!cancelled) { setCatalog(next); setProcedures(candidates.items); setError('') } })
      .catch(() => { if (!cancelled) setError('Không tải được danh mục đã công khai. Bấm Làm mới để thử lại; không có dữ liệu nào bị xóa.') })
    return () => { cancelled = true }
  }, [cases])

  const configuredUnits = activeDirectoryUnits(settings)
  const units = configuredUnits.map(unit => [unit.id, unit.name] as const)
  const unitProcedures = procedures.filter(p => !unit || p.primary_organization_unit_id === unit || p.supporting_organization_unit_ids?.includes(unit))
  const domains = domainsForUnit(settings, unit).map(domain => domain.code)
  const filteredProcedures = unitProcedures.filter(p => !domain || p.domain === domain)
  const matchesProcedure = (id: string, fallbackDomain = '') => {
    const p = procedures.find(p => p.procedure_id === id)
    return (!unit || unitProcedures.some(p => p.procedure_id === id)) && (!domain || (p?.domain || fallbackDomain) === domain) && (!procedure || id === procedure)
  }
  const needle = query.trim().toLocaleLowerCase('vi')
  const activeRows = catalog.assets.filter(asset => (!status || status === 'public') && catalog.bindings.some(b => b.form_id === asset.form_id && matchesProcedure(b.procedure_id)) && `${asset.canonical_name} ${asset.form_code || ''}`.toLocaleLowerCase('vi').includes(needle))
  const caseRows = cases.filter(item => item.status !== 'released' && (!status || stateGroup(item.status) === status) && (status === 'archived' || stateGroup(item.status) !== 'archived') && matchesProcedure(item.procedure_id, item.domain) && item.title.toLocaleLowerCase('vi').includes(needle))
  const rows = [...caseRows.map(item => ({ key: item.case_id, item, asset: undefined as FormManagementAsset | undefined })), ...activeRows.map(asset => ({ key: `asset-${asset.form_id}`, asset, item: undefined as FormReviewCaseV17 | undefined }))]
  const pageCount = Math.max(1, Math.ceil(rows.length / 10)); const currentPage = Math.min(page, pageCount)
  const linkedProcedures = (asset: FormManagementAsset) => procedures.filter(p => catalog.bindings.some(b => b.form_id === asset.form_id && b.procedure_id === p.procedure_id))
  const updateFilters = () => setPage(1)
  const work = async (action: () => Promise<void>) => {
    setBusy(true); setModalError('')
    try { await action() } catch (err) { const message = formatApiError(err, 'Không thể thực hiện. Dữ liệu công khai chưa thay đổi.'); setModalError(message); toast.error(message) } finally { setBusy(false) }
  }
  const showHistory = (item?: FormReviewCaseV17, asset?: FormManagementAsset) => void work(async () => {
    setHistory(item ? await legalImportApi.formCaseHistory(item.case_id) : await legalImportApi.formAssetHistory(asset!.form_id))
  })
  const openEdit = (item: FormReviewCaseV17) => { setEdit(item); setTitle(item.title); setUrl(item.current_submission.source_url); setEditProcedure(item.procedure_id); setKind(item.current_submission.asset_kind || 'file'); setModalError('') }
  const save = () => void work(async () => {
    if (!edit) return
    const selected = procedures.find(p => p.procedure_id === editProcedure)
    if (!selected || title.trim().length < 3 || !safeUrl(url)) { setModalError('Chọn thủ tục, nhập tên ít nhất 3 ký tự và đường dẫn nguồn hợp lệ.'); return }
    await legalImportApi.editFormDraft(edit, { ...edit.current_submission, title: title.trim(), procedure_id: selected.procedure_id, domain: selected.domain, source_url: url.trim(), asset_kind: kind, source_checksum: null })
    setEdit(null); toast.success('Đã lưu. Nguồn sẽ được kiểm tra lại trước khi duyệt.'); await onDone()
  })
  const confirmRemove = () => void work(async () => {
    if (!remove || reason.trim().length < 3) { setModalError('Hãy ghi rõ lý do xóa hoặc gỡ mẫu.'); return }
    if (remove.item) await legalImportApi.deleteFormDraft(remove.item, reason.trim())
    else if (remove.asset) {
      const draft = await legalImportApi.withdrawFormPreview(remove.asset.form_id, reason.trim())
      const checked = await legalImportApi.validateFormRelease(draft.release_id)
      if (!checked.gate_report?.passed) { setModalError('Chưa thể gỡ: danh mục chưa đạt điều kiện phát hành. Mẫu hiện tại vẫn được giữ nguyên.'); return }
      await legalImportApi.activateFormRelease(draft.release_id)
    }
    setRemove(null); toast.success('Đã xóa khỏi danh sách sử dụng; lịch sử được giữ lại.'); await onDone()
  })

  return <section aria-label="Danh sách biểu mẫu" className="space-y-4">
    <h3 className="text-lg font-semibold">Danh sách biểu mẫu</h3>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2 lg:grid-cols-3">
      <div><Label htmlFor="forms-unit">Phòng ban</Label><select id="forms-unit" className={selectClass} value={unit} onChange={e => { setUnit(e.target.value); setDomain(''); setProcedure(''); updateFilters() }}><option value="">Tất cả phòng ban</option>{units.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></div>
      <div><Label htmlFor="forms-domain">Lĩnh vực</Label><select id="forms-domain" className={selectClass} value={domain} onChange={e => { setDomain(e.target.value); setProcedure(''); updateFilters() }}><option value="">Tất cả lĩnh vực</option>{domains.map(d => <option key={d} value={d}>{domainLabel(d, settings) || formDomainName(d)}</option>)}</select></div>
      <div><Label htmlFor="forms-procedure">Thủ tục</Label><select id="forms-procedure" className={selectClass} value={procedure} onChange={e => { setProcedure(e.target.value); updateFilters() }}><option value="">Tất cả thủ tục</option>{filteredProcedures.map(p => <option key={p.procedure_id} value={p.procedure_id}>{p.name}</option>)}</select></div>
      <div className="sm:col-span-2"><Label htmlFor="forms-query">Tìm tên hoặc mã biểu mẫu</Label><Input id="forms-query" value={query} onChange={e => { setQuery(e.target.value); updateFilters() }} /></div>
      <div><Label htmlFor="forms-status">Trạng thái</Label><select id="forms-status" className={selectClass} value={status} onChange={e => { setStatus(e.target.value); updateFilters() }}><option value="">Tất cả mẫu đang sử dụng / xử lý</option>{Object.entries(groups).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
    </div>
    <p role="status" className="text-sm text-muted-foreground">{rows.length} biểu mẫu · Trang {currentPage}/{pageCount}</p>
    {rows.length === 0 && <p className="rounded-lg border border-dashed p-4">Không có biểu mẫu phù hợp với bộ lọc.</p>}
    {rows.slice((currentPage - 1) * 10, currentPage * 10).map(({ key, item, asset }) => <details key={key} open={expanded === key} onToggle={event => { if (event.currentTarget.open) setExpanded(key); else setExpanded(current => current === key ? null : current) }} className="rounded-lg border p-4">
      <summary className="cursor-pointer text-sm leading-6"><span className="font-semibold">{item?.title || asset?.canonical_name}</span><span className="ml-3 inline-block rounded-full bg-muted px-3 py-1">{asset ? 'Đã công khai' : groups[stateGroup(item!.status)]}</span><p className="mt-1 text-muted-foreground">{item ? procedures.find(p => p.procedure_id === item.procedure_id)?.name || item.procedure_id : linkedProcedures(asset!).map(p => p.name).join(' · ')}</p></summary>
      <div className="mt-4 space-y-4">
        <div className="flex flex-wrap gap-2">
          {safeUrl(item?.current_submission.source_url || asset?.source_url) && <>
            <Button variant="outline" onClick={() => setPreview({ url: safeUrl(item?.current_submission.source_url || asset?.source_url)!, title: item?.title || asset!.canonical_name, pdf: /\.pdf(?:[?#]|$)/i.test(item?.current_submission.source_url || asset!.source_url) })}>Xem trước</Button>
            <Button asChild variant="outline"><a href={safeUrl(item?.current_submission.source_url || asset?.source_url)} target="_blank" rel="noopener noreferrer">Mở nguồn</a></Button>
          </>}
          {item?.current_submission.asset_kind === 'file' && <Button variant="outline" disabled={busy} onClick={() => void work(async () => { const blob = await legalImportApi.testFormDownload(item.case_id); const href = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = href; a.download = `bieu-mau.${/\.docx/i.test(item.current_submission.source_url) ? 'docx' : 'pdf'}`; a.click(); setTimeout(() => URL.revokeObjectURL(href), 10000); toast.success('Đã kiểm tra và tải tệp.') })}>Tải thử file</Button>}
          {asset?.asset_kind === 'file' && <Button asChild variant="outline"><a href={asset.download_url?.startsWith('/api/procedures/forms-catalog/assets/') ? asset.download_url : safeUrl(asset.source_url)} target="_blank" rel="noopener noreferrer">Tải thử file</a></Button>}
          {item && !['withdrawn', 'superseded', 'quarantined', 'expired'].includes(item.status) && <><Button variant="outline" onClick={() => openEdit(item)}>Sửa thông tin</Button><Button variant="outline" onClick={() => { setRemove({ item }); setReason(''); setModalError('') }}>Xóa bản nháp</Button></>}
          {asset && <><Button onClick={() => setReplacement(asset)}>Thay mẫu / sửa bản công khai</Button><Button variant="outline" onClick={() => { setRemove({ asset }); setReason(''); setModalError('') }}>Gỡ khỏi công khai</Button></>}
          <Button variant="outline" disabled={busy} onClick={() => showHistory(item, asset)}>Lịch sử thay đổi</Button>
        </div>
        {item ? (expanded === key && renderCase(item)) : <div className="grid gap-3 text-sm sm:grid-cols-2"><p>Hiệu lực từ: {asset!.effective_from || 'Chưa ghi ngày bắt đầu'}</p><p>Hiệu lực đến: {asset!.effective_to || 'Không ghi ngày kết thúc'}</p><p>Người dùng: {asset!.audiences.map(a => a === 'citizen' ? 'Người dân' : a === 'officer' ? 'Cán bộ' : 'Người dân và cán bộ').join(', ')}</p><p>Bản này đang thuộc danh mục công khai. Chatbot còn kiểm tra thủ tục, ngày hiệu lực và người sử dụng khi trả lời.</p></div>}
      </div>
    </details>)}
    <div className="flex justify-end gap-2"><Button variant="outline" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Trước</Button><Button variant="outline" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>Sau</Button></div>
    <Dialog open={Boolean(edit)} onOpenChange={open => { if (!open && !busy) setEdit(null) }}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl"><DialogTitle>Sửa thông tin biểu mẫu</DialogTitle>
      <p className="text-sm text-muted-foreground">Lưu sửa đổi sẽ yêu cầu kiểm tra và duyệt lại. Không đổi bản đang công khai.</p>
      <div><Label htmlFor="edit-form-procedure">Gắn với thủ tục</Label><select id="edit-form-procedure" className={selectClass} value={editProcedure} disabled={Boolean(edit?.legal_metadata?._replaces)} onChange={e => setEditProcedure(e.target.value)}>{procedures.map(p => <option key={p.procedure_id} value={p.procedure_id}>{p.name}</option>)}</select></div>
      <div><Label htmlFor="edit-form-name">Tên biểu mẫu</Label><Input id="edit-form-name" value={title} onChange={e => setTitle(e.target.value)} /></div>
      <div><Label htmlFor="edit-form-url">Đường dẫn nguồn chính thức</Label><Input id="edit-form-url" type="url" value={url} onChange={e => setUrl(e.target.value)} /></div>
      <div><Label htmlFor="edit-form-kind">Loại nguồn</Label><select id="edit-form-kind" className={selectClass} value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="file">Tệp PDF / DOCX tải trực tiếp</option><option value="eform">Biểu mẫu trực tuyến</option></select></div>
      {modalError && <p role="alert" className="text-destructive">{modalError}</p>}<Button disabled={busy} onClick={save}>{busy ? 'Đang lưu…' : 'Lưu và kiểm tra lại'}</Button>
    </DialogContent></Dialog>
    <Dialog open={Boolean(remove)} onOpenChange={open => { if (!open && !busy) setRemove(null) }}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-lg"><DialogTitle>{remove?.asset ? 'Gỡ biểu mẫu khỏi công khai?' : 'Xóa bản nháp?'}</DialogTitle><p>{remove?.asset ? 'Mẫu sẽ ngừng được cung cấp cho tất cả thủ tục đang liên kết sau khi kiểm tra thành công. Nếu FAQ đang bắt buộc mẫu này, thao tác có thể bị chặn; hãy thay mẫu trước.' : 'Bản nháp sẽ ngừng xử lý. Lịch sử được giữ lại để đối chiếu.'}</p><Label htmlFor="remove-form-reason">Lý do</Label><Textarea id="remove-form-reason" value={reason} onChange={e => setReason(e.target.value)} />{modalError && <p role="alert" className="text-destructive">{modalError}</p>}<Button variant="destructive" disabled={busy} onClick={confirmRemove}>{busy ? 'Đang kiểm tra…' : 'Xác nhận'}</Button></DialogContent></Dialog>
    <Dialog open={Boolean(replacement)} onOpenChange={open => { if (!open && !busy) setReplacement(null) }}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl"><DialogTitle>Thay biểu mẫu đang công khai</DialogTitle><p>Bản cũ vẫn được sử dụng cho đến khi bản mới được duyệt và phát hành. Thay mẫu này sẽ áp dụng cho các thủ tục sau:</p><ul className="list-disc pl-5">{replacement && linkedProcedures(replacement).map(p => <li key={p.procedure_id}>{p.name}</li>)}</ul>{modalError && <p role="alert" className="text-destructive">{modalError}</p>}<Button disabled={busy || !replacement || !linkedProcedures(replacement).length} onClick={() => void work(async () => { if (!replacement) return; const created = await legalImportApi.replaceManagedForm(replacement.form_id, linkedProcedures(replacement)[0].procedure_id); setReplacement(null); await onDone(); openEdit(created); toast.success('Đã tạo bản thay thế. Hãy cập nhật nguồn và duyệt lại.') })}>Tạo bản thay thế</Button></DialogContent></Dialog>
    <Dialog open={history !== null} onOpenChange={open => { if (!open) setHistory(null) }}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl"><DialogTitle>Lịch sử thay đổi</DialogTitle>{history?.length === 0 && <p>Chưa có lịch sử xử lý được ghi nhận cho mẫu này. Mẫu có thể được nhập từ danh mục ban đầu.</p>}<ol className="space-y-3">{history?.map(event => <li key={event.event_id} className="rounded-md border p-3 text-sm"><b>{historyLabels[event.action] || (event.to_status ? groups[stateGroup(event.to_status)] : 'Cập nhật hồ sơ')}</b><p>{new Date(event.occurred_at).toLocaleString('vi-VN')} · Người xử lý: {event.actor_id}</p>{event.reason_code && <p>{event.reason_code}</p>}</li>)}</ol></DialogContent></Dialog>
    <Dialog open={Boolean(preview)} onOpenChange={open => { if (!open) setPreview(null) }}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-3xl"><DialogTitle>{preview?.title}</DialogTitle>{preview?.pdf ? <iframe title="Xem trước biểu mẫu PDF" src={preview.url} className="h-[55dvh] w-full rounded border" sandbox="allow-same-origin" /> : <p>Nguồn trực tuyến hoặc DOCX không hiển thị trực tiếp trong khung này. Bấm Mở nguồn để xem; PDF có thể xem ngay trong khung.</p>}<Button asChild variant="outline"><a href={preview?.url} target="_blank" rel="noopener noreferrer">Mở nguồn trong trang mới</a></Button><p className="text-xs text-muted-foreground">Nếu nguồn chặn xem trong khung, hãy mở trong trang mới.</p></DialogContent></Dialog>
  </section>
}
