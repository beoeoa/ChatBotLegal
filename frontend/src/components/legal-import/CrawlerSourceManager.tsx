'use client'

import { FormEvent, useMemo, useState } from 'react'
import { ChevronDown, Plus, RefreshCcw, Save, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  CrawlSourceCreatePayload,
  LegalCrawlSource,
} from '@/lib/api/legal-import'

type SourceUpdate = Partial<Pick<LegalCrawlSource,
  'name' | 'base_url' | 'sitemap_scope' | 'enabled' | 'interval_minutes' |
  'lookback_days' | 'max_documents_per_run' | 'max_listing_pages_per_run' |
  'rate_limit_seconds' | 'filter_keyword' | 'content_fetch_allowed'
>>

interface CrawlerSourceManagerProps {
  sources: LegalCrawlSource[]
  scanningSourceId: string | null
  onRefresh: () => Promise<void> | void
  onScan: (sourceId: string) => Promise<void> | void
  onCreate: (payload: CrawlSourceCreatePayload) => Promise<void> | void
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
  }
}

function sourceState(source: LegalCrawlSource): string {
  if (source.source_kind === 'internal_queue') return 'Nguồn nội bộ'
  if (!source.enabled) return 'Đang tạm dừng'
  if (source.last_status === 'failed') return 'Cần kiểm tra'
  if (source.last_status === 'completed') return 'Đang hoạt động'
  return 'Chưa kiểm tra lần nào'
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
  onUpdate,
  onDelete,
}: CrawlerSourceManagerProps) {
  const [newSource, setNewSource] = useState<SourceDraft>(EMPTY_DRAFT)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, SourceDraft>>({})
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const webSourceCount = useMemo(
    () => sources.filter((source) => source.source_kind !== 'internal_queue').length,
    [sources],
  )
  const internalCount = sources.length - webSourceCount

  const saveNewSource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      await onCreate({
        name: newSource.name,
        base_url: newSource.base_url,
        sitemap_scope: newSource.sitemap_scope,
        interval_minutes: Number(newSource.interval_minutes),
        lookback_days: Number(newSource.lookback_days),
        max_documents_per_run: Number(newSource.max_documents_per_run),
        max_listing_pages_per_run: Number(newSource.max_listing_pages_per_run),
        rate_limit_seconds: Number(newSource.rate_limit_seconds),
        filter_keyword: newSource.filter_keyword.trim() || null,
      })
      setNewSource(EMPTY_DRAFT)
      setMessage('Đã lưu nguồn mới ở trạng thái tắt. Hãy kiểm tra lại rồi bật nguồn khi sẵn sàng.')
    } catch {
      setMessage('Không lưu được nguồn. Hãy kiểm tra URL HTTPS và tên miền cơ quan nhà nước.')
    } finally {
      setSaving(false)
    }
  }

  const saveSource = async (source: LegalCrawlSource) => {
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
              filter_keyword: draft.filter_keyword.trim() || null,
            }),
      })
      setEditingId(null)
    } catch {
      setMessage('Không lưu được thay đổi. Hãy kiểm tra lại dữ liệu và quyền quản trị.')
    } finally {
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
        {message && <p className="rounded border bg-muted/40 p-2 text-xs text-muted-foreground">{message}</p>}
        <div className="grid gap-3 md:grid-cols-2">
          {sources.map((source) => {
            const isInternal = source.source_kind === 'internal_queue'
            const isEditing = editingId === source.id
            const draft = drafts[source.id] || draftFromSource(source)
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
                    Tần suất: {intervalLabel(source.interval_minutes)} · Kiểm tra gần nhất: {source.last_checked_at ? new Date(source.last_checked_at).toLocaleString('vi-VN') : 'Chưa có'}
                    {' · '}Toàn văn: {source.content_fetch_allowed ? 'đang bật' : 'đang tắt'}
                    {source.last_error ? <p className="mt-1 text-destructive">Lỗi gần nhất: {source.last_error}</p> : null}
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
                    <div className="flex gap-2"><Button type="button" size="sm" onClick={() => void saveSource(source)} disabled={saving}><Save className="mr-1 h-3 w-3" />Lưu</Button><Button type="button" size="sm" variant="ghost" onClick={() => setEditingId(null)}>Hủy</Button></div>
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  {source.can_scan !== false && !isInternal && <Button type="button" size="sm" variant="outline" onClick={() => void onScan(source.id)} disabled={scanningSourceId === source.id}><RefreshCcw className="mr-1 h-3 w-3" />{scanningSourceId === source.id ? 'Đang quét...' : 'Quét nguồn này'}</Button>}
                  {!isInternal && <Button type="button" size="sm" variant="ghost" onClick={() => { void Promise.resolve(onUpdate(source.id, { enabled: !source.enabled })).catch(() => setMessage('Không cập nhật được trạng thái nguồn.')) }}>{source.enabled ? 'Tạm dừng' : 'Bật kiểm tra'}</Button>}
                  {!isInternal && <Button type="button" size="sm" variant="ghost" onClick={() => { void Promise.resolve(onUpdate(source.id, { content_fetch_allowed: !source.content_fetch_allowed })).catch(() => setMessage('Không cập nhật được chế độ lấy toàn văn.')) }}>{source.content_fetch_allowed ? 'Tắt lấy toàn văn' : 'Bật lấy toàn văn'}</Button>}
                  {!isEditing && <Button type="button" size="sm" variant="ghost" onClick={() => { setDrafts((current) => ({ ...current, [source.id]: draftFromSource(source) })); setEditingId(source.id) }}>Sửa</Button>}
                  {source.can_delete && <Button type="button" size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => { if (window.confirm(`Xóa nguồn “${source.name}” khỏi danh sách vận hành? Dữ liệu ứng viên, lượt chạy và lịch sử kiểm toán vẫn được giữ lại.`)) void Promise.resolve(onDelete(source.id)).catch(() => setMessage('Không xóa được nguồn.')) }}><Trash2 className="mr-1 h-3 w-3" />Xóa</Button>}
                  {source.is_default && !isInternal && <span className="self-center text-xs text-muted-foreground">Nguồn mặc định: chỉ có thể tắt, không xóa.</span>}
                  {isInternal && !isEditing && <span className="self-center text-xs text-muted-foreground">Có thể đổi tên hoặc xóa khỏi danh sách; dữ liệu lịch sử vẫn được giữ.</span>}
                </div>
              </div>
            )
          })}
        </div>

        <form className="space-y-3 rounded border border-dashed p-3" onSubmit={saveNewSource}>
          <div><p className="font-medium">Thêm nguồn web chính thức</p><p className="text-xs text-muted-foreground">Chỉ nhận đường dẫn HTTPS của cơ quan nhà nước. Nguồn mới được lưu ở trạng thái tạm dừng để bạn kiểm tra trước.</p></div>
          <div className="grid gap-2 md:grid-cols-2">
            <Label className="space-y-1 text-xs">Tên nguồn<Input required value={newSource.name} onChange={(event) => setNewSource((current) => ({ ...current, name: event.target.value }))} placeholder="Ví dụ: Cổng thông tin Hải Phòng" /></Label>
            <Label className="space-y-1 text-xs">Đường dẫn nguồn<Input required type="url" value={newSource.base_url} onChange={(event) => setNewSource((current) => ({ ...current, base_url: event.target.value }))} placeholder="https://..." /></Label>
            <Select value={newSource.sitemap_scope} onValueChange={(value) => setNewSource((current) => ({ ...current, sitemap_scope: value as SourceDraft['sitemap_scope'] }))}>
              <SelectTrigger aria-label="Phạm vi nguồn"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="central">Trung ương</SelectItem><SelectItem value="haiphong">Hải Phòng</SelectItem><SelectItem value="local">Cấp phường/xã</SelectItem></SelectContent>
            </Select>
            <Label className="space-y-1 text-xs">Tần suất kiểm tra<Select value={newSource.interval_minutes} onValueChange={(value) => setNewSource((current) => ({ ...current, interval_minutes: value }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1440">Hằng ngày</SelectItem><SelectItem value="4320">3 ngày một lần</SelectItem><SelectItem value="10080">7 ngày một lần</SelectItem></SelectContent></Select></Label>
          </div>
          <Button type="submit" size="sm" disabled={saving}><Plus className="mr-1 h-3 w-3" />{saving ? 'Đang lưu...' : 'Thêm nguồn'}</Button>
        </form>
        <Button type="button" size="sm" variant="outline" onClick={() => void onRefresh()}><RefreshCcw className="mr-1 h-3 w-3" />Làm mới danh sách nguồn</Button>
      </div>
    </details>
  )
}
