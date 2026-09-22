'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ClipboardList, Edit3, FileText, Plus, RefreshCcw, Search, Trash2, X } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { CatalogFormPicker } from './CatalogFormPicker'

type ProcedureField = { code: string; name: string }

type ProcedureDepartment = {
  id: string
  name: string
  fields: ProcedureField[]
}

export type ProcedureForm = {
  form_id?: string
  catalog_procedure_id?: string
  name: string
  file_type: string
  download_url: string
  official_level: 'official' | 'reference'
  review_status: 'approved' | 'candidate_pending_review'
}

type ManagedProcedure = {
  id: string
  name: string
  domain_slug: string
  department?: string | null
  primary_organization_unit_id?: string | null
  supporting_organization_unit_ids?: string[]
  steps?: string[]
  documents_required?: string[]
  guidance?: string | null
  submission_place?: string | null
  legal_basis?: string[]
  duration?: string | null
  fee?: string | null
  forms?: ProcedureForm[]
}

type ProcedurePayload = Omit<ManagedProcedure, 'id' | 'department'> & { reason: string }

const EMPTY_PROCEDURE: ProcedurePayload = {
  name: '',
  domain_slug: '',
  primary_organization_unit_id: '',
  supporting_organization_unit_ids: [],
  steps: [],
  documents_required: [],
  guidance: '',
  submission_place: '',
  legal_basis: [],
  duration: '',
  fee: '',
  forms: [],
  reason: '',
}

function errorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map(value => typeof value === 'object' && value !== null && 'msg' in value ? String(value.msg) : '').filter(Boolean).join('; ')
  }
  return fallback
}

function FieldList({
  label,
  values,
  onChange,
}: {
  label: string
  values: string[]
  onChange: (values: string[]) => void
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="space-y-2">
        {values.map((value, index) => (
          <div className="flex gap-2" key={`${label}-${index}`}>
            <Input
              value={value}
              placeholder={`${label} ${index + 1}`}
              onChange={event => onChange(values.map((item, itemIndex) => itemIndex === index ? event.target.value : item))}
            />
            <Button
              type="button"
              variant="outline"
              className="min-h-11 shrink-0"
              aria-label={`Xóa ${label.toLocaleLowerCase('vi-VN')} ${index + 1}`}
              onClick={() => onChange(values.filter((_, itemIndex) => itemIndex !== index))}
            >
              <Trash2 aria-hidden="true" className="h-4 w-4 text-destructive" />
            </Button>
          </div>
        ))}
      </div>
      <Button type="button" variant="outline" className="min-h-11" onClick={() => onChange([...values, ''])}>
        <Plus aria-hidden="true" className="mr-2 h-4 w-4" />Thêm {label.toLocaleLowerCase('vi-VN')}
      </Button>
    </div>
  )
}


function ProcedureDialog({
  open,
  item,
  departments,
  onClose,
  onSaved,
}: {
  open: boolean
  item: ManagedProcedure | null
  departments: ProcedureDepartment[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<ProcedurePayload>(EMPTY_PROCEDURE)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setForm(item ? {
      name: item.name,
      domain_slug: item.domain_slug,
      primary_organization_unit_id: item.primary_organization_unit_id || '',
      supporting_organization_unit_ids: item.supporting_organization_unit_ids || [],
      steps: item.steps || [],
      documents_required: item.documents_required || [],
      guidance: item.guidance || '',
      submission_place: item.submission_place || '',
      legal_basis: item.legal_basis || [],
      duration: item.duration || '',
      fee: item.fee || '',
      forms: (item.forms || []).map(value => ({
        name: value.name || '',
        file_type: value.file_type || 'pdf',
        download_url: value.download_url || '',
        official_level: value.official_level || 'reference',
        review_status: value.review_status || 'candidate_pending_review',
      })),
      reason: '',
    } : EMPTY_PROCEDURE)
  }, [item, open])

  const department = departments.find(value => value.id === form.primary_organization_unit_id)
  const update = <K extends keyof ProcedurePayload>(key: K, value: ProcedurePayload[K]) => {
    setForm(current => ({ ...current, [key]: value }))
  }

  const save = async () => {
    if (form.name.trim().length < 3 || !form.primary_organization_unit_id || !form.domain_slug) {
      toast.error('Hãy nhập tên thủ tục, phòng ban chủ trì và lĩnh vực.')
      return
    }
    if (form.reason.trim().length < 10) {
      toast.error('Vui lòng ghi nguồn hoặc lý do nghiệp vụ (tối thiểu 10 ký tự).')
      return
    }
    if ((form.forms || []).some(value => !value.form_id && (value.name.trim().length < 2 || !value.download_url.trim()))) {
      toast.error('Mỗi biểu mẫu cần có tên và đường dẫn tải.')
      return
    }
    setSaving(true)
    try {
      if (item) {
        await apiClient.put(`/procedures/admin/records/${encodeURIComponent(item.id)}`, form)
        toast.success('Đã cập nhật thủ tục hành chính.')
      } else {
        await apiClient.post('/procedures/admin/records', form)
        toast.success('Đã thêm thủ tục hành chính.')
      }
      onSaved()
      onClose()
    } catch (error) {
      toast.error(errorMessage(error, 'Không thể lưu thủ tục hành chính.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={value => !value && onClose()}>
      <DialogContent className="!grid !h-[min(760px,calc(100dvh-2rem))] !w-[min(900px,calc(100vw-2rem))] !max-w-[900px] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b px-5 py-4 pr-12">
          <DialogTitle>{item ? 'Sửa thủ tục hành chính' : 'Thêm thủ tục hành chính'}</DialogTitle>
          <DialogDescription>
            Các trường dưới đây dùng chung với trang Thủ tục hành chính. Quản lý Biểu mẫu chính thức liên quan bằng mục Thêm biểu mẫu trong nội dung bên dưới.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto px-5 py-4">
          <div className="grid gap-4">
          <div className="space-y-2">
            <Label htmlFor="procedure-name">Tên thủ tục</Label>
            <Textarea id="procedure-name" rows={2} className="min-h-16" value={form.name} onChange={event => update('name', event.target.value)} />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="procedure-department">Phòng ban chủ trì</Label>
              <Select value={form.primary_organization_unit_id || undefined} onValueChange={value => {
                const next = departments.find(candidate => candidate.id === value)
                setForm(current => ({
                  ...current,
                  primary_organization_unit_id: value,
                  domain_slug: next?.fields.find(field => field.code === current.domain_slug)?.code || next?.fields[0]?.code || '',
                }))
              }}>
                <SelectTrigger id="procedure-department"><SelectValue placeholder="Chọn phòng ban" /></SelectTrigger>
                <SelectContent>{departments.map(value => <SelectItem key={value.id} value={value.id}>{value.name}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="procedure-domain">Lĩnh vực</Label>
              <Select value={form.domain_slug || undefined} onValueChange={value => update('domain_slug', value)} disabled={!department}>
                <SelectTrigger id="procedure-domain"><SelectValue placeholder="Chọn lĩnh vực" /></SelectTrigger>
                <SelectContent>{(department?.fields || []).map(field => <SelectItem key={field.code} value={field.code}>{field.name}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="procedure-guidance">Hướng dẫn giải quyết</Label>
              <Textarea id="procedure-guidance" rows={3} className="min-h-24" value={form.guidance || ''} onChange={event => update('guidance', event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="procedure-submission-place">Nơi tiếp nhận và trả kết quả</Label>
              <Textarea id="procedure-submission-place" rows={3} className="min-h-24" value={form.submission_place || ''} onChange={event => update('submission_place', event.target.value)} />
            </div>
          </div>
          <FieldList label="Căn cứ pháp lý" values={form.legal_basis || []} onChange={values => update('legal_basis', values)} />
          <FieldList label="Các bước thực hiện" values={form.steps || []} onChange={values => update('steps', values)} />
          <FieldList label="Thành phần hồ sơ" values={form.documents_required || []} onChange={values => update('documents_required', values)} />
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="procedure-duration">Thời hạn</Label>
              <Input id="procedure-duration" value={form.duration || ''} onChange={event => update('duration', event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="procedure-fee">Lệ phí</Label>
              <Input id="procedure-fee" value={form.fee || ''} onChange={event => update('fee', event.target.value)} />
            </div>
          </div>
          <CatalogFormPicker values={form.forms || []} onChange={values => update('forms', values)} />
          <div className="space-y-2">
            <Label htmlFor="procedure-reason">Nguồn hoặc lý do thay đổi</Label>
            <Textarea id="procedure-reason" rows={2} value={form.reason} onChange={event => update('reason', event.target.value)} placeholder="Ví dụ: cập nhật theo văn bản đã được đơn vị cung cấp" />
          </div>
          </div>
        </div>
        <div className="flex justify-end gap-3 border-t bg-background px-5 py-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>Hủy</Button>
          <Button onClick={() => void save()} disabled={saving}>{saving ? 'Đang lưu...' : 'Lưu thủ tục'}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function ProcedureManagementView() {
  const role = useAuthStore(state => state.role)
  const [items, setItems] = useState<ManagedProcedure[]>([])
  const [departments, setDepartments] = useState<ProcedureDepartment[]>([])
  const [query, setQuery] = useState('')
  const [departmentId, setDepartmentId] = useState('all')
  const [page, setPage] = useState(1)
  const pageSize = 10
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ManagedProcedure | null>(null)
  const [deleting, setDeleting] = useState<ManagedProcedure | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [records, directory] = await Promise.all([
        apiClient.get<{ items: ManagedProcedure[] }>('/procedures/admin/records'),
        apiClient.get<{ departments: ProcedureDepartment[] }>('/procedures/directory'),
      ])
      setItems(records.data.items || [])
      setDepartments(directory.data.departments || [])
    } catch (error) {
      toast.error(errorMessage(error, 'Không tải được danh sách thủ tục hành chính.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (role === 'admin') void load()
  }, [load, role])

  const filteredItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('vi-VN')
    return items.filter(item => {
      const text = [item.name, item.department, item.guidance, item.submission_place, ...(item.steps || [])]
        .filter(Boolean).join(' ').toLocaleLowerCase('vi-VN')
      return (departmentId === 'all' || item.primary_organization_unit_id === departmentId) && (!normalized || text.includes(normalized))
    })
  }, [departmentId, items, query])
  const pageCount = Math.max(1, Math.ceil(filteredItems.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const pagedItems = filteredItems.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  useEffect(() => { setPage(1) }, [query, departmentId])

  const stats = useMemo(() => ({
    total: items.length,
    withSteps: items.filter(item => (item.steps || []).length > 0).length,
    withDocuments: items.filter(item => (item.documents_required || []).length > 0).length,
    withForms: items.filter(item => (item.forms || []).length > 0).length,
  }), [items])

  const remove = async () => {
    if (!deleting) return
    setDeleteLoading(true)
    try {
      await apiClient.delete(`/procedures/admin/records/${encodeURIComponent(deleting.id)}`)
      toast.success('Đã xóa thủ tục khỏi danh mục.')
      setDeleting(null)
      await load()
    } catch (error) {
      toast.error(errorMessage(error, 'Không thể xóa thủ tục hành chính.'))
    } finally {
      setDeleteLoading(false)
    }
  }

  if (role !== 'admin') {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
          <main className="mx-auto max-w-xl"><Card><CardContent className="flex items-center gap-3 p-6 text-destructive"><X aria-hidden="true" className="h-5 w-5" />Chỉ Admin có quyền quản lý thủ tục hành chính.</CardContent></Card></main>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <main className="mx-auto w-full max-w-7xl space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-3 text-xl font-bold tracking-tight md:text-2xl"><ClipboardList aria-hidden="true" className="h-7 w-7 text-primary" />Quản lý thủ tục hành chính</h1>
              <p className="mt-1 text-sm text-muted-foreground">Danh mục thủ tục đang quản lý tại các phòng ban. Chỉ hiển thị dữ liệu thực tế đã được lưu.</p>
            </div>
            <Button variant="outline" asChild><a href="/procedures"><FileText aria-hidden="true" className="mr-2 h-4 w-4" />Xem trang công khai</a></Button>
          </div>

          <div className="grid gap-3 sm:grid-cols-4">
            {[
              ['Tổng thủ tục', stats.total, 'Đang quản lý'],
              ['Có các bước', stats.withSteps, 'Có trình tự thực hiện'],
              ['Có hồ sơ', stats.withDocuments, 'Có thành phần hồ sơ'],
              ['Có biểu mẫu', stats.withForms, 'Có biểu mẫu liên quan'],
            ].map(([label, value, description]) => (
              <Card key={String(label)}><CardContent className="p-4"><p className="text-sm text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p><p className="text-xs text-muted-foreground">{description}</p></CardContent></Card>
            ))}
          </div>

          <Card>
            <CardContent className="flex flex-col gap-3 p-4 lg:flex-row lg:items-center">
              <div className="relative min-w-0 flex-1"><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><Input className="min-h-11 pl-9" placeholder="Tìm theo tên hoặc nội dung hướng dẫn..." value={query} onChange={event => setQuery(event.target.value)} /></div>
              <Select value={departmentId} onValueChange={setDepartmentId}><SelectTrigger className="min-h-11 w-full lg:w-64"><SelectValue placeholder="Tất cả phòng ban" /></SelectTrigger><SelectContent><SelectItem value="all">Tất cả phòng ban</SelectItem>{departments.map(department => <SelectItem key={department.id} value={department.id}>{department.name}</SelectItem>)}</SelectContent></Select>
              <Button variant="outline" className="min-h-11" onClick={() => void load()} disabled={loading}><RefreshCcw aria-hidden="true" className="mr-2 h-4 w-4" />Làm mới</Button>
              <Button className="min-h-11" onClick={() => { setEditing(null); setDialogOpen(true) }}><Plus aria-hidden="true" className="mr-2 h-4 w-4" />Thêm thủ tục</Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Danh sách thủ tục ({filteredItems.length}{filteredItems.length !== items.length ? `/${items.length}` : ''})</CardTitle><CardDescription>Chọn Sửa để cập nhật hoặc Xóa để ẩn thủ tục khỏi danh mục đang sử dụng.</CardDescription></CardHeader>
            <CardContent>
              {loading ? <p role="status" className="py-10 text-center text-sm text-muted-foreground">Đang tải dữ liệu thủ tục...</p> : filteredItems.length === 0 ? <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Không có thủ tục phù hợp với bộ lọc hiện tại.</div> : <div className="grid gap-3 md:grid-cols-2">{pagedItems.map(item => <article key={item.id} className="rounded-lg border p-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><Badge variant="outline">{departments.flatMap(department => department.fields).find(field => field.code === item.domain_slug)?.name || item.domain_slug}</Badge><h3 className="mt-2 text-sm font-semibold leading-6">{item.name}</h3><p className="mt-1 text-xs text-muted-foreground">{item.department || 'Chưa gán phòng ban'}</p></div><div className="flex shrink-0 gap-2"><Button variant="outline" size="sm" className="min-h-11" onClick={() => { setEditing(item); setDialogOpen(true) }}><Edit3 aria-hidden="true" className="mr-1 h-4 w-4" />Sửa</Button><Button variant="outline" size="sm" className="min-h-11 text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => setDeleting(item)}><Trash2 aria-hidden="true" className="mr-1 h-4 w-4" />Xóa</Button></div></div><div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground"><span>{(item.steps || []).length} bước</span><span>{(item.documents_required || []).length} thành phần hồ sơ</span><span>{(item.legal_basis || []).length} căn cứ</span><span>{(item.forms || []).length} biểu mẫu</span></div></article>)}</div>}
              <nav aria-label="Phân trang thủ tục" className="mt-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">Trang {currentPage}/{pageCount} · {filteredItems.length} thủ tục · {pageSize} thủ tục/trang</p><div className="flex items-center gap-2"><Button variant="outline" disabled={currentPage === 1 || loading} onClick={() => setPage(currentPage - 1)}>Trước</Button><label className="text-sm">Trang <select aria-label="Chọn trang thủ tục" value={currentPage} className="h-11 rounded-md border bg-background px-3" onChange={event => setPage(Number(event.target.value))}>{Array.from({ length: pageCount }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></label><Button variant="outline" disabled={currentPage === pageCount || loading} onClick={() => setPage(currentPage + 1)}>Sau</Button></div></nav>
            </CardContent>
          </Card>
        </main>
      </div>
      <ProcedureDialog open={dialogOpen} item={editing} departments={departments} onClose={() => { setDialogOpen(false); setEditing(null) }} onSaved={() => void load()} />
      <ConfirmDialog open={Boolean(deleting)} onOpenChange={open => !open && setDeleting(null)} title="Xóa thủ tục hành chính?" description={`Thủ tục “${deleting?.name || ''}” sẽ bị ẩn khỏi danh mục đang sử dụng.`} confirmText="Xóa thủ tục" confirmVariant="destructive" onConfirm={() => void remove()} isLoading={deleteLoading} />
    </AppShell>
  )
}
