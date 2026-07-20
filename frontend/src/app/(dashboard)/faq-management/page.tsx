'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Edit3,
  FileText,
  GripVertical,
  MessageCircleQuestion,
  Plus,
  RefreshCcw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import Link from 'next/link'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore } from '@/lib/stores/auth-store'
import {
  DOMAIN_OPTIONS,
  REVIEW_STATUS_OPTIONS,
  faqAdminApi,
  type FaqCreatePayload,
  type FaqItem,
  type FaqUpdatePayload,
} from '@/lib/api/faq-admin'

/* ─── helpers ─── */

function statusBadge(status: string) {
  const variant =
    status === 'approved'
      ? 'default'
      : status === 'rejected'
        ? 'destructive'
        : 'secondary'
  return <Badge variant={variant}>{REVIEW_STATUS_OPTIONS[status] || status}</Badge>
}

function domainLabel(domain: string) {
  return DOMAIN_OPTIONS[domain] || domain
}

function apiErrorDetail(error: unknown, fallback: string): string {
  if (typeof error !== 'object' || error === null || !('response' in error)) return fallback
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  const detail = response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg || '').join('; ')
  return fallback
}

/* ─── editable list helper ─── */

function EditableStringList({
  label,
  items,
  onChange,
  placeholder,
}: {
  label: string
  items: string[]
  onChange: (items: string[]) => void
  placeholder?: string
}) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const trimmed = draft.trim()
    if (!trimmed) return
    onChange([...items, trimmed])
    setDraft('')
  }
  const remove = (index: number) => onChange(items.filter((_, i) => i !== index))
  const move = (from: number, to: number) => {
    if (to < 0 || to >= items.length) return
    const next = [...items]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    onChange(next)
  }

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {items.length > 0 && (
        <div className="space-y-1">
          {items.map((item, idx) => (
            <div key={`${idx}-${item.slice(0, 20)}`} className="group flex items-start gap-2 rounded-md border bg-muted/30 p-2 text-sm">
              <div className="flex flex-col gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                <button type="button" onClick={() => move(idx, idx - 1)} disabled={idx === 0} className="text-muted-foreground hover:text-foreground disabled:opacity-30">
                  <ChevronDown className="h-3 w-3 rotate-180" />
                </button>
                <button type="button" onClick={() => move(idx, idx + 1)} disabled={idx === items.length - 1} className="text-muted-foreground hover:text-foreground disabled:opacity-30">
                  <ChevronDown className="h-3 w-3" />
                </button>
              </div>
              <GripVertical className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/40" />
              <span className="flex-1 leading-relaxed">{item}</span>
              <button type="button" onClick={() => remove(idx)} className="shrink-0 text-muted-foreground hover:text-destructive">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={placeholder || `Thêm ${label.toLowerCase()}...`}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <Button type="button" variant="outline" size="sm" onClick={add} disabled={!draft.trim()}>
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}

/* ─── form dialog ─── */

const EMPTY_FAQ: FaqCreatePayload = {
  question: '',
  answer: '',
  submission_place: '',
  legal_basis: [],
  guidance_label: 'Hướng dẫn nghiệp vụ đã duyệt; cần đối chiếu văn bản hiện hành khi áp dụng.',
  requires_forms: false,
  steps: [],
  form_ids: [],
  domain: 'hanh_chinh_cong',
  ward_scope: 'Le Chan',
  review_status: 'draft',
}

function FaqFormDialog({
  open,
  editItem,
  onClose,
  onSaved,
}: {
  open: boolean
  editItem: FaqItem | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<FaqCreatePayload>(EMPTY_FAQ)
  const [saving, setSaving] = useState(false)
  const isEdit = Boolean(editItem)

  useEffect(() => {
    if (editItem) {
      setForm({
        question: editItem.question,
        answer: editItem.answer,
        submission_place: editItem.submission_place || '',
        legal_basis: editItem.legal_basis || [],
        guidance_label: editItem.guidance_label || '',
        requires_forms: editItem.requires_forms,
        steps: editItem.steps || [],
        form_ids: editItem.form_ids || [],
        domain: editItem.domain,
        ward_scope: editItem.ward_scope || '',
        review_status: editItem.review_status as 'draft' | 'approved' | 'rejected',
      })
    } else {
      setForm(EMPTY_FAQ)
    }
  }, [editItem, open])

  const update = <K extends keyof FaqCreatePayload>(key: K, value: FaqCreatePayload[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const handleSave = async () => {
    if (!form.question.trim() || form.question.length < 5) {
      toast.error('Câu hỏi phải có ít nhất 5 ký tự.')
      return
    }
    if (!form.answer.trim() || form.answer.length < 10) {
      toast.error('Câu trả lời phải có ít nhất 10 ký tự.')
      return
    }
    setSaving(true)
    try {
      if (isEdit && editItem) {
        const updatePayload: FaqUpdatePayload = { ...form }
        await faqAdminApi.update(editItem.id, updatePayload)
        toast.success('Đã cập nhật FAQ thành công.')
      } else {
        await faqAdminApi.create(form)
        toast.success('Đã tạo FAQ mới thành công.')
      }
      onSaved()
      onClose()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể lưu FAQ.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageCircleQuestion className="h-5 w-5 text-primary" />
            {isEdit ? 'Sửa câu hỏi thường gặp' : 'Thêm câu hỏi thường gặp mới'}
          </DialogTitle>
          <DialogDescription>
            {isEdit ? 'Chỉnh sửa nội dung FAQ. Thay đổi trạng thái sang "Đã duyệt" để hiển thị cho người dân.' : 'Tạo FAQ mới. Mặc định trạng thái "Nháp", admin phải duyệt để công khai.'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 py-2">
          {/* Row: Domain + Status + Ward */}
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="faq-domain">Lĩnh vực</Label>
              <Select value={form.domain} onValueChange={(v) => update('domain', v)}>
                <SelectTrigger id="faq-domain"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(DOMAIN_OPTIONS).map(([key, label]) => (
                    <SelectItem key={key} value={key}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="faq-status">Trạng thái</Label>
              <Select value={form.review_status || 'draft'} onValueChange={(v) => update('review_status', v as 'draft' | 'approved' | 'rejected')}>
                <SelectTrigger id="faq-status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="draft">Nháp</SelectItem>
                  <SelectItem value="approved">Đã duyệt</SelectItem>
                  <SelectItem value="rejected">Từ chối</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="faq-ward">Phường / xã</Label>
              <Input id="faq-ward" value={form.ward_scope || ''} onChange={(e) => update('ward_scope', e.target.value)} placeholder="VD: Le Chan" />
            </div>
          </div>

          {/* Question */}
          <div className="space-y-2">
            <Label htmlFor="faq-question">Câu hỏi</Label>
            <Textarea id="faq-question" rows={2} value={form.question} onChange={(e) => update('question', e.target.value)} placeholder="Nhập câu hỏi thường gặp..." />
          </div>

          {/* Answer */}
          <div className="space-y-2">
            <Label htmlFor="faq-answer">Trả lời đã duyệt</Label>
            <Textarea id="faq-answer" rows={5} value={form.answer} onChange={(e) => update('answer', e.target.value)} placeholder="Nhập câu trả lời chi tiết..." />
          </div>

          {/* Submission place */}
          <div className="space-y-2">
            <Label htmlFor="faq-submit-place">Nơi nộp</Label>
            <Input id="faq-submit-place" value={form.submission_place || ''} onChange={(e) => update('submission_place', e.target.value)} placeholder="VD: Bộ phận Một cửa UBND phường..." />
          </div>

          {/* Legal basis */}
          <EditableStringList
            label="Căn cứ pháp lý"
            items={form.legal_basis || []}
            onChange={(items) => update('legal_basis', items)}
            placeholder="VD: Luật Hộ tịch 2014, Điều 16"
          />

          {/* Steps */}
          <EditableStringList
            label="Các bước xử lý"
            items={form.steps || []}
            onChange={(items) => update('steps', items)}
            placeholder="VD: Chuẩn bị hồ sơ đầy đủ"
          />

          {/* Forms */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex items-center gap-3">
              <Checkbox
                id="faq-requires-forms"
                checked={form.requires_forms}
                onCheckedChange={(v) => update('requires_forms', Boolean(v))}
              />
              <Label htmlFor="faq-requires-forms">Yêu cầu biểu mẫu</Label>
            </div>
          </div>

          {form.requires_forms && (
            <EditableStringList
              label="ID biểu mẫu liên quan"
              items={form.form_ids || []}
              onChange={(items) => update('form_ids', items)}
              placeholder="Nhập ID biểu mẫu từ kho biểu mẫu"
            />
          )}

          {/* Guidance label */}
          <div className="space-y-2">
            <Label htmlFor="faq-guidance">Ghi chú / lưu ý nghiệp vụ</Label>
            <Input id="faq-guidance" value={form.guidance_label || ''} onChange={(e) => update('guidance_label', e.target.value)} placeholder="VD: Cần đối chiếu văn bản hiện hành..." />
          </div>
        </div>

        <div className="flex justify-end gap-3 border-t pt-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>Hủy</Button>
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving ? 'Đang lưu...' : isEdit ? 'Cập nhật' : 'Tạo FAQ'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/* ─── delete confirmation ─── */

function DeleteDialog({
  open,
  item,
  onClose,
  onDeleted,
}: {
  open: boolean
  item: FaqItem | null
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const handleDelete = async () => {
    if (!item) return
    setDeleting(true)
    try {
      await faqAdminApi.delete(item.id)
      toast.success('Đã xóa FAQ thành công.')
      onDeleted()
      onClose()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể xóa FAQ.'))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-destructive">Xóa câu hỏi thường gặp</DialogTitle>
          <DialogDescription>
            Bạn có chắc muốn xóa FAQ này? Hành động không thể hoàn tác.
          </DialogDescription>
        </DialogHeader>
        {item && (
          <div className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="line-clamp-2 font-medium">{item.question}</p>
            <p className="mt-1 text-xs text-muted-foreground">{domainLabel(item.domain)} · {REVIEW_STATUS_OPTIONS[item.review_status] || item.review_status}</p>
          </div>
        )}
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={onClose} disabled={deleting}>Hủy</Button>
          <Button variant="destructive" onClick={() => void handleDelete()} disabled={deleting}>
            {deleting ? 'Đang xóa...' : 'Xóa'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/* ─── main page ─── */

export default function FaqManagementPage() {
  const role = useAuthStore((state) => state.role)
  const [faqs, setFaqs] = useState<FaqItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  // Filters
  const [searchQuery, setSearchQuery] = useState('')
  const [filterDomain, setFilterDomain] = useState('all')
  const [filterStatus, setFilterStatus] = useState('all')

  // Dialogs
  const [formOpen, setFormOpen] = useState(false)
  const [editItem, setEditItem] = useState<FaqItem | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteItem, setDeleteItem] = useState<FaqItem | null>(null)

  const loadFaqs = useCallback(async () => {
    setLoading(true)
    try {
      const params: { domain?: string; review_status?: string; q?: string } = {}
      if (filterDomain !== 'all') params.domain = filterDomain
      if (filterStatus !== 'all') params.review_status = filterStatus
      if (searchQuery.trim()) params.q = searchQuery.trim()
      const result = await faqAdminApi.list(params)
      setFaqs(result.items)
      setTotal(result.total)
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không tải được danh sách FAQ.'))
    } finally {
      setLoading(false)
    }
  }, [filterDomain, filterStatus, searchQuery])

  useEffect(() => {
    if (role === 'admin') void loadFaqs()
  }, [loadFaqs, role])

  // Quick status change
  const quickStatusChange = async (item: FaqItem, newStatus: 'approved' | 'rejected' | 'draft') => {
    try {
      await faqAdminApi.update(item.id, { review_status: newStatus })
      toast.success(`Đã ${newStatus === 'approved' ? 'duyệt' : newStatus === 'rejected' ? 'từ chối' : 'chuyển nháp'} FAQ.`)
      void loadFaqs()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể thay đổi trạng thái.'))
    }
  }

  const handleSeed = async () => {
    try {
      const result = await faqAdminApi.seed()
      toast.success(`Đã nạp ${result.imported} FAQ mới. Tổng: ${result.total}.`)
      void loadFaqs()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể nạp dữ liệu mẫu.'))
    }
  }

  // Stats
  const stats = useMemo(() => {
    const approved = faqs.filter((f) => f.review_status === 'approved').length
    const draft = faqs.filter((f) => f.review_status === 'draft').length
    const rejected = faqs.filter((f) => f.review_status === 'rejected').length
    return { approved, draft, rejected }
  }, [faqs])

  if (role !== 'admin') {
    return (
      <main className="p-8">
        <Card>
          <CardContent className="flex items-center gap-3 p-6 text-destructive">
            <X className="h-5 w-5" />
            Chỉ Admin có quyền quản lý Câu hỏi thường gặp.
          </CardContent>
        </Card>
      </main>
    )
  }

  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 p-4 pb-16 md:p-8">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-bold tracking-tight">
            <MessageCircleQuestion className="h-7 w-7 text-primary" />
            Quản lý Câu hỏi thường gặp
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Thêm, sửa, xóa và duyệt câu hỏi thường gặp. FAQ đã duyệt sẽ hiển thị cho người dân tại trang Thủ tục.
          </p>
        </div>
        <div className="flex gap-2 self-start sm:self-auto">
          <Button variant="outline" asChild>
            <Link href="/procedures"><FileText className="mr-2 h-4 w-4" />Xem trang công khai</Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href="/notebooks"><ArrowLeft className="mr-2 h-4 w-4" />Trang chính</Link>
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid gap-3 sm:grid-cols-4">
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="rounded-md bg-primary/10 p-2 text-primary">
              <MessageCircleQuestion className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Tổng FAQ</p>
              <p className="text-2xl font-semibold">{total}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="rounded-md bg-green-500/10 p-2 text-green-600">
              <Check className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Đã duyệt</p>
              <p className="text-2xl font-semibold">{stats.approved}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="rounded-md bg-amber-500/10 p-2 text-amber-600">
              <Edit3 className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Nháp</p>
              <p className="text-2xl font-semibold">{stats.draft}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="rounded-md bg-red-500/10 p-2 text-red-600">
              <X className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Từ chối</p>
              <p className="text-2xl font-semibold">{stats.rejected}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Toolbar */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 p-4">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
              placeholder="Tìm theo từ khóa trong câu hỏi/trả lời..."
            />
          </div>
          <Select value={filterDomain} onValueChange={setFilterDomain}>
            <SelectTrigger className="w-[200px]"><SelectValue placeholder="Lĩnh vực" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Tất cả lĩnh vực</SelectItem>
              {Object.entries(DOMAIN_OPTIONS).map(([key, label]) => (
                <SelectItem key={key} value={key}>{label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={filterStatus} onValueChange={setFilterStatus}>
            <SelectTrigger className="w-[160px]"><SelectValue placeholder="Trạng thái" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Tất cả</SelectItem>
              <SelectItem value="approved">Đã duyệt</SelectItem>
              <SelectItem value="draft">Nháp</SelectItem>
              <SelectItem value="rejected">Từ chối</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={() => void loadFaqs()} disabled={loading}>
            <RefreshCcw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </Button>
          <Button onClick={() => { setEditItem(null); setFormOpen(true) }}>
            <Plus className="mr-2 h-4 w-4" />
            Thêm FAQ
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void handleSeed()}>
            Nạp dữ liệu mẫu
          </Button>
        </CardContent>
      </Card>

      {/* FAQ List */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Danh sách câu hỏi ({faqs.length})</CardTitle>
          <CardDescription>Click vào câu hỏi để sửa. Dùng nút bên phải để duyệt/từ chối/xóa nhanh.</CardDescription>
        </CardHeader>
        <CardContent>
          {loading && <p className="py-8 text-center text-sm text-muted-foreground">Đang tải...</p>}
          {!loading && faqs.length === 0 && (
            <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
              <MessageCircleQuestion className="mx-auto mb-3 h-8 w-8 opacity-40" />
              <p>Chưa có câu hỏi nào phù hợp với bộ lọc.</p>
              <p className="mt-1">Thử thay đổi bộ lọc hoặc thêm FAQ mới.</p>
            </div>
          )}
          {!loading && faqs.length > 0 && (
            <div className="space-y-2">
              {faqs.map((faq) => (
                <div
                  key={faq.id}
                  className="group flex items-start gap-4 rounded-lg border p-4 transition-colors hover:bg-muted/30"
                >
                  {/* Content */}
                  <div
                    className="min-w-0 flex-1 cursor-pointer"
                    onClick={() => {
                      setEditItem(faq)
                      setFormOpen(true)
                    }}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {statusBadge(faq.review_status)}
                      <Badge variant="outline">{domainLabel(faq.domain)}</Badge>
                      {faq.requires_forms && <Badge variant="secondary">Cần biểu mẫu</Badge>}
                      {faq.ward_scope && (
                        <span className="text-xs text-muted-foreground">{faq.ward_scope}</span>
                      )}
                    </div>
                    <h3 className="mt-1.5 text-sm font-medium leading-snug">{faq.question}</h3>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                      {faq.answer}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                      {faq.steps.length > 0 && <span>{faq.steps.length} bước</span>}
                      {faq.legal_basis.length > 0 && <span>{faq.legal_basis.length} căn cứ</span>}
                      {faq.form_ids.length > 0 && <span>{faq.form_ids.length} biểu mẫu</span>}
                      <span>ID: {faq.id}</span>
                      <span>Cập nhật: {new Date(faq.updated_at).toLocaleDateString('vi-VN')}</span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex shrink-0 flex-col gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    {faq.review_status !== 'approved' && (
                      <Button size="sm" variant="outline" className="text-green-600 hover:bg-green-50 hover:text-green-700" onClick={() => void quickStatusChange(faq, 'approved')}>
                        <Check className="mr-1 h-3 w-3" />Duyệt
                      </Button>
                    )}
                    {faq.review_status !== 'rejected' && (
                      <Button size="sm" variant="outline" className="text-amber-600 hover:bg-amber-50 hover:text-amber-700" onClick={() => void quickStatusChange(faq, 'rejected')}>
                        <X className="mr-1 h-3 w-3" />Từ chối
                      </Button>
                    )}
                    {faq.review_status !== 'draft' && (
                      <Button size="sm" variant="outline" onClick={() => void quickStatusChange(faq, 'draft')}>
                        <Edit3 className="mr-1 h-3 w-3" />Nháp
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive hover:bg-destructive/10"
                      onClick={() => {
                        setDeleteItem(faq)
                        setDeleteOpen(true)
                      }}
                    >
                      <Trash2 className="mr-1 h-3 w-3" />Xóa
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dialogs */}
      <FaqFormDialog
        open={formOpen}
        editItem={editItem}
        onClose={() => {
          setFormOpen(false)
          setEditItem(null)
        }}
        onSaved={() => void loadFaqs()}
      />
      <DeleteDialog
        open={deleteOpen}
        item={deleteItem}
        onClose={() => {
          setDeleteOpen(false)
          setDeleteItem(null)
        }}
        onDeleted={() => void loadFaqs()}
      />
    </main>
  )
}
