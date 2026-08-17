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
  Sparkles,
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
import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { AppShell } from '@/components/layout/AppShell'
import {
  DOMAIN_OPTIONS,
  REVIEW_STATUS_OPTIONS,
  faqAdminApi,
  type FaqCreatePayload,
  type FaqItem,
  type FaqRelease,
  type FaqUpdatePayload,
} from '@/lib/api/faq-admin'
import { legalImportApi, type FormProcedureCandidateV18 } from '@/lib/api/legal-import'

/* ─── helpers ─── */

function statusBadge(status: string) {
  const variant =
    status === 'released'
      ? 'default'
      : status === 'dismissed' || status === 'blocked'
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
  domain: 'hanh_chinh_cong',
  confirmed_procedure_id: '',
  ward_scope: 'Le Chan',
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
  const [procedureQuery, setProcedureQuery] = useState('')
  const [procedureCandidates, setProcedureCandidates] = useState<FormProcedureCandidateV18[]>([])
  const [searchingProcedure, setSearchingProcedure] = useState(false)
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
        domain: editItem.domain,
        confirmed_procedure_id: editItem.confirmed_procedure_id || '',
        ward_scope: editItem.ward_scope || '',
      })
    } else {
      setForm(EMPTY_FAQ)
    }
  }, [editItem, open])

  const update = <K extends keyof FaqCreatePayload>(key: K, value: FaqCreatePayload[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const searchProcedures = async () => {
    setSearchingProcedure(true)
    try {
      const result = await legalImportApi.formProcedureCandidates({
        q: procedureQuery.trim() || undefined,
        domain: form.domain === 'hanh_chinh_cong' ? undefined : form.domain,
        limit: 20,
      })
      setProcedureCandidates(result.items)
    } catch {
      toast.error('Không tải được danh sách thủ tục trong phạm vi hiện hành.')
    } finally {
      setSearchingProcedure(false)
    }
  }

  const handleSave = async () => {
    if (!form.question.trim() || form.question.length < 5) {
      toast.error('Câu hỏi phải có ít nhất 5 ký tự.')
      return
    }
    if (!form.answer.trim() || form.answer.length < 10) {
      toast.error('Câu trả lời phải có ít nhất 10 ký tự.')
      return
    }
    if (!form.confirmed_procedure_id) {
      toast.error('Hãy tìm và xác nhận đúng thủ tục trước khi lưu FAQ.')
      return
    }
    setSaving(true)
    try {
      if (isEdit && editItem) {
        const updatePayload: FaqUpdatePayload = { ...form }
        await faqAdminApi.update(editItem.id, updatePayload)
        toast.success('Đã tạo revision mới; nội dung công khai chưa thay đổi.')
      } else {
        await faqAdminApi.create(form)
        toast.success('Đã tạo FAQ chờ xác nhận; nội dung chưa công khai.')
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
            {isEdit ? 'Mỗi thay đổi tạo một revision mới và không sửa bản đang công khai.' : 'FAQ mới phải được xác nhận, kiểm tra release và phát hành riêng.'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 py-2">
          {/* Row: Domain + Ward */}
          <div className="grid gap-4 sm:grid-cols-2">
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
              <Label htmlFor="faq-ward">Phường / xã</Label>
              <Input id="faq-ward" value={form.ward_scope || ''} onChange={(e) => update('ward_scope', e.target.value)} placeholder="VD: Le Chan" />
            </div>
          </div>

          <div className="space-y-3 rounded-md border bg-muted/30 p-3">
            <div>
              <Label htmlFor="faq-procedure-search">Thủ tục được xác nhận</Label>
              <p className="text-xs text-muted-foreground">Tìm theo tên hoặc mã; biểu mẫu sẽ tự lấy từ release Feature 017 của thủ tục này.</p>
            </div>
            <div className="flex gap-2">
              <Input id="faq-procedure-search" value={procedureQuery} onChange={event => setProcedureQuery(event.target.value)} placeholder="Tên hoặc mã thủ tục" />
              <Button type="button" variant="outline" onClick={() => void searchProcedures()} disabled={searchingProcedure}>Tìm thủ tục</Button>
            </div>
            <select aria-label="Kết quả thủ tục" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={form.confirmed_procedure_id} onChange={event => update('confirmed_procedure_id', event.target.value)}>
              <option value="">Chọn đúng thủ tục</option>
              {editItem?.confirmed_procedure_id && !procedureCandidates.some(item => item.procedure_id === editItem.confirmed_procedure_id) && <option value={editItem.confirmed_procedure_id}>Thủ tục hiện tại · {editItem.confirmed_procedure_id}</option>}
              {procedureCandidates.map(item => <option key={item.procedure_id} value={item.procedure_id}>{item.name} · {item.procedure_code} · {domainLabel(item.domain)}</option>)}
            </select>
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
            {form.requires_forms && (
              <div className="text-right">
                <Link href="/legal-import?tab=forms" target="_blank" className="text-xs text-blue-600 hover:underline inline-flex items-center gap-1">
                  Thủ tục thiếu biểu mẫu? Thêm biểu mẫu mới <FileText className="h-3 w-3" />
                </Link>
              </div>
            )}
          </div>

          {form.requires_forms && <p className="rounded-md bg-muted p-3 text-sm">Không nhập mã biểu mẫu. Khi phát hành, hệ thống lấy đúng biểu mẫu đã phát hành, còn hiệu lực và hợp lệ checksum từ thủ tục đã xác nhận.</p>}

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
  const [releaseSelection, setReleaseSelection] = useState<string[]>([])
  const [release, setRelease] = useState<FaqRelease | null>(null)

  const [suggestions, setSuggestions] = useState<Array<{ id: string; question: string; domain: string; suggested_answer: string }>>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  const loadSuggestions = useCallback(async () => {
    setSuggestionsLoading(true)
    try {
      const response = await apiClient.get<{ items: Array<{ id: string; question: string; domain: string; suggested_answer: string }> }>('/faq/suggestions')
      setSuggestions(response.data.items || [])
    } catch {
      setSuggestions([])
    } finally {
      setSuggestionsLoading(false)
    }
  }, [])

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
    if (role === 'admin') {
      void loadFaqs()
      void loadSuggestions()
    }
  }, [loadFaqs, loadSuggestions, role])

  const confirmRevision = async (item: FaqItem) => {
    try {
      await faqAdminApi.confirm(item.revision_id)
      toast.success('Đã xác nhận nội dung và thủ tục. FAQ vẫn chưa công khai cho đến khi phát hành.')
      void loadFaqs()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể xác nhận revision FAQ.'))
    }
  }

  const buildRelease = async () => {
    if (!releaseSelection.length) return
    try {
      setRelease(await faqAdminApi.previewRelease(releaseSelection))
      toast.success('Đã tạo bản phát hành thử. FAQ công khai chưa thay đổi.')
      void loadFaqs()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể tạo bản phát hành thử.'))
    }
  }

  const validateRelease = async () => {
    if (!release) return
    try {
      const next = await faqAdminApi.validateRelease(release.id)
      setRelease(next)
      if (next.status === 'validated') toast.success('Release Gate đã đạt; vẫn cần thao tác Phát hành riêng.')
      else toast.error(`Release Gate chưa đạt: ${(next.manifest.gate_report?.errors || []).join(', ')}`)
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể kiểm tra bản phát hành FAQ.'))
    }
  }

  const activateRelease = async () => {
    if (!release || release.status !== 'validated') return
    if (!window.confirm('Phát hành FAQ đã xác nhận cho người dân? Biểu mẫu sẽ lấy từ release Feature 017 đang được khóa.')) return
    try {
      await faqAdminApi.activateRelease(release.id)
      toast.success('Đã phát hành FAQ và biểu mẫu dẫn xuất từ release Feature 017.')
      setRelease(null); setReleaseSelection([]); void loadFaqs()
    } catch (error) {
      toast.error(apiErrorDetail(error, 'Không thể phát hành; dữ liệu công khai không bị thay đổi.'))
    }
  }

  // Stats
  const stats = useMemo(() => {
    const released = faqs.filter((f) => f.public_state === 'released').length
    const confirmed = faqs.filter((f) => f.public_state === 'confirmed').length
    const pending = faqs.filter((f) => ['pending', 'needs_review'].includes(f.public_state)).length
    return { released, confirmed, pending }
  }, [faqs])

  if (role !== 'admin') {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
          <main className="mx-auto max-w-xl">
            <Card>
              <CardContent className="flex items-center gap-3 p-6 text-destructive">
                <X className="h-5 w-5" />
                Chỉ Admin có quyền quản lý Câu hỏi thường gặp.
              </CardContent>
            </Card>
          </main>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <main className="mx-auto w-full max-w-7xl space-y-6">
          {/* Header */}
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-3 text-xl md:text-2xl font-bold tracking-tight">
                <MessageCircleQuestion className="h-7 w-7 text-primary" />
                Quản lý Câu hỏi thường gặp
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Tạo revision, xác nhận thủ tục, chạy Release Gate rồi phát hành. Chỉ bản đã phát hành mới hiển thị cho người dân.
              </p>
            </div>
            <div className="flex gap-2 self-start sm:self-auto">
              <Button variant="outline" asChild>
                <Link href="/procedures"><FileText className="mr-2 h-4 w-4" />Xem trang công khai</Link>
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
              <p className="text-xs text-muted-foreground">Đã phát hành</p>
              <p className="text-2xl font-semibold">{stats.released}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="rounded-md bg-amber-500/10 p-2 text-amber-600">
              <Edit3 className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Đã xác nhận · chờ phát hành</p>
              <p className="text-2xl font-semibold">{stats.confirmed}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="rounded-md bg-red-500/10 p-2 text-red-600">
              <X className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Chờ xác nhận</p>
              <p className="text-2xl font-semibold">{stats.pending}</p>
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
              <SelectItem value="released">Đã phát hành</SelectItem>
              <SelectItem value="confirmed">Đã xác nhận · chưa công khai</SelectItem>
              <SelectItem value="pending">Chờ xác nhận</SelectItem>
              <SelectItem value="blocked">Đang bị chặn</SelectItem>
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
        </CardContent>
      </Card>

      <Card className="border-primary/30">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Phát hành FAQ đã xác nhận</CardTitle>
          <CardDescription>Ba bước riêng: tạo bản thử → kiểm tra FAQ và form release → phát hành. Xác nhận không tự công khai.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm">Đã chọn {releaseSelection.length} revision ở trạng thái “Đã xác nhận”.</p>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void buildRelease()} disabled={!releaseSelection.length}>Tạo bản phát hành thử</Button>
            {release && <Button variant="outline" onClick={() => void validateRelease()} disabled={release.status !== 'candidate'}>Chạy Release Gate</Button>}
            {release && <Button onClick={() => void activateRelease()} disabled={release.status !== 'validated'}>Phát hành cho người dân</Button>}
          </div>
          {release && <div className="rounded-md bg-muted p-3 text-sm"><p>Bản {release.version} · {release.status} · form release {release.form_release_id}</p>{release.manifest.gate_report && <p>{release.manifest.gate_report.passed ? 'Đã đạt toàn bộ cổng kiểm tra.' : `Chưa đạt: ${release.manifest.gate_report.errors.join(', ')}`}</p>}</div>}
        </CardContent>
      </Card>

      {/* Suggestions Card */}
      {suggestionsLoading && (
        <Card className="border-amber-200 bg-amber-50/40">
          <CardContent className="py-5 text-sm text-amber-900">
            Đang tổng hợp gợi ý FAQ từ các câu hỏi đã được phép sử dụng.
          </CardContent>
        </Card>
      )}
      {suggestions.length > 0 && (
        <Card className="border-amber-200 bg-amber-50/40">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2 text-amber-900">
              <Sparkles className="h-4 w-4 text-amber-600" />
              Gợi ý FAQ tự động từ Chat Logs công dân ({suggestions.length})
            </CardTitle>
            <CardDescription className="text-amber-800/80">
              Hệ thống tự động trích xuất các thắc mắc phổ biến từ phiên chat hỗ trợ trực tuyến và tra cứu công dân.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {suggestions.map((sug) => (
              <div key={sug.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-200 bg-background p-3 text-xs">
                <div className="space-y-0.5">
                  <span className="font-medium text-foreground">{sug.question}</span>
                  <div className="flex gap-2 text-muted-foreground">
                    <Badge variant="outline" className="text-[10px]">{domainLabel(sug.domain)}</Badge>
                    <span>ID: {sug.id}</span>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1 text-xs text-amber-900 hover:bg-amber-100"
                  onClick={() => {
                    setEditItem(null)
                    setFormOpen(true)
                  }}
                >
                  <Plus className="h-3 w-3" />
                  Thêm vào kho FAQ
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* FAQ List */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Danh sách câu hỏi ({faqs.length})</CardTitle>
          <CardDescription>Chọn thủ tục trước, xác nhận revision sau, rồi đưa revision đã xác nhận vào một bản phát hành có kiểm tra.</CardDescription>
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
                      {statusBadge(faq.public_state)}
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
                      {faq.forms.length > 0 && <span>{faq.forms.length} biểu mẫu từ Feature 017</span>}
                      <span>Thủ tục: {faq.confirmed_procedure_id}</span>
                      <span>Revision {faq.revision_number}</span>
                      <span>Cập nhật: {new Date(faq.updated_at).toLocaleDateString('vi-VN')}</span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex shrink-0 flex-col gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    {['pending', 'needs_review'].includes(faq.public_state) && (
                      <Button size="sm" variant="outline" className="text-green-600 hover:bg-green-50 hover:text-green-700" onClick={() => void confirmRevision(faq)}>
                        <Check className="mr-1 h-3 w-3" />Xác nhận nội dung và thủ tục
                      </Button>
                    )}
                    {faq.public_state === 'confirmed' && <label className="flex items-center gap-2 rounded-md border px-2 py-1 text-xs"><Checkbox checked={releaseSelection.includes(faq.revision_id)} onCheckedChange={checked => setReleaseSelection(current => checked ? [...new Set([...current, faq.revision_id])] : current.filter(id => id !== faq.revision_id))} />Chọn vào bản phát hành</label>}
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
    </main>
      </div>
    </AppShell>
  )
}