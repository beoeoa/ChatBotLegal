'use client'

import { useEffect, useState } from 'react'
import { legalImportApi, type FormManagementCatalog } from '@/lib/api/legal-import'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { ProcedureForm } from './ProcedureManagementView'

export function CatalogFormPicker({ values, onChange }: { values: ProcedureForm[]; onChange: (values: ProcedureForm[]) => void }) {
  const [catalog, setCatalog] = useState<FormManagementCatalog | null>(null)
  const [query, setQuery] = useState('')
  const [procedureId, setProcedureId] = useState('')
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const load = () => { setError(''); void legalImportApi.formManagementCatalog().then(setCatalog).catch(() => setError('Không tải được kho biểu mẫu. Hãy thử lại; các mẫu đã chọn vẫn được giữ.')) }
  useEffect(() => { load() }, [])
  const procedures = catalog?.procedures.filter(p => catalog.bindings.some(b => b.procedure_id === p.procedure_id && b.form_id)) || []
  const assets = catalog?.assets.filter(asset => catalog.bindings.some(binding => binding.form_id === asset.form_id && binding.procedure_id === procedureId && ['citizen', 'both'].includes(binding.audience)) && asset.canonical_name.toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi'))) || []
  const choose = (formId: string) => {
    const asset = assets.find(a => a.form_id === formId)
    if (!asset || values.some(value => value.form_id === formId && value.catalog_procedure_id === procedureId)) return
    onChange([...values, { form_id: asset.form_id, catalog_procedure_id: procedureId, name: asset.canonical_name, file_type: asset.asset_kind === 'eform' ? 'eform' : 'file', download_url: asset.download_url || asset.source_url, official_level: 'official', review_status: 'approved' }])
  }
  return <section className="space-y-3 rounded-lg border bg-muted/20 p-3">
    <Label>Biểu mẫu chính thức liên quan</Label>
    <p className="text-xs text-muted-foreground">Chọn từ Thủ tục & biểu mẫu. Liên kết dùng bản đang công khai; không tạo bản sao hoặc tự duyệt mẫu mới.</p>
    {values.map((value, index) => <div key={`${value.form_id || 'legacy'}-${index}`} className="flex items-start justify-between gap-3 rounded border bg-background p-3"><div className="min-w-0"><p className="break-words text-sm font-medium">{value.name}</p><p className="text-xs text-muted-foreground">{value.form_id ? 'Đã liên kết kho biểu mẫu' : 'Mẫu nhập riêng trước đây — có thể gỡ để chọn lại từ kho'}</p>{value.review_status !== 'approved' && <p className="text-sm text-destructive">Mẫu hiện không được công khai. Hãy gỡ hoặc chọn lại.</p>}</div><Button type="button" variant="outline" onClick={() => onChange(values.filter((_, i) => i !== index))}>Gỡ liên kết</Button></div>)}
    <Button type="button" variant="outline" onClick={() => setOpen(!open)}>{open ? 'Đóng danh sách chọn' : 'Chọn biểu mẫu có sẵn'}</Button>
    {open && <div className="space-y-3 rounded border bg-background p-3">
      <p className="text-sm">Chọn đúng thủ tục gốc của biểu mẫu trước khi liên kết.</p>
      <Label htmlFor="catalog-form-procedure">Thủ tục trong kho biểu mẫu</Label><select id="catalog-form-procedure" value={procedureId} onChange={e => { setProcedureId(e.target.value); setQuery('') }} className="h-11 w-full min-w-0 rounded border bg-background px-3"><option value="">Chọn thủ tục</option>{procedures.map(p => <option key={p.procedure_id} value={p.procedure_id}>{p.name}</option>)}</select>
      <Label htmlFor="catalog-form-query">Tìm tên biểu mẫu</Label><Input id="catalog-form-query" value={query} onChange={e => setQuery(e.target.value)} />
      {error ? <div role="alert">{error}<Button type="button" variant="outline" onClick={load}>Thử lại</Button></div> : !catalog ? <p role="status">Đang tải kho biểu mẫu…</p> : <div className="max-h-60 space-y-2 overflow-y-auto">{assets.map(asset => <div key={asset.form_id} className="flex items-center justify-between gap-3 rounded border p-3 text-sm"><span>{asset.canonical_name}{asset.form_code ? ` · ${asset.form_code}` : ''}</span><Button type="button" variant="outline" disabled={values.some(v => v.form_id === asset.form_id && v.catalog_procedure_id === procedureId)} onClick={() => choose(asset.form_id)}>Chọn mẫu</Button></div>)}{procedureId && assets.length === 0 && <p>Chưa có mẫu công khai phù hợp. Hãy bổ sung và phát hành ở Thủ tục & biểu mẫu.</p>}</div>}
      <a href="/procedure-management" target="_blank" rel="noopener noreferrer" className="text-sm text-primary underline">Mở Thủ tục & biểu mẫu</a>
    </div>}
  </section>
}
