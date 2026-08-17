"use client"

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { FileDown, FileText, FolderOpen, HelpCircle, Search } from 'lucide-react'
import { toast } from 'sonner'

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
  ward_scope?: string
  review_status: string
}

interface FaqListResponse {
  total: number
  items: FaqItem[]
}

const DOMAINS: Record<string, string> = {
  ho_tich_chung_thuc: 'Hộ tịch - chứng thực',
  dat_dai_xay_dung: 'Đất đai - xây dựng',
  an_sinh_y_te_giao_duc: 'An sinh - y tế - giáo dục',
  cu_tru_an_ninh: 'Cư trú - an ninh',
  khieu_nai_to_cao_xu_phat: 'Khiếu nại - tố cáo - xử phạt',
  hanh_chinh_cong: 'Hành chính công',
  trat_tu_do_thi: 'Trật tự đô thị',
}

function getToken(): string {
  try {
    return String(JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.token || '')
  } catch {
    return ''
  }
}

export default function ProceduresPage() {
  const searchParams = useSearchParams()
  const [faqs, setFaqs] = useState<FaqItem[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState(searchParams.get('domain') || 'all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setLoading(true)
        const response = await apiClient.get<FaqListResponse>('/faq?limit=100')
        if (!cancelled) {
          const items = response.data.items || []
          setFaqs(items)
          setSelectedId(items[0]?.id || null)
        }
      } catch {
        if (!cancelled) toast.error('Không tải được danh mục thủ tục.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [])

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('vi-VN')
    return faqs.filter((faq) => {
      const byDomain = domain === 'all' || faq.domain === domain
      const content = [faq.question, faq.answer, ...(faq.steps || [])].join(' ').toLocaleLowerCase('vi-VN')
      return byDomain && (!normalized || content.includes(normalized))
    })
  }, [domain, faqs, query])

  useEffect(() => {
    if (!filtered.some((faq) => faq.id === selectedId)) setSelectedId(filtered[0]?.id || null)
  }, [filtered, selectedId])

  const selected = filtered.find((faq) => faq.id === selectedId) || null
  const grouped = useMemo(() => Object.keys(DOMAINS).map((key) => [key, filtered.filter((faq) => faq.domain === key)] as const).filter(([, items]) => items.length > 0), [filtered])

  const downloadForm = async (form: OfficialForm) => {
    if (form.official_level !== 'official' || form.review_status !== 'approved' || !form.download_url) {
      toast.error('Biểu mẫu chưa có file chính thức hợp lệ.')
      return
    }
    try {
      setDownloadingId(form.id)
      const response = await fetch(form.download_url, { headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {} })
      if (!response.ok) throw new Error('Biểu mẫu chưa có file chính thức hợp lệ.')
      const blobUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = blobUrl
      anchor.download = `${form.name}.${form.file_type || 'file'}`
      anchor.click()
      URL.revokeObjectURL(blobUrl)
      toast.success('Đã tải biểu mẫu chính thức.')
    } catch (caught: unknown) {
      toast.error(caught instanceof Error ? caught.message : 'Không thể tải biểu mẫu.')
    } finally {
      setDownloadingId(null)
    }
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <div className="mx-auto flex max-w-7xl flex-col gap-5">
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="text-xl md:text-2xl font-bold">Thủ tục hành chính</h1>
              <p className="mt-1 text-sm text-muted-foreground">Chọn lĩnh vực và thủ tục để xem hướng dẫn thực hiện và tải biểu mẫu tại Lê Chân, Hải Phòng.</p>
            </div>
          </header>

          <div className="relative max-w-xl">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder="Tìm câu hỏi, thủ tục hoặc từ khóa..." />
          </div>

          <div className="grid min-h-[580px] grid-cols-1 overflow-hidden rounded-xl border bg-card lg:grid-cols-[minmax(290px,0.9fr)_minmax(0,2fr)]">
            <aside className="border-b bg-muted/20 lg:border-b-0 lg:border-r">
              <div className="flex flex-wrap gap-2 border-b p-3">
                <Button size="sm" variant={domain === 'all' ? 'default' : 'outline'} onClick={() => setDomain('all')}>Tất cả</Button>
                {Object.entries(DOMAINS).map(([key, label]) => <Button key={key} size="sm" variant={domain === key ? 'default' : 'outline'} onClick={() => setDomain(key)}>{label}</Button>)}
              </div>
              <div className="max-h-[45vh] space-y-4 overflow-y-auto p-3 lg:max-h-[calc(100vh-20rem)]">
                {loading && <p className="p-3 text-sm text-muted-foreground">Đang tải danh mục thủ tục...</p>}
                {!loading && grouped.length === 0 && <p className="p-3 text-sm text-muted-foreground">Không tìm thấy thủ tục phù hợp.</p>}
                {grouped.map(([groupDomain, items]) => <section key={groupDomain}>
                  <div className="mb-1 flex items-center gap-2 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground"><FolderOpen className="h-3.5 w-3.5" />{DOMAINS[groupDomain]} <Badge variant="secondary">{items.length}</Badge></div>
                  <div className="space-y-1">{items.map((faq) => <button key={faq.id} type="button" onClick={() => setSelectedId(faq.id)} className={`w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${faq.id === selectedId ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}><span className="line-clamp-2">{faq.question}</span></button>)}</div>
                </section>)}
              </div>
            </aside>

            <main className="min-w-0 p-4 md:p-7">
              {!selected && !loading && <div className="flex h-full min-h-64 items-center justify-center text-center text-muted-foreground"><HelpCircle className="mr-2 h-5 w-5" />Chọn một thủ tục ở danh sách bên trái.</div>}
              {selected && <article className="space-y-6">
                <div className="space-y-2"><Badge variant="secondary">{DOMAINS[selected.domain] || 'Lĩnh vực khác'}</Badge><h2 className="text-xl font-semibold leading-snug md:text-2xl">{selected.question}</h2></div>
                <Card><CardHeader className="pb-2"><CardTitle className="text-base">Hướng dẫn giải quyết</CardTitle></CardHeader><CardContent className="whitespace-pre-line text-sm leading-7 text-foreground/90">{selected.answer}</CardContent></Card>
                <div className="grid gap-4 md:grid-cols-2">
                  <Card><CardHeader className="pb-2"><CardTitle className="text-sm">Nơi tiếp nhận và trả kết quả</CardTitle></CardHeader><CardContent className="text-sm leading-6">{selected.submission_place || 'Bộ phận tiếp nhận và trả kết quả cấp phường/xã.'}</CardContent></Card>
                  <Card><CardHeader className="pb-2"><CardTitle className="text-sm">Căn cứ pháp lý & Lưu ý</CardTitle></CardHeader><CardContent className="space-y-2 text-sm leading-6">{selected.legal_basis && selected.legal_basis.length > 0 ? <ul className="list-disc space-y-1 pl-4">{selected.legal_basis.map((basis) => <li key={basis}>{basis}</li>)}</ul> : <p>{selected.guidance_label || 'Thực hiện theo quy định pháp luật hiện hành.'}</p>}</CardContent></Card>
                </div>
                {selected.steps && selected.steps.length > 0 && <Card><CardHeader className="pb-2"><CardTitle className="text-base">Trình tự thực hiện</CardTitle></CardHeader><CardContent><ol className="space-y-3">{selected.steps.map((step, index) => <li key={`${index}-${step}`} className="flex gap-3 text-sm leading-6"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">{index + 1}</span><span>{step}</span></li>)}</ol></CardContent></Card>}
                {selected.requires_forms && <Card className="border-primary/20"><CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><FileText className="h-4 w-4" />Biểu mẫu chính thức liên quan</CardTitle></CardHeader><CardContent className="space-y-3">{(selected.forms || []).slice(0, 3).length > 0 ? (selected.forms || []).slice(0, 3).map((form) => <div key={form.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3"><div><p className="text-sm font-medium">{form.name}</p><p className="text-xs text-muted-foreground">Biểu mẫu chính thức đã duyệt · {form.file_type.toUpperCase()}</p></div><Button size="sm" disabled={downloadingId === form.id} onClick={() => void downloadForm(form)}><FileDown className="mr-2 h-4 w-4" />{downloadingId === form.id ? 'Đang tải...' : 'Tải biểu mẫu'}</Button></div>) : <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">Chưa có biểu mẫu chính thức được duyệt.</p>}</CardContent></Card>}
              </article>}
            </main>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
