"use client"

import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, ChevronLeft, ChevronRight, FileDown, FileText, RefreshCw, Search } from 'lucide-react'
import { toast } from 'sonner'
import { formatApiError } from '@/lib/utils/error-handler'
import { getApiUrl } from '@/lib/config'
import { useAuthStore } from '@/lib/stores/auth-store'
import { officialDownloadTarget } from '@/lib/utils/official-download'

import { apiClient } from '@/lib/api/client'
import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

interface OfficialForm {
  id: string
  name: string
  file_type: string
  download_url: string
  official_level: 'official'
  review_status: 'approved'
}

interface ProcedureForm {
  name: string
  file_type: string
  download_url: string
  official_level?: string
  review_status?: string
}

interface ProcedureRecord {
  id: string
  name: string
  department: string
  domain_slug: string
  primary_organization_unit_id?: string
  supporting_organization_unit_ids?: string[]
  steps?: string[]
  documents_required?: string[]
  guidance?: string
  submission_place?: string
  legal_basis?: string[]
  duration?: string
  fee?: string
  forms?: ProcedureForm[]
  source_url?: string | null
  catalog_status?: 'approved' | 'candidate_pending_review' | string
}

interface FaqItem {
  id: string
  question: string
  answer: string
  submission_place?: string
  legal_basis?: string[]
  guidance_label?: string
  requires_forms?: boolean
  steps?: string[]
  forms?: OfficialForm[]
  forms_unavailable?: boolean
  domain: string
  department?: string
  primary_organization_unit_id?: string
  ward_scope?: string
  review_status: string
  confirmed_procedure_id?: string
  documents_required?: string[]
  duration?: string
  fee?: string
  source_url?: string | null
  catalog_status?: 'approved' | 'candidate_pending_review' | string
}

interface FaqListResponse {
  total: number
  items: FaqItem[]
}

interface ProcedureDepartment {
  id: string
  name: string
  code: string
  fields: Array<{ code: string; name: string; domains?: string[] }>
}

interface ReleaseCatalogItem {
  procedure_id: string
  procedure_code?: string
  name: string
  domain: string
  authority?: string | null
  official_source_url?: string | null
  coverage_status?: string
  primary_organization_unit_id?: string | null
  primary_organization_unit_name?: string | null
  supporting_organization_unit_ids?: string[]
  form_status: 'resolved' | 'source_gap' | 'not_requested' | string
  forms_unavailable: boolean
  reason?: string | null
  department?: string | null
  steps?: string[]
  documents_required?: string[]
  guidance?: string
  submission_place?: string
  legal_basis?: string[]
  duration?: string
  fee?: string
  catalog_status?: 'approved' | string
  publication_source?: 'managed_runtime' | string
  forms: Array<{
    form_id: string
    name?: string
    display_name?: string
    file_type?: string
    download_url: string
    official_level: 'official'
    review_status: 'approved'
  }>
}

interface ReleaseCatalogResponse {
  release_id?: string | null
  version?: number | null
  legal_as_of: string
  items: ReleaseCatalogItem[]
  total: number
}

const LEGACY_DOMAIN_LABELS: Record<string, string> = {
  ho_tich_chung_thuc: 'Tư pháp - Hộ tịch & Chứng thực',
  dat_dai_xay_dung: 'Đất đai - Xây dựng - Môi trường',
  an_sinh_y_te_giao_duc: 'Lao động - An sinh xã hội',
  van_hoa_giao_duc_y_te: 'Văn hóa - Giáo dục - Y tế',
  cu_tru_an_ninh: 'Cư trú - Căn cước - ANTT',
  trat_tu_do_thi: 'Trật tự đô thị - Hè phố',
  quoc_phong_quan_su: 'Quốc phòng - Nghĩa vụ quân sự',
  khieu_nai_to_cao_xu_phat: 'Tiếp dân - Khiếu nại - Tố cáo',
  hanh_chinh_cong: 'Dịch vụ công Một cửa - Văn thư',
  y_te_co_so: 'Y tế cơ sở',
}

function hasReadableDomainName(code: string, name: string): boolean {
  const normalize = (value: string) => value.trim().toLocaleLowerCase('vi-VN').replace(/[\s_-]+/g, '')
  return Boolean(name.trim()) && normalize(name) !== normalize(code)
}

const PAGE_SIZE = 10

function buildSearchBackHref(params: { get(name: string): string | null }): string {
  const query = new URLSearchParams()
  const value = params.get('q')
  const selectedDomain = params.get('domain')
  if (value) query.set('q', value)
  if (selectedDomain) query.set('domain', selectedDomain)
  const suffix = query.toString()
  return suffix ? `/search?${suffix}` : '/search'
}

function getToken(): string {
  return useAuthStore.getState().token || ''
}

export function ProcedureCatalogPage() {
  const searchParams = useSearchParams()
  const backHref = buildSearchBackHref(searchParams)
  const [departments, setDepartments] = useState<ProcedureDepartment[]>([])
  const [faqs, setFaqs] = useState<FaqItem[]>([])
  const [loading, setLoading] = useState(true)
  const [release, setRelease] = useState<{ id: string; version?: number | null } | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [query, setQuery] = useState(searchParams.get('q') || '')
  const [domain, setDomain] = useState(searchParams.get('domain') || 'all')
  const [page, setPage] = useState(1)
  const [department, setDepartment] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const downloadInFlight = useRef(false)

  const domainLabels = useMemo(() => {
    const labels: Record<string, string> = { ...LEGACY_DOMAIN_LABELS }
    for (const unit of departments) {
      for (const field of unit.fields) {
        const label = hasReadableDomainName(field.code, field.name)
          ? field.name
          : labels[field.code] || field.name
        labels[field.code] = label
        for (const code of field.domains || []) labels[code] = label
      }
    }
    return labels
  }, [departments])

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setLoading(true)
        const [releaseResponse, proceduresResponse, faqResponse, departments] = await Promise.all([
          apiClient.get<ReleaseCatalogResponse>('/procedures/forms-catalog/public-catalog?audience=citizen'),
          apiClient.get<ProcedureRecord[]>('/procedures'),
          // The public catalog is release-backed: even an admin viewing this
          // route must see only FAQs that have been approved/published.
          apiClient.get<FaqListResponse>('/faq?review_status=approved&limit=500').catch(() => ({ data: { total: 0, items: [] } })),
          apiClient.get<{ departments: ProcedureDepartment[]; total_procedures: number }>('/procedures/directory'),
        ])
        if (!cancelled) {
          const publishedFaqs = (faqResponse.data.items || []).filter(item => Boolean(item.confirmed_procedure_id))
          const proceduresById = new Map(
            (proceduresResponse.data || []).map(item => [String(item.id), item]),
          )
          const guidanceByProcedureId = new Map(
            publishedFaqs.map(item => [String(item.confirmed_procedure_id), item]),
          )
          const buildItem = (
            published: ReleaseCatalogItem,
            procedure: ProcedureRecord | undefined,
            guidance: FaqItem | undefined,
          ): FaqItem => {
            const procedureId = String(published.procedure_id)
            const forms: OfficialForm[] = (published.forms || []).map(form => ({
              id: form.form_id,
              name: form.display_name || form.name || form.form_id,
              file_type: form.file_type || 'file',
              download_url: form.download_url,
              official_level: 'official',
              review_status: 'approved',
            }))
            return {
              id: guidance?.id || procedureId,
              question: guidance?.question || published.name || procedure?.name || '',
              answer: guidance?.answer || published.guidance || procedure?.guidance || 'Thông tin thủ tục được công bố theo hồ sơ và trình tự bên dưới.',
              submission_place: guidance?.submission_place || published.submission_place || procedure?.submission_place || published.authority || procedure?.department || '',
              legal_basis: guidance?.legal_basis?.length ? guidance.legal_basis : (published.legal_basis?.length ? published.legal_basis : (procedure?.legal_basis || [])),
              guidance_label: guidance?.guidance_label,
              requires_forms: Boolean(forms.length || published.forms_unavailable || published.form_status === 'source_gap'),
              steps: guidance?.steps?.length ? guidance.steps : (published.steps?.length ? published.steps : (procedure?.steps || [])),
              forms,
              forms_unavailable: published.forms_unavailable || !forms.length,
              domain: published.domain || guidance?.domain || procedure?.domain_slug || 'hanh_chinh_cong',
              department: published.primary_organization_unit_name || published.department || procedure?.department,
              primary_organization_unit_id: published.primary_organization_unit_id || guidance?.primary_organization_unit_id || procedure?.primary_organization_unit_id,
              ward_scope: guidance?.ward_scope,
              review_status: 'approved',
              confirmed_procedure_id: procedureId,
              documents_required: guidance?.documents_required?.length ? guidance.documents_required : (published.documents_required?.length ? published.documents_required : (procedure?.documents_required || [])),
              duration: guidance?.duration || published.duration || procedure?.duration,
              fee: guidance?.fee || published.fee || procedure?.fee,
              source_url: published.official_source_url || guidance?.source_url || procedure?.source_url,
              catalog_status: published.catalog_status || procedure?.catalog_status || 'approved',
            }
          }
          // The active form release is the publication boundary. Procedure
          // records and FAQs enrich its text only; they can never re-add a
          // withdrawn/replaced form or an unpublished procedure.
          const items: FaqItem[] = (releaseResponse.data.items || []).map(published =>
            buildItem(
              published,
              proceduresById.get(String(published.procedure_id)),
              guidanceByProcedureId.get(String(published.procedure_id)),
            ),
          )
          setFaqs(items)
          setRelease({
            id: String(releaseResponse.data.release_id || ''),
            version: releaseResponse.data.version,
          })
          setDepartments((departments.data.departments || []).map(unit => ({
            ...unit,
            fields: (unit.fields || []).map(field => ({
              ...field,
              name: field.name,
              domains: field.domains || [field.code],
            })),
          })))
          setSelectedId(null)
        }
      } catch {
        if (!cancelled) toast.error('Không tải được danh mục thủ tục.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [refreshKey])

  useEffect(() => {
    const refreshOnFocus = () => setRefreshKey(current => current + 1)
    window.addEventListener('focus', refreshOnFocus)
    return () => window.removeEventListener('focus', refreshOnFocus)
  }, [])

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('vi-VN')
    return faqs.filter((faq) => {
      const unit = departments.find(item => item.id === department)
      const field = unit?.fields.find(item => item.code === domain)
      const byDomain = domain === 'all' || faq.domain === domain || !!field?.domains?.includes(faq.domain)
      const content = [faq.question, faq.answer, ...(faq.steps || [])].join(' ').toLocaleLowerCase('vi-VN')
      // Legacy combined domains remain shared until an explicit assignment exists.
      // Never infer the receiving department from words in a citizen's question.
      const byDepartment = !unit || (faq.primary_organization_unit_id
        ? unit.id === faq.primary_organization_unit_id
        : unit.fields.some(item => item.code === faq.domain || item.domains?.includes(faq.domain)))
      return byDomain && byDepartment && (!normalized || content.includes(normalized))
    })
  }, [domain, faqs, query, department, departments])

  useEffect(() => {
    setPage(1)
  }, [domain, query])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  const paged = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, page])

  useEffect(() => {
    if (!paged.some((faq) => faq.id === selectedId)) setSelectedId(null)
  }, [paged, selectedId])

  const selected = filtered.find((faq) => faq.id === selectedId) || null
  const downloadForm = async (form: OfficialForm) => {
    if (downloadInFlight.current) return
    if (form.official_level !== 'official' || form.review_status !== 'approved' || !form.download_url) {
      toast.error('Biểu mẫu chưa có file chính thức hợp lệ.')
      return
    }
    downloadInFlight.current = true
    try {
      setDownloadingId(form.id)
      const target = officialDownloadTarget(form.download_url, window.location.origin, await getApiUrl())
      if (!target.authenticated) {
        // Public official sources need no application token. A normal link
        // also works when the source does not allow cross-origin fetches.
        const external = document.createElement('a')
        external.href = target.href
        external.target = '_blank'
        external.rel = 'noopener noreferrer'
        external.click()
        toast.success('Đã mở nguồn chính thức để tải biểu mẫu.')
        return
      }
      const token = getToken()
      const response = await fetch(target.href, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        redirect: 'error',
      })
      if (!response.ok) throw new Error('Biểu mẫu chưa có file chính thức hợp lệ.')
      const blobUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = blobUrl
      anchor.download = `${form.name}.${form.file_type || 'file'}`
      anchor.click()
      URL.revokeObjectURL(blobUrl)
      toast.success('Đã tải biểu mẫu chính thức.')
    } catch (caught: unknown) {
      toast.error(formatApiError(caught, 'Không thể tải biểu mẫu.'))
    } finally {
      downloadInFlight.current = false
      setDownloadingId(null)
    }
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 lg:overflow-hidden lg:p-6">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4 lg:h-full lg:min-h-0">
          <header className="flex shrink-0 flex-wrap items-start justify-between gap-3">
            <div>
              <Link href={backHref} className="mb-2 inline-flex min-h-9 items-center gap-1 text-sm font-medium text-primary transition-colors hover:underline">
                <ArrowLeft className="h-4 w-4" /> Quay lại hỏi đáp
              </Link>
              <h1 className="font-display text-2xl font-bold text-foreground">Thủ tục hành chính</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Hiển thị thủ tục và biểu mẫu từ bản phát hành đang hoạt động; thay đổi đã phát hành được dùng đồng thời tại đây và trong chatbot.</p>
              <p role="status" aria-live="polite" className="mt-1 text-xs text-muted-foreground">
                {loading ? 'Đang đồng bộ danh mục công khai…' : release?.id ? `Danh mục công khai đã đồng bộ · Phiên bản ${release.version ?? release.id}` : 'Chưa có danh mục công khai.'}
              </p>
            </div>
            <Button type="button" variant="outline" className="min-h-11" disabled={loading} onClick={() => setRefreshKey(current => current + 1)}>
              <RefreshCw aria-hidden="true" className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Làm mới danh mục
            </Button>
          </header>

          <div className="relative w-full max-w-2xl shrink-0">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input value={query} onChange={(event) => { setQuery(event.target.value); setDomain('all'); setDepartment(null); setPage(1) }} className="h-12 pl-10 shadow-sm" placeholder="Tìm câu hỏi, thủ tục hoặc từ khóa..." aria-label="Tìm thủ tục hành chính" />
          </div>

          <div className="grid min-h-0 grid-cols-1 overflow-hidden rounded-xl border border-border/80 bg-card lg:flex-1 lg:grid-cols-[240px_minmax(0,1fr)]">
            <aside aria-label="Danh mục phòng ban" className="max-h-[35dvh] overflow-y-auto border-b bg-primary/[0.025] lg:max-h-none lg:border-b-0 lg:border-r">
              <div className="border-b border-border/70 p-4">
                <p className="mb-3 text-sm font-semibold">Phòng ban → Lĩnh vực → Thủ tục</p>
                <div className="space-y-2">
                  {departments.map(unit => <details key={unit.id} className="rounded-lg border bg-background" open={department === unit.id}>
                    <summary className="cursor-pointer p-3 text-sm font-medium" onClick={event => { event.preventDefault(); setDepartment(department === unit.id ? null : unit.id); setDomain('all'); setPage(1); setSelectedId(null) }}>{unit.name}</summary>
                    {unit.fields.map(field => <button key={field.code} type="button" aria-pressed={domain === field.code && department === unit.id} className="w-full border-t p-3 text-left text-sm hover:bg-muted aria-pressed:bg-primary/10" onClick={() => { setDomain(field.code); setPage(1); setSelectedId(null) }}>{domainLabels[field.code] || field.name}</button>)}
                    {!unit.fields.length && <p className="border-t p-3 text-sm text-muted-foreground">Chưa có lĩnh vực được phân công.</p>}
                  </details>)}
                </div>
              </div>
            </aside>

            <main aria-busy={loading} className="min-h-0 min-w-0 bg-card p-4 lg:overflow-y-auto lg:p-6">
              {!selected && <section aria-label="Danh sách thủ tục" className="space-y-4">
                <h2 className="text-xl font-semibold">{departments.find(unit => unit.id === department)?.fields.find(field => field.code === domain)?.name || 'Danh sách thủ tục'}</h2>
                {loading ? <p role="status">Đang tải danh mục thủ tục...</p> : !(department || query) ? <p className="text-muted-foreground">Mở phòng ban và chọn lĩnh vực để xem các thủ tục, câu hỏi và biểu mẫu.</p> : <>
                  <div className="overflow-x-auto rounded-lg border">
                    <table className="w-full min-w-[760px] text-left text-sm">
                      <thead className="border-b bg-muted/50"><tr><th className="p-3 font-medium">Thủ tục / Câu hỏi</th><th className="p-3 font-medium">Lĩnh vực</th><th className="p-3 font-medium">Biểu mẫu</th></tr></thead>
                      <tbody>{paged.map(faq => <tr key={faq.id} className="border-b last:border-0 hover:bg-muted/30">
                        <td className="p-3"><button type="button" className="text-left font-medium text-primary hover:underline underline-offset-4" onClick={() => setSelectedId(faq.id)}>{faq.question}</button></td>
                        <td className="p-3 text-muted-foreground">{domainLabels[faq.domain] || faq.domain}</td>
                        <td className="p-3">{faq.forms?.length ? `${faq.forms.length} biểu mẫu` : '—'}</td>
                      </tr>)}</tbody>
                    </table>
                    {paged.length === 0 && <p className="p-4 text-muted-foreground">Không tìm thấy thủ tục phù hợp.</p>}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
                    <span>Trang {page}/{totalPages} · {filtered.length} thủ tục</span>
                    <div className="flex gap-2"><Button size="sm" variant="outline" aria-label="Trang trước" disabled={page === 1} onClick={() => setPage(current => Math.max(1, current - 1))}><ChevronLeft className="h-4 w-4" />Trước</Button><Button size="sm" variant="outline" aria-label="Trang sau" disabled={page === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Sau<ChevronRight className="h-4 w-4" /></Button></div>
                  </div>
                </>}
              </section>}
              {selected && <article className="space-y-6">
                <Button variant="ghost" size="sm" onClick={() => setSelectedId(null)}><ArrowLeft className="mr-2 h-4 w-4" />Danh sách thủ tục</Button>
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">{domainLabels[selected.domain] || 'Lĩnh vực khác'}</Badge>
                    {selected.confirmed_procedure_id && <Badge variant="outline">Mã thủ tục: {selected.confirmed_procedure_id}</Badge>}
                  </div>
                  <h2 className="font-display text-2xl font-semibold leading-snug text-foreground md:text-3xl">{selected.question}</h2>
                </div>
                <Card><CardHeader className="pb-2"><CardTitle className="text-base">Hướng dẫn giải quyết</CardTitle></CardHeader><CardContent className="whitespace-pre-line text-sm leading-7 text-foreground/90">{selected.answer}</CardContent></Card>
                <div className="grid gap-4 md:grid-cols-2">
                  <Card><CardHeader className="pb-2"><CardTitle className="text-sm">Nơi tiếp nhận và trả kết quả</CardTitle></CardHeader><CardContent className="text-sm leading-6">{selected.submission_place || 'Bộ phận tiếp nhận và trả kết quả cấp phường/xã.'}</CardContent></Card>
                  <Card><CardHeader className="pb-2"><CardTitle className="text-sm">Căn cứ pháp lý & Lưu ý</CardTitle></CardHeader><CardContent className="space-y-2 text-sm leading-6">{selected.legal_basis && selected.legal_basis.length > 0 ? <ul className="list-disc space-y-1 pl-4">{selected.legal_basis.map((basis, index) => <li key={`${index}-${basis}`}>{basis}</li>)}</ul> : <p>{selected.guidance_label || 'Chưa có lưu ý nghiệp vụ đã duyệt cho thủ tục này.'}</p>}</CardContent></Card>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <Card><CardHeader className="pb-2"><CardTitle className="text-sm">Thành phần hồ sơ</CardTitle></CardHeader><CardContent className="text-sm leading-6">{selected.documents_required?.length ? <ul className="list-disc space-y-1 pl-4">{selected.documents_required.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p>Chưa cập nhật thành phần hồ sơ.</p>}</CardContent></Card>
                  <Card><CardHeader className="pb-2"><CardTitle className="text-sm">Thời hạn và lệ phí</CardTitle></CardHeader><CardContent className="space-y-2 text-sm leading-6"><p><span className="font-medium">Thời hạn:</span> {selected.duration || 'Chưa cập nhật'}</p><p><span className="font-medium">Lệ phí:</span> {selected.fee || 'Chưa cập nhật'}</p></CardContent></Card>
                </div>
                {selected.steps && selected.steps.length > 0 && <Card><CardHeader className="pb-2"><CardTitle className="text-base">Trình tự thực hiện</CardTitle></CardHeader><CardContent><ol className="space-y-3">{selected.steps.map((step, index) => <li key={`${index}-${step}`} className="flex gap-3 text-sm leading-6"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">{index + 1}</span><span>{step}</span></li>)}</ol></CardContent></Card>}
                {selected.requires_forms && <Card className="border-primary/20"><CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><FileText className="h-4 w-4" />Biểu mẫu chính thức liên quan</CardTitle></CardHeader><CardContent className="space-y-3">{selected.forms_unavailable ? <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">Chưa có biểu mẫu chính thức được phát hành cho thủ tục này.</p> : (selected.forms || []).slice(0, 3).length > 0 ? (selected.forms || []).slice(0, 3).map((form, index) => <div key={`${form.id}-${index}`} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3"><div><p className="text-sm font-medium">{form.name}</p><p className="text-xs text-muted-foreground">Biểu mẫu chính thức đã duyệt · {form.file_type.toUpperCase()}</p></div><Button size="sm" className="min-h-11" disabled={downloadingId === form.id} onClick={() => void downloadForm(form)}><FileDown className="mr-2 h-4 w-4" />{downloadingId === form.id ? 'Đang tải...' : 'Tải biểu mẫu'}</Button></div>) : <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">Chưa có biểu mẫu chính thức được duyệt.</p>}</CardContent></Card>}
              </article>}
            </main>
          </div>
        </div>
      </div>
    </AppShell>
  )
}

export default function ProceduresPage() {
      return <ProcedureCatalogPage />
}
