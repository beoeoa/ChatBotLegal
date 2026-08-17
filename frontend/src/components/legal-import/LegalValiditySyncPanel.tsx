'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Clock3, RefreshCcw, Search, ShieldAlert, TriangleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  legalImportApi,
  type LegalValidityDecisionAction,
  type LegalValidityDocumentTimeline,
  type LegalValidityEvent,
  type LegalValiditySyncClient,
  type LegalValiditySyncStatus,
  type LegalVectorCleanupManifest,
} from '@/lib/api/legal-import'

interface LegalValiditySyncPanelProps {
  client?: LegalValiditySyncClient
}

function dateTime(value?: string | null): string {
  if (!value) return 'Chưa có'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Chưa xác định'
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' }).format(parsed)
}

function healthCopy(status: LegalValiditySyncStatus['status']) {
  if (status === 'healthy') {
    return { title: 'Đồng bộ hiệu lực đang ổn định', tone: 'text-emerald-700', icon: CheckCircle2 }
  }
  if (status === 'stale') {
    return { title: 'Dữ liệu hiệu lực đã cũ', tone: 'text-amber-700', icon: Clock3 }
  }
  if (status === 'missing') {
    return { title: 'Chưa có dữ liệu kiểm tra hiệu lực', tone: 'text-red-700', icon: ShieldAlert }
  }
  return { title: 'Nguồn kiểm tra đang gián đoạn', tone: 'text-red-700', icon: TriangleAlert }
}

function eventStatusLabel(event: LegalValidityEvent): string {
  const labels: Record<string, string> = {
    active: 'Còn hiệu lực',
    not_yet_effective: 'Chưa có hiệu lực',
    expired: 'Hết hiệu lực',
    expired_partial: 'Hết hiệu lực một phần',
    suspended: 'Tạm ngưng hiệu lực',
    suspended_partial: 'Tạm ngưng một phần',
    amended: 'Đã được sửa đổi, bổ sung',
    replaced: 'Đã được thay thế',
    repealed: 'Đã bị bãi bỏ',
    unknown: 'Chưa xác minh được',
  }
  return labels[event.normalized_status || ''] || event.raw_status || 'Chưa xác định'
}

function priorityLabel(event: LegalValidityEvent): string {
  return event.severity === 'critical' ? 'Ưu tiên cao' : 'Cần kiểm tra'
}

function servingLabel(event: LegalValidityEvent): string {
  if (event.serving_action === 'deactivate') return 'Tạm ngừng phục vụ tra cứu'
  if (event.serving_action === 'activate') return 'Có thể đưa vào phục vụ tra cứu'
  if (event.serving_action === 'historical_only') return 'Chỉ dùng để tra cứu lịch sử'
  return 'Cần quản trị viên quyết định'
}

export function LegalValiditySyncPanel({
  client = legalImportApi as LegalValiditySyncClient,
}: LegalValiditySyncPanelProps) {
  const [status, setStatus] = useState<LegalValiditySyncStatus | null>(null)
  const [events, setEvents] = useState<LegalValidityEvent[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [priority, setPriority] = useState('all')
  const [validity, setValidity] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [runReason, setRunReason] = useState('')
  const [decisionReasons, setDecisionReasons] = useState<Record<string, string>>({})
  const [documentAnalysis, setDocumentAnalysis] = useState<Record<string, LegalValidityDocumentTimeline>>({})
  const [cleanupPreviews, setCleanupPreviews] = useState<Record<string, LegalVectorCleanupManifest | null>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(false)
    try {
      const [nextStatus, page] = await Promise.all([
        client.validityStatus(),
        client.validityEvents({ review_status: 'open', limit: 50 }),
      ])
      const nextEvents = page.items || []
      setStatus(nextStatus)
      setEvents(nextEvents)
      setSelectedId((current) => current && nextEvents.some((item) => item.id === current)
        ? current
        : nextEvents[0]?.id || null)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void load()
  }, [load])

  const health = useMemo(() => status ? healthCopy(status.status) : null, [status])
  const filteredEvents = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('vi')
    return events.filter((event) => {
      const matchesQuery = !needle || [event.law_number, event.document_title]
        .filter(Boolean)
        .some((value) => value?.toLocaleLowerCase('vi').includes(needle))
      const matchesPriority = priority === 'all'
        || (priority === 'critical' ? event.severity === 'critical' : event.severity !== 'critical')
      const matchesValidity = validity === 'all' || event.normalized_status === validity
      return matchesQuery && matchesPriority && matchesValidity
    })
  }, [events, priority, query, validity])
  const selected = events.find((event) => event.id === selectedId) || filteredEvents[0] || null

  async function runNow() {
    const reason = runReason.trim()
    if (reason.length < 10) return
    setBusy('run')
    setActionMessage(null)
    try {
      await client.runValiditySync({ reason, scope: ['central', 'haiphong', 'local'], limit: 100 })
      setRunReason('')
      await load()
      setActionMessage({ tone: 'success', text: 'Đã kiểm tra lại nguồn chính thức và cập nhật danh sách.' })
    } catch {
      setActionMessage({ tone: 'error', text: 'Không thể kiểm tra lúc này. Dữ liệu đã xác minh vẫn được giữ nguyên.' })
    } finally {
      setBusy(null)
    }
  }

  async function decide(event: LegalValidityEvent, action: LegalValidityDecisionAction) {
    const reason = (decisionReasons[event.id] || '').trim()
    if (reason.length < 10) return
    setBusy(event.id)
    setActionMessage(null)
    try {
      await client.decideValidityEvent(event.id, { action, reason })
      await load()
      setActionMessage({ tone: 'success', text: 'Đã lưu kết quả đối chiếu hiệu lực.' })
    } catch {
      setActionMessage({ tone: 'error', text: 'Không lưu được kết quả đối chiếu. Vui lòng thử lại.' })
    } finally {
      setBusy(null)
    }
  }

  async function inspectDocument(event: LegalValidityEvent) {
    if (!event.document_id) return
    setBusy(`inspect:${event.id}`)
    setActionMessage(null)
    try {
      const timeline = await client.validityDocument(event.document_id)
      setDocumentAnalysis((current) => ({ ...current, [event.id]: timeline }))
      try {
        const preview = await client.previewValidityVectorCleanup(event.document_id)
        setCleanupPreviews((current) => ({ ...current, [event.id]: preview }))
      } catch {
        setCleanupPreviews((current) => ({ ...current, [event.id]: null }))
      }
    } catch {
      setActionMessage({ tone: 'error', text: 'Không thể tải hồ sơ hiệu lực. Hệ thống chưa thay đổi dữ liệu.' })
    } finally {
      setBusy(null)
    }
  }

  async function cleanupSearchIndex(event: LegalValidityEvent) {
    if (!event.document_id) return
    const reason = (decisionReasons[event.id] || '').trim()
    if (reason.length < 10) return
    setBusy(`cleanup:${event.id}`)
    setActionMessage(null)
    try {
      const result = await client.cleanupValidityVectors(event.document_id, { reason })
      setCleanupPreviews((current) => ({ ...current, [event.id]: result }))
      setActionMessage(result.state === 'vector_cleanup_completed'
        ? { tone: 'success', text: 'Đã cập nhật chỉ mục tìm kiếm theo trạng thái hiệu lực đã xác minh.' }
        : { tone: 'error', text: 'Chỉ mục chưa cập nhật hoàn tất. Văn bản vẫn bị chặn phục vụ để bảo đảm an toàn.' })
    } catch {
      setActionMessage({ tone: 'error', text: 'Không thể cập nhật chỉ mục. Văn bản vẫn bị chặn phục vụ.' })
    } finally {
      setBusy(null)
    }
  }

  if (loading && !status) {
    return <Card><CardContent className="py-8 text-sm text-muted-foreground">Đang tải tình trạng hiệu lực…</CardContent></Card>
  }

  if (error && !status) {
    return (
      <Card className="border-red-200">
        <CardContent className="flex items-center justify-between gap-4 py-6">
          <p className="text-sm text-red-700">Không tải được trạng thái đồng bộ hiệu lực.</p>
          <Button type="button" variant="outline" onClick={() => void load()}>Thử lại</Button>
        </CardContent>
      </Card>
    )
  }

  if (!status || !health) return null
  const HealthIcon = health.icon
  const degradedSource = status.sources.find((source) => source.status === 'degraded')
  const observedCoverage = Math.min(status.coverage.observed, status.coverage.eligible)
  const freshCoverage = Math.min(status.coverage.fresh, status.coverage.eligible)
  const coverageMismatch = status.coverage.observed > status.coverage.eligible

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className={`flex items-center gap-2 font-semibold ${health.tone}`}>
                <HealthIcon className="h-5 w-5" />
                {health.title}
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {status.counts.open_events} văn bản đang chờ quản trị viên đối chiếu.
              </p>
            </div>
            <p className="text-sm text-muted-foreground">Cập nhật gần nhất: {dateTime(status.last_success_at)}</p>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Đã kiểm tra</p>
              <p className="font-medium">{observedCoverage}/{status.coverage.eligible} văn bản</p>
            </div>
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Dữ liệu còn mới</p>
              <p className="font-medium">{freshCoverage} văn bản</p>
            </div>
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Lần kiểm tra kế tiếp</p>
              <p className="font-medium">{dateTime(status.next_run_at)}</p>
            </div>
          </div>

          {coverageMismatch && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
              Số liệu kiểm tra từ máy chủ chưa đồng nhất.
            </div>
          )}
          {degradedSource && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
              Nguồn VBPL đang gián đoạn. Dữ liệu đã xác minh gần nhất vẫn được giữ nguyên.
            </div>
          )}
          {actionMessage && (
            <div role="status" className={`rounded-lg border p-3 text-sm ${actionMessage.tone === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-red-200 bg-red-50 text-red-800'}`}>
              {actionMessage.text}
            </div>
          )}

          <details className="rounded-lg border px-4 py-3">
            <summary className="cursor-pointer text-sm font-medium">Kiểm tra lại dữ liệu từ nguồn chính thức</summary>
            <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
              <div className="space-y-2">
                <Label htmlFor="validity-run-reason">Lý do kiểm tra ngay</Label>
                <Textarea
                  id="validity-run-reason"
                  value={runReason}
                  onChange={(event) => setRunReason(event.target.value)}
                  placeholder="Ví dụ: Kiểm tra sau khi nguồn chính thức công bố văn bản mới…"
                />
              </div>
              <Button type="button" onClick={() => void runNow()} disabled={busy !== null || runReason.trim().length < 10}>
                <RefreshCcw className="mr-2 h-4 w-4" />
                {busy === 'run' ? 'Đang kiểm tra…' : 'Kiểm tra ngay'}
              </Button>
            </div>
          </details>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Danh sách cần đối chiếu</CardTitle>
          <CardDescription>Chọn một văn bản để xem bằng chứng và ghi nhận kết quả ở khung bên phải.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-[minmax(240px,1fr)_200px_220px]">
            <Label className="space-y-2">
              <span>Tìm kiếm văn bản</span>
              <span className="relative block">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Số hoặc tên văn bản" />
              </span>
            </Label>
            <Label className="space-y-2">
              <span>Mức độ</span>
              <select className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={priority} onChange={(event) => setPriority(event.target.value)}>
                <option value="all">Tất cả</option>
                <option value="critical">Ưu tiên cao</option>
                <option value="normal">Cần kiểm tra</option>
              </select>
            </Label>
            <Label className="space-y-2">
              <span>Tình trạng hiệu lực</span>
              <select className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={validity} onChange={(event) => setValidity(event.target.value)}>
                <option value="all">Tất cả</option>
                <option value="active">Còn hiệu lực</option>
                <option value="not_yet_effective">Chưa có hiệu lực</option>
                <option value="expired">Hết hiệu lực</option>
                <option value="expired_partial">Hết hiệu lực một phần</option>
                <option value="unknown">Chưa xác minh được</option>
              </select>
            </Label>
          </div>

          <div className="grid min-h-[420px] gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.75fr)]">
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[720px] text-left text-sm" aria-label="Danh sách văn bản cần kiểm tra hiệu lực">
                <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 font-medium">Mức độ</th>
                    <th className="px-4 py-3 font-medium">Văn bản</th>
                    <th className="px-4 py-3 font-medium">Hệ thống phát hiện</th>
                    <th className="px-4 py-3 font-medium">Trạng thái</th>
                    <th className="px-4 py-3"><span className="sr-only">Thao tác</span></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEvents.map((event) => (
                    <tr key={event.id} className={`border-b last:border-0 ${selected?.id === event.id ? 'bg-primary/5' : ''}`}>
                      <td className="px-4 py-3"><Badge variant={event.severity === 'critical' ? 'destructive' : 'secondary'}>{priorityLabel(event)}</Badge></td>
                      <td className="px-4 py-3">
                        <p className="font-medium">{event.law_number}</p>
                        {event.document_title && <p className="line-clamp-2 text-muted-foreground">{event.document_title}</p>}
                      </td>
                      <td className="px-4 py-3">{dateTime(event.created_at)}</td>
                      <td className="px-4 py-3">{eventStatusLabel(event)}</td>
                      <td className="px-4 py-3 text-right"><Button type="button" size="sm" variant="outline" onClick={() => setSelectedId(event.id)}>Xem</Button></td>
                    </tr>
                  ))}
                  {filteredEvents.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-10 text-center text-muted-foreground">Không có văn bản phù hợp.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <aside className="rounded-lg border bg-muted/15 p-4">
              <h2 className="text-base font-semibold">Chi tiết đối chiếu</h2>
              {!selected ? (
                <p className="mt-3 text-sm text-muted-foreground">Chọn một văn bản trong danh sách để xem chi tiết.</p>
              ) : (
                <div className="mt-4 space-y-4">
                  <div>
                    <p className="font-semibold">{selected.law_number}</p>
                    {selected.document_title && <p className="mt-1 text-sm text-muted-foreground">{selected.document_title}</p>}
                  </div>
                  <dl className="grid gap-3 text-sm">
                    <div><dt className="text-muted-foreground">Tình trạng nguồn ghi nhận</dt><dd className="font-medium">{eventStatusLabel(selected)}</dd></div>
                    <div><dt className="text-muted-foreground">Ảnh hưởng đến tra cứu</dt><dd className="font-medium">{servingLabel(selected)}</dd></div>
                    <div><dt className="text-muted-foreground">Thời điểm phát hiện</dt><dd className="font-medium">{dateTime(selected.created_at)}</dd></div>
                  </dl>
                  {selected.source_url && <a href={selected.source_url} target="_blank" rel="noreferrer" className="inline-flex text-sm font-medium text-primary hover:underline">Mở bằng chứng nguồn</a>}
                  <div className="space-y-2">
                    <Label htmlFor={`validity-decision-${selected.id}`}>Lý do xử lý {selected.law_number}</Label>
                    <Textarea
                      id={`validity-decision-${selected.id}`}
                      value={decisionReasons[selected.id] || ''}
                      onChange={(change) => setDecisionReasons((current) => ({ ...current, [selected.id]: change.target.value }))}
                      placeholder="Ghi rõ nội dung đã đối chiếu với nguồn chính thức…"
                    />
                    <p className="text-xs text-muted-foreground">Cần ghi lý do cụ thể, tối thiểu 10 ký tự.</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" disabled={busy !== null || (decisionReasons[selected.id] || '').trim().length < 10} onClick={() => void decide(selected, 'confirm_mapping')}>Xác nhận kết quả</Button>
                    <Button type="button" size="sm" variant="outline" disabled={busy !== null || (decisionReasons[selected.id] || '').trim().length < 10} onClick={() => void decide(selected, 'reject_match')}>Kết quả không đúng</Button>
                    <Button type="button" size="sm" variant="secondary" disabled={busy !== null || (decisionReasons[selected.id] || '').trim().length < 10} onClick={() => void decide(selected, 'request_recheck')}>Yêu cầu kiểm tra lại</Button>
                  </div>
                  {selected.document_id && (
                    <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void inspectDocument(selected)}>
                      {busy === `inspect:${selected.id}` ? 'Đang kiểm tra…' : 'Kiểm tra quan hệ thay thế'}
                    </Button>
                  )}
                  {documentAnalysis[selected.id] && (
                    <div className="space-y-3 rounded-lg border bg-background p-3 text-sm">
                      <p className="font-medium">Văn bản có thể thay thế</p>
                      {documentAnalysis[selected.id].replacement_discovery.candidates.length > 0 ? documentAnalysis[selected.id].replacement_discovery.candidates.map((candidate) => (
                        <div key={candidate.law_number} className="rounded-md border p-2">
                          <p><strong>{candidate.law_number}</strong></p>
                          <p className="text-muted-foreground">Chờ quản trị viên đối chiếu; hệ thống chưa tự kích hoạt.</p>
                          <a href={candidate.source_url} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">Mở nguồn quan hệ thay thế</a>
                        </div>
                      )) : <p className="text-muted-foreground">Nguồn hiện tại chưa ghi nhận quan hệ thay thế trực tiếp. Hệ thống không tự suy đoán.</p>}
                      <details className="rounded-md border px-3 py-2">
                        <summary className="cursor-pointer font-medium">Thông tin chỉ mục tìm kiếm</summary>
                        {cleanupPreviews[selected.id] ? (
                          <div className="mt-3">
                            <p>{cleanupPreviews[selected.id]?.expected_vector_count} đoạn dữ liệu sẽ được cập nhật sau khi văn bản đã được xác minh và chặn phục vụ.</p>
                            <Button type="button" size="sm" variant="destructive" className="mt-2" disabled={busy !== null || (decisionReasons[selected.id] || '').trim().length < 10} onClick={() => void cleanupSearchIndex(selected)}>
                              {busy === `cleanup:${selected.id}` ? 'Đang cập nhật…' : 'Cập nhật chỉ mục tìm kiếm'}
                            </Button>
                          </div>
                        ) : <p className="mt-2 text-muted-foreground">Chưa đủ điều kiện cập nhật chỉ mục tìm kiếm.</p>}
                      </details>
                    </div>
                  )}
                </div>
              )}
            </aside>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
