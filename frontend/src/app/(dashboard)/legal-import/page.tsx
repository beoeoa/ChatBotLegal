'use client'

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  CheckCircle2,
  ClipboardPaste,
  DatabaseZap,
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
import { duplicateLabels } from '@/components/legal-import/CandidateDuplicateReview'
import { ImportSourceViewer, type ImportExtractionDetails } from '@/components/legal-import/ImportSourceViewer'
import { CandidateSourceViewer } from '@/components/legal-import/CandidateSourceViewer'
import {
  ImportPreview,
  LegalCrawlCandidate,
  LegalCandidateMetadataUpdate,
  LegalDuplicateResolutionRequest,
  CandidateExtractionResult,
  LegalCrawlSource,
  LegalCrawlSummary,
  LegalField,
  LegalImportExtractor,
  LegalImportReadiness,
  LegalImportPayload,
  legalImportApi,
} from '@/lib/api/legal-import'
import { formatApiError } from '@/lib/utils/error-handler'
import { extractionReasonCopy } from '@/lib/utils/crawler-copy'
import { useSettings } from '@/lib/hooks/use-settings'
import { activeDirectoryUnits, domainsForUnit, fieldForDomain } from '@/lib/utils/organization-directory'
import { detectLegalDocumentMetadata } from '@/lib/utils/legal-document-metadata'

const CANDIDATE_PAGE_SIZE = 20

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
  domain_slug: null,
  domain_codes: [],
  primary_organization_unit_id: null,
  organization_unit_ids: [],
  organization_assignment_state: 'unassigned',
}

function errorMessage(error: unknown): string {
  const detail = (error as {
    response?: { data?: { detail?: unknown } }
  })?.response?.data?.detail
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
      if (item.type === 'string_too_short') return `${label} quá ngắn.`
      if (item.type === 'missing') return `Vui lòng nhập ${label.toLocaleLowerCase('vi')}.`
      return `${label} chưa hợp lệ.`
    }).join(' ')
  }
  return formatApiError(error, 'Không thể hoàn tất thao tác. Vui lòng kiểm tra thông tin và thử lại.')
}

function proposerUnitLabel(candidate: LegalCrawlCandidate): string {
  const snapshot = candidate.raw_metadata?.submitter_organization_snapshot
  if (!snapshot || typeof snapshot !== 'object') return 'Chưa ghi nhận tại thời điểm đề xuất'
  const values = snapshot as Record<string, unknown>
  return String(values.department || values.organization_unit_id || 'Chưa phân công')
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
    new: 'Chưa tìm thấy bản trùng',
    updated: 'Khác bản đang có',
    unchanged: 'Không thay đổi',
    duplicate: 'Có thể trùng',
    ...duplicateLabels,
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
  const { data: settings } = useSettings()
  const organizationUnits = activeDirectoryUnits(settings)
  const searchParams = useSearchParams()
  const tabParam = searchParams.get('tab')
  const [activeTab, setActiveTab] = useState(tabParam === 'import' ? 'import' : 'proposals')
  const [form, setForm] = useState<LegalImportPayload>(initialForm)
  const [fields, setFields] = useState<LegalField[]>([])
  const [fieldQuery, setFieldQuery] = useState('')
  const importDomains = domainsForUnit(settings, form.primary_organization_unit_id)
  const importDomainOptions = importDomains.map(domain => ({
    ...domain,
    field: fieldForDomain(domain, fields),
  }))
  const selectedImportDomain = importDomainOptions.find(
    option => option.code === form.domain_slug,
  )
  const manualFieldChoices = useMemo(() => {
    const query = fieldQuery.trim().toLocaleLowerCase('vi')
    const selected = fields.find(field => field.id === form.field_id)
    const matching = fields
      .filter(field => !query || field.name.toLocaleLowerCase('vi').includes(query))
      .slice(0, 100)
    return selected && !matching.some(field => field.id === selected.id)
      ? [selected, ...matching]
      : matching
  }, [fieldQuery, fields, form.field_id])
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [sourceDetails, setSourceDetails] = useState<ImportExtractionDetails | null>(null)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [readingFile, setReadingFile] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const [crawlError, setCrawlError] = useState('')
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
  const [candidateType, setCandidateType] = useState('all')
  const [scanningSourceId, setScanningSourceId] = useState<string | null>(null)
  const [reviewingCandidateId, setReviewingCandidateId] = useState<string | null>(null)
  const [importingCandidateId, setImportingCandidateId] = useState<string | null>(null)
  const [resolvingCandidateId, setResolvingCandidateId] = useState<string | null>(null)
  const crawlerLoadSequence = useRef(0)
  const fileReadSequence = useRef(0)
  const [candidateStatus, setCandidateStatus] = useState('all')
  const [candidatePage, setCandidatePage] = useState(1)
  const [candidateTotal, setCandidateTotal] = useState(0)
  const [candidateMetadataDrafts, setCandidateMetadataDrafts] = useState<Record<string, LegalCandidateMetadataUpdate>>({})
  const [savingCandidateMetadataId, setSavingCandidateMetadataId] = useState<string | null>(null)
  const [importReadiness, setImportReadiness] = useState<LegalImportReadiness | null>(null)
  const [readinessUnavailable, setReadinessUnavailable] = useState(false)
  // These three reads share one cold database/projection path. A strict
  // 1.8-second abort turned a merely slow first load into an empty queue and
  // forced the administrator to click "Thử lại". Keep the request bounded,
  // but allow it to finish so the page remains functionally usable.
  const METADATA_TIMEOUT_MS = 15_000

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const requestedTab = params.get('tab')
    if (requestedTab && ['proposals', 'forms', 'import'].includes(requestedTab)) {
      setActiveTab(requestedTab)
    }
    const requestedStatus = params.get('status')
    if (requestedStatus && ['all', 'pending', 'rejected', 'import_queued', 'imported', 'needs_attention', 'duplicate_archived', 'replacement_review'].includes(requestedStatus)) {
      setCandidateStatus(requestedStatus)
    }
  }, [])

  useEffect(() => {
    legalImportApi.fields(METADATA_TIMEOUT_MS)
      .then(setFields)
      .catch((error) => toast.error(errorMessage(error)))
  }, [])

  const loadCrawlerData = useCallback(async () => {
    const sequence = ++crawlerLoadSequence.current
    setCrawlerLoading(true)
    setCrawlerLoadError(null)
    try {
      // Render each section as soon as it arrives. One unavailable section
      // must not hide successfully loaded source settings or review records.
      const results = await Promise.allSettled([
        legalImportApi.crawlSummary(METADATA_TIMEOUT_MS).then(summary => {
          if (sequence === crawlerLoadSequence.current) {
            setCrawlSummary(summary)
            setCrawlerSources(summary.sources || [])
          }
        }),
        legalImportApi.crawlCandidatePage(candidateStatus, CANDIDATE_PAGE_SIZE, (candidatePage - 1) * CANDIDATE_PAGE_SIZE, candidateType, candidateOrigin, METADATA_TIMEOUT_MS).then(page => {
          if (sequence !== crawlerLoadSequence.current) return
          setPendingCandidates(page.candidates)
          setCandidateTotal(page.total)
          const lastPage = Math.max(1, Math.ceil(page.total / CANDIDATE_PAGE_SIZE))
          if (candidatePage > lastPage) setCandidatePage(lastPage)
        }),
      ])
      if (sequence !== crawlerLoadSequence.current) return
      const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
      if (failed) throw failed.reason
      setLastRefreshedAt(new Date())
    } catch (error) {
      if (sequence !== crawlerLoadSequence.current) return
      const timedOut = (error instanceof DOMException && error.name === 'AbortError')
        || Boolean(error && typeof error === 'object' && 'code' in error && (error as { code?: string }).code === 'ECONNABORTED')
      const message = timedOut
        ? 'Máy chủ chưa trả thông tin văn bản trong thời gian chờ. Hãy thử lại; hệ thống chưa thay đổi dữ liệu.'
        : errorMessage(error)
      setCrawlerLoadError(message)
      toast.error(message)
    } finally {
      if (sequence === crawlerLoadSequence.current) setCrawlerLoading(false)
    }
  }, [candidateStatus, candidateType, candidateOrigin, candidatePage])

  useEffect(() => {
    void loadCrawlerData()
  }, [loadCrawlerData])

  useEffect(() => {
    // Fast refresh only while import work is queued; pause when the tab is hidden.
    if (candidateStatus !== 'import_queued') return

    const tick = () => {
      if (document.visibilityState === 'visible') void loadCrawlerData()
    }
    const intervalId = window.setInterval(tick, 5000)
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void loadCrawlerData()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [candidateStatus, loadCrawlerData])

  useEffect(() => {
    let mounted = true
    const loadImportReadiness = async () => {
      try {
        const report = await legalImportApi.importReadiness(METADATA_TIMEOUT_MS)
        if (mounted) {
          setImportReadiness(report)
          setReadinessUnavailable(false)
        }
      } catch {
        if (mounted) {
          setImportReadiness(null)
          setReadinessUnavailable(true)
        }
      }
    }
    void loadImportReadiness()

    // Match admin dashboard: respect visibility. Poll slower when idle/ready
    // (60s) and keep 15s while import work may still be pending.
    const idle =
      candidateStatus !== 'import_queued' &&
      importReadiness?.status === 'ready'
    const delayMs = idle ? 60_000 : 15_000
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadImportReadiness()
    }, delayMs)
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void loadImportReadiness()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      mounted = false
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [candidateStatus, importReadiness?.status])

  const canImport = useMemo(
    () => Boolean(preview?.valid && !checking && !importing),
    [preview, checking, importing]
  )
  const canQueueOcrCandidate = useMemo(
    () => Boolean(
      sourceFile
      && sourceDetails?.ocr_status === 'queued'
      && !form.content.trim()
      && form.title.trim().length >= 5
      && form.law_number.trim()
      && form.document_type.trim()
      && form.issuing_agency.trim()
      && form.effective_date
      && form.field_id > 0
      && !readingFile
      && !importing
    ),
    [form, importing, readingFile, sourceDetails?.ocr_status, sourceFile],
  )
  const importPipelineReady = importReadiness?.status === 'ready'
  const unhealthyCrawlerSources = useMemo(
    () => crawlerSources.filter((source) => {
      if (!source.enabled || source.source_kind === 'internal_queue') return false
      const status = String(source.last_status || '').toLocaleLowerCase('vi')
      return ['error', 'failed', 'failure'].includes(status) || Boolean(source.last_error)
    }),
    [crawlerSources],
  )
  const systemReady = importPipelineReady && unhealthyCrawlerSources.length === 0

  const visibleCandidates = pendingCandidates
  const pagedCandidates = pendingCandidates
  const candidatePageCount = Math.max(1, Math.ceil(candidateTotal / CANDIDATE_PAGE_SIZE))
  const changeCandidatePage = (page: number) => {
    setPendingCandidates([])
    setCandidatePage(page)
  }
  const changeCandidateFilter = (setter: (value: string) => void, value: string) => {
    setPendingCandidates([])
    setCandidatePage(1)
    setter(value)
  }

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
    assignment_state: candidate.assignment_state || 'unassigned',
    primary_organization_unit_id: candidate.primary_organization_unit_id || null,
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

  const updateCandidateAssignmentDraft = (
    candidate: LegalCrawlCandidate,
    value: string
  ) => {
    setCandidateMetadataDrafts((current) => ({
      ...current,
      [candidate.id]: {
        ...candidateMetadataDefaults(candidate),
        ...(current[candidate.id] || {}),
        assignment_state:
          value === '__shared__'
            ? 'shared'
            : value === '__unassigned__'
              ? 'unassigned'
              : 'assigned',
        primary_organization_unit_id:
          value.startsWith('__') ? null : value
      }
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
        toast.warning(`Đã lưu. Còn ${result.validation_errors.length} điều kiện đang được hệ thống kiểm tra khi nhập kho.`)
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
    if (!/\.(txt|md|json|doc|docx|pdf|xls|xlsx|png|jpg|jpeg|webp|bmp|tif|tiff)$/i.test(file.name)) {
      toast.error('Hỗ trợ TXT, MD, JSON, DOC, DOCX, PDF, XLS, XLSX và ảnh PNG, JPG, WEBP, BMP, TIFF.')
      event.target.value = ''
      return
    }
    const sequence = ++fileReadSequence.current
    let content = ''
    setReadingFile(true)
    setSourceFile(file)
    setFileName(file.name)
    setSourceDetails(null)
    setForm(initialForm)
    setCrawlUrl('')
    setCrawlError('')
    setPreview(null)
    setSourceError('')
    let details: ImportExtractionDetails | null = null
    if (/\.(doc|docx|pdf|xls|xlsx|png|jpg|jpeg|webp|bmp|tif|tiff)$/i.test(file.name)) {
      try {
        const extracted = await legalImportApi.extractFile(file, fileExtractor)
        if (sequence !== fileReadSequence.current) return
        content = extracted.content
        details = extracted
      } catch (error) {
        toast.error(errorMessage(error))
        setSourceDetails({complete: false})
        setSourceError('Chưa trích xuất được nội dung. Mở tệp gốc để đối chiếu; có thể dán bổ sung toàn văn.')
        event.target.value = ''
        setReadingFile(false)
        return
      }
    } else {
      try { content = await file.text() } catch (error) {
        toast.error(errorMessage(error))
        setReadingFile(false)
        event.target.value = ''
        return
      }
    }
    if (sequence !== fileReadSequence.current) return
    setFileName(file.name)
    setSourceFile(file)
    setSourceDetails(details)
    const detected = detectLegalDocumentMetadata(content, file.name)
    setForm(current => ({
      ...current,
      ...detected,
      content,
      source_url: '',
      applicability_info: '',
      uploaded_pdf_sha256: details?.file_fingerprint || null,
      confirmed_official_source: false,
    }))
    setPreview(null)

    const jobId = details?.extraction_job_id
    if (details?.ocr_status === 'queued' && jobId) {
      setSourceDetails({...details, extraction_status: 'processing', ocr_status: 'pending'})
      setSourceError(details.reason || 'Đang OCR các trang ảnh trong nền. Không cần gửi duyệt trước.')
      try {
        for (let attempt = 0; attempt < 360; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 1000))
          if (sequence !== fileReadSequence.current) return
          const job = await legalImportApi.extractionJob(jobId)
          if (job.extraction_status === 'processing') continue
          if (job.extraction_status === 'error') {
            throw new Error(job.error || 'OCR không hoàn tất.')
          }
          const completedContent = job.extracted_text?.trim() || content
          const completedDetails: ImportExtractionDetails = {
            ...details,
            ...job,
            complete: job.extraction_status === 'complete',
            deferred_ocr: false,
            extraction_status: job.extraction_status,
            ocr_status: job.extraction_status === 'complete' ? 'ok' : 'partial',
            reason: job.extraction_status === 'complete'
              ? 'Đã đọc xong toàn bộ tài liệu.'
              : 'Đã đọc một phần; còn trang cần đối chiếu thủ công.',
          }
          setSourceDetails(completedDetails)
          setForm(current => ({
            ...current,
            content: completedContent,
            uploaded_pdf_sha256: details?.file_fingerprint || current.uploaded_pdf_sha256,
          }))
          if (job.extraction_status === 'complete') {
            setSourceError('')
            toast.success(`Đã đọc xong ${job.processed_pages || job.page_count || ''}/${job.total_pages || job.page_count || ''} trang PDF.`)
          } else {
            setSourceError('OCR chỉ hoàn tất một phần. Hãy mở toàn văn để kiểm tra các trang còn thiếu.')
            toast.warning('Tệp đã được đọc một phần; còn trang cần đối chiếu thủ công.')
          }
          setReadingFile(false)
          return
        }
        throw new Error('OCR quá thời gian chờ 6 phút. Tác vụ vẫn được giữ để kiểm tra lại.')
      } catch (error) {
        if (sequence !== fileReadSequence.current) return
        setSourceDetails(current => ({...current, complete: false, deferred_ocr: false, extraction_status: 'error', ocr_status: 'failed'}))
        setSourceError(errorMessage(error))
        toast.error(errorMessage(error))
        setReadingFile(false)
        return
      }
    }
    if (details?.ocr_status === 'queued') {
      setSourceError('Chưa tạo được tác vụ OCR nền. Hãy chọn lại tệp hoặc thử lại.')
    } else {
      setSourceError('')
    }
    setReadingFile(false)
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
      await legalImportApi.importDocument(form, sourceFile)
      toast.success(
        'Đã gửi văn bản vào danh sách chờ duyệt.'
      )
      setForm(initialForm)
      setFileName('')
      setSourceFile(null)
      setSourceDetails(null)
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
    setCrawlError('')
    setSourceError('')
    update('source_url', crawlUrl.trim())
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
        uploaded_pdf_sha256: null,
      }))
      setSourceFile(null)
      setFileName(result.title || crawlUrl.trim())
      setSourceDetails(null)
      setPreview(null)
      toast.success(`Đã lấy ${result.characters.toLocaleString('vi-VN')} ký tự${detectedCount > 0 ? ` và điền ${detectedCount} thông tin có nhãn rõ` : ''}. Hãy đối chiếu lại với văn bản gốc.`)
    } catch (error) {
      setCrawlError('Chưa lấy được toàn văn từ liên kết này. Đường dẫn đã được giữ lại; bạn có thể tải tệp gốc hoặc dán nội dung để tiếp tục gửi duyệt. ' + errorMessage(error))
      toast.error(errorMessage(error))
    } finally {
      setCrawling(false)
    }
  }

  const runScan = async (sourceId?: string) => {
    setScanningSourceId(sourceId || '__all__')
    try {
      const result = await legalImportApi.scanNow(sourceId)
      const summary = `${result.created} văn bản mới, ${result.updated} văn bản có cập nhật`
      const firstError = result.errors[0]?.message
      if (result.status === 'failed') {
        toast.error(formatApiError(firstError, 'Quét không hoàn tất. Hãy kiểm tra trạng thái nguồn và thử lại.'))
      } else if (result.status === 'busy') {
        toast.warning('Nguồn đang được quét bởi một tác vụ khác. Không tạo thêm phiên quét trùng.')
      } else if (result.status === 'partial' || result.status === 'completed_with_warnings') {
        toast.warning(
          `Quét có cảnh báo: ${summary}; ${result.failed_count} nguồn lỗi, ${result.warning_count} nguồn có bản ghi lỗi.`
        )
      } else if (result.status === 'skipped') {
        toast.info('Không có nguồn web nào cần quét hoặc các nguồn đang tạm dừng.')
      } else {
        toast.success(`Kiểm tra xong: ${summary}.`)
      }
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
      const result = await legalImportApi.reviewCandidate(
        candidateId,
        decision,
        decision === 'rejected'
          ? 'Quản trị viên xác nhận không đưa bản ghi này vào kho tra cứu.'
          : '',
      )
      if (decision === 'approved' && result.import_status === 'duplicate_conflict') {
        toast.warning('Chưa nhập kho: phát hiện cùng định danh. Cần đối chiếu bản trùng hoặc bản thay thế.')
        setCandidateStatus('needs_attention')
      } else if (decision === 'approved' && result.status !== 'import_queued') {
        toast.warning('Chưa xếp hàng nhập kho. Hãy kiểm tra thông tin cần bổ sung.')
        setCandidateStatus('needs_attention')
      } else {
        toast.success(decision === 'approved' ? 'Đã duyệt. Văn bản đang được nhập kho.' : 'Đã không duyệt; văn bản không đưa vào tra cứu.')
      }
      if (decision === 'approved' && result.status === 'import_queued') setCandidateStatus('import_queued')
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
        setCandidateStatus('needs_attention')
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

  const resolveDuplicate = async (candidateId: string, payload: LegalDuplicateResolutionRequest) => {
    setResolvingCandidateId(candidateId)
    try {
      await legalImportApi.resolveDuplicate(candidateId, payload)
      // Remove only after the server confirms; invalidate any older list fetch.
      ++crawlerLoadSequence.current
      setPendingCandidates(current => current.filter(item => item.id !== candidateId))
      toast.success(payload.action === 'archive_duplicate'
        ? 'Đã lưu trữ bản trùng. Văn bản và dữ liệu tra cứu trong kho được giữ nguyên.'
        : 'Đã chuyển sang đối chiếu thay thế. Văn bản trong kho chưa thay đổi.')
      await loadCrawlerData()
    } catch (error) {
      throw new Error(errorMessage(error))
    } finally {
      setResolvingCandidateId(null)
    }
  }

  return (
    <AppShell>
      <div aria-busy={activeTab === 'proposals' && crawlerLoading} className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 sm:p-6 md:pb-16">
        <div className="mx-auto w-full min-w-0 max-w-7xl space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-3 text-xl md:text-2xl font-bold">
                <DatabaseZap className="h-8 w-8 text-primary" />
                Tiếp nhận và duyệt văn bản
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Nhận dữ liệu từ liên kết hoặc tệp, đối chiếu và duyệt trước khi lập chỉ mục vào kho tra cứu.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant={systemReady ? 'outline' : 'secondary'}>
                  <Activity className="mr-1 h-3 w-3" />
                  {importReadiness === null
                    ? readinessUnavailable ? 'Chưa kiểm tra được hệ thống nhập kho' : 'Đang kiểm tra hệ thống'
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
        <TabsList className="grid h-auto w-full grid-cols-1 gap-1 p-1 sm:grid-cols-2">
          <TabsTrigger value="proposals" className="gap-2">
            <Inbox className="h-4 w-4" />
            Đề xuất chờ duyệt ({crawlSummary?.pending_review_count ?? 0})
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
                onPreview={async (payload) => {
                  try {
                    return await legalImportApi.previewCrawlSource(payload)
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
                    Danh sách đề xuất ({candidateTotal})
                  </h3>
                  <p className="mt-1 text-sm text-muted-foreground">Quản trị viên là người duyệt cuối. Sau khi duyệt, hệ thống tự đưa văn bản vào hàng nhập kho và cập nhật kết quả tại mục Đã nhập kho.</p>
                </div>
                <div className="grid gap-3 rounded-lg border bg-muted/20 p-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_auto] xl:items-end">
                  <div className="space-y-1.5">
                  <Label htmlFor="candidate-status">Danh sách</Label>
                  <Select value={candidateStatus} onValueChange={value => changeCandidateFilter(setCandidateStatus, value)}>
                    <SelectTrigger id="candidate-status" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Tất cả trạng thái</SelectItem>
                      <SelectItem value="pending">Chờ duyệt</SelectItem>
                      <SelectItem value="rejected">Không duyệt</SelectItem>
                      <SelectItem value="import_queued">Đang nhập kho</SelectItem>
                      <SelectItem value="imported">Đã nhập kho</SelectItem>
                      <SelectItem value="needs_attention">Cần xử lý / đối chiếu</SelectItem>
                      <SelectItem value="duplicate_archived">Đã lưu trữ trùng</SelectItem>
                      <SelectItem value="replacement_review">Chờ đối chiếu thay thế</SelectItem>
                    </SelectContent>
                  </Select>
                  </div>
                  <div className="space-y-1.5">
                  <Label htmlFor="candidate-type">Loại dữ liệu</Label>
                  <Select value={candidateType} onValueChange={value => changeCandidateFilter(setCandidateType, value)}>
                    <SelectTrigger id="candidate-type" className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Tất cả loại dữ liệu</SelectItem>
                      <SelectItem value="document">Văn bản pháp luật</SelectItem>
                      <SelectItem value="procedure">Thủ tục hành chính</SelectItem>
                      <SelectItem value="form">Biểu mẫu</SelectItem>
                      <SelectItem value="reference">Tài liệu tham khảo</SelectItem>
                      <SelectItem value="unclassified">Chưa phân loại</SelectItem>
                    </SelectContent>
                  </Select>
                  </div>
                  <div className="space-y-1.5">
                  <Label htmlFor="candidate-origin">Nguồn đề xuất</Label>
                  <Select value={candidateOrigin} onValueChange={value => changeCandidateFilter(setCandidateOrigin, value)}>
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
                {visibleCandidates.length === 0 && (crawlerLoading || crawlerLoadError) ? (
                  <p role="status" className="rounded-lg border p-6 text-sm text-muted-foreground">
                    {crawlerLoading ? 'Đang tải hàng chờ văn bản…' : 'Chưa tải được hàng chờ. Không thể kết luận danh sách đang trống; hãy bấm Làm mới.'}
                  </p>
                ) : visibleCandidates.length === 0 ? (
                  <div className="rounded-lg border border-dashed px-6 py-12 text-center">
                    <CheckCircle2 className="mx-auto h-9 w-9 text-emerald-600" />
                    <p className="mt-3 font-medium">Không có dữ liệu trong mục này</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {pendingCandidates.length > 0
                        ? `Bộ lọc nguồn đang ẩn ${pendingCandidates.length} bản ghi trong trạng thái và loại dữ liệu này.`
                        : 'Bạn có thể kiểm tra nguồn để tìm văn bản mới hoặc tự thêm một văn bản.'}
                    </p>
                    <div className="mt-4 flex flex-wrap justify-center gap-2">
                      {pendingCandidates.length > 0 && candidateOrigin !== 'all' && (
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
                    {pagedCandidates.map((candidate) => (
                      <div key={candidate.id} className="rounded-lg border p-4">
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                          <div className="min-w-0 flex-1 space-y-2 break-words">
                            <div className="flex flex-wrap items-center gap-2">
                              <h4 className="font-medium">{candidate.title}</h4>
                              <Badge variant={candidate.comparison_status === 'updated' ? 'secondary' : 'outline'}>
                                {comparisonStatusLabel(candidate.comparison_status)}
                              </Badge>
                              <Badge variant="secondary">{candidate.raw_metadata?.candidate_origin === 'officer_document_proposal' ? 'Cán bộ đề xuất' : 'Hệ thống thu thập'}</Badge>
                              {candidate.source_type === 'form' && (
                                <Badge variant="outline">Biểu mẫu — quản lý ở mục Biểu mẫu</Badge>
                              )}
                              {candidate.source_type === 'procedure' && (
                                <Badge variant="outline">Thủ tục — chưa vào kho văn bản</Badge>
                              )}
                              {candidate.source_type === 'reference' && (
                                <Badge variant="outline">Tài liệu tham khảo — không phải căn cứ pháp lý</Badge>
                              )}
                              {!candidate.source_type && (
                                <Badge variant="outline">Chưa phân loại</Badge>
                              )}
                              {candidateNeedsOcr(candidate) && (
                                <Badge variant="destructive" className="bg-amber-500 hover:bg-amber-600 text-white border-none gap-1">
                                  <TriangleAlert className="h-3.5 w-3.5" />
                                  Tệp ảnh – cần nhận dạng chữ
                                </Badge>
                              )}
                            </div>
                            <p className="text-sm text-muted-foreground">
                              {candidate.source_type === 'procedure'
                                ? String(candidate.raw_metadata?.procedure_code || 'Chưa có mã thủ tục')
                                : candidate.law_number || 'Chưa có số, ký hiệu'} · {candidate.document_type || 'Chưa rõ loại văn bản'} · {candidate.scope || 'Chưa rõ phạm vi'}
                            </p>
                            <p className="text-sm text-muted-foreground">Cơ quan ban hành: {candidate.issuing_agency || 'Chưa có thông tin'}</p>
                            {candidate.raw_metadata?.candidate_origin === 'officer_document_proposal' && <p className="text-sm text-muted-foreground">Đơn vị đề xuất: {proposerUnitLabel(candidate)}</p>}
                            <p className="text-sm text-muted-foreground">Đơn vị chịu trách nhiệm: {candidate.assignment_state === 'shared' ? 'Dùng chung' : settings?.organization_units?.find(unit => unit.id === candidate.primary_organization_unit_id)?.name || candidate.primary_organization_unit_id || 'Chưa phân công'}</p>
                            {candidate.description && (
                              <p className="text-sm text-muted-foreground line-clamp-3">{candidate.description}</p>
                            )}
                            {candidate.source_url && <p className="text-sm"><a className="text-primary underline" href={candidate.source_url} target="_blank" rel="noreferrer">Mở nguồn gốc</a></p>}
                            <CandidateSourceViewer id={candidate.id} title={candidate.title} />
                            {(candidate.detected_change_details || []).length > 0 && <div className="rounded border bg-muted/20 p-3 text-xs"><b>So sánh cũ/mới</b><ul className="mt-2 list-disc space-y-1 pl-4">{candidate.detected_change_details?.map((change, index) => <li key={`${change.field}-${index}`}>{change.label}: <span className="text-red-700">{String(change.old_value ?? 'trống')}</span> → <span className="text-green-700">{String(change.new_value ?? 'trống')}</span></li>)}</ul></div>}
                            {candidate.extraction_result && (
                              <details className="mt-3 rounded-lg border bg-muted/20 p-3 text-xs">
                                <summary className="cursor-pointer font-medium">Xem nội dung hệ thống đã đọc</summary>
                                <div className="mt-3 space-y-2 border-t pt-3">
                                <div className="flex flex-wrap items-center gap-2 font-semibold">
                                  <span>Kết quả đọc tệp</span>
                                  <Badge variant="outline">{candidate.extraction_result.pdf_kind === 'scan' ? 'PDF dạng ảnh' : candidate.extraction_result.pdf_kind === 'hybrid' ? 'PDF kết hợp chữ + ảnh' : candidate.extraction_result.pdf_kind === 'text_based' ? 'PDF có chữ' : 'Tệp nguồn'}</Badge>
                                  <Badge variant={candidate.extraction_result.ocr_status === 'ok' || candidate.extraction_result.ocr_status === 'not_required' || candidate.extraction_result.ocr_status === 'not_applicable' ? 'outline' : 'destructive'}>
                                    {candidate.extraction_result.ocr_status === 'ok' ? 'Đã nhận dạng chữ' : candidate.extraction_result.ocr_status === 'not_required' || candidate.extraction_result.ocr_status === 'not_applicable' ? 'Không cần nhận dạng' : 'Cần kiểm tra nhận dạng chữ'}
                                  </Badge>
                                  {Number(candidate.extraction_result.characters || 0) <= 0 && candidate.extraction_result.preview?.trim() && (
                                  <Badge variant="secondary">Chưa có toàn văn</Badge>
                                  )}
                                </div>
                                <p className="text-muted-foreground">
                                  {candidate.extraction_result.processed_pages ?? candidate.extraction_result.page_count ?? 0}/{candidate.extraction_result.total_pages ?? candidate.extraction_result.page_count ?? 0} trang
                                  {candidate.extraction_result.coverage_percent != null ? ` · phủ ${candidate.extraction_result.coverage_percent}%` : ''} - {extractionCharacterSummary(candidate.extraction_result, candidate.content_characters)} - {candidate.extraction_result.language || 'không rõ ngôn ngữ'}
                                  {candidate.extraction_result.ocr_confidence != null ? ` - Độ chính xác nhận dạng ${Math.round(candidate.extraction_result.ocr_confidence)}%` : ''}
                                </p>
                                {(candidate.extraction_result.native_text_pages?.length || candidate.extraction_result.ocr_pages?.length) && (
                                  <p className="text-muted-foreground">
                                    {candidate.extraction_result.native_text_pages?.length ? `Lớp chữ gốc: trang ${candidate.extraction_result.native_text_pages.join(', ')}` : ''}
                                    {candidate.extraction_result.native_text_pages?.length && candidate.extraction_result.ocr_pages?.length ? ' · ' : ''}
                                    {candidate.extraction_result.ocr_pages?.length ? `Nhận dạng ảnh: trang ${candidate.extraction_result.ocr_pages.join(', ')}` : ''}
                                  </p>
                                )}
                                {(candidate.extraction_result.table_count != null || candidate.extraction_result.table_extracted_count != null) && (
                                  <p className="text-muted-foreground">Bảng phát hiện: {candidate.extraction_result.table_count ?? 0} · bảng đã trích cấu trúc: {candidate.extraction_result.table_extracted_count ?? 0}</p>
                                )}
                                {candidate.extraction_result.reason && <p className="text-amber-700">{extractionReasonCopy(candidate.extraction_result.reason)}</p>}
                                {candidate.extraction_result.preview && <p className="rounded bg-background p-2 text-muted-foreground line-clamp-4 whitespace-pre-wrap">{candidate.extraction_result.preview}</p>}
                                </div>
                              </details>
                            )}
                            {['pending', 'changes_requested', 'approved', 'import_failed', 'replacement_review'].includes(candidate.status) && (
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
                                  <div className="space-y-1">
                                    <Label>Phòng ban tiếp nhận</Label>
                                    <Select
                                      value={
                                        candidateMetadataDraftFor(candidate).assignment_state === 'shared'
                                          ? '__shared__'
                                          : candidateMetadataDraftFor(candidate).primary_organization_unit_id || '__unassigned__'
                                      }
                                      onValueChange={(value) => updateCandidateAssignmentDraft(candidate, value)}
                                    >
                                      <SelectTrigger><SelectValue /></SelectTrigger>
                                      <SelectContent>
                                        <SelectItem value="__unassigned__">Chưa phân công — chưa thể duyệt</SelectItem>
                                        <SelectItem value="__shared__">Dùng chung toàn hệ thống</SelectItem>
                                        {organizationUnits.map((unit) => (
                                          <SelectItem key={unit.id} value={unit.id}>{unit.name}</SelectItem>
                                        ))}
                                      </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">
                                      Chọn phòng ban chủ trì hoặc xác nhận tài liệu dùng chung trước khi duyệt.
                                    </p>
                                  </div>
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
                          <div className="flex w-full min-w-0 shrink-0 flex-col gap-2 lg:w-[360px]">
                            {candidate.source_type === 'form' ? (
                              <div className="rounded-lg border bg-muted/30 p-4 text-sm">
                                <p className="font-medium">Biểu mẫu được hiển thị để đối chiếu</p>
                                <p className="mt-2 text-muted-foreground">Biểu mẫu không đi qua luồng nhập kho văn bản pháp luật. Mở mục Biểu mẫu để xử lý phát hành và liên kết thủ tục.</p>
                                <Button asChild type="button" variant="outline" className="mt-3 min-h-11">
                                  <Link href="/procedure-management">Mở quản lý biểu mẫu</Link>
                                </Button>
                              </div>
                            ) : candidate.source_type === 'procedure' ? (
                              <div className="rounded-lg border bg-muted/30 p-4 text-sm"><p className="font-medium">Thủ tục hành chính</p><p className="mt-2 text-muted-foreground">Đã nhận nguồn thủ tục. Mở kho thủ tục để đối chiếu mã, hồ sơ và cơ quan thực hiện.</p><Link className="mt-3 inline-flex min-h-11 items-center rounded border px-3 font-medium" href={`/procedures?q=${encodeURIComponent(candidate.law_number || candidate.title)}`}>Mở kho thủ tục để đối chiếu</Link></div>
                            ) : candidate.source_type === 'reference' ? (
                              <div className="rounded-lg border bg-muted/30 p-4 text-sm">
                                <p className="font-medium">Chỉ dùng làm tài liệu tham khảo</p>
                                <p className="mt-2 text-muted-foreground">Không thể duyệt vào kho căn cứ pháp luật hiện hành.</p>
                              </div>
                            ) : <CandidateLifecycleActions
                              candidate={candidate}
                              busy={
                                reviewingCandidateId === candidate.id
                                || importingCandidateId === candidate.id
                                || savingCandidateMetadataId === candidate.id
                                || resolvingCandidateId === candidate.id
                              }
                              onReview={(candidateId, decision) => void reviewCandidate(candidateId, decision)}
                              onRetryImport={(candidateId) => void retryCandidateImport(candidateId)}
                              onResolveDuplicate={resolveDuplicate}
                            />}
                          </div>
                        </div>
                      </div>
                    ))}
                    {candidatePageCount > 1 && (
                      <div className="flex items-center justify-between gap-3 rounded-lg border bg-muted/20 px-3 py-2 text-sm">
                        <span className="text-muted-foreground">
                          Trang {candidatePage} / {candidatePageCount} · tổng {candidateTotal} bản ghi · tối đa {CANDIDATE_PAGE_SIZE} bản ghi mỗi trang
                        </span>
                        <div className="flex gap-2">
                          <Button type="button" variant="outline" size="sm" onClick={() => changeCandidatePage(Math.max(1, candidatePage - 1))} disabled={crawlerLoading || candidatePage <= 1}>
                            Trang trước
                          </Button>
                          <Button type="button" variant="outline" size="sm" onClick={() => changeCandidatePage(Math.min(candidatePageCount, candidatePage + 1))} disabled={crawlerLoading || candidatePage >= candidatePageCount}>
                            Trang sau
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 2: Form nạp văn bản thủ công */}
        <TabsContent value="import">
          <form onSubmit={checkDocument} className="grid items-start gap-6 lg:grid-cols-[1.15fr_1fr]">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle><h2>Bước 1 — Lấy nội dung văn bản</h2></CardTitle>
                <CardDescription>Chọn một trong ba cách. Nội dung lấy được vẫn có thể chỉnh sửa trước khi gửi duyệt.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 lg:grid-cols-3">
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center gap-2 font-medium"><Link2 className="h-5 w-5 text-primary" /> Từ liên kết</div>
                  <p className="text-sm text-muted-foreground">Nhập liên kết nguồn. Nếu trang không cho lấy nội dung, dùng tệp gốc hoặc dán toàn văn.</p>
                  <Label htmlFor="crawl-url">Đường dẫn văn bản</Label>
                  <Input
                    id="crawl-url"
                    type="url"
                    placeholder="https://..."
                    value={crawlUrl}
                    onChange={(event) => setCrawlUrl(event.target.value)}
                  />
                  <Button type="button" className="w-full" onClick={crawlPreview} disabled={crawling || readingFile || importing || !crawlUrl.trim()}>
                    {crawling ? <><RefreshCcw className="mr-2 h-4 w-4 animate-spin" /> Đang lấy nội dung...</> : 'Lấy nội dung từ liên kết'}
                  </Button>
                  {crawlError && <p role="alert" className="text-sm text-amber-700">{crawlError}</p>}
                </div>
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center gap-2 font-medium"><FileUp className="h-5 w-5 text-primary" /> Từ tệp trên máy</div>
                  <p className="text-sm text-muted-foreground">Hỗ trợ DOC, DOCX, PDF, TXT, XLS, XLSX và ảnh. PDF đọc lớp chữ trước; trang ảnh xử lý nền.</p>
                  <Label htmlFor="legal-file" className="flex min-h-24 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed p-4 text-center hover:bg-muted/50">
                    <Upload className="mb-2 h-6 w-6" />
                    <span className="text-sm font-medium">{readingFile ? 'Đang đọc toàn bộ tệp…' : fileName || 'Chọn tệp'}</span>
                    <span className="text-xs text-muted-foreground">Tối đa theo giới hạn máy chủ</span>
                  </Label>
                  <Input id="legal-file" className="hidden" type="file" disabled={readingFile || crawling || importing} accept=".txt,.md,.json,.doc,.docx,.pdf,.xls,.xlsx,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,text/plain,text/markdown,application/json,application/pdf,application/msword,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={readFile} />
                  {sourceError && <p role={sourceDetails?.extraction_status === 'processing' ? 'status' : 'alert'} aria-live="polite" className="text-sm text-amber-700">{sourceError}</p>}
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
                    onClick={() => setSourceOpen(true)}
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
                    <SelectTrigger aria-label="Phạm vi áp dụng"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="Trung ương - toàn quốc">Trung ương - toàn quốc</SelectItem>
                      <SelectItem value="Thành phố Hải Phòng">Thành phố Hải Phòng</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Lĩnh vực *</Label>
                  <Select
                    value={form.domain_slug || ''}
                    onValueChange={(value) => {
                      const option = importDomainOptions.find(item => item.code === value)
                      if (!option) return
                      setFieldQuery('')
                      setForm(current => ({
                        ...current,
                        domain_slug: value,
                        domain_codes: [value],
                        field_id: option.field?.id || 0,
                      }))
                    }}
                  >
                    <SelectTrigger aria-label="Lĩnh vực"><SelectValue placeholder="Chọn lĩnh vực" /></SelectTrigger>
                    <SelectContent>
                      {importDomainOptions.map((option) => (
                        <SelectItem key={option.code} value={option.code}>
                          {option.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedImportDomain && !selectedImportDomain.field && (
                    <p className="text-xs text-amber-700">
                      Đây là lĩnh vực quản lý mới. Hãy chọn chủ đề dữ liệu pháp lý cụ thể của văn bản ở mục bên dưới.
                    </p>
                  )}
                </div>
                {selectedImportDomain && !selectedImportDomain.field && (
                  <div className="space-y-2 sm:col-span-2 rounded-lg border border-amber-300/70 bg-amber-50/60 p-3">
                    <Label htmlFor="legal-field-search">Chủ đề dữ liệu pháp lý cụ thể *</Label>
                    <Input
                      id="legal-field-search"
                      value={fieldQuery}
                      onChange={(event) => setFieldQuery(event.target.value)}
                      placeholder="Gõ để lọc, ví dụ: kinh tế xây dựng, công thương, tài chính…"
                      aria-describedby="legal-field-help"
                    />
                    <select
                      aria-label="Chủ đề dữ liệu pháp lý cụ thể"
                      className="h-11 w-full rounded-md border bg-background px-3 text-sm"
                      value={form.field_id > 0 ? String(form.field_id) : ''}
                      onChange={(event) => update('field_id', Number(event.target.value) || 0)}
                      required
                    >
                      <option value="">Chọn đúng chủ đề của văn bản</option>
                      {manualFieldChoices.map(field => (
                        <option key={field.id} value={field.id}>{field.name}</option>
                      ))}
                    </select>
                    <p id="legal-field-help" className="text-xs text-muted-foreground">
                      “{selectedImportDomain.name}” vẫn là lĩnh vực dùng để phân công và giới hạn chatbot; chủ đề này chỉ là khóa lưu trữ tương thích của chính văn bản đang nhập.
                      {!fieldQuery.trim() && fields.length > 100 ? ' Gõ từ khóa để lọc trong toàn bộ danh mục.' : ''}
                    </p>
                  </div>
                )}
                <div className="space-y-2">
                  <Label>Phòng ban tiếp nhận *</Label>
                  <Select
                    value={
                      form.organization_assignment_state === 'shared'
                        ? '__shared__'
                        : form.primary_organization_unit_id || '__unassigned__'
                    }
                    onValueChange={(value) => {
                      if (value === '__shared__') {
                        setForm((current) => ({
                          ...current,
                          organization_assignment_state: 'shared',
                          primary_organization_unit_id: null,
                          organization_unit_ids: [],
                          domain_slug: null,
                          domain_codes: [],
                          field_id: 0,
                        }))
                      } else if (value === '__unassigned__') {
                        setForm((current) => ({
                          ...current,
                          organization_assignment_state: 'unassigned',
                          primary_organization_unit_id: null,
                          organization_unit_ids: [],
                          domain_slug: null,
                          domain_codes: [],
                          field_id: 0,
                        }))
                      } else {
                        setForm((current) => ({
                          ...current,
                          organization_assignment_state: 'assigned',
                          primary_organization_unit_id: value,
                          organization_unit_ids: [value],
                          domain_slug: null,
                          domain_codes: [],
                          field_id: 0
                        }))
                      }
                      setPreview(null)
                    }}
                  >
                    <SelectTrigger aria-label="Phòng ban tiếp nhận"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__unassigned__">Chưa phân công</SelectItem>
                      <SelectItem value="__shared__">Dùng chung toàn hệ thống</SelectItem>
                      {organizationUnits.map((unit) => (
                        <SelectItem key={unit.id} value={unit.id}>{unit.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Văn bản chưa phân công sẽ được giữ ở hàng chờ và chưa thể duyệt khi hệ thống vận hành theo phòng ban.
                  </p>
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
                  <ImportSourceViewer name={fileName || form.title} file={sourceFile} content={form.content} details={sourceDetails} onChange={text => update('content', text)} open={sourceOpen} onOpenChange={setSourceOpen} />
                  <div className="flex items-start gap-3 rounded-lg border p-3">
                    <Checkbox
                      id="official-source"
                      checked={form.confirmed_official_source}
                      onCheckedChange={(checked) => update('confirmed_official_source', checked === true)}
                    />
                    <Label htmlFor="official-source" className="leading-5">
                      Tôi đã đối chiếu nội dung với liên kết hoặc bản gốc. Có thể để người duyệt xác nhận sau.
                    </Label>
                  </div>
                  <Button type="submit" className="w-full" disabled={checking || importing || readingFile || crawling}>
                    <ShieldCheck className="mr-2 h-4 w-4" />
                    {checking ? 'Đang kiểm tra...' : 'Bước 3 — Kiểm tra trước khi gửi'}
                  </Button>
                  {sourceFile && sourceDetails?.ocr_status === 'queued' && !form.content.trim() && (
                    <div role="status" className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
                      <p>
                        Tệp chưa có lớp chữ để kiểm tra ngay. Điền đủ thông tin văn bản rồi gửi tệp vào hàng chờ; OCR sẽ chạy nền và kết quả vẫn phải được duyệt trước khi nhập kho.
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        className="w-full bg-background"
                        disabled={!canQueueOcrCandidate}
                        onClick={importDocument}
                      >
                        <DatabaseZap className="mr-2 h-4 w-4" />
                        {importing ? 'Đang gửi...' : 'Gửi tệp vào hàng chờ OCR và duyệt'}
                      </Button>
                    </div>
                  )}
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
