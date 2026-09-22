'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  legalImportApi,
  type LegalValidityEvent,
  type LegalValiditySyncClient,
} from '@/lib/api/legal-import'
import { formatApiError } from '@/lib/utils/error-handler'

interface LegalValiditySyncPanelProps {
  client?: LegalValiditySyncClient
}

const PAGE_SIZE = 20

function dateTime(value?: string | null): string {
  if (!value) return 'Chưa có'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Chưa xác định'
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' }).format(parsed)
}

function apiErrorDetail(error: unknown, fallback: string): string {
  return formatApiError(error, fallback)
}

export function LegalValiditySyncPanel({
  client = legalImportApi as LegalValiditySyncClient,
}: LegalValiditySyncPanelProps) {
  const [events, setEvents] = useState<LegalValidityEvent[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [currentCursor, setCurrentCursor] = useState<string | null>(null)
  const [previousCursors, setPreviousCursors] = useState<Array<string | null>>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [decisionReasons, setDecisionReasons] = useState<Record<string, string>>({})
  const [replacementSourceUrls, setReplacementSourceUrls] = useState<Record<string, string>>({})
  const [replacementFiles, setReplacementFiles] = useState<Record<string, { filename: string; content: string; characters: number }>>({})
  const [pendingQuarantine, setPendingQuarantine] = useState<LegalValidityEvent | null>(null)
  const [actionMessage, setActionMessage] = useState<{ tone: 'success' | 'error'; text: string; href?: string; linkLabel?: string } | null>(null)

  const loadPage = useCallback(async (cursor: string | null, preferredId?: string | null) => {
    setLoading(true)
    setError(false)
    try {
      const page = await client.validityEvents({
        review_status: 'open',
        current_snapshot_only: true,
        limit: PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      })
      const nextEvents = page.items || []
      setEvents(nextEvents)
      setNextCursor(page.next_cursor)
      setSelectedId((current) => {
        const requested = preferredId === undefined ? current : preferredId
        return requested && nextEvents.some((item) => item.id === requested)
          ? requested
          : nextEvents[0]?.id || null
      })
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void loadPage(null)
  }, [loadPage])

  const filteredEvents = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('vi')
    if (!needle) return events
    return events.filter((event) => [event.law_number, event.document_title]
      .filter(Boolean)
      .some((value) => value?.toLocaleLowerCase('vi').includes(needle)))
  }, [events, query])
  const selected = events.find((event) => event.id === selectedId) || filteredEvents[0] || null

  async function finishDecision(event: LegalValidityEvent, action: 'mark_historical' | 'quarantine') {
    const reason = (decisionReasons[event.id] || '').trim()
    if (reason.length < 10) return
    setPendingQuarantine(null)
    setBusy(action)
    setActionMessage(null)
    try {
      const result = await client.decideValidityEvent(event.id, { action, reason })
      const applied = result.operation?.status === 'applied'
      if (!applied) throw new Error('serving_projection_not_applied')
      const currentIndex = events.findIndex((item) => item.id === event.id)
      const nextEvent = events[currentIndex + 1] || events[currentIndex - 1] || null
      await loadPage(currentCursor, nextEvent?.id || null)
      const documentId = String(result.operation?.document_id || event.document_id || '').replace(/^legal_document:/, '')
      setActionMessage({
        tone: 'success',
        text: action === 'quarantine'
          ? `${event.law_number}: Đã xác nhận loại khỏi mọi tìm kiếm. Văn bản không bị xóa và vẫn còn trong kho quản trị.`
          : `${event.law_number}: Đã chuyển sang tra cứu lịch sử. Văn bản không dùng cho câu hỏi hiện hành và vẫn đọc được khi tra cứu theo thời điểm cũ.`,
        href: action === 'quarantine' ? `/legal-management/${documentId}` : `/legal-documents/${documentId}`,
        linkLabel: action === 'quarantine' ? 'Mở hồ sơ còn lưu trong kho quản trị' : 'Mở văn bản trong tra cứu lịch sử',
      })
    } catch (caught) {
      setActionMessage({ tone: 'error', text: apiErrorDetail(caught, 'Không thực hiện được thao tác; trạng thái cũ được giữ nguyên.') })
    } finally {
      setBusy(null)
    }
  }

  async function replaceDocument(event: LegalValidityEvent) {
    const reason = (decisionReasons[event.id] || '').trim()
    const sourceUrl = (replacementSourceUrls[event.id] || '').trim()
    const file = replacementFiles[event.id]
    if (!event.document_id || sourceUrl.length < 8 || reason.length < 10 || !client.createReplacementWorkflow) return
    setBusy('replace')
    setActionMessage(null)
    try {
      const result = await client.createReplacementWorkflow(event.document_id, {
        event_id: event.id,
        source_url: sourceUrl,
        reason,
        ...(file ? { uploaded_content: file.content, uploaded_filename: file.filename } : {}),
      })
      if (result.status !== 'activated') throw new Error('replacement_not_activated')
      const currentIndex = events.findIndex((item) => item.id === event.id)
      const nextEvent = events[currentIndex + 1] || events[currentIndex - 1] || null
      await loadPage(currentCursor, nextEvent?.id || null)
      setActionMessage({
        tone: 'success',
          text: `${event.law_number}: Văn bản thay thế đã được tạo dữ liệu tìm kiếm và đưa vào hỏi đáp hiện tại; văn bản cũ đã chuyển sang tra cứu lịch sử.`,
      })
    } catch (caught) {
      setActionMessage({
        tone: 'error',
        text: apiErrorDetail(caught, 'Không kích hoạt được văn bản thay thế; văn bản cũ và hàng chờ được giữ nguyên.'),
      })
    } finally {
      setBusy(null)
    }
  }

  async function extractReplacementFile(event: LegalValidityEvent, file: File | null) {
    if (!file || !client.extractFile) return
    setBusy('file')
    setActionMessage(null)
    try {
      if (!file.name.toLowerCase().endsWith('.pdf')) {
        throw new Error('Chỉ hỗ trợ tệp PDF cho văn bản thay thế.')
      }
      const extracted = await client.extractFile(file, 'auto')
      setReplacementFiles((current) => ({
        ...current,
        [event.id]: {
          filename: extracted.filename,
          content: extracted.content,
          characters: extracted.characters,
        },
      }))
    } catch (caught) {
      setActionMessage({ tone: 'error', text: apiErrorDetail(caught, 'Không trích xuất được tệp văn bản thay thế.') })
    } finally {
      setBusy(null)
    }
  }

  async function goNext() {
    if (!nextCursor) return
    setPreviousCursors((current) => [...current, currentCursor])
    setCurrentCursor(nextCursor)
    setPageNumber((current) => current + 1)
    setActionMessage(null)
    await loadPage(nextCursor)
  }

  async function goPrevious() {
    if (!previousCursors.length) return
    const cursor = previousCursors[previousCursors.length - 1]
    setPreviousCursors((current) => current.slice(0, -1))
    setCurrentCursor(cursor)
    setPageNumber((current) => Math.max(1, current - 1))
    setActionMessage(null)
    await loadPage(cursor)
  }

  if (loading && events.length === 0) {
    return <Card><CardContent className="py-8 text-sm text-muted-foreground">Đang tải văn bản hết hiệu lực theo dữ liệu hiện hành…</CardContent></Card>
  }

  if (error && events.length === 0) {
    return (
      <Card className="border-red-200">
        <CardContent className="flex items-center justify-between gap-4 py-6">
          <p className="text-sm text-red-700">Không tải được danh sách văn bản cần xử lý.</p>
          <Button type="button" variant="outline" onClick={() => void loadPage(currentCursor)}>Thử lại</Button>
        </CardContent>
      </Card>
    )
  }

  const reasonLength = selected ? (decisionReasons[selected.id] || '').trim().length : 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>Văn bản hết hiệu lực cần xử lý</CardTitle>
        <CardDescription>Danh sách lấy từ cùng ảnh chụp hiệu lực đang dùng tại Kho văn bản; mỗi văn bản chỉ xuất hiện một lần.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {actionMessage && (
          <div role="status" className={`rounded-lg border p-3 text-sm ${actionMessage.tone === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-red-200 bg-red-50 text-red-800'}`}>
            {actionMessage.text}
            {actionMessage.href && <a href={actionMessage.href} className="mt-2 block font-semibold underline">{actionMessage.linkLabel}</a>}
          </div>
        )}
        <Label className="block max-w-md space-y-2">
          <span>Tìm trong trang hiện tại</span>
          <span className="relative block">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Số hoặc tên văn bản" />
          </span>
        </Label>

        <div className="grid min-h-[420px] gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.75fr)]">
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[680px] text-left text-sm" aria-label="Danh sách văn bản hết hiệu lực cần xử lý">
                <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                  <tr><th className="px-4 py-3 font-medium">Văn bản</th><th className="px-4 py-3 font-medium">Ngày hết hiệu lực</th><th className="px-4 py-3 font-medium">Phát hiện</th><th className="px-4 py-3"><span className="sr-only">Thao tác</span></th></tr>
                </thead>
                <tbody>
                  {filteredEvents.map((event) => (
                    <tr key={event.id} className={`border-b last:border-0 ${selected?.id === event.id ? 'bg-primary/5' : ''}`}>
                      <td className="px-4 py-3"><p className="font-medium">{event.law_number}</p>{event.document_title && <p className="line-clamp-2 text-muted-foreground">{event.document_title}</p>}</td>
                      <td className="px-4 py-3">{event.effective_to || 'Chưa xác định'}</td>
                      <td className="px-4 py-3">{dateTime(event.created_at)}</td>
                      <td className="px-4 py-3 text-right"><Button type="button" size="sm" variant="outline" onClick={() => setSelectedId(event.id)}>Xem</Button></td>
                    </tr>
                  ))}
                  {filteredEvents.length === 0 && <tr><td colSpan={4} className="px-4 py-10 text-center text-muted-foreground">Không có văn bản cần xử lý.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between text-sm">
              <Button type="button" variant="outline" disabled={!previousCursors.length || loading} onClick={() => void goPrevious()}>Trang trước</Button>
              <span>Trang {pageNumber} · {events.length}/20 văn bản</span>
              <Button type="button" variant="outline" disabled={!nextCursor || loading} onClick={() => void goNext()}>Trang sau</Button>
            </div>
          </div>

          <aside className="rounded-lg border bg-muted/15 p-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto">
            <h2 className="text-base font-semibold">Xử lý văn bản</h2>
            {!selected ? <p className="mt-3 text-sm text-muted-foreground">Chọn một văn bản để xử lý.</p> : (
              <div className="mt-4 space-y-4">
                <div><p className="font-semibold">{selected.law_number}</p>{selected.document_title && <p className="mt-1 text-sm text-muted-foreground">{selected.document_title}</p>}</div>
                <dl className="grid gap-2 text-sm">
                  <div><dt className="text-muted-foreground">Ngày hết hiệu lực</dt><dd className="font-medium">{selected.effective_to || 'Chưa xác định'}</dd></div>
                  <div><dt className="text-muted-foreground">Nguồn</dt><dd>{selected.source_url ? <a href={selected.source_url} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">Mở nguồn chính thức</a> : 'Chưa có'}</dd></div>
                </dl>
                <div className="space-y-2">
                  <Label htmlFor={`validity-decision-${selected.id}`}>Lý do xử lý</Label>
                  <Textarea id={`validity-decision-${selected.id}`} value={decisionReasons[selected.id] || ''} onChange={(change) => setDecisionReasons((current) => ({ ...current, [selected.id]: change.target.value }))} placeholder="Ghi lý do cụ thể, tối thiểu 10 ký tự…" />
                  {reasonLength > 0 && reasonLength < 10 && <p className="text-xs text-amber-700">Cần ít nhất 10 ký tự ({reasonLength}/10).</p>}
                </div>

                <div className="space-y-2 rounded-lg border p-3">
                  <Label htmlFor={`replacement-source-url-${selected.id}`}>URL nguồn chính thức của văn bản thay thế</Label>
                  <Input
                    id={`replacement-source-url-${selected.id}`}
                    type="url"
                    inputMode="url"
                    autoComplete="off"
                    value={replacementSourceUrls[selected.id] || ''}
                    onChange={(change) => setReplacementSourceUrls((current) => ({ ...current, [selected.id]: change.target.value }))}
                    placeholder="https://vbpl.vn/.../van-ban-moi"
                    disabled={busy !== null}
                    aria-describedby={`replacement-source-url-help-${selected.id}`}
                  />
                  <p id={`replacement-source-url-help-${selected.id}`} className="text-xs text-muted-foreground">Dán đúng trang chính thức của văn bản mới để hệ thống kiểm tra số hiệu, cơ quan và quan hệ thay thế. Không dùng lại URL của văn bản cũ.</p>
                </div>

                <div className="space-y-2 rounded-lg border p-3">
                  <Label htmlFor={`replacement-file-${selected.id}`}>Tệp PDF bổ sung (không bắt buộc)</Label>
                  <Input id={`replacement-file-${selected.id}`} type="file" accept=".pdf,application/pdf" disabled={busy !== null} onChange={(change) => void extractReplacementFile(selected, change.target.files?.[0] || null)} />
                  <p className="mt-1 text-xs text-muted-foreground">Không bắt buộc. Nếu không tải tệp, hệ thống sẽ tự crawl toàn văn từ URL chính thức ở trên.</p>
                  {replacementFiles[selected.id] && <p className="mt-2 rounded bg-emerald-50 p-2 text-xs text-emerald-800">Đã trích xuất {replacementFiles[selected.id].filename}: {replacementFiles[selected.id].characters.toLocaleString('vi-VN')} ký tự.</p>}
                </div>

                <div className="grid gap-2" aria-label="Ba thao tác xử lý văn bản">
                  <Button type="button" disabled={busy !== null || reasonLength < 10 || (replacementSourceUrls[selected.id] || '').trim().length < 8 || !client.createReplacementWorkflow} onClick={() => void replaceDocument(selected)}>{busy === 'replace' ? 'Đang đọc nguồn, tạo dữ liệu tra cứu và kích hoạt…' : 'Thay bằng văn bản thay thế'}</Button>
                  <Button type="button" variant="outline" disabled={busy !== null || reasonLength < 10} onClick={() => void finishDecision(selected, 'mark_historical')}>{busy === 'mark_historical' ? 'Đang chuyển…' : 'Đưa vào tra cứu lịch sử'}</Button>
                  <Button type="button" variant="destructive" disabled={busy !== null || reasonLength < 10} onClick={() => setPendingQuarantine(selected)}>Cách ly khỏi tra cứu</Button>
                </div>
              </div>
            )}
          </aside>
        </div>
      </CardContent>
      {pendingQuarantine && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true" aria-labelledby="quarantine-title">
          <div className="w-full max-w-lg rounded-2xl border bg-background p-6 shadow-2xl">
            <h3 id="quarantine-title" className="text-lg font-semibold">Cách ly {pendingQuarantine.law_number} khỏi tra cứu?</h3>
            <div className="mt-3 space-y-2 text-sm text-muted-foreground">
              <p>Sau khi xác nhận:</p>
              <ul className="list-disc space-y-1 pl-5">
                <li>Văn bản không còn được dùng để trả lời hỏi đáp hiện tại.</li>
                <li>Văn bản cũng không xuất hiện trong tra cứu lịch sử.</li>
                <li>Dữ liệu gốc không bị xóa; vẫn còn trong kho quản trị và nhật ký kiểm toán.</li>
                <li>Hệ thống chỉ báo thành công sau khi xác nhận văn bản đã bị loại khỏi tìm kiếm.</li>
              </ul>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setPendingQuarantine(null)}>Hủy</Button>
              <Button type="button" variant="destructive" onClick={() => void finishDecision(pendingQuarantine, 'quarantine')}>Xác nhận cách ly</Button>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
