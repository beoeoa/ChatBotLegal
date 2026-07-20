'use client'

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { AxiosError } from 'axios'
import { ArrowLeft, CheckCircle2, DatabaseZap, FileUp, RefreshCcw, ShieldCheck, TriangleAlert } from 'lucide-react'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import {
  ImportPreview,
  LegalCrawlCandidate,
  LegalCrawlSource,
  LegalCrawlSummary,
  LegalField,
  LegalFormsCatalogStatus,
  LegalFormCandidate,
  LegalImportExtractor,
  LegalImportPayload,
  legalImportApi,
} from '@/lib/api/legal-import'

const initialForm: LegalImportPayload = {
  title: '',
  law_number: '',
  document_type: '',
  issuing_agency: '',
  scope: 'Trung ương - toàn quốc',
  sector: '',
  field_id: 0,
  issued_date: null,
  effective_date: '',
  expired_date: null,
  source_url: '',
  applicability_info: '',
  content: '',
  confirmed_official_source: false,
}

function errorMessage(error: unknown): string {
  const axiosError = error as AxiosError<{
    detail?: string | Array<{ loc?: Array<string | number>; msg?: string; type?: string }> | {
      code?: string
      message?: string
      suggestion?: string
      trace_id?: string
    }
  }>
  const detail = axiosError.response?.data?.detail
  if (!axiosError.response) {
    if (axiosError.code === 'ECONNABORTED') {
      return 'Yêu cầu quá thời gian chờ. Backend hoặc dịch vụ AI/crawler đang xử lý quá lâu, hãy thử lại hoặc kiểm tra log backend.'
    }
    return 'Không kết nối được backend. Hãy kiểm tra backend local đang chạy, API URL đúng và mạng nội bộ không bị chặn.'
  }
  if (Array.isArray(detail)) {
    const labels: Record<string, string> = {
      title: 'Tên văn bản',
      content: 'Nội dung toàn văn',
      law_number: 'Số, ký hiệu',
      document_type: 'Loại văn bản',
      issuing_agency: 'Cơ quan ban hành',
      scope: 'Phạm vi áp dụng',
      effective_date: 'Ngày có hiệu lực',
    }
    return detail.map((item) => {
      const field = String(item.loc?.[item.loc.length - 1] || '')
      const label = labels[field] || field || 'Dữ liệu'
      if (item.type === 'string_too_short') {
        return `${label} quá ngắn.`
      }
      return `${label}: ${item.msg || 'không hợp lệ'}`
    }).join(' ')
  }
  if (detail && typeof detail === 'object') {
    return [detail.message, detail.suggestion, detail.trace_id ? `Mã lỗi: ${detail.trace_id}` : '']
      .filter(Boolean)
      .join(' ')
  }
  return detail || axiosError.message || 'Không thể xử lý yêu cầu.'
}

function candidateNeedsOcr(candidate: LegalCrawlCandidate): boolean {
  const raw = candidate.raw_metadata as Record<string, unknown> | null | undefined
  return Boolean(candidate.needs_ocr || raw?.needs_ocr)
}

export default function LegalImportPage() {
  const [form, setForm] = useState<LegalImportPayload>(initialForm)
  const [fields, setFields] = useState<LegalField[]>([])
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [checking, setChecking] = useState(false)
  const [importing, setImporting] = useState(false)
  const [fileName, setFileName] = useState('')
  const [fileExtractor, setFileExtractor] = useState<LegalImportExtractor>('auto')
  const [crawlUrl, setCrawlUrl] = useState('')
  const [crawling, setCrawling] = useState(false)
  const [crawlSummary, setCrawlSummary] = useState<LegalCrawlSummary | null>(null)
  const [pendingCandidates, setPendingCandidates] = useState<LegalCrawlCandidate[]>([])
  const [crawlerSources, setCrawlerSources] = useState<LegalCrawlSource[]>([])
  const [candidateOrigin, setCandidateOrigin] = useState('all')
  const [scanningSourceId, setScanningSourceId] = useState<string | null>(null)
  const [assessingCandidateId, setAssessingCandidateId] = useState<string | null>(null)
  const [reviewingCandidateId, setReviewingCandidateId] = useState<string | null>(null)
  const [candidateStatus, setCandidateStatus] = useState('pending')
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({})
  const [formsCatalog, setFormsCatalog] = useState<LegalFormsCatalogStatus | null>(null)
  const [loadingFormsCatalog, setLoadingFormsCatalog] = useState(false)

  const [pendingForms, setPendingForms] = useState<LegalFormCandidate[]>([])
  const [reviewingFormId, setReviewingFormId] = useState<string | null>(null)
  const [formReviewStatus, setFormReviewStatus] = useState('candidate_pending_review')
  const [formReviewNotes, setFormReviewNotes] = useState<Record<string, string>>({})
  const [formEditNames, setFormEditNames] = useState<Record<string, string>>({})
  const [formEditDomains, setFormEditDomains] = useState<Record<string, string>>({})
  const [formEditProcedures, setFormEditProcedures] = useState<Record<string, string>>({})

  useEffect(() => {
    legalImportApi.fields()
      .then(setFields)
      .catch((error) => toast.error(errorMessage(error)))
  }, [])

  const loadCrawlerData = useCallback(async () => {
    try {
      const [summary, candidates, sources] = await Promise.all([
        legalImportApi.crawlSummary(),
        legalImportApi.crawlCandidates(candidateStatus, 50),
        legalImportApi.crawlSources(),
      ])
      setCrawlSummary(summary)
      setPendingCandidates(candidates)
      setCrawlerSources(sources)
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }, [candidateStatus])

  useEffect(() => {
    void loadCrawlerData()
  }, [loadCrawlerData])

  const loadFormsCatalog = useCallback(async () => {
    setLoadingFormsCatalog(true)
    try {
      setFormsCatalog(await legalImportApi.formsCatalogStatus())
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setLoadingFormsCatalog(false)
    }
  }, [])

  useEffect(() => {
    void loadFormsCatalog()
  }, [loadFormsCatalog])

  const loadFormsCandidates = useCallback(async () => {
    try {
      const candidates = await legalImportApi.formsCatalogCandidates(undefined, formReviewStatus)
      setPendingForms(candidates)
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }, [formReviewStatus])

  useEffect(() => {
    void loadFormsCandidates()
  }, [loadFormsCandidates])

  const canImport = useMemo(
    () => Boolean(preview?.valid && !checking && !importing),
    [preview, checking, importing]
  )

  const visibleCandidates = useMemo(() => pendingCandidates.filter((candidate) => {
    if (candidateOrigin === 'all') return true
    const origin = String(candidate.raw_metadata?.candidate_origin || '')
    if (candidateOrigin === 'officer') return origin === 'officer_document_proposal'
    return origin !== 'officer_document_proposal'
  }), [candidateOrigin, pendingCandidates])

  const update = <K extends keyof LegalImportPayload>(
    key: K,
    value: LegalImportPayload[K]
  ) => {
    setForm((current) => ({ ...current, [key]: value }))
    setPreview(null)
  }

  const readFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!/\.(txt|md|json|docx|pdf)$/i.test(file.name)) {
      toast.error('Hiện hỗ trợ tệp .txt, .md, .json, .docx và .pdf.')
      event.target.value = ''
      return
    }
    let content = ''
    if (/\.(docx|pdf)$/i.test(file.name)) {
      try {
        const extracted = await legalImportApi.extractFile(file, fileExtractor)
        content = extracted.content
      } catch (error) {
        toast.error(errorMessage(error))
        event.target.value = ''
        return
      }
    } else {
      content = await file.text()
    }
    setFileName(file.name)
    update('content', content)
  }

  const checkDocument = async (event: FormEvent) => {
    event.preventDefault()
    setChecking(true)
    try {
      const result = await legalImportApi.preview(form)
      setPreview(result)
      if (result.valid) {
        toast.success(`Đã nhận diện ${result.article_count} điều, ${result.chunk_count} chunk.`)
      } else {
        toast.error('Văn bản chưa đạt điều kiện nạp.')
      }
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setChecking(false)
    }
  }

  const importDocument = async () => {
    setImporting(true)
    try {
      await legalImportApi.importDocument(form)
      toast.success(
        `Đã gửi văn bản vào hàng đợi duyệt của admin thành công.`
      )
      setForm(initialForm)
      setFileName('')
      setPreview(null)
      await loadCrawlerData()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setImporting(false)
    }
  }

  const crawlPreview = async () => {
    if (!crawlUrl.trim()) return
    setCrawling(true)
    try {
      const result = await legalImportApi.crawlPreview(crawlUrl.trim())
      setForm((current) => ({
        ...current,
        title: current.title || result.title,
        source_url: result.source_url,
        content: result.content,
        confirmed_official_source: false,
      }))
      setPreview(null)
      toast.success(`Đã quét ${result.characters} ký tự. Hãy kiểm tra trước khi nạp.`)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setCrawling(false)
    }
  }

  const runScan = async (sourceId?: string) => {
    setScanningSourceId(sourceId || '__all__')
    try {
      const result = await legalImportApi.scanNow(sourceId)
      toast.success(
        `Quét xong: ${result.created || 0} văn bản mới, ${result.updated || 0} văn bản cập nhật.`
      )
      await loadCrawlerData()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setScanningSourceId(null)
    }
  }

  const reviewCandidate = async (
    candidateId: string,
    decision: 'approved' | 'rejected' | 'changes_requested'
  ) => {
    const note = reviewNotes[candidateId] || ''
    if (decision === 'changes_requested' && note.trim().length < 5) {
      toast.error('Hãy ghi rõ nội dung cán bộ cần bổ sung.')
      return
    }
    setReviewingCandidateId(candidateId)
    try {
      await legalImportApi.reviewCandidate(candidateId, decision, note)
      toast.success(decision === 'approved' ? 'Đã duyệt và xếp hàng nhập kho.' : decision === 'changes_requested' ? 'Đã gửi yêu cầu bổ sung cho cán bộ.' : 'Đã từ chối văn bản.')
      setReviewNotes((current) => ({ ...current, [candidateId]: '' }))
      await loadCrawlerData()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setReviewingCandidateId(null)
    }
  }

  const assessCandidate = async (candidateId: string) => {
    setAssessingCandidateId(candidateId)
    try {
      const result = await legalImportApi.assessCandidate(candidateId)
      if (result.ai_assessment) {
        toast.success('Đã cập nhật AI assessment cho văn bản thành công!')
      } else {
        toast.warning(result.warning || 'AI chưa đánh giá được candidate. Hệ thống vẫn đã cập nhật khuyến nghị kiểm duyệt theo rule-based checks.')
      }
      setPendingCandidates((current) =>
        current.map((c) => (c.id === candidateId ? result.candidate : c))
      )
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setAssessingCandidateId(null)
    }
  }

  const reviewForm = async (formId: string, decision: 'approved' | 'rejected') => {
    setReviewingFormId(formId)
    try {
      await legalImportApi.reviewForm(formId, decision, formReviewNotes[formId] || '', {
        form_name: formEditNames[formId],
        domain: formEditDomains[formId],
        procedure_id: formEditProcedures[formId],
        reason: formReviewNotes[formId] || '',
      })
      toast.success(decision === 'approved' ? 'Đã duyệt biểu mẫu thành công.' : 'Đã từ chối biểu mẫu.')
      setFormReviewNotes((current) => ({ ...current, [formId]: '' }))
      await loadFormsCandidates()
      await loadFormsCatalog()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setReviewingFormId(null)
    }
  }



  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6 pb-16">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-semibold">
            <DatabaseZap className="h-8 w-8 text-primary" />
            Nạp dữ liệu pháp luật
          </h1>
          <p className="mt-2 text-muted-foreground">
            Kiểm tra văn bản, tách theo điều và embedding bằng VNLegal-LAL vào kho tra cứu Hải Phòng.
          </p>
        </div>
        <Button variant="outline" className="gap-2 self-start sm:self-auto" asChild>
          <Link href="/notebooks">
            <ArrowLeft className="h-4 w-4" />
            Quay lại Trang chính
          </Link>
        </Button>
      </div>

      {/* Kho biểu mẫu */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Kho biểu mẫu</CardTitle>
            <CardDescription>
              Chỉ file vượt qua kiểm tra định dạng và nguồn tải mới được tính là biểu mẫu chính thức.
            </CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void loadFormsCatalog()}
            disabled={loadingFormsCatalog}
          >
            <RefreshCcw className={`mr-2 h-4 w-4 ${loadingFormsCatalog ? 'animate-spin' : ''}`} />
            Làm mới
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-5">
            <div className="rounded-lg border border-blue-300 bg-blue-50 p-4">
              <p className="text-sm text-blue-800">Danh mục ưu tiên</p>
              <p className="mt-1 text-2xl font-semibold text-blue-950">
                {formsCatalog?.summary.hai_phong_official?.priority_200?.selected_count ?? 0}
              </p>
              <p className="mt-1 text-xs text-blue-700">Mục tiêu tối thiểu 200 mẫu</p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Tham chiếu đã nạp</p>
              <p className="mt-1 text-2xl font-semibold">
                {formsCatalog?.summary.hai_phong_official?.total_source_references ?? 0}
              </p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Biểu mẫu có thể tải</p>
              <p className="mt-1 text-2xl font-semibold">
                {formsCatalog?.summary.hai_phong_official?.available_canonical_forms ?? 0}
              </p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Bị chặn do hết hiệu lực</p>
              <p className="mt-1 text-2xl font-semibold">
                {formsCatalog?.summary.hai_phong_official?.blocked_effectivity_flags ?? 0}
              </p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Tên mẫu cần duyệt</p>
              <p className="mt-1 text-2xl font-semibold">
                {formsCatalog?.summary.hai_phong_official?.review_required_title ?? 0}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Biểu mẫu mới cần admin duyệt */}
      <Card>
        <CardHeader>
          <CardTitle>Biểu mẫu mới cần admin duyệt</CardTitle>
          <CardDescription>
            Duyệt hoặc từ chối biểu mẫu trước khi đưa vào kho chính thức Hải Phòng và Procedures RAG.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor="form-review-status">Trạng thái</Label>
            <Select value={formReviewStatus} onValueChange={setFormReviewStatus}>
              <SelectTrigger id="form-review-status" className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="candidate_pending_review">Chờ duyệt</SelectItem>
                <SelectItem value="approved">Đã duyệt</SelectItem>
                <SelectItem value="rejected">Đã bỏ qua / từ chối</SelectItem>
              </SelectContent>
            </Select>
            <Button type="button" variant="outline" onClick={() => void loadFormsCandidates()}>
              Làm mới danh sách
            </Button>
          </div>

          <div className="space-y-3 rounded-lg border p-4">
            <div>
              <h3 className="font-medium">Nguồn crawler và lịch chạy</h3>
              <p className="text-sm text-muted-foreground">Nguồn mặc định chạy mỗi 10.080 phút (7 ngày). Admin có thể tắt nguồn hoặc quét riêng ngay.</p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {crawlerSources.map((source) => <div key={source.id} className="rounded border p-3">
                <div className="flex items-start justify-between gap-3">
                  <div><p className="font-medium">{source.name}</p><p className="break-all text-xs text-muted-foreground">{source.base_url}</p></div>
                  <Badge variant={source.enabled ? 'secondary' : 'outline'}>{source.enabled ? 'Đang bật' : 'Đã tắt'}</Badge>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>Chu kỳ: {source.interval_minutes} phút</span>
                  <span>·</span><span>Lần cuối: {source.last_checked_at ? new Date(source.last_checked_at).toLocaleString('vi-VN') : 'Chưa quét'}</span>
                </div>
                <div className="mt-3 flex gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={() => runScan(source.id)} disabled={scanningSourceId === source.id}>{scanningSourceId === source.id ? 'Đang quét...' : 'Quét nguồn này'}</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={async () => { try { await legalImportApi.updateCrawlSource(source.id, { enabled: !source.enabled }); await loadCrawlerData() } catch (error) { toast.error(errorMessage(error)) } }}>{source.enabled ? 'Tắt' : 'Bật'}</Button>
                </div>
              </div>)}
            </div>
          </div>
          {pendingForms.length === 0 ? (
            <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
              Chưa có biểu mẫu nào trong hàng đợi duyệt.
            </div>
          ) : (
            <div className="space-y-3">
              {pendingForms.map((item) => {
                const displayName = formEditNames[item.id] ?? item.title ?? item.detected_form_name ?? item.file_name ?? 'Biểu mẫu chưa rõ tên'
                const displayDomain = formEditDomains[item.id] ?? item.domain ?? item.suggested_domain ?? ''
                const displayProcedure = formEditProcedures[item.id] ?? item.procedure_id ?? item.suggested_procedure_id ?? ''
                return (
                <div key={item.id} className="rounded-lg border p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div className="space-y-3 w-full">
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="font-medium">{displayName}</h4>
                        <Badge variant="outline">{item.official_level || 'candidate'}</Badge>
                        {typeof item.confidence === 'number' && (
                          <Badge variant="secondary">conf {item.confidence.toFixed(2)}</Badge>
                        )}
                        <Badge variant="outline">{item.review_status || formReviewStatus}</Badge>
                      </div>
                      <p className="text-sm text-muted-foreground">
                        ID: <span className="font-mono text-xs">{item.id}</span>
                        {item.file_name ? ` • File: ${item.file_name}` : ''}
                      </p>
                      {item.source_url && (
                        <p className="text-xs text-muted-foreground break-all">
                          Nguồn: <a href={item.source_url} target="_blank" rel="noreferrer" className="underline hover:text-primary">{item.source_url}</a>
                        </p>
                      )}
                      <div className="grid gap-3 md:grid-cols-3">
                        <div className="space-y-1">
                          <Label htmlFor={`form-name-${item.id}`}>Tên biểu mẫu</Label>
                          <Input
                            id={`form-name-${item.id}`}
                            value={displayName}
                            onChange={(event) =>
                              setFormEditNames((current) => ({
                                ...current,
                                [item.id]: event.target.value,
                              }))
                            }
                            placeholder="Sửa tên trước khi duyệt"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor={`form-domain-${item.id}`}>Lĩnh vực (domain)</Label>
                          <Select
                            value={displayDomain || 'unknown'}
                            onValueChange={(value) =>
                              setFormEditDomains((current) => ({
                                ...current,
                                [item.id]: value,
                              }))
                            }
                          >
                            <SelectTrigger id={`form-domain-${item.id}`}>
                              <SelectValue placeholder="Chọn lĩnh vực" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="ho_tich">ho_tich</SelectItem>
                              <SelectItem value="cu_tru">cu_tru</SelectItem>
                              <SelectItem value="dat_dai_xay_dung">dat_dai_xay_dung</SelectItem>
                              <SelectItem value="trat_tu_do_thi">trat_tu_do_thi</SelectItem>
                              <SelectItem value="khieu_nai_to_cao">khieu_nai_to_cao</SelectItem>
                              <SelectItem value="xu_phat_hanh_chinh">xu_phat_hanh_chinh</SelectItem>
                              <SelectItem value="unknown">unknown</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor={`form-proc-${item.id}`}>procedure_id</Label>
                          <Input
                            id={`form-proc-${item.id}`}
                            value={displayProcedure}
                            onChange={(event) =>
                              setFormEditProcedures((current) => ({
                                ...current,
                                [item.id]: event.target.value,
                              }))
                            }
                            placeholder="vd: dang_ky_khai_sinh"
                          />
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`form-review-note-${item.id}`}>Ghi chú / lý do duyệt</Label>
                        <Textarea
                          id={`form-review-note-${item.id}`}
                          rows={2}
                          placeholder="Nhập ghi chú duyệt hoặc lý do từ chối..."
                          value={formReviewNotes[item.id] || item.review_note || item.reason || ''}
                          onChange={(event) =>
                            setFormReviewNotes((current) => ({
                              ...current,
                              [item.id]: event.target.value,
                            }))
                          }
                        />
                      </div>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => reviewForm(item.id, 'rejected')}
                        disabled={reviewingFormId === item.id}
                      >
                        Từ chối
                      </Button>
                      <Button
                        type="button"
                        onClick={() => reviewForm(item.id, 'approved')}
                        disabled={reviewingFormId === item.id}
                      >
                        Duyệt
                      </Button>
                    </div>
                  </div>
                </div>
              )})}
            </div>
          )}
        </CardContent>
      </Card>

      {/* VBPL tự động */}
      <Card>
        <CardHeader>
          <CardTitle>Văn bản mới cần admin duyệt (Review Queue)</CardTitle>
          <CardDescription>
            Bao gồm văn bản thu thập tự động từ VBPL và các bản nháp nạp thủ công đang chờ duyệt để embedding.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-4 md:grid-cols-4">
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Chờ duyệt</p>
              <p className="mt-1 text-2xl font-semibold">{crawlSummary?.pending_review_count ?? 0}</p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Thông báo chưa đọc</p>
              <p className="mt-1 text-2xl font-semibold">{crawlSummary?.unread_notification_count ?? 0}</p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Nguồn crawler</p>
              <p className="mt-1 text-2xl font-semibold">{crawlSummary?.source_count ?? 0}</p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-sm text-muted-foreground">Lần quét gần nhất</p>
              <p className="mt-1 text-sm font-medium">
                {crawlSummary?.last_checked_at ? new Date(crawlSummary.last_checked_at).toLocaleString('vi-VN') : 'Chưa có'}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" onClick={() => runScan()} disabled={scanningSourceId === '__all__'}>
              <RefreshCcw className="mr-2 h-4 w-4" />
              {scanningSourceId === '__all__' ? 'Đang quét...' : 'Quét ngay tất cả nguồn'}
            </Button>
            <p className="text-sm text-muted-foreground">
              Hệ thống tự quét 7 ngày một lần, so sánh metadata cũ/mới và chỉ tạo candidate để admin duyệt.
            </p>
          </div>

          <div className="space-y-4">
            <div>
              <h3 className="font-medium text-sm">Văn bản chờ duyệt trong hàng đợi</h3>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Label htmlFor="candidate-status">Trạng thái duyệt</Label>
              <Select value={candidateStatus} onValueChange={setCandidateStatus}>
                <SelectTrigger id="candidate-status" className="w-[220px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pending">Chờ duyệt (Pending)</SelectItem>
                  <SelectItem value="changes_requested">Cần bổ sung</SelectItem>
                  <SelectItem value="approved">Đã duyệt (Approved)</SelectItem>
                  <SelectItem value="import_queued">Đang chờ nhập kho</SelectItem>
                  <SelectItem value="rejected">Đã bỏ qua (Rejected)</SelectItem>
                  <SelectItem value="imported">Đã nhập kho (Imported)</SelectItem>
                </SelectContent>
              </Select>
              <Select value={candidateOrigin} onValueChange={setCandidateOrigin}>
                <SelectTrigger className="w-[220px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Tất cả nguồn đề xuất</SelectItem>
                  <SelectItem value="crawler">Crawler tự động</SelectItem>
                  <SelectItem value="officer">Cán bộ đề xuất</SelectItem>
                </SelectContent>
              </Select>
              <Button type="button" variant="outline" onClick={() => void loadCrawlerData()}>
                Làm mới danh sách
              </Button>
            </div>
            {visibleCandidates.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                Chưa có văn bản nào trong hàng đợi duyệt.
              </div>
            ) : (
              <div className="space-y-3">
                {visibleCandidates.map((candidate) => (
                  <div key={candidate.id} className="rounded-lg border p-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <h4 className="font-medium">{candidate.title}</h4>
                          <Badge variant="outline">{candidate.suggested_action}</Badge>
                          <Badge variant={candidate.comparison_status === 'updated' ? 'secondary' : 'outline'}>
                            {candidate.comparison_status}
                          </Badge>
                          <Badge variant="secondary">{candidate.raw_metadata?.candidate_origin === 'officer_document_proposal' ? 'Cán bộ đề xuất' : 'Crawler'}</Badge>
                          {candidateNeedsOcr(candidate) && (
                            <Badge variant="destructive" className="bg-amber-500 hover:bg-amber-600 text-white border-none gap-1">
                              <TriangleAlert className="h-3.5 w-3.5" />
                              Bản Quét (Scan) / Cần OCR
                            </Badge>
                          )}
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {candidate.law_number || 'Chưa tách được số hiệu'} • {candidate.scope || 'Chưa rõ phạm vi'}
                        </p>
                        {candidate.description && (
                          <p className="text-sm text-muted-foreground line-clamp-3">{candidate.description}</p>
                        )}
                        {candidate.source_url && <p className="text-sm"><a className="text-primary underline" href={candidate.source_url} target="_blank" rel="noreferrer">Mở nguồn gốc</a></p>}
                        {(candidate.detected_change_details || []).length > 0 && <div className="rounded border bg-muted/20 p-3 text-xs"><b>So sánh cũ/mới</b><ul className="mt-2 list-disc space-y-1 pl-4">{candidate.detected_change_details?.map((change, index) => <li key={`${change.field}-${index}`}>{change.label}: <span className="text-red-700">{String(change.old_value ?? 'trống')}</span> → <span className="text-green-700">{String(change.new_value ?? 'trống')}</span></li>)}</ul></div>}
                        {candidate.extraction_result && (
                          <div className="mt-3 rounded-lg border bg-muted/20 p-3 text-xs space-y-2">
                            <div className="flex flex-wrap items-center gap-2 font-semibold">
                              <span>Trích xuất kiểm duyệt</span>
                              <Badge variant="outline">{candidate.extraction_result.pdf_kind === 'scan' ? 'PDF scan' : candidate.extraction_result.pdf_kind === 'text_based' ? 'PDF có text' : candidate.extraction_result.extractor_used || 'Tệp nguồn'}</Badge>
                              <Badge variant={candidate.extraction_result.ocr_status === 'ok' || candidate.extraction_result.ocr_status === 'not_required' || candidate.extraction_result.ocr_status === 'not_applicable' ? 'outline' : 'destructive'}>
                                OCR: {candidate.extraction_result.ocr_status || 'chưa chạy'}
                              </Badge>
                            </div>
                            <p className="text-muted-foreground">
                              {candidate.extraction_result.page_count ?? 0} trang - {candidate.extraction_result.characters ?? 0} ký tự - {candidate.extraction_result.language || 'không rõ ngôn ngữ'}
                              {candidate.extraction_result.ocr_confidence != null ? ` - OCR ${Math.round(candidate.extraction_result.ocr_confidence)}%` : ''}
                            </p>
                            {candidate.extraction_result.reason && <p className="text-amber-700">{candidate.extraction_result.reason}</p>}
                            {candidate.extraction_result.preview && <p className="rounded bg-background p-2 text-muted-foreground line-clamp-4 whitespace-pre-wrap">{candidate.extraction_result.preview}</p>}
                            {candidate.extraction_result.text_fingerprint && <p className="font-mono text-[10px] text-muted-foreground">Fingerprint: {candidate.extraction_result.text_fingerprint.slice(0, 16)}?</p>}
                          </div>
                        )}
                        {candidate.review_recommendation && (
                          <div className="mt-3 rounded-lg border border-primary/20 bg-primary/[0.03] p-3 space-y-2">
                            <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-primary">
                              <span>Khuyến nghị kiểm duyệt</span>
                              <Badge variant="outline">Admin quyết định cuối</Badge>
                            </div>
                            {candidate.review_recommendation.scores && (
                              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-xs">
                                {Object.entries(candidate.review_recommendation.scores).map(([key, value]) => (
                                  <div key={key} className="rounded border bg-background p-2">
                                    <div className="flex justify-between gap-2"><span className="text-muted-foreground">{key.replaceAll('_', ' ')}</span><strong>{value}/100</strong></div>
                                    <div className="mt-1 h-1.5 overflow-hidden rounded bg-muted"><div className="h-full bg-primary" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>
                                  </div>
                                ))}
                              </div>
                            )}
                            {(candidate.review_recommendation.evidence || []).length > 0 && (
                              <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                                {candidate.review_recommendation.evidence?.map((item) => <li key={item}>{item}</li>)}
                              </ul>
                            )}
                            {(candidate.review_recommendation.evidence_snippets || []).map((snippet) => (
                              <p key={`${snippet.kind}-${snippet.text.slice(0, 30)}`} className="rounded bg-background p-2 text-xs text-muted-foreground line-clamp-3 whitespace-pre-wrap">{snippet.text}</p>
                            ))}
                          </div>
                        )}
                        {candidate.ai_assessment && (
                          <div className="mt-3 rounded-lg border bg-muted/30 p-3 space-y-2">
                            <div className="flex items-center gap-2 text-xs font-semibold text-primary">
                              <span>🤖 ĐÁNH GIÁ CỦA AI (Độ tin cậy: {Math.round(candidate.ai_assessment.confidence * 100)}%)</span>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <Badge variant="outline" className="bg-primary/5">
                                Lĩnh vực: {
                                  candidate.ai_assessment.domain === 'ho_tich_chung_thuc' ? 'Hộ tịch - Chứng thực' :
                                  candidate.ai_assessment.domain === 'dat_dai_xay_dung' ? 'Đất đai - Xây dựng' :
                                  candidate.ai_assessment.domain === 'an_sinh_y_te_giao_duc' ? 'An sinh - Y tế - Giáo dục' :
                                  candidate.ai_assessment.domain === 'hanh_chinh_cong' ? 'Hành chính công' :
                                  candidate.ai_assessment.domain === 'cu_tru_an_ninh' ? 'Cư trú - An ninh' :
                                  candidate.ai_assessment.domain === 'khieu_nai_to_cao_xu_phat' ? 'Khiếu nại - Tố cáo - Xử phạt' :
                                  candidate.ai_assessment.domain
                                }
                              </Badge>
                              <Badge variant="outline" className="bg-secondary/5">
                                Cấp áp dụng: {
                                  candidate.ai_assessment.scope === 'central' ? 'Trung ương' :
                                  candidate.ai_assessment.scope === 'haiphong' ? 'Hải Phòng' :
                                  candidate.ai_assessment.scope === 'local' ? 'Cấp phường/xã' :
                                  candidate.ai_assessment.scope
                                }
                              </Badge>
                              <Badge variant="outline">
                                Phân loại: {
                                  candidate.ai_assessment.official_level === 'official' ? 'Văn bản QPPL chính thức' :
                                  candidate.ai_assessment.official_level === 'reference' ? 'Tài liệu tham khảo/hướng dẫn' :
                                  candidate.ai_assessment.official_level === 'internal' ? 'Văn bản nội bộ' :
                                  candidate.ai_assessment.official_level
                                }
                              </Badge>
                              <Badge variant="outline" className={
                                candidate.ai_assessment.effective_status === 'con_hieu_luc' ? 'border-green-300 text-green-700 bg-green-50/50' :
                                candidate.ai_assessment.effective_status === 'het_hieu_luc' ? 'border-destructive/30 text-destructive bg-destructive/5' :
                                'border-amber-300 text-amber-700 bg-amber-50/50'
                              }>
                                Hiệu lực: {
                                  candidate.ai_assessment.effective_status === 'con_hieu_luc' ? 'Còn hiệu lực' :
                                  candidate.ai_assessment.effective_status === 'het_hieu_luc' ? 'Hết hiệu lực' :
                                  candidate.ai_assessment.effective_status === 'chua_co_hieu_luc' ? 'Chưa có hiệu lực' :
                                  'Không rõ hiệu lực'
                                }
                              </Badge>
                              <Badge variant="outline" className={
                                candidate.ai_assessment.duplicate_risk === 'none' ? 'border-green-300 text-green-700 bg-green-50/50' :
                                candidate.ai_assessment.duplicate_risk === 'possible' ? 'border-yellow-400 text-yellow-800 bg-yellow-50/50' :
                                'border-destructive text-destructive bg-destructive/5 font-semibold'
                              }>
                                Trùng lặp: {
                                  candidate.ai_assessment.duplicate_risk === 'none' ? 'Không trùng' :
                                  candidate.ai_assessment.duplicate_risk === 'possible' ? 'Có thể trùng' :
                                  'Khả năng cao trùng'
                                }
                              </Badge>
                            </div>
                            {candidate.ai_assessment.reasons && candidate.ai_assessment.reasons.length > 0 && (
                              <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-1">
                                {candidate.ai_assessment.reasons.map((reason, idx) => (
                                  <li key={idx}>{reason}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                        <div className="space-y-2 mt-2">
                          <Label htmlFor={`review-note-${candidate.id}`}>Ghi chú duyệt</Label>
                          <Textarea
                            id={`review-note-${candidate.id}`}
                            rows={3}
                            placeholder="Lý do duyệt, bỏ qua, hoặc ghi chú cập nhật..."
                            value={reviewNotes[candidate.id] || candidate.review_note || ''}
                            onChange={(event) =>
                              setReviewNotes((current) => ({
                                ...current,
                                [candidate.id]: event.target.value,
                              }))
                            }
                          />
                        </div>
                      </div>
                      <div className="flex flex-col gap-2">
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="secondary"
                            onClick={() => reviewCandidate(candidate.id, 'changes_requested')}
                            disabled={reviewingCandidateId === candidate.id}
                          >
                            Yêu cầu bổ sung
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => reviewCandidate(candidate.id, 'rejected')}
                            disabled={reviewingCandidateId === candidate.id}
                          >
                            Bỏ qua
                          </Button>
                          <Button
                            type="button"
                            onClick={() => reviewCandidate(candidate.id, 'approved')}
                            disabled={reviewingCandidateId === candidate.id}
                          >
                            Duyệt
                          </Button>
                        </div>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="w-full gap-1.5"
                          onClick={() => assessCandidate(candidate.id)}
                          disabled={assessingCandidateId === candidate.id}
                        >
                          <RefreshCcw className={`h-3 w-3 ${assessingCandidateId === candidate.id ? 'animate-spin' : ''}`} />
                          🤖 AI Đánh giá
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Form nạp văn bản thủ công */}
      <form onSubmit={checkDocument} className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Thông tin văn bản</CardTitle>
            <CardDescription>
              Không dùng AI để đoán số hiệu, ngày hiệu lực hoặc cơ quan ban hành.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="title">Tên văn bản *</Label>
              <Input id="title" minLength={5} value={form.title} onChange={(e) => update('title', e.target.value)} required />
              <p className="text-xs text-muted-foreground">Tối thiểu 5 ký tự.</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="law-number">Số, ký hiệu *</Label>
              <Input id="law-number" value={form.law_number} onChange={(e) => update('law_number', e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="document-type">Loại văn bản *</Label>
              <Input id="document-type" placeholder="Luật, Nghị định, Quyết định..." value={form.document_type} onChange={(e) => update('document_type', e.target.value)} required />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="agency">Cơ quan ban hành *</Label>
              <Input id="agency" value={form.issuing_agency} onChange={(e) => update('issuing_agency', e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label>Phạm vi áp dụng *</Label>
              <Select value={form.scope} onValueChange={(value) => update('scope', value)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="Trung ương - toàn quốc">Trung ương - toàn quốc</SelectItem>
                  <SelectItem value="Thành phố Hải Phòng">Thành phố Hải Phòng</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Lĩnh vực *</Label>
              <Select
                value={form.field_id ? String(form.field_id) : ''}
                onValueChange={(value) => update('field_id', Number(value))}
              >
                <SelectTrigger><SelectValue placeholder="Chọn lĩnh vực" /></SelectTrigger>
                <SelectContent>
                  {fields.map((field) => (
                    <SelectItem key={field.id} value={String(field.id)}>{field.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="issued-date">Ngày ban hành</Label>
              <Input id="issued-date" type="date" value={form.issued_date || ''} onChange={(e) => update('issued_date', e.target.value || null)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="effective-date">Ngày có hiệu lực *</Label>
              <Input id="effective-date" type="date" value={form.effective_date} onChange={(e) => update('effective_date', e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="expired-date">Ngày hết hiệu lực</Label>
              <Input id="expired-date" type="date" value={form.expired_date || ''} onChange={(e) => update('expired_date', e.target.value || null)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="sector">Ngành/chủ đề</Label>
              <Input id="sector" value={form.sector} onChange={(e) => update('sector', e.target.value)} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="source-url">URL nguồn chính thức</Label>
              <Input id="source-url" type="url" placeholder="https://..." value={form.source_url} onChange={(e) => update('source_url', e.target.value)} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="applicability">Thông tin áp dụng/sửa đổi</Label>
              <Textarea id="applicability" value={form.applicability_info} onChange={(e) => update('applicability_info', e.target.value)} />
            </div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Nội dung toàn văn</CardTitle>
              <CardDescription>
                Tệp phải có các tiêu đề dạng &quot;Điều 1. ...&quot;. Hỗ trợ TXT, Markdown, Word và PDF.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Label htmlFor="legal-file" className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed p-4 text-center hover:bg-muted/50">
                <FileUp className="mb-2 h-7 w-7" />
                <span>{fileName || 'Chọn tệp văn bản'}</span>
                <span className="text-xs text-muted-foreground">.txt, .md, .json, .docx hoặc .pdf</span>
              </Label>
              <Input id="legal-file" className="hidden" type="file" accept=".txt,.md,.json,.docx,.pdf,text/plain,text/markdown,application/json,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={readFile} />
              <div className="space-y-2">
                <Label>Chế độ trích xuất PDF/DOCX</Label>
                <Select value={fileExtractor} onValueChange={(value) => setFileExtractor(value as LegalImportExtractor)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">Tự động - ưu tiên trích xuất nâng cao nếu sẵn sàng</SelectItem>
                    <SelectItem value="basic">Cơ bản - nhanh, gọn</SelectItem>
                    <SelectItem value="rag_anything">Trích xuất nâng cao - PDF/Văn phòng phức tạp</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Textarea
                className="min-h-64 font-mono text-xs"
                minLength={20}
                placeholder={'Điều 1. Phạm vi điều chỉnh\nNội dung điều luật...'}
                value={form.content}
                onChange={(e) => update('content', e.target.value)}
                required
              />
              <p className="text-xs text-muted-foreground">
                {form.content.length} ký tự. Tối thiểu 20 ký tự và phải có tiêu đề &quot;Điều 1&quot;.
              </p>
              <div className="space-y-2 rounded-lg border p-3">
                <Label htmlFor="crawl-url">Bộ quét tự động từ URL chính thức</Label>
                <div className="flex gap-2">
                  <Input
                    id="crawl-url"
                    type="url"
                    placeholder="https://..."
                    value={crawlUrl}
                    onChange={(event) => setCrawlUrl(event.target.value)}
                  />
                  <Button type="button" variant="outline" onClick={crawlPreview} disabled={crawling || !crawlUrl.trim()}>
                    {crawling ? 'Đang quét...' : 'Quét'}
                  </Button>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-lg border p-3">
                <Checkbox
                  id="official-source"
                  checked={form.confirmed_official_source}
                  onCheckedChange={(checked) => update('confirmed_official_source', checked === true)}
                />
                <Label htmlFor="official-source" className="leading-5">
                  Tôi đã đối chiếu nội dung và metadata với nguồn văn bản chính thức.
                </Label>
              </div>
              <Button type="submit" className="w-full" disabled={checking || importing}>
                <ShieldCheck className="mr-2 h-4 w-4" />
                {checking ? 'Đang kiểm tra...' : 'Kiểm tra trước khi nạp'}
              </Button>
            </CardContent>
          </Card>

          {preview && (
            <Card className={preview.valid ? 'border-green-500/60' : 'border-destructive/60'}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {preview.valid ? <CheckCircle2 className="text-green-600" /> : <TriangleAlert className="text-destructive" />}
                  {preview.valid ? 'Văn bản đạt điều kiện' : 'Cần sửa thông tin'}
                </CardTitle>
                <CardDescription>
                  {preview.article_count} điều, {preview.chunk_count} chunk sẽ được duyệt trước khi embedding.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {preview.errors.map((error) => <p key={error} className="text-sm text-destructive">• {error}</p>)}
                {preview.warnings.map((warning) => <p key={warning} className="text-sm text-amber-700">• {warning}</p>)}
                <Button type="button" className="w-full" disabled={!canImport} onClick={importDocument}>
                  <DatabaseZap className="mr-2 h-4 w-4" />
                  {importing ? 'Đang gửi bản nháp...' : 'Nạp vào hàng đợi chờ duyệt'}
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      </form>
    </div>
  )
}
