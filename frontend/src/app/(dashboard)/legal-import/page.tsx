'use client'

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { AxiosError } from 'axios'
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  ClipboardPaste,
  DatabaseZap,
  FileCode,
  FilePlus2,
  FileUp,
  Inbox,
  Link2,
  RefreshCcw,
  ShieldCheck,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { toast } from 'sonner'

import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CrawlerSourceManager } from '@/components/legal-import/CrawlerSourceManager'
import { CandidateLifecycleActions } from '@/components/legal-import/CandidateLifecycleActions'
import { FormGovernancePanel } from '@/components/legal-import/FormGovernancePanel'
import {
  ImportPreview,
  LegalCrawlCandidate,
  LegalCandidateMetadataUpdate,
  CandidateExtractionResult,
  LegalCrawlSource,
  LegalCrawlSummary,
  LegalField,
  LegalImportExtractor,
  LegalImportReadiness,
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
      validation_errors?: string[]
      blockers?: string[]
      fields?: Record<string, string>
    }
  }>
  const detail = axiosError.response?.data?.detail
  if (!axiosError.response) {
    if (axiosError.code === 'ECONNABORTED') {
      return 'Hệ thống đang xử lý lâu hơn bình thường. Dữ liệu bạn đã nhập vẫn được giữ; vui lòng thử lại sau ít phút.'
    }
    return 'Không kết nối được máy chủ dữ liệu. Vui lòng thử lại; nếu lỗi tiếp diễn, liên hệ người phụ trách hệ thống.'
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
    return [
      detail.message,
      ...(detail.validation_errors || detail.blockers || []),
      ...Object.values(detail.fields || {}),
      detail.suggestion,
      detail.trace_id ? `Mã lỗi: ${detail.trace_id}` : '',
    ]
      .filter(Boolean)
      .join(' ')
  }
  return detail || 'Không thể hoàn tất thao tác. Vui lòng kiểm tra thông tin và thử lại.'
}

function candidateNeedsOcr(candidate: LegalCrawlCandidate): boolean {
  const raw = candidate.raw_metadata as Record<string, unknown> | null | undefined
  return Boolean(candidate.needs_ocr || raw?.needs_ocr)
}

function extractionCharacterSummary(
  extraction: CandidateExtractionResult,
  contentCharacters?: number | null,
): string {
  const reportedCharacters = Number(extraction.characters || 0)
  if (Number.isFinite(reportedCharacters) && reportedCharacters > 0) {
    return `${reportedCharacters} ký tự`
  }
  if (Number.isFinite(contentCharacters) && (contentCharacters || 0) > 0) {
    return `${contentCharacters} ký tự (toàn văn)`
  }
  const previewCharacters = extraction.preview?.trim().length || 0
  return previewCharacters > 0
    ? `chưa ghi toàn văn · xem trước ${previewCharacters} ký tự`
    : 'chưa ghi nhận ký tự'
}

function comparisonStatusLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    new: 'Chưa có trong kho',
    updated: 'Khác bản đang có',
    unchanged: 'Không thay đổi',
    duplicate: 'Có thể trùng',
  }
  return labels[value || ''] || 'Chưa đối chiếu'
}

function vietnameseDateToInput(value: string | undefined): string | undefined {
  const match = value?.trim().match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/)
  if (!match) return undefined
  return `${match[3]}-${match[2].padStart(2, '0')}-${match[1].padStart(2, '0')}`
}

function extractOfficialPageMetadata(content: string): Partial<LegalImportPayload> {
  const clean = content.replace(/\s+/g, ' ').trim()
  const capture = (pattern: RegExp) => clean.match(pattern)?.[1]?.trim()
  const issuedDate = vietnameseDateToInput(capture(/Số\s*ký\s*hiệu\s*:?[\s\S]*?Ngày\s*ban\s*hành\s*:?\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})/i))
  const effectiveDate = vietnameseDateToInput(capture(/Ngày\s*có\s*hiệu\s*lực\s*:?\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})/i))
  return {
    law_number: capture(/Số\s*ký\s*hiệu\s*:?\s*(.+?)\s+Ngày\s*ban\s*hành/i),
    issued_date: issuedDate,
    effective_date: effectiveDate,
    document_type: capture(/Loại\s*văn\s*bản\s*:?\s*(.+?)\s+Cơ\s*quan\s*ban\s*hành/i),
    issuing_agency: capture(/Cơ\s*quan\s*ban\s*hành\s*:?\s*(.+?)\s+(?:Người\s*ký|Trích\s*yếu|Tài\s*liệu\s*đính\s*kèm)/i),
  }
}

export default function LegalImportPage() {
  const searchParams = useSearchParams()
  const tabParam = searchParams.get('tab')
  const [activeTab, setActiveTab] = useState(tabParam || 'proposals')
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
  const [crawlerLoading, setCrawlerLoading] = useState(true)
  const [crawlerLoadError, setCrawlerLoadError] = useState<string | null>(null)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null)
  const [pendingCandidates, setPendingCandidates] = useState<LegalCrawlCandidate[]>([])
  const [crawlerSources, setCrawlerSources] = useState<LegalCrawlSource[]>([])
  const [candidateOrigin, setCandidateOrigin] = useState('all')
  const [scanningSourceId, setScanningSourceId] = useState<string | null>(null)
  const [reviewingCandidateId, setReviewingCandidateId] = useState<string | null>(null)
  const [importingCandidateId, setImportingCandidateId] = useState<string | null>(null)
  const [candidateStatus, setCandidateStatus] = useState('pending')
  const [candidateMetadataDrafts, setCandidateMetadataDrafts] = useState<Record<string, LegalCandidateMetadataUpdate>>({})
  const [savingCandidateMetadataId, setSavingCandidateMetadataId] = useState<string | null>(null)
  const [importReadiness, setImportReadiness] = useState<LegalImportReadiness | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const requestedTab = params.get('tab')
    if (requestedTab && ['proposals', 'forms', 'import'].includes(requestedTab)) {
      setActiveTab(requestedTab)
    }
    const requestedStatus = params.get('status')
    if (requestedStatus && ['all', 'pending', 'needs_attention', 'import_queued', 'rejected', 'imported'].includes(requestedStatus)) {
      setCandidateStatus(requestedStatus)
    }
  }, [])

  useEffect(() => {
    legalImportApi.fields()
      .then(setFields)
      .catch((error) => toast.error(errorMessage(error)))
  }, [])

  const loadCrawlerData = useCallback(async () => {
    setCrawlerLoading(true)
    setCrawlerLoadError(null)
    try {
      const [summary, candidates, sources] = await Promise.all([
        legalImportApi.crawlSummary(),
        legalImportApi.crawlCandidates(candidateStatus, 50),
        legalImportApi.crawlSources(),
      ])
      setCrawlSummary(summary)
      setPendingCandidates(candidates)
      setCrawlerSources(sources)
      setLastRefreshedAt(new Date())
    } catch (error) {
      const message = errorMessage(error)
      setCrawlerLoadError(message)
      toast.error(message)
    } finally {
      setCrawlerLoading(false)
    }
  }, [candidateStatus])

  useEffect(() => {
    void loadCrawlerData()
  }, [loadCrawlerData])

  useEffect(() => {
    if (candidateStatus !== 'import_queued') return
    const intervalId = window.setInterval(() => void loadCrawlerData(), 5000)
    return () => window.clearInterval(intervalId)
  }, [candidateStatus, loadCrawlerData])

  useEffect(() => {
    let mounted = true
    const loadImportReadiness = async () => {
      try {
        const report = await legalImportApi.importReadiness()
        if (mounted) setImportReadiness(report)
      } catch {
        if (mounted) setImportReadiness(null)
      }
    }
    void loadImportReadiness()
    const intervalId = window.setInterval(() => void loadImportReadiness(), 15_000)
    return () => {
      mounted = false
      window.clearInterval(intervalId)
    }
  }, [])

  const canImport = useMemo(
    () => Boolean(preview?.valid && !checking && !importing),
    [preview, checking, importing]
  )
  const importPipelineReady = importReadiness?.status === 'ready'
  const unhealthyCrawlerSources = useMemo(
    () => crawlerSources.filter((source) => source.enabled && source.source_kind !== 'internal_queue' && source.last_status === 'error'),
    [crawlerSources],
  )
  const systemReady = importPipelineReady && unhealthyCrawlerSources.length === 0

  const documentCandidates = useMemo(
    () => pendingCandidates.filter((candidate) => candidate.source_type !== 'form'),
    [pendingCandidates],
  )

  const visibleCandidates = useMemo(() => documentCandidates.filter((candidate) => {
    if (candidateOrigin === 'all') return true
    const origin = String(candidate.raw_metadata?.candidate_origin || '')
    if (candidateOrigin === 'officer') return origin === 'officer_document_proposal'
    return origin !== 'officer_document_proposal'
  }), [candidateOrigin, documentCandidates])

  const candidateMetadataDefaults = (candidate: LegalCrawlCandidate): LegalCandidateMetadataUpdate => ({
    title: candidate.title || '',
    law_number: candidate.law_number || '',
    document_type: candidate.document_type || '',
    issuing_agency: candidate.issuing_agency || '',
    scope: (candidate.scope === 'haiphong' || candidate.scope === 'local' ? candidate.scope : 'central'),
    sector: String(candidate.raw_metadata?.sector || candidate.inferred_domain || ''),
    issued_date: String(candidate.raw_metadata?.issued_date || ''),
    effective_date: String(candidate.raw_metadata?.effective_date || ''),
    expired_date: String(candidate.raw_metadata?.expired_date || ''),
    source_url: candidate.source_url || '',
    confirmed_official_source: Boolean(candidate.raw_metadata?.confirmed_official_source),
  })

  const candidateMetadataDraftFor = (candidate: LegalCrawlCandidate): LegalCandidateMetadataUpdate => ({
    ...candidateMetadataDefaults(candidate),
    ...(candidateMetadataDrafts[candidate.id] || {}),
  })

  const updateCandidateMetadataDraft = <K extends keyof LegalCandidateMetadataUpdate>(
    candidate: LegalCrawlCandidate,
    key: K,
    value: LegalCandidateMetadataUpdate[K],
  ) => {
    setCandidateMetadataDrafts((current) => ({
      ...current,
      [candidate.id]: {
        ...candidateMetadataDefaults(candidate),
        ...(current[candidate.id] || {}),
        [key]: value,
      },
    }))
  }

  const saveCandidateMetadata = async (candidate: LegalCrawlCandidate) => {
    setSavingCandidateMetadataId(candidate.id)
    try {
      const payload = {
        ...candidateMetadataDefaults(candidate),
        ...(candidateMetadataDrafts[candidate.id] || {}),
      }
      const result = await legalImportApi.updateCandidateMetadata(candidate.id, payload)
      setPendingCandidates((current) => current.map((item) => item.id === candidate.id ? result.candidate : item))
      setCandidateMetadataDrafts((current) => {
        const next = { ...current }
        delete next[candidate.id]
        return next
      })
      if (result.validation_errors.length > 0) {
        toast.warning(`Đã lưu. Còn ${result.validation_errors.length} điều kiện cần xử lý trước khi nhập kho.`)
      } else {
        toast.success('Đã lưu và kiểm tra đủ thông tin trước khi duyệt.')
      }
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setSavingCandidateMetadataId(null)
    }
  }

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
        toast.success(`Đã nhận diện ${result.article_count} điều và ${result.chunk_count} đoạn tra cứu.`)
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
        'Đã gửi văn bản vào danh sách chờ duyệt.'
      )
      setForm(initialForm)
      setFileName('')
      setCrawlUrl('')
      setPreview(null)
      setCandidateStatus('pending')
      setActiveTab('proposals')
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
      const detected = extractOfficialPageMetadata(result.content)
      const detectedCount = Object.values(detected).filter(Boolean).length
      setForm((current) => ({
        ...current,
        title: current.title || result.title,
        law_number: current.law_number || result.law_number || detected.law_number || '',
        document_type: current.document_type || result.document_type || detected.document_type || '',
        issuing_agency: current.issuing_agency || result.issuing_agency || detected.issuing_agency || '',
        issued_date: current.issued_date || result.issued_date || detected.issued_date || null,
        effective_date: current.effective_date || result.effective_date || detected.effective_date || '',
        expired_date: current.expired_date || result.expired_date || null,
        source_url: result.source_url,
        content: result.content,
        confirmed_official_source: false,
      }))
      setPreview(null)
      toast.success(`Đã lấy ${result.characters.toLocaleString('vi-VN')} ký tự${detectedCount > 0 ? ` và điền ${detectedCount} thông tin có nhãn rõ` : ''}. Hãy đối chiếu lại với văn bản gốc.`)
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
        `Kiểm tra xong: ${result.created || 0} văn bản mới, ${result.updated || 0} văn bản có cập nhật.`
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
    decision: 'approved' | 'rejected'
  ) => {
    setReviewingCandidateId(candidateId)
    try {
      const result = await legalImportApi.reviewCandidate(candidateId, decision)
      if (decision === 'approved' && result.import_status === 'duplicate_conflict') {
        toast.warning('Văn bản có số hiệu trùng trong kho. Đã chuyển sang mục cần đối chiếu và chưa đưa vào kho.')
      } else if (decision === 'approved' && (result.status === 'changes_requested' || result.pipeline_stage === 'blocked')) {
        const blockers = (result.blockers || []).join('; ')
        toast.warning(blockers ? `Chưa thể nhập kho: ${blockers}` : 'Chưa thể nhập kho. Hãy bổ sung dữ liệu được đánh dấu trên văn bản.')
      } else {
        toast.success(decision === 'approved' ? 'Đã duyệt và xếp hàng đưa vào kho.' : 'Đã từ chối; đề xuất sẽ không được đưa vào kho.')
      }
      if (decision === 'approved' && result.status === 'import_queued') {
        setCandidateStatus('import_queued')
      }
      await loadCrawlerData()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setReviewingCandidateId(null)
    }
  }

  const retryCandidateImport = async (candidateId: string) => {
    setImportingCandidateId(candidateId)
    try {
      const result = await legalImportApi.importCandidate(candidateId)
      if (result.status === 'duplicate_conflict') {
        toast.warning('Văn bản trùng số hiệu với kho đang phục vụ. Đã chuyển sang mục cần đối chiếu.')
        setCandidateStatus('changes_requested')
      } else {
        toast.success('Đã xếp hàng đưa vào kho. Hệ thống sẽ chuẩn hóa và tạo chỉ mục tra cứu ở chế độ nền.')
      }
      await loadCrawlerData()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setImportingCandidateId(null)
    }
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <div className="mx-auto w-full max-w-7xl space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-3 text-xl md:text-2xl font-bold">
                <DatabaseZap className="h-8 w-8 text-primary" />
                Trung tâm dữ liệu pháp luật
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Thu thập, kiểm tra và đưa văn bản chính thức vào kho tra cứu Hải Phòng.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant={systemReady ? 'outline' : 'secondary'}>
                  <Activity className="mr-1 h-3 w-3" />
                  {importReadiness === null
                    ? 'Đang kiểm tra hệ thống'
                    : systemReady
                      ? 'Hệ thống sẵn sàng'
                      : unhealthyCrawlerSources.length > 0
                        ? `${unhealthyCrawlerSources.length} nguồn thu thập đang lỗi`
                        : 'Hệ thống nhập kho cần kiểm tra'}
                </Badge>
                {lastRefreshedAt && <span>Cập nhật lúc {lastRefreshedAt.toLocaleTimeString('vi-VN')}</span>}
              </div>
            </div>
          </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full space-y-6">
        <TabsList className="grid h-auto w-full grid-cols-1 gap-1 p-1 sm:grid-cols-3">
          <TabsTrigger value="proposals" className="gap-2">
            <Inbox className="h-4 w-4" />
            Đề xuất chờ duyệt ({crawlSummary?.pending_review_count ?? 0})
          </TabsTrigger>
          <TabsTrigger value="forms" className="gap-2">
            <FileCode className="h-4 w-4" />
            Biểu mẫu chờ duyệt
          </TabsTrigger>
          <TabsTrigger value="import" className="gap-2">
            <Upload className="h-4 w-4" />
            Thêm văn bản
          </TabsTrigger>
        </TabsList>

        {/* Tab 1: Đề xuất văn bản từ Cán bộ & Crawler (Hiện ngay đầu trang!) */}
        <TabsContent value="proposals">
          <Card>
            <CardHeader>
              <CardTitle>Đề xuất chờ duyệt</CardTitle>
              <CardDescription>
                Xem thông tin và nguồn gốc, sau đó chọn Duyệt hoặc Từ chối. Các kiểm tra an toàn được hệ thống thực hiện tự động.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {crawlerLoadError && (
                <div role="alert" className="flex flex-col gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="font-medium text-destructive">Không tải được dữ liệu vận hành</p>
                    <p className="mt-1 text-sm text-muted-foreground">{crawlerLoadError}</p>
                  </div>
                  <Button type="button" variant="outline" onClick={() => void loadCrawlerData()} disabled={crawlerLoading}>
                    <RefreshCcw className="mr-2 h-4 w-4" /> Thử lại
                  </Button>
                </div>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 p-3">
                <div>
                  <p className="font-medium">{crawlSummary?.pending_review_count ?? 0} đề xuất đang chờ quyết định</p>
                  <p className="text-sm text-muted-foreground">Đề xuất đến từ crawler dùng chung hoặc cán bộ. Không đề xuất nào tự được đưa vào kho.</p>
                </div>
                <Button type="button" variant="outline" onClick={() => runScan()} disabled={scanningSourceId !== null}>
                  <RefreshCcw className="mr-2 h-4 w-4" />
                  {scanningSourceId === '__all__' ? 'Đang kiểm tra...' : 'Kiểm tra nguồn'}
                </Button>
              </div>

              <CrawlerSourceManager
                sources={crawlerSources}
                scanningSourceId={scanningSourceId}
                onRefresh={loadCrawlerData}
                onScan={runScan}
                onCreate={async (payload) => {
                  try {
                    await legalImportApi.createCrawlSource(payload)
                    toast.success('Đã thêm nguồn mới ở trạng thái tắt.')
                    await loadCrawlerData()
                  } catch (error) {
                    toast.error(errorMessage(error))
                    throw error
                  }
                }}
                onUpdate={async (sourceId, payload) => {
                  try {
                    await legalImportApi.updateCrawlSource(sourceId, payload)
                    toast.success('Đã lưu thay đổi nguồn thu thập.')
                    await loadCrawlerData()
                  } catch (error) {
                    toast.error(errorMessage(error))
                    throw error
                  }
                }}
                onDelete={async (sourceId) => {
                  try {
                    await legalImportApi.deleteCrawlSource(sourceId)
                    toast.success('Đã xóa nguồn khỏi danh sách vận hành; lịch sử và ứng viên được giữ nguyên.')
                    await loadCrawlerData()
                  } catch (error) {
                    toast.error(errorMessage(error))
                    throw error
                  }
                }}
              />

              <div className="space-y-4">
                <div>
                  <h3 className="font-semibold">
                    Danh sách đề xuất ({visibleCandidates.length})
                  </h3>
                  {!importPipelineReady && importReadiness !== null && <p className="mt-1 text-sm text-amber-700">Hệ thống nhập kho chưa sẵn sàng nên nút Duyệt tạm thời bị khóa.</p>}
                </div>
                <div className="grid gap-3 rounded-lg border bg-muted/20 p-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
                  <div className="space-y-1.5">
                  <Label htmlFor="candidate-status">Danh sách</Label>
                  <Select value={candidateStatus} onValueChange={setCandidateStatus}>
                    <SelectTrigger id="candidate-status" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="pending">Cần duyệt</SelectItem>
                      <SelectItem value="needs_attention">Cần xử lý lỗi</SelectItem>
                      <SelectItem value="import_queued">Đang nhập kho</SelectItem>
                      <SelectItem value="rejected">Đã từ chối</SelectItem>
                      <SelectItem value="imported">Đã nhập kho</SelectItem>
                    </SelectContent>
                  </Select>
                  </div>
                  <div className="space-y-1.5">
                  <Label htmlFor="candidate-origin">Nguồn đề xuất</Label>
                  <Select value={candidateOrigin} onValueChange={setCandidateOrigin}>
                    <SelectTrigger id="candidate-origin" className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Tất cả</SelectItem>
                      <SelectItem value="crawler">Hệ thống tự thu thập</SelectItem>
                      <SelectItem value="officer">Cán bộ đề xuất</SelectItem>
                    </SelectContent>
                  </Select>
                  </div>
                  <Button type="button" variant="outline" onClick={() => void loadCrawlerData()} disabled={crawlerLoading}>
                    <RefreshCcw className={`mr-2 h-4 w-4 ${crawlerLoading ? 'animate-spin' : ''}`} />
                    {crawlerLoading ? 'Đang tải...' : 'Làm mới'}
                  </Button>
                </div>
                {visibleCandidates.length === 0 ? (
                  <div className="rounded-lg border border-dashed px-6 py-12 text-center">
                    <CheckCircle2 className="mx-auto h-9 w-9 text-emerald-600" />
                    <p className="mt-3 font-medium">Không có văn bản trong mục này</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {documentCandidates.length > 0
                        ? `Bộ lọc nguồn đang ẩn ${documentCandidates.length} văn bản trong trạng thái này.`
                        : 'Bạn có thể kiểm tra nguồn để tìm văn bản mới hoặc tự thêm một văn bản.'}
                    </p>
                    <div className="mt-4 flex flex-wrap justify-center gap-2">
                      {documentCandidates.length > 0 && candidateOrigin !== 'all' && (
                        <Button type="button" variant="outline" onClick={() => setCandidateOrigin('all')}>
                          Hiện tất cả nguồn
                        </Button>
                      )}
                      <Button type="button" variant="outline" onClick={() => runScan()} disabled={scanningSourceId !== null}>
                        <RefreshCcw className="mr-2 h-4 w-4" /> Kiểm tra nguồn
                      </Button>
                      <Button type="button" onClick={() => setActiveTab('import')}>
                        <FilePlus2 className="mr-2 h-4 w-4" /> Thêm văn bản
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {visibleCandidates.map((candidate) => (
                      <div key={candidate.id} className="rounded-lg border p-4">
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                          <div className="space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <h4 className="font-medium">{candidate.title}</h4>
                              <Badge variant={candidate.comparison_status === 'updated' ? 'secondary' : 'outline'}>
                                {comparisonStatusLabel(candidate.comparison_status)}
                              </Badge>
                              <Badge variant="secondary">{candidate.raw_metadata?.candidate_origin === 'officer_document_proposal' ? 'Cán bộ đề xuất' : 'Hệ thống thu thập'}</Badge>
                              {candidateNeedsOcr(candidate) && (
                                <Badge variant="destructive" className="bg-amber-500 hover:bg-amber-600 text-white border-none gap-1">
                                  <TriangleAlert className="h-3.5 w-3.5" />
                                  Tệp ảnh – cần nhận dạng chữ
                                </Badge>
                              )}
                            </div>
                            <p className="text-sm text-muted-foreground">
                              {candidate.law_number || 'Chưa có số, ký hiệu'} · {candidate.document_type || 'Chưa rõ loại văn bản'} · {candidate.scope || 'Chưa rõ phạm vi'}
                            </p>
                            <p className="text-sm text-muted-foreground">Cơ quan ban hành: {candidate.issuing_agency || 'Chưa có thông tin'}</p>
                            {candidate.description && (
                              <p className="text-sm text-muted-foreground line-clamp-3">{candidate.description}</p>
                            )}
                            {candidate.source_url && <p className="text-sm"><a className="text-primary underline" href={candidate.source_url} target="_blank" rel="noreferrer">Mở nguồn gốc</a></p>}
                            {(candidate.detected_change_details || []).length > 0 && <div className="rounded border bg-muted/20 p-3 text-xs"><b>So sánh cũ/mới</b><ul className="mt-2 list-disc space-y-1 pl-4">{candidate.detected_change_details?.map((change, index) => <li key={`${change.field}-${index}`}>{change.label}: <span className="text-red-700">{String(change.old_value ?? 'trống')}</span> → <span className="text-green-700">{String(change.new_value ?? 'trống')}</span></li>)}</ul></div>}
                            {candidate.extraction_result && (
                              <details className="mt-3 rounded-lg border bg-muted/20 p-3 text-xs">
                                <summary className="cursor-pointer font-medium">Xem nội dung hệ thống đã đọc</summary>
                                <div className="mt-3 space-y-2 border-t pt-3">
                                <div className="flex flex-wrap items-center gap-2 font-semibold">
                                  <span>Kết quả đọc tệp</span>
                                  <Badge variant="outline">{candidate.extraction_result.pdf_kind === 'scan' ? 'PDF dạng ảnh' : candidate.extraction_result.pdf_kind === 'text_based' ? 'PDF có chữ' : 'Tệp nguồn'}</Badge>
                                  <Badge variant={candidate.extraction_result.ocr_status === 'ok' || candidate.extraction_result.ocr_status === 'not_required' || candidate.extraction_result.ocr_status === 'not_applicable' ? 'outline' : 'destructive'}>
                                    {candidate.extraction_result.ocr_status === 'ok' ? 'Đã nhận dạng chữ' : candidate.extraction_result.ocr_status === 'not_required' || candidate.extraction_result.ocr_status === 'not_applicable' ? 'Không cần nhận dạng' : 'Cần kiểm tra nhận dạng chữ'}
                                  </Badge>
                                  {Number(candidate.extraction_result.characters || 0) <= 0 && candidate.extraction_result.preview?.trim() && (
                                  <Badge variant="secondary">Chưa có toàn văn</Badge>
                                  )}
                                </div>
                                <p className="text-muted-foreground">
                                  {candidate.extraction_result.page_count ?? 0} trang - {extractionCharacterSummary(candidate.extraction_result, candidate.content_characters)} - {candidate.extraction_result.language || 'không rõ ngôn ngữ'}
                                  {candidate.extraction_result.ocr_confidence != null ? ` - Độ chính xác nhận dạng ${Math.round(candidate.extraction_result.ocr_confidence)}%` : ''}
                                </p>
                                {candidate.extraction_result.reason && <p className="text-amber-700">{candidate.extraction_result.reason}</p>}
                                {candidate.extraction_result.preview && <p className="rounded bg-background p-2 text-muted-foreground line-clamp-4 whitespace-pre-wrap">{candidate.extraction_result.preview}</p>}
                                </div>
                              </details>
                            )}
                            {(candidate.status === 'pending' || candidate.status === 'changes_requested') && (
                              <details className="mt-3 rounded-lg border border-blue-200 bg-blue-50/40 p-3">
                                <summary className="cursor-pointer text-sm font-medium">Xem hoặc sửa thông tin chi tiết</summary>
                                <div className="mt-3 space-y-3 border-t border-blue-200 pt-3">
                                <div>
                                  <p className="text-xs text-muted-foreground">Đối chiếu với nguồn gốc, sửa thông tin nếu cần và chỉ xác nhận nguồn chính thức khi đã kiểm tra.</p>
                                </div>
                                <div className="grid gap-3 md:grid-cols-2">
                                  <div className="space-y-1 md:col-span-2"><Label>Tên văn bản</Label><Input value={candidateMetadataDraftFor(candidate).title || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'title', event.target.value)} /></div>
                                  <div className="space-y-1"><Label>Số, ký hiệu</Label><Input value={candidateMetadataDraftFor(candidate).law_number || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'law_number', event.target.value)} /></div>
                                  <div className="space-y-1"><Label>Loại văn bản</Label><Input value={candidateMetadataDraftFor(candidate).document_type || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'document_type', event.target.value)} /></div>
                                  <div className="space-y-1"><Label>Cơ quan ban hành</Label><Input value={candidateMetadataDraftFor(candidate).issuing_agency || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'issuing_agency', event.target.value)} /></div>
                                  <div className="space-y-1"><Label>Phạm vi</Label><Select value={candidateMetadataDraftFor(candidate).scope || 'central'} onValueChange={(value: 'central' | 'haiphong' | 'local') => updateCandidateMetadataDraft(candidate, 'scope', value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="central">Trung ương</SelectItem><SelectItem value="haiphong">Hải Phòng</SelectItem><SelectItem value="local">Phường/xã</SelectItem></SelectContent></Select></div>
                                  <div className="space-y-1"><Label>Ngày ban hành</Label><Input type="date" value={candidateMetadataDraftFor(candidate).issued_date || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'issued_date', event.target.value)} /></div>
                                  <div className="space-y-1"><Label>Ngày có hiệu lực</Label><Input type="date" value={candidateMetadataDraftFor(candidate).effective_date || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'effective_date', event.target.value)} /></div>
                                  <div className="space-y-1 md:col-span-2"><Label>URL nguồn chính thức</Label><Input type="url" value={candidateMetadataDraftFor(candidate).source_url || ''} onChange={(event) => updateCandidateMetadataDraft(candidate, 'source_url', event.target.value)} /></div>
                                </div>
                                <div className="flex items-start gap-2 rounded border bg-background p-3">
                                  <Checkbox id={`official-source-${candidate.id}`} checked={Boolean(candidateMetadataDraftFor(candidate).confirmed_official_source)} onCheckedChange={(checked) => updateCandidateMetadataDraft(candidate, 'confirmed_official_source', checked === true)} />
                                  <Label htmlFor={`official-source-${candidate.id}`} className="font-normal leading-5">Tôi đã mở và đối chiếu URL trên đúng cổng thông tin chính thức.</Label>
                                </div>
                                <Button type="button" variant="outline" onClick={() => void saveCandidateMetadata(candidate)} disabled={savingCandidateMetadataId === candidate.id}>
                                  <ShieldCheck className="mr-2 h-4 w-4" />{savingCandidateMetadataId === candidate.id ? 'Đang lưu...' : 'Lưu thông tin'}
                                </Button>
                                </div>
                              </details>
                            )}
                          </div>
                          <div className="flex w-full flex-col gap-2 lg:w-[360px]">
                            <CandidateLifecycleActions
                              candidate={candidate}
                              importReady={importPipelineReady}
                              busy={
                                reviewingCandidateId === candidate.id
                                || importingCandidateId === candidate.id
                                || savingCandidateMetadataId === candidate.id
                              }
                              onReview={(candidateId, decision) => void reviewCandidate(candidateId, decision)}
                              onRetryImport={(candidateId) => void retryCandidateImport(candidateId)}
                            />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 2: Quy trình PostgreSQL chuẩn duy nhất cho biểu mẫu */}
        <TabsContent value="forms">
          <FormGovernancePanel />
        </TabsContent>

        {/* Tab 3: Form nạp văn bản thủ công */}
        <TabsContent value="import">
          <form onSubmit={checkDocument} className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle><h2>Bước 1 — Lấy nội dung văn bản</h2></CardTitle>
                <CardDescription>Chọn một trong ba cách. Nội dung lấy được vẫn có thể chỉnh sửa trước khi gửi duyệt.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 lg:grid-cols-3">
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center gap-2 font-medium"><Link2 className="h-5 w-5 text-primary" /> Từ liên kết chính thức</div>
                  <p className="text-sm text-muted-foreground">Dán đường dẫn VBPL hoặc trang cơ quan nhà nước.</p>
                  <Label htmlFor="crawl-url">Đường dẫn văn bản</Label>
                  <Input
                    id="crawl-url"
                    type="url"
                    placeholder="https://..."
                    value={crawlUrl}
                    onChange={(event) => setCrawlUrl(event.target.value)}
                  />
                  <Button type="button" className="w-full" onClick={crawlPreview} disabled={crawling || !crawlUrl.trim()}>
                    {crawling ? <><RefreshCcw className="mr-2 h-4 w-4 animate-spin" /> Đang lấy nội dung...</> : 'Lấy nội dung từ liên kết'}
                  </Button>
                </div>
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center gap-2 font-medium"><FileUp className="h-5 w-5 text-primary" /> Từ tệp trên máy</div>
                  <p className="text-sm text-muted-foreground">Hỗ trợ Word, PDF, TXT, Markdown và JSON.</p>
                  <Label htmlFor="legal-file" className="flex min-h-24 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed p-4 text-center hover:bg-muted/50">
                    <Upload className="mb-2 h-6 w-6" />
                    <span className="text-sm font-medium">{fileName || 'Chọn tệp'}</span>
                    <span className="text-xs text-muted-foreground">Tối đa theo giới hạn máy chủ</span>
                  </Label>
                  <Input id="legal-file" className="hidden" type="file" accept=".txt,.md,.json,.docx,.pdf,text/plain,text/markdown,application/json,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={readFile} />
                  <details className="text-xs text-muted-foreground">
                    <summary className="cursor-pointer">Tùy chọn đọc tệp nâng cao</summary>
                    <div className="mt-2">
                      <Select value={fileExtractor} onValueChange={(value) => setFileExtractor(value as LegalImportExtractor)}>
                        <SelectTrigger aria-label="Cách đọc tệp"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="auto">Tự động (khuyên dùng)</SelectItem>
                          <SelectItem value="basic">Đọc nhanh</SelectItem>
                          <SelectItem value="rag_anything">Tệp phức tạp hoặc nhiều bảng</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </details>
                </div>
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center gap-2 font-medium"><ClipboardPaste className="h-5 w-5 text-primary" /> Dán nội dung</div>
                  <p className="text-sm text-muted-foreground">Dùng khi bạn đã có toàn văn và muốn nhập trực tiếp.</p>
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    onClick={() => document.getElementById('legal-content')?.focus()}
                  >
                    Mở ô nhập nội dung
                  </Button>
                  <div className="rounded-md bg-muted/40 p-3 text-xs text-muted-foreground">
                    {form.content.length > 0 ? `Đã có ${form.content.length.toLocaleString('vi-VN')} ký tự.` : 'Chưa có nội dung.'}
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle><h2>Bước 2 — Thông tin văn bản</h2></CardTitle>
                <CardDescription>
                  Nhập theo văn bản gốc. Hệ thống không tự đoán số hiệu, ngày hiệu lực hoặc cơ quan ban hành.
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
                  <CardTitle>Nội dung đã lấy</CardTitle>
                  <CardDescription>
                    Kiểm tra và sửa trực tiếp. Văn bản nên có các tiêu đề dạng &quot;Điều 1. ...&quot;.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Label htmlFor="legal-content">Toàn văn *</Label>
                  <Textarea
                    id="legal-content"
                    className="min-h-64 font-mono text-xs"
                    minLength={20}
                    placeholder={'Điều 1. Phạm vi điều chỉnh\nNội dung điều luật...'}
                    value={form.content}
                    onChange={(e) => update('content', e.target.value)}
                    required
                  />
                  <p className="text-xs text-muted-foreground">
                    {form.content.length.toLocaleString('vi-VN')} ký tự. Tối thiểu 20 ký tự và nên có tiêu đề &quot;Điều 1&quot;.
                  </p>
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
                    {checking ? 'Đang kiểm tra...' : 'Bước 3 — Kiểm tra trước khi gửi'}
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
                      {preview.article_count} điều, {preview.chunk_count} đoạn tra cứu. Văn bản chỉ vào kho sau khi được duyệt.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {preview.errors.map((error) => <p key={error} className="text-sm text-destructive">• {error}</p>)}
                    {preview.warnings.map((warning) => <p key={warning} className="text-sm text-amber-700">• {warning}</p>)}
                    <Button type="button" className="w-full" disabled={!canImport} onClick={importDocument}>
                      <DatabaseZap className="mr-2 h-4 w-4" />
                      {importing ? 'Đang gửi...' : 'Gửi vào danh sách chờ duyệt'}
                    </Button>
                  </CardContent>
                </Card>
              )}
            </div>
          </form>
        </TabsContent>
      </Tabs>
    </div>
      </div>
    </AppShell>
  )
}