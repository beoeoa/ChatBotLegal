'use client'

import { FormEvent, useMemo, useRef, useState } from 'react'
import { ChevronDown, Plus, RefreshCcw, Save, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  CrawlSourceCreatePayload,
  CrawlSourcePreview,
  LegalCrawlSource,
} from '@/lib/api/legal-import'
import { crawlerCandidateTypeCopy, crawlerErrorCopy } from '@/lib/utils/crawler-copy'
import { useSettings } from '@/lib/hooks/use-settings'

type SourceUpdate = Partial<Pick<LegalCrawlSource,
  'name' | 'base_url' | 'sitemap_scope' | 'enabled' | 'interval_minutes' |
  'lookback_days' | 'max_documents_per_run' | 'max_listing_pages_per_run' |
  'rate_limit_seconds' | 'filter_keyword' | 'content_fetch_allowed' |
  'website_type' | 'link_selector' | 'next_page_selector' | 'include_patterns' | 'exclude_patterns' |
  'compatibility_mode'
  | 'domains' | 'default_organization_unit_id' | 'unassigned_policy'
>>

interface CrawlerSourceManagerProps {
  sources: LegalCrawlSource[]
  scanningSourceId: string | null
  onRefresh: () => Promise<void> | void
  onScan: (sourceId: string) => Promise<void> | void
  onCreate: (payload: CrawlSourceCreatePayload) => Promise<void> | void
  onPreview?: (payload: CrawlSourceCreatePayload) => Promise<CrawlSourcePreview>
  onUpdate: (sourceId: string, payload: SourceUpdate) => Promise<void> | void
  onDelete: (sourceId: string) => Promise<void> | void
}

type SourceDraft = {
  name: string
  base_url: string
  sitemap_scope: 'central' | 'haiphong' | 'local'
  interval_minutes: string
  lookback_days: string
  max_documents_per_run: string
  max_listing_pages_per_run: string
  rate_limit_seconds: string
  filter_keyword: string
  website_type: NonNullable<LegalCrawlSource['website_type']>
  compatibility_mode: NonNullable<LegalCrawlSource['compatibility_mode']>
  link_selector: string
  next_page_selector: string
  include_patterns: string
  exclude_patterns: string
  default_organization_unit_id: string
  unassigned_policy: 'unassigned' | 'shared'
}

const EMPTY_DRAFT: SourceDraft = {
  name: '',
  base_url: '',
  sitemap_scope: 'haiphong',
  interval_minutes: '10080',
  lookback_days: '30',
  max_documents_per_run: '30',
  max_listing_pages_per_run: '10',
  rate_limit_seconds: '1.5',
  filter_keyword: '',
  website_type: 'legal_documents',
  compatibility_mode: 'standard',
  link_selector: '',
  next_page_selector: '',
  include_patterns: '',
  exclude_patterns: '',
  default_organization_unit_id: '',
  unassigned_policy: 'unassigned',
}

function draftFromSource(source: LegalCrawlSource): SourceDraft {
  const scope = ['central', 'haiphong', 'local'].includes(source.sitemap_scope)
    ? source.sitemap_scope as SourceDraft['sitemap_scope']
    : 'haiphong'
  return {
    name: source.name || '',
    base_url: source.base_url || '',
    sitemap_scope: scope,
    interval_minutes: String(source.interval_minutes || 10080),
    lookback_days: String(source.lookback_days || 30),
    max_documents_per_run: String(source.max_documents_per_run || 30),
    max_listing_pages_per_run: String(source.max_listing_pages_per_run || 10),
    rate_limit_seconds: String(source.rate_limit_seconds || 1.5),
    filter_keyword: source.filter_keyword || '',
    website_type: source.website_type || 'mixed_official',
    compatibility_mode: source.compatibility_mode || 'standard',
    link_selector: source.link_selector || '',
    next_page_selector: source.next_page_selector || '',
    include_patterns: (source.include_patterns || []).join(', '),
    exclude_patterns: (source.exclude_patterns || []).join(', '),
    default_organization_unit_id: source.default_organization_unit_id || '',
    unassigned_policy: source.unassigned_policy || 'unassigned',
  }
}

function splitPatterns(value: string): string[] {
  return value.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean).slice(0, 20)
}

function payloadFromDraft(draft: SourceDraft): CrawlSourceCreatePayload {
  return {
    name: draft.name,
    base_url: draft.base_url,
    sitemap_scope: draft.sitemap_scope,
    interval_minutes: Number(draft.interval_minutes),
    lookback_days: Number(draft.lookback_days),
    max_documents_per_run: Number(draft.max_documents_per_run),
    max_listing_pages_per_run: Number(draft.max_listing_pages_per_run),
    rate_limit_seconds: Number(draft.rate_limit_seconds),
    filter_keyword: draft.filter_keyword.trim() || null,
    website_type: draft.website_type,
    compatibility_mode: draft.compatibility_mode,
    link_selector: draft.link_selector.trim() || null,
    next_page_selector: draft.next_page_selector.trim() || null,
    include_patterns: splitPatterns(draft.include_patterns),
    exclude_patterns: splitPatterns(draft.exclude_patterns),
    default_organization_unit_id: draft.default_organization_unit_id || null,
    unassigned_policy: draft.unassigned_policy,
  }
}

function websiteTypeLabel(value: LegalCrawlSource['website_type']): string {
  if (value === 'legal_documents') return 'Văn bản pháp luật'
  if (value === 'procedures') return 'Thủ tục hành chính'
  if (value === 'forms') return 'Biểu mẫu'
  if (value === 'reference') return 'Tin tức / tài liệu tham khảo'
  return 'Nguồn chính thức hỗn hợp'
}

function websiteTypeDescription(value: LegalCrawlSource['website_type']): string {
  if (value === 'legal_documents') return 'Lấy từng luật, nghị định, thông tư, quyết định hoặc nghị quyết.'
  if (value === 'procedures') return 'Lấy từng thủ tục, thành phần hồ sơ, thời hạn và nơi tiếp nhận.'
  if (value === 'forms') return 'Lấy các tệp biểu mẫu PDF, DOC hoặc DOCX.'
  if (value === 'reference') return 'Lấy bài viết để tham khảo; không dùng làm căn cứ pháp lý.'
  return 'Trang có nhiều loại nội dung; cần quét thử và kiểm tra kỹ trước khi bật.'
}

function sourceState(source: LegalCrawlSource): string {
  if (source.source_kind === 'internal_queue') return 'Nguồn nội bộ'
  if (source.last_status === 'retired') return 'Đã ngừng nguồn cũ'
  if (!source.enabled) return 'Đang tạm dừng'
  if (source.last_status === 'failed') return 'Cần kiểm tra'
  if (source.last_status === 'completed_with_warnings') return 'Hoạt động có cảnh báo'
  if (source.last_status === 'completed') return 'Đang hoạt động'
  return 'Chưa kiểm tra lần nào'
}

function lastRunSummary(source: LegalCrawlSource): string | null {
  const stats = source.last_run_stats
  if (!stats || Object.keys(stats).length === 0) return null
  const parts = [
    stats.listing_pages ? `${stats.listing_pages} trang` : null,
    Number.isFinite(stats.discovered) ? `${stats.discovered} bản ghi thấy được` : null,
    Number.isFinite(stats.created) ? `${stats.created} mới` : null,
    stats.duplicates ? `${stats.duplicates} trùng đã bỏ qua` : null,
    stats.outside_scope ? `${stats.outside_scope} ngoài phạm vi` : null,
    stats.item_errors ? `${stats.item_errors} lỗi bản ghi` : null,
    stats.extraction_failed ? `${stats.extraction_failed} lỗi trích xuất` : null,
    stats.pagination_unavailable ? 'chỉ quét được trang hiện tại' : null,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(' · ') : null
}

function intervalLabel(minutes: number): string {
  if (minutes >= 10080) return '7 ngày một lần'
  if (minutes >= 4320) return '3 ngày một lần'
  if (minutes >= 1440) return 'Hằng ngày'
  if (minutes >= 60) return `${Math.round(minutes / 60)} giờ một lần`
  return `${minutes} phút một lần`
}

function isPresetInterval(value: string): boolean {
  return ['1440', '4320', '10080'].includes(value)
}

export function CrawlerSourceManager({
  sources,
  scanningSourceId,
  onRefresh,
  onScan,
  onCreate,
  onPreview,
  onUpdate,
  onDelete,
}: CrawlerSourceManagerProps) {
  const { data: settings } = useSettings()
  const organizationUnits = (settings?.organization_units || []).filter(
    (unit) => unit.is_active
  )
  const [newSource, setNewSource] = useState<SourceDraft>(EMPTY_DRAFT)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, SourceDraft>>({})
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewResult, setPreviewResult] = useState<CrawlSourcePreview | null>(null)
  const [previewLabel, setPreviewLabel] = useState('')
  const [previewedDraftKey, setPreviewedDraftKey] = useState<string | null>(null)
  const mutationInFlight = useRef(false)
  const previewInFlight = useRef(false)
  const webSourceCount = useMemo(
    () => sources.filter((source) => source.source_kind !== 'internal_queue').length,
    [sources],
  )
  const internalCount = sources.length - webSourceCount

  const saveNewSource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (mutationInFlight.current) return
    if (previewedDraftKey !== JSON.stringify(payloadFromDraft(newSource))) {
      setMessage('Hãy bấm “Kiểm tra nguồn” và xem được ít nhất một nội dung cụ thể trước khi thêm nguồn.')
      return
    }
    mutationInFlight.current = true
    setSaving(true)
    setMessage(null)
    try {
      await onCreate(payloadFromDraft(newSource))
      setNewSource(EMPTY_DRAFT)
      setPreviewedDraftKey(null)
      setPreviewResult(null)
      setMessage('Đã lưu nguồn mới ở trạng thái tắt. Hãy kiểm tra lại rồi bật nguồn khi sẵn sàng.')
    } catch (error) {
      setMessage(crawlerErrorCopy(error, 'Không lưu được nguồn. Hãy kiểm tra đường dẫn và quyền quản trị.'))
    } finally {
      mutationInFlight.current = false
      setSaving(false)
    }
  }

  const previewDraft = async (draft: SourceDraft, label: string) => {
    if (!onPreview || previewInFlight.current) return
    previewInFlight.current = true
    setPreviewing(true)
    setPreviewResult(null)
    setPreviewedDraftKey(null)
    setMessage('Đang kết nối và kiểm tra nội dung nguồn. Quá trình này có thể mất khoảng một phút; vui lòng chờ kết quả trước khi thử lại.')
    try {
      const result = await onPreview(payloadFromDraft(draft))
      if (result.discovered_count <= 0 && !result.listing_verified) {
        setMessage('Chưa tìm thấy nội dung cụ thể nào. Nguồn chưa thể được lưu để quét tự động.')
        return
      }
      setPreviewResult(result)
      setPreviewLabel(label)
      setPreviewedDraftKey(JSON.stringify(payloadFromDraft(draft)))
      setMessage(result.notice || `Quét thử hoàn tất: tìm thấy ${result.discovered_count} bản ghi; chưa ghi dữ liệu.`)
    } catch (error) {
      setMessage(crawlerErrorCopy(error, 'Không kiểm tra được nguồn. Hãy kiểm tra đường dẫn và loại nội dung cần lấy.'))
    } finally {
      previewInFlight.current = false
      setPreviewing(false)
    }
  }

  const saveSource = async (source: LegalCrawlSource) => {
    if (mutationInFlight.current) return
    mutationInFlight.current = true
    const draft = drafts[source.id] || draftFromSource(source)
    setSaving(true)
    setMessage(null)
    try {
      await onUpdate(source.id, {
        ...(source.source_kind === 'internal_queue'
          ? { name: draft.name }
          : source.is_default
            ? {}
          : {
              name: draft.name,
              base_url: draft.base_url,
              sitemap_scope: draft.sitemap_scope,
            }),
        ...(source.source_kind === 'internal_queue'
          ? {}
          : {
              interval_minutes: Number(draft.interval_minutes),
              lookback_days: Number(draft.lookback_days),
              max_documents_per_run: Number(draft.max_documents_per_run),
              max_listing_pages_per_run: Number(draft.max_listing_pages_per_run),
              rate_limit_seconds: Number(draft.rate_limit_seconds),
              compatibility_mode: draft.compatibility_mode,
              filter_keyword: draft.filter_keyword.trim(),
              website_type: draft.website_type,
              link_selector: draft.link_selector.trim(),
              next_page_selector: draft.next_page_selector.trim(),
              include_patterns: splitPatterns(draft.include_patterns),
              exclude_patterns: splitPatterns(draft.exclude_patterns),
              default_organization_unit_id:
                draft.default_organization_unit_id || null,
              unassigned_policy: draft.unassigned_policy,
            }),
      })
      setEditingId(null)
      setMessage('Đã lưu cấu hình nguồn.')
    } catch (error) {
      setMessage(crawlerErrorCopy(error, 'Không lưu được thay đổi. Hãy kiểm tra lại dữ liệu và quyền quản trị.'))
    } finally {
      mutationInFlight.current = false
      setSaving(false)
    }
  }

  const runSourceAction = async (action: () => Promise<void> | void, success: string | null) => {
    if (mutationInFlight.current) return
    mutationInFlight.current = true
    setSaving(true)
    setMessage(null)
    try {
      await action()
      setMessage(success)
    } catch (error) {
      setMessage(crawlerErrorCopy(error, 'Chưa hoàn tất thao tác. Hãy thử lại sau khi kiểm tra nguồn.'))
    } finally {
      mutationInFlight.current = false
      setSaving(false)
    }
  }

  return (
    <details className="rounded-lg border p-4" data-testid="crawler-source-manager">
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
        <div>
          <h3 className="font-medium">Nguồn thu thập tự động</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {webSourceCount} nguồn web chính thức · {internalCount} nguồn nội bộ. Mở để xem lịch và cấu hình.
          </p>
        </div>
        <ChevronDown className="mt-1 h-4 w-4 text-muted-foreground" />
      </summary>

      <div className="mt-4 space-y-4 border-t pt-4">
        {message && <p role="status" aria-live="polite" className="rounded border bg-muted/40 p-2 text-xs text-muted-foreground">{message}</p>}
        {previewResult && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-3 text-sm" role="status">
            <p className="font-medium">Kết quả quét thử: {previewLabel}</p>
            <p className="mt-1 text-xs text-muted-foreground">Tìm thấy {previewResult.discovered_count} bản ghi · hiển thị {previewResult.preview_count} · chưa lưu nguồn hoặc đề xuất.</p>
            {previewResult.notice && <p className="mt-2 text-sm">{previewResult.notice}</p>}
            <ul className="mt-3 space-y-2">
              {previewResult.candidates.map((candidate) => (
                <li key={candidate.url} className="rounded border bg-background p-2">
                  <p className="font-medium">{candidate.title}</p>
                  <p className="mt-1 break-all text-xs text-muted-foreground">{crawlerCandidateTypeCopy(candidate.source_type)} · {candidate.url}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {sources.map((source) => {
            const isInternal = source.source_kind === 'internal_queue'
            const isEditing = editingId === source.id
            const draft = drafts[source.id] || draftFromSource(source)
            const runSummary = lastRunSummary(source)
            return (
              <div key={source.id} className="rounded border p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium">{source.name}</p>
                    <p className="break-all text-xs text-muted-foreground">
                      {isInternal ? source.purpose || 'Hàng đợi nội bộ' : source.base_url}
                    </p>
                  </div>
                  <Badge variant={isInternal ? 'outline' : source.enabled ? 'secondary' : 'outline'}>{sourceState(source)}</Badge>
                </div>
                {!isInternal && (
                  <div className="mt-3 text-xs text-muted-foreground">
                    Loại website: {websiteTypeLabel(source.website_type)} ·{' '}
                    Tần suất: {intervalLabel(source.interval_minutes)} · Kiểm tra gần nhất: {source.last_checked_at ? new Date(source.last_checked_at).toLocaleString('vi-VN') : 'Chưa có'}
                    {' · '}Cách tải: {source.compatibility_mode === 'high' ? 'Tương thích cao' : 'Tiêu chuẩn'}
                    {' · '}Toàn văn: {source.content_fetch_allowed ? 'đang bật' : 'đang tắt'}
                    {' · '}Nơi nhận đề xuất:{' '}
                    {organizationUnits.find(
                      (unit) => unit.id === source.default_organization_unit_id
                    )?.name ||
                      (source.unassigned_policy === 'shared'
                        ? 'Dùng chung toàn hệ thống'
                        : 'Admin phân công sau')}
                    {runSummary ? <p className="mt-1">Lần quét gần nhất: {runSummary}</p> : null}
                    {source.last_error ? <p className="mt-1 text-destructive">Cần chú ý: {crawlerErrorCopy(source.last_error)}</p> : null}
                  </div>
                )}
                {isEditing && (
                  <div className="mt-3 grid gap-2 text-xs">
                    {isInternal ? (
                      <>
                        <Input
                          aria-label="Tên hiển thị nội bộ"
                          value={draft.name}
                          onChange={(event) => setDrafts((current) => ({
                            ...current,
                            [source.id]: { ...draft, name: event.target.value },
                          }))}
                        />
                        <p className="text-muted-foreground">
                          Loại và địa chỉ nội bộ được khóa để bảo toàn luồng dữ liệu.
                        </p>
                      </>
                    ) : !source.is_default && <>
                      <Input aria-label={`Tên nguồn ${source.name}`} value={draft.name} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, name: event.target.value } }))} />
                      <Input aria-label={`URL nguồn ${source.name}`} value={draft.base_url} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, base_url: event.target.value } }))} />
                      <Select value={draft.sitemap_scope} onValueChange={(value) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, sitemap_scope: value as SourceDraft['sitemap_scope'] } }))}>
                        <SelectTrigger aria-label={`Phạm vi ${source.name}`}><SelectValue /></SelectTrigger>
                        <SelectContent><SelectItem value="central">Trung ương</SelectItem><SelectItem value="haiphong">Hải Phòng</SelectItem><SelectItem value="local">Cấp phường/xã</SelectItem></SelectContent>
                      </Select>
                    </>}
                    {!isInternal && <Label className="space-y-1 text-xs">Tần suất kiểm tra<Select value={draft.interval_minutes} onValueChange={(value) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, interval_minutes: value } }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{!isPresetInterval(draft.interval_minutes) && <SelectItem value={draft.interval_minutes}>{intervalLabel(Number(draft.interval_minutes))}</SelectItem>}<SelectItem value="1440">Hằng ngày</SelectItem><SelectItem value="4320">3 ngày một lần</SelectItem><SelectItem value="10080">7 ngày một lần</SelectItem></SelectContent></Select></Label>}
                    {!isInternal && <>
                      <Label className="space-y-1 text-xs">
                        Nội dung cần lấy
                        <Select value={draft.website_type} onValueChange={(value) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, website_type: value as SourceDraft['website_type'] } }))}>
                          <SelectTrigger aria-label={`Loại website ${source.name}`}><SelectValue /></SelectTrigger>
                          <SelectContent><SelectItem value="legal_documents">Văn bản pháp luật</SelectItem><SelectItem value="procedures">Thủ tục hành chính</SelectItem><SelectItem value="forms">Biểu mẫu</SelectItem><SelectItem value="reference">Tin tức / tài liệu tham khảo</SelectItem><SelectItem value="mixed_official">Trang có nhiều loại nội dung</SelectItem></SelectContent>
                        </Select>
                        <span className="block font-normal text-muted-foreground">{websiteTypeDescription(draft.website_type)}</span>
                      </Label>
                      <Label className="space-y-1 text-xs">
                        Phòng ban nhận đề xuất
                        <Select
                          value={draft.default_organization_unit_id || (draft.unassigned_policy === 'shared' ? '__shared__' : '__unassigned__')}
                          onValueChange={(value) =>
                            setDrafts((current) => ({
                              ...current,
                              [source.id]: {
                                ...draft,
                                default_organization_unit_id:
                                  value === '__unassigned__' || value === '__shared__'
                                    ? ''
                                    : value,
                                unassigned_policy:
                                  value === '__shared__' ? 'shared' : 'unassigned'
                              }
                            }))
                          }
                        >
                          <SelectTrigger aria-label={`Phòng ban nhận đề xuất từ ${source.name}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__unassigned__">Admin phân công sau</SelectItem>
                            <SelectItem value="__shared__">Dùng chung toàn hệ thống</SelectItem>
                            {organizationUnits.map((unit) => (
                              <SelectItem key={unit.id} value={unit.id}>
                                {unit.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <span className="block font-normal text-muted-foreground">
                          Đây chỉ là nơi nhận đề xuất mặc định; Admin vẫn kiểm tra trước khi nhập kho.
                        </span>
                      </Label>
                      <Label className="space-y-1 text-xs">
                        Cách tải trang
                        <Select value={draft.compatibility_mode} onValueChange={(value) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, compatibility_mode: value as SourceDraft['compatibility_mode'] } }))}>
                          <SelectTrigger aria-label={`Cách tải trang ${source.name}`}><SelectValue /></SelectTrigger>
                          <SelectContent><SelectItem value="standard">Tiêu chuẩn — nhanh hơn</SelectItem><SelectItem value="high">Tương thích cao — dành cho trang tải chậm hoặc dùng JavaScript</SelectItem></SelectContent>
                        </Select>
                      </Label>
                      <Label className="space-y-1 text-xs">Chỉ lấy nội dung có từ khóa (không bắt buộc)<Input value={draft.filter_keyword} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, filter_keyword: event.target.value } }))} placeholder="Ví dụ: hộ tịch, khai sinh" /></Label>
                      <details className="rounded border p-2">
                        <summary className="cursor-pointer font-medium">Thiết lập kỹ thuật — chỉ dùng khi quét thử chưa đúng</summary>
                        <p className="mt-2 text-muted-foreground">Nếu không phụ trách kỹ thuật, hãy để trống các ô dưới đây để hệ thống tự nhận diện.</p>
                        <div className="mt-2 grid gap-2">
                          <Label>Vùng chứa liên kết văn bản<Input value={draft.link_selector} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, link_selector: event.target.value } }))} placeholder="Ví dụ kỹ thuật: article a[href]" /></Label>
                          <Label>Đường dẫn thường có trong văn bản chi tiết<Input value={draft.include_patterns} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, include_patterns: event.target.value } }))} placeholder="Ví dụ: /van-ban/, /chi-tiet/" /></Label>
                          <Label>Đường dẫn cần bỏ qua<Input value={draft.exclude_patterns} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, exclude_patterns: event.target.value } }))} placeholder="Ví dụ: /dang-nhap/, /the/" /></Label>
                          <Label>Nút sang trang tiếp theo<Input value={draft.next_page_selector} onChange={(event) => setDrafts((current) => ({ ...current, [source.id]: { ...draft, next_page_selector: event.target.value } }))} placeholder="Ví dụ kỹ thuật: a[rel=next]" /></Label>
                        </div>
                      </details>
                    </>}
                    <div className="flex flex-wrap gap-2"><Button type="button" size="sm" onClick={() => void saveSource(source)} disabled={saving}><Save className="mr-1 h-3 w-3" />Lưu</Button>{!isInternal && onPreview && <Button type="button" size="sm" variant="outline" onClick={() => void previewDraft(draft, source.name)} disabled={previewing}>{previewing ? 'Đang quét thử...' : 'Quét thử cấu hình'}</Button>}<Button type="button" size="sm" variant="ghost" onClick={() => setEditingId(null)}>Hủy</Button></div>
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  {source.can_scan !== false && !isInternal && <Button type="button" size="sm" variant="outline" onClick={() => void runSourceAction(() => onScan(source.id), null)} disabled={saving || scanningSourceId !== null}><RefreshCcw className="mr-1 h-3 w-3" />{scanningSourceId === source.id ? 'Đang quét...' : 'Quét nguồn này'}</Button>}
                  {source.can_scan !== false && !isInternal && <Button type="button" size="sm" variant="ghost" disabled={saving || scanningSourceId !== null} onClick={() => void runSourceAction(() => onUpdate(source.id, { enabled: !source.enabled }), source.enabled ? 'Đã tạm dừng kiểm tra tự động.' : 'Đã bật kiểm tra tự động.')}>{source.enabled ? 'Tạm dừng' : 'Bật kiểm tra'}</Button>}
                  {!isInternal && <Button type="button" size="sm" variant="ghost" disabled={saving || scanningSourceId !== null} onClick={() => void runSourceAction(() => onUpdate(source.id, { content_fetch_allowed: !source.content_fetch_allowed }), 'Đã cập nhật chế độ lấy toàn văn.')}>{source.content_fetch_allowed ? 'Tắt lấy toàn văn' : 'Bật lấy toàn văn'}</Button>}
                  {!isEditing && <Button type="button" size="sm" variant="ghost" onClick={() => { setDrafts((current) => ({ ...current, [source.id]: draftFromSource(source) })); setEditingId(source.id) }}>Sửa</Button>}
                  {source.can_delete && <Button type="button" size="sm" variant="ghost" disabled={saving || scanningSourceId !== null} className="text-destructive hover:text-destructive" onClick={() => { if (!mutationInFlight.current && window.confirm(`Xóa nguồn “${source.name}” khỏi danh sách vận hành? Các đề xuất, lượt quét và lịch sử thao tác vẫn được giữ lại.`)) void runSourceAction(() => onDelete(source.id), 'Đã xóa nguồn khỏi danh sách vận hành; các đề xuất và lịch sử vẫn được giữ lại.') }}><Trash2 className="mr-1 h-3 w-3" />Xóa</Button>}
                  {source.is_default && !isInternal && <span className="self-center text-xs text-muted-foreground">Nguồn được tạo từ cấu hình ban đầu; quản trị viên vẫn có thể xóa khỏi vận hành.</span>}
                  {isInternal && !isEditing && <span className="self-center text-xs text-muted-foreground">Có thể đổi tên hoặc xóa khỏi danh sách; dữ liệu lịch sử vẫn được giữ.</span>}
                </div>
              </div>
            )
          })}
        </div>

        <form className="space-y-4 rounded-lg border border-dashed p-4" onSubmit={saveNewSource}>
          <div>
            <p className="font-medium">Thêm nguồn web theo 3 bước</p>
            <p className="mt-1 text-xs text-muted-foreground">Không cần biết mã hoặc cấu trúc website. Hệ thống chỉ cho lưu sau khi quét thử tìm thấy nội dung cụ thể.</p>
          </div>

          <section className="space-y-2 rounded-lg border bg-muted/10 p-3">
            <p className="text-sm font-semibold">1. Bạn muốn lấy nội dung nào?</p>
            <Select value={newSource.website_type} onValueChange={(value) => setNewSource((current) => ({ ...current, website_type: value as SourceDraft['website_type'] }))}>
              <SelectTrigger aria-label="Nội dung cần lấy"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="legal_documents">Văn bản pháp luật</SelectItem><SelectItem value="procedures">Thủ tục hành chính</SelectItem><SelectItem value="forms">Biểu mẫu</SelectItem><SelectItem value="reference">Tin tức / tài liệu tham khảo</SelectItem><SelectItem value="mixed_official">Trang có nhiều loại nội dung</SelectItem></SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{websiteTypeDescription(newSource.website_type)}</p>
          </section>

          <section className="space-y-3 rounded-lg border bg-muted/10 p-3">
            <p className="text-sm font-semibold">2. Dán địa chỉ trang công bố danh sách</p>
            <p className="text-xs text-muted-foreground">Nên dùng trang có nhiều dòng văn bản và mỗi dòng có nút mở chi tiết. Không dùng trang chủ chung của cơ quan.</p>
            <div className="grid gap-3 md:grid-cols-2">
              <Label className="space-y-1 text-xs">Tên dễ nhận biết<Input required value={newSource.name} onChange={(event) => setNewSource((current) => ({ ...current, name: event.target.value }))} placeholder="Ví dụ: Văn bản Sở Tư pháp" /></Label>
              <Label className="space-y-1 text-xs">Đường dẫn trang danh sách<Input required type="url" value={newSource.base_url} onChange={(event) => setNewSource((current) => ({ ...current, base_url: event.target.value }))} placeholder="https://..." /></Label>
              <Label className="space-y-1 text-xs">Phạm vi<Select value={newSource.sitemap_scope} onValueChange={(value) => setNewSource((current) => ({ ...current, sitemap_scope: value as SourceDraft['sitemap_scope'] }))}><SelectTrigger aria-label="Phạm vi nguồn"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="central">Trung ương</SelectItem><SelectItem value="haiphong">Tỉnh/thành phố</SelectItem><SelectItem value="local">Phường/xã</SelectItem></SelectContent></Select></Label>
              <Label className="space-y-1 text-xs">Tần suất kiểm tra<Select value={newSource.interval_minutes} onValueChange={(value) => setNewSource((current) => ({ ...current, interval_minutes: value }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1440">Hằng ngày</SelectItem><SelectItem value="4320">3 ngày một lần</SelectItem><SelectItem value="10080">7 ngày một lần</SelectItem></SelectContent></Select></Label>
              <Label className="space-y-1 text-xs">
                Phòng ban nhận đề xuất
                <Select
                  value={newSource.default_organization_unit_id || (newSource.unassigned_policy === 'shared' ? '__shared__' : '__unassigned__')}
                  onValueChange={(value) =>
                    setNewSource((current) => ({
                      ...current,
                      default_organization_unit_id:
                        value === '__unassigned__' || value === '__shared__' ? '' : value,
                      unassigned_policy: value === '__shared__' ? 'shared' : 'unassigned'
                    }))
                  }
                >
                  <SelectTrigger aria-label="Phòng ban nhận đề xuất"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__unassigned__">Admin phân công sau</SelectItem>
                    <SelectItem value="__shared__">Dùng chung toàn hệ thống</SelectItem>
                    {organizationUnits.map((unit) => (
                      <SelectItem key={unit.id} value={unit.id}>{unit.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="block font-normal text-muted-foreground">
                  Chọn nơi thường xử lý tài liệu từ nguồn này. Có thể sửa lại khi duyệt.
                </span>
              </Label>
              <Label className="space-y-1 text-xs">Cách tải trang<Select value={newSource.compatibility_mode} onValueChange={(value) => setNewSource((current) => ({ ...current, compatibility_mode: value as SourceDraft['compatibility_mode'] }))}><SelectTrigger aria-label="Cách tải trang"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="standard">Tiêu chuẩn — nhanh hơn</SelectItem><SelectItem value="high">Tương thích cao — trang tải chậm hoặc dùng JavaScript</SelectItem></SelectContent></Select></Label>
              <Label className="space-y-1 text-xs">Chỉ lấy nội dung có từ khóa (không bắt buộc)<Input value={newSource.filter_keyword} onChange={(event) => setNewSource((current) => ({ ...current, filter_keyword: event.target.value }))} placeholder="Ví dụ: hộ tịch, khai sinh" /></Label>
            </div>
          </section>

          <details className="rounded-lg border p-3 text-xs">
            <summary className="cursor-pointer font-medium">Thiết lập kỹ thuật — chỉ dùng khi quét thử chưa đúng</summary>
            <p className="mt-2 text-muted-foreground">Người dùng thông thường có thể bỏ qua phần này. Hệ thống sẽ tự tìm liên kết chi tiết và bỏ qua menu, đăng nhập, thẻ và trang phân loại.</p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Label>Vùng chứa liên kết văn bản<Input value={newSource.link_selector} onChange={(event) => setNewSource((current) => ({ ...current, link_selector: event.target.value }))} placeholder="Ví dụ kỹ thuật: article a[href]" /></Label>
              <Label>Đường dẫn thường có trong trang chi tiết<Input value={newSource.include_patterns} onChange={(event) => setNewSource((current) => ({ ...current, include_patterns: event.target.value }))} placeholder="Ví dụ: /van-ban/, /chi-tiet/" /></Label>
              <Label>Đường dẫn cần bỏ qua<Input value={newSource.exclude_patterns} onChange={(event) => setNewSource((current) => ({ ...current, exclude_patterns: event.target.value }))} placeholder="Ví dụ: /dang-nhap/, /the/" /></Label>
              <Label>Nút sang trang tiếp theo<Input value={newSource.next_page_selector} onChange={(event) => setNewSource((current) => ({ ...current, next_page_selector: event.target.value }))} placeholder="Ví dụ kỹ thuật: a[rel=next]" /></Label>
            </div>
          </details>

          <section className="space-y-2 rounded-lg border bg-muted/10 p-3">
            <p className="text-sm font-semibold">3. Kiểm tra trước khi lưu</p>
            <p className="text-xs text-muted-foreground">Kết quả phải là từng văn bản, thủ tục hoặc biểu mẫu cụ thể; không phải nguyên trang danh sách.</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" disabled={!onPreview || previewing || !newSource.base_url.trim()} onClick={() => void previewDraft(newSource, newSource.name || 'Nguồn mới')}>{previewing ? 'Đang kiểm tra...' : 'Kiểm tra nguồn — không lưu dữ liệu'}</Button>
              <Button type="submit" size="sm" disabled={saving || previewedDraftKey !== JSON.stringify(payloadFromDraft(newSource))}><Plus className="mr-1 h-3 w-3" />{saving ? 'Đang lưu...' : 'Thêm nguồn'}</Button>
            </div>
          </section>
        </form>
        <Button type="button" size="sm" variant="outline" onClick={() => void onRefresh()}><RefreshCcw className="mr-1 h-3 w-3" />Làm mới danh sách nguồn</Button>
      </div>
    </details>
  )
}
