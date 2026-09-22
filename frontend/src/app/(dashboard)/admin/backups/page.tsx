'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  HardDriveDownload,
  Loader2,
  RefreshCw,
  RotateCcw,
  SearchCheck,
  ShieldCheck,
} from 'lucide-react'
import { toast } from 'sonner'

import { AppShell } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { adminBackupsApi, type BackupManifest, type BackupPreflight } from '@/lib/api/admin-backups'

function formatBytes(value?: number | null): string {
  if (!value || value <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = value
  let index = 0
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1 }
  return `${amount.toLocaleString('vi-VN', { maximumFractionDigits: 1 })} ${units[index]}`
}

function formatTime(value?: string | null): string {
  if (!value) return 'Chưa có'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })
}

function statusLabel(status?: string | null): string {
  return {
    planned: 'Đã xếp lịch', running: 'Đang sao lưu', verifying: 'Đang kiểm chứng',
    successful: 'Đã hoàn tất', restore_drill_passed: 'Đã diễn tập khôi phục',
    partial: 'Hoàn tất một phần', failed: 'Thất bại', interrupted: 'Bị gián đoạn',
  }[status || ''] || 'Chưa xác định'
}

function statusClass(status?: string | null): string {
  if (status === 'successful' || status === 'restore_drill_passed') return 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200'
  if (status === 'partial' || status === 'verifying' || status === 'running' || status === 'planned') return 'border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200'
  return 'border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-200'
}

function warningLabel(value: string): string {
  return {
    POSTGRES_URL_NOT_CONFIGURED: 'Chưa cấu hình kết nối PostgreSQL',
    SURREAL_URL_NOT_CONFIGURED: 'Chưa cấu hình kết nối SurrealDB',
    PG_DUMP_NOT_FOUND: 'Máy chủ chưa có công cụ pg_dump',
    SURREAL_EXPORT_TOOL_NOT_FOUND: 'Máy chủ chưa có công cụ xuất SurrealDB',
    BACKUP_ENCRYPTION_NOT_CONFIGURED: 'Chưa cấu hình mã hóa gói sao lưu',
    BACKUP_ENCRYPTION_KEY_UNAVAILABLE: 'Không đọc được khóa mã hóa backup trên máy chủ',
    PG_RESTORE_NOT_FOUND: 'Máy chủ chưa có công cụ pg_restore để kiểm chứng dump',
    APP_DATA_SOURCE_NOT_FOUND: 'Không tìm thấy thư mục dữ liệu ứng dụng',
    CHROMA_SOURCE_NOT_FOUND: 'Không tìm thấy kho Chroma',
    LEGAL_WRITE_JOB_RUNNING: 'Đang có tác vụ ghi dữ liệu pháp lý',
  }[value] || value
}

export default function AdminBackupsPage() {
  const [preflight, setPreflight] = useState<BackupPreflight | null>(null)
  const [items, setItems] = useState<BackupManifest[]>([])
  const [selected, setSelected] = useState<BackupManifest | null>(null)
  const [reason, setReason] = useState('')
  const [kind, setKind] = useState<'full' | 'application' | 'retrieval'>('full')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [actionLoading, setActionLoading] = useState('')

  const applyList = useCallback((nextList: { items: BackupManifest[] }) => {
    setItems(nextList.items || [])
    setSelected(current => current ? (nextList.items || []).find(item => item.backup_id === current.backup_id) || current : null)
  }, [])

  const loadList = useCallback(async () => {
    const nextList = await adminBackupsApi.list()
    applyList(nextList)
  }, [applyList])

  const load = useCallback(async () => {
    try {
      const [nextPreflight, nextList] = await Promise.all([adminBackupsApi.preflight(), adminBackupsApi.list()])
      setPreflight(nextPreflight)
      applyList(nextList)
    } catch {
      toast.error('Không tải được trạng thái sao lưu. Bạn kiểm tra quyền Admin rồi thử lại.')
    } finally {
      setLoading(false)
    }
  }, [applyList])

  useEffect(() => { void load() }, [load])

  const hasActiveJob = useMemo(() => items.some(item => ['planned', 'running', 'verifying'].includes(item.status || '')), [items])
  useEffect(() => {
    if (!hasActiveJob) return undefined
    // Poll the small status list while a job is active. Preflight inventories
    // can include large Chroma/application stores, so refresh that only when
    // the admin explicitly refreshes or starts a new job.
    const timer = window.setInterval(() => { void loadList() }, 2500)
    return () => window.clearInterval(timer)
  }, [hasActiveJob, loadList])

  const create = async () => {
    if (reason.trim().length < 3) {
      toast.error('Hãy ghi lý do nghiệp vụ trước khi tạo bản sao.')
      return
    }
    setCreating(true)
    try {
      const result = await adminBackupsApi.create(reason.trim(), kind)
      toast.success(`Đã tạo công việc ${result.backup_id}.`)
      setReason('')
      await load()
    } catch {
      toast.error('Không thể tạo công việc sao lưu. Kiểm tra preflight và thử lại.')
    } finally {
      setCreating(false)
    }
  }

  const select = async (item: BackupManifest) => {
    setSelected(item)
    try { setSelected(await adminBackupsApi.get(item.backup_id)) } catch { /* list data remains usable */ }
  }

  const verify = async () => {
    if (!selected) return
    setActionLoading('verify')
    try {
      const result = await adminBackupsApi.verify(selected.backup_id)
      toast[result.passed ? 'success' : 'error'](result.passed ? 'Checksum bản sao khớp.' : 'Checksum không khớp; bản sao không được coi là điểm khôi phục.')
      await load()
      setSelected(await adminBackupsApi.get(selected.backup_id))
    } catch { toast.error('Không thể kiểm chứng bản sao.') } finally { setActionLoading('') }
  }

  const drill = async () => {
    if (!selected) return
    setActionLoading('drill')
    try {
      const result = await adminBackupsApi.restoreDrill(selected.backup_id)
      toast[result.passed ? 'success' : 'error'](result.passed ? 'Diễn tập khôi phục cô lập đã đạt.' : 'Diễn tập khôi phục chưa đạt.')
      await load()
      setSelected(await adminBackupsApi.get(selected.backup_id))
    } catch { toast.error('Không thể diễn tập khôi phục bản sao.') } finally { setActionLoading('') }
  }

  const latest = items[0]

  return (
    <AppShell>
      <main className="min-h-0 flex-1 overflow-auto bg-muted/20 p-4 pt-16 md:p-8 md:pt-8">
        <div className="mx-auto max-w-6xl space-y-5">
          <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-bold"><HardDriveDownload className="h-6 w-6 text-primary" aria-hidden="true" /> Sao lưu dữ liệu</h1>
              <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Tạo bản sao có manifest và checksum cho dữ liệu pháp lý, tìm kiếm và ứng dụng. Bản đầu tiên chỉ cho kiểm chứng và diễn tập khôi phục cô lập.</p>
            </div>
            <Button variant="outline" className="min-h-11 gap-2" onClick={() => void load()} disabled={loading}><RefreshCw className="h-4 w-4" aria-hidden="true" /> Làm mới</Button>
          </header>

          <section className="grid gap-4 md:grid-cols-3">
            <Card><CardContent className="flex items-start justify-between gap-3 p-4"><div><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Bản sao gần nhất</p><p className="mt-1 text-sm font-semibold">{latest ? formatTime(latest.created_at) : 'Chưa có'}</p><p className="mt-1 text-xs text-muted-foreground">{latest ? statusLabel(latest.status) : 'Tạo bản sao đầu tiên để bắt đầu'}</p></div><Clock3 className="h-5 w-5 text-primary/60" aria-hidden="true" /></CardContent></Card>
            <Card><CardContent className="flex items-start justify-between gap-3 p-4"><div><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Trạng thái đích</p><p className="mt-1 text-sm font-semibold">{preflight?.writable ? 'Có thể ghi' : 'Chưa sẵn sàng'}</p><p className="mt-1 text-xs text-muted-foreground">Còn {formatBytes(preflight?.free_bytes)}</p></div><ShieldCheck className={`h-5 w-5 ${preflight?.writable ? 'text-emerald-600' : 'text-red-600'}`} aria-hidden="true" /></CardContent></Card>
            <Card><CardContent className="flex items-start justify-between gap-3 p-4"><div><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Dung lượng dự kiến</p><p className="mt-1 text-sm font-semibold">{formatBytes(preflight?.reserved_bytes)}</p><p className="mt-1 text-xs text-muted-foreground">Chưa tính chính xác dump cơ sở dữ liệu</p></div><HardDriveDownload className="h-5 w-5 text-primary/60" aria-hidden="true" /></CardContent></Card>
          </section>

          <section className="grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
            <Card>
              <CardHeader><CardTitle className="text-lg">Kiểm tra trước khi sao lưu</CardTitle><CardDescription>Không copy khi đang có tác vụ ghi dữ liệu pháp lý.</CardDescription></CardHeader>
              <CardContent className="space-y-4">
                <div className="rounded-xl border border-border/70 bg-muted/20 p-3 text-sm">
                  <div className="flex items-center justify-between gap-3"><span>Quyền ghi thư mục đích</span><Badge variant="outline" className={preflight?.writable ? 'border-emerald-300 text-emerald-700' : 'border-red-300 text-red-700'}>{preflight?.writable ? 'Sẵn sàng' : 'Bị chặn'}</Badge></div>
                  <div className="mt-2 flex items-center justify-between gap-3"><span>Tác vụ ghi đang chạy</span><span className="font-semibold">{preflight?.write_jobs ?? '—'}</span></div>
                  <div className="mt-2 flex items-center justify-between gap-3"><span>Thành phần dự kiến</span><span className="font-semibold">{preflight?.components?.length ?? '—'}</span></div>
                </div>
                {preflight?.warnings?.length ? <div className="space-y-2 rounded-xl border border-amber-300/60 bg-amber-50/70 p-3 text-sm text-amber-900 dark:bg-amber-950/20 dark:text-amber-100"><p className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4" aria-hidden="true" /> Cảnh báo preflight</p><ul className="list-disc space-y-1 pl-5">{preflight.warnings.map(item => <li key={item}>{warningLabel(item)}</li>)}</ul></div> : null}
                <div className="space-y-3 border-t border-border/70 pt-4">
                  <label htmlFor="backup-reason" className="text-sm font-medium">Lý do nghiệp vụ</label>
                  <Textarea id="backup-reason" value={reason} onChange={event => setReason(event.target.value)} rows={3} maxLength={1000} placeholder="Ví dụ: Sao lưu trước đợt cập nhật dữ liệu thủ tục tháng 9." disabled={creating} />
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="text-sm font-medium" htmlFor="backup-kind">Phạm vi</label>
                    <select id="backup-kind" className="h-11 rounded-lg border border-input bg-background px-3 text-sm" value={kind} onChange={event => setKind(event.target.value as typeof kind)} disabled={creating}><option value="full">Đầy đủ</option><option value="application">Dữ liệu ứng dụng</option><option value="retrieval">Kho tìm kiếm</option></select>
                  </div>
                  <Button className="min-h-11 w-full gap-2" onClick={() => void create()} disabled={creating || !preflight || !preflight.can_start}>{creating ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <HardDriveDownload className="h-4 w-4" aria-hidden="true" />}{creating ? 'Đang xếp công việc…' : 'Tạo bản sao'}</Button>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader><div className="flex items-center justify-between gap-3"><div><CardTitle className="text-lg">Lịch sử bản sao</CardTitle><CardDescription>Chọn một manifest để kiểm chứng hoặc diễn tập khôi phục.</CardDescription></div><Badge variant="outline">{items.length} bản sao</Badge></div></CardHeader>
              <CardContent className="space-y-3">
                {loading ? <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Đang tải lịch sử…</div> : items.length === 0 ? <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">Chưa có bản sao nào.</div> : <div className="space-y-2">{items.map(item => <button key={item.backup_id} type="button" onClick={() => void select(item)} className={`w-full rounded-xl border p-3 text-left transition-colors hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected?.backup_id === item.backup_id ? 'border-primary bg-primary/5' : 'border-border/70 bg-background/50'}`}><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-xs text-muted-foreground">{item.backup_id}</span><Badge variant="outline" className={statusClass(item.status)}>{statusLabel(item.status)}</Badge></div><div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"><span>{formatTime(item.created_at)}</span><span>{item.kind === 'full' ? 'Đầy đủ' : item.kind === 'application' ? 'Ứng dụng' : 'Kho tìm kiếm'}</span><span>{item.actor_id || 'Admin'}</span></div></button>)}</div>}
              </CardContent>
            </Card>
          </section>

          {selected && (
            <Card aria-live="polite">
              <CardHeader><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><CardTitle className="text-lg">Chi tiết bản sao</CardTitle><CardDescription>{selected.reason || 'Không có lý do'} · tạo lúc {formatTime(selected.created_at)}</CardDescription></div><Badge variant="outline" className={statusClass(selected.status)}>{statusLabel(selected.status)}</Badge></div></CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{(selected.components || []).map(component => <div key={`${component.name}-${component.source}`} className="rounded-xl border border-border/70 p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{component.name}</span>{component.status === 'successful' ? <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-label="Đã hoàn tất" /> : <AlertTriangle className="h-4 w-4 text-amber-600" aria-label={component.status || 'Cần chú ý'} />}</div><p className="mt-1 text-xs text-muted-foreground">{component.status || '—'} · {formatBytes(component.bytes)}</p></div>)}</div>
                {selected.warnings?.length ? <div className="rounded-xl border border-amber-300/60 bg-amber-50/70 p-3 text-sm text-amber-900 dark:bg-amber-950/20 dark:text-amber-100"><p className="font-semibold">Cảnh báo</p><ul className="mt-1 list-disc space-y-1 pl-5">{selected.warnings.map(item => <li key={item}>{warningLabel(item)}</li>)}</ul></div> : null}
                <div className="flex flex-wrap gap-2 border-t border-border/70 pt-4"><Button variant="outline" className="min-h-11 gap-2" onClick={() => void verify()} disabled={Boolean(actionLoading)}>{actionLoading === 'verify' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <SearchCheck className="h-4 w-4" aria-hidden="true" />} Kiểm chứng checksum</Button><Button variant="outline" className="min-h-11 gap-2" onClick={() => void drill()} disabled={Boolean(actionLoading) || !['successful', 'restore_drill_passed'].includes(selected.status || '')}>{actionLoading === 'drill' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RotateCcw className="h-4 w-4" aria-hidden="true" />} Diễn tập khôi phục cô lập</Button></div>
                <p className="text-xs leading-5 text-muted-foreground">Diễn tập chỉ sao chép sang thư mục cô lập. Không có thao tác ghi đè dữ liệu production từ màn hình này.</p>
              </CardContent>
            </Card>
          )}
        </div>
      </main>
    </AppShell>
  )
}
