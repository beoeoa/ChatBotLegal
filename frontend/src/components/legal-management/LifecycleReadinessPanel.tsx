'use client'

import Link from 'next/link'
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CalendarClock, FilePlus2, Layers3, ShieldCheck } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  legalManagementApi,
  type ChangeEventCandidate,
  type Feature018LifecycleDocument,
  type Feature018LifecycleSummary,
  type Feature018VectorManifest,
  type LifecycleBucket,
} from '@/lib/api/legal-management'

const BUCKETS: Array<[LifecycleBucket, string]> = [
  ['active', 'Đang có hiệu lực'],
  ['future', 'Sắp có hiệu lực'],
  ['expiring_90', 'Hết hạn trong 90 ngày'],
  ['expiring_30', 'Hết hạn trong 30 ngày'],
  ['expiring_7', 'Hết hạn trong 7 ngày'],
  ['expiring_1', 'Hết hạn trong 1 ngày'],
  ['expired', 'Đã hết hiệu lực'],
  ['partially_expired', 'Hết hiệu lực một phần'],
  ['replaced', 'Đã bị thay thế'],
  ['repealed', 'Đã bị bãi bỏ'],
  ['suspended', 'Đang đình chỉ'],
  ['unknown', 'Chưa rõ hiệu lực'],
]

const EVENT_TYPES = [
  ['expiry', 'Hết hiệu lực'],
  ['replace', 'Bị thay thế'],
  ['repeal', 'Bị bãi bỏ'],
  ['suspend', 'Đình chỉ'],
  ['restore', 'Khôi phục hiệu lực'],
  ['correct', 'Đính chính'],
] as const

function today() {
  return new Date().toISOString().slice(0, 10)
}

function bucketLabel(bucket: string) {
  return BUCKETS.find(([value]) => value === bucket)?.[1] || bucket
}

function indexStateLabel(state: string) {
  const labels: Record<string, string> = {
    active: 'Đang phục vụ',
    historical: 'Lưu trữ lịch sử',
    staging: 'Chờ phát hành',
    missing: 'Thiếu dữ liệu',
    orphan: 'Không còn văn bản nguồn',
    duplicate: 'Trùng dữ liệu',
    fingerprint_mismatch: 'Dữ liệu không đồng bộ',
  }
  return labels[state] || state
}

function CountCard({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="rounded-lg border bg-background p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function LifecycleReadinessPanel() {
  const legalAsOf = useMemo(today, [])
  const [summary, setSummary] = useState<Feature018LifecycleSummary | null>(null)
  const [documents, setDocuments] = useState<Feature018LifecycleDocument[]>([])
  const [manifest, setManifest] = useState<Feature018VectorManifest | null>(null)
  const [bucket, setBucket] = useState<LifecycleBucket | ''>('')
  const [vectorState, setVectorState] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [documentId, setDocumentId] = useState('')
  const [eventType, setEventType] = useState('expiry')
  const [effectiveFrom, setEffectiveFrom] = useState(legalAsOf)
  const [sourceUrl, setSourceUrl] = useState('')
  const [candidateReason, setCandidateReason] = useState('')
  const [confirmReason, setConfirmReason] = useState('')
  const [candidate, setCandidate] = useState<ChangeEventCandidate | null>(null)
  const [actionMessage, setActionMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    const [summaryResult, documentsResult, manifestResult] = await Promise.allSettled([
      legalManagementApi.lifecycleSummary(legalAsOf),
      legalManagementApi.lifecycleDocuments({
        legal_as_of: legalAsOf,
        bucket: bucket || undefined,
        vector_state: vectorState || undefined,
      }),
      legalManagementApi.activeIndexManifest(),
    ])
    if (summaryResult.status === 'fulfilled') setSummary(summaryResult.value)
    if (documentsResult.status === 'fulfilled') setDocuments(documentsResult.value)
    else setDocuments([])
    if (manifestResult.status === 'fulfilled') setManifest(manifestResult.value)
    if ([summaryResult, documentsResult, manifestResult].every((item) => item.status === 'rejected')) {
      setError('Dữ liệu vòng đời chưa được kích hoạt cho môi trường này.')
    }
    setLoading(false)
  }, [bucket, legalAsOf, vectorState])

  useEffect(() => {
    void load()
  }, [load])

  const openCandidate = (id: string) => {
    setDocumentId(id)
    setCandidate(null)
    setCandidateReason('')
    setConfirmReason('')
    setSourceUrl('')
    setActionMessage('')
    setDialogOpen(true)
  }

  const createCandidate = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setActionMessage('')
    try {
      const result = await legalManagementApi.createChangeEventCandidate({
        document_id: documentId,
        event_type: eventType,
        effective_from: effectiveFrom,
        source_url: sourceUrl.trim(),
        scope: 'whole_document',
        provisions: [],
        provenance: { entered_from: 'feature018_lifecycle_panel' },
        reason: candidateReason.trim(),
      })
      setCandidate(result)
      setActionMessage('Đã tạo ứng viên. Trạng thái phục vụ chưa thay đổi cho tới khi xác nhận.')
    } catch {
      setActionMessage('Không thể tạo ứng viên. Kiểm tra nguồn, ngày áp dụng và quyền Admin.')
    } finally {
      setSubmitting(false)
    }
  }

  const confirmCandidate = async () => {
    if (!candidate) return
    setSubmitting(true)
    try {
      const result = await legalManagementApi.confirmChangeEvent(candidate.id, {
        evidence_fingerprint: candidate.evidence_fingerprint,
        reason: confirmReason.trim(),
      })
      setActionMessage(`Đã xác nhận và tạo ${result.impact_case_count} việc rà soát ảnh hưởng.`)
      setCandidate(null)
      await load()
    } catch {
      setActionMessage('Không thể xác nhận: bằng chứng đã đổi hoặc lý do chưa hợp lệ.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Vòng đời và trạng thái chỉ mục</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Dữ liệu tổng hợp tại ngày {legalAsOf}; mọi thay đổi pháp lý đều phải được kiểm tra trước khi áp dụng.
            </p>
          </div>
          <Badge variant={manifest?.gate_passed ? 'default' : 'destructive'}>
            {manifest?.gate_passed ? 'Cấu hình kho đạt yêu cầu' : 'Cấu hình kho cần xử lý'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {error && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Chưa có dữ liệu vòng đời chuẩn</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <CountCard label="Sắp hết hạn 30 ngày" value={summary?.counts.expiring_30 || 0} />
          <CountCard label="Hết hiệu lực" value={summary?.counts.expired || 0} />
          <CountCard label="Hết hiệu lực một phần" value={summary?.counts.partially_expired || 0} />
          <CountCard label="Thiếu dữ liệu trong chỉ mục" value={manifest?.counts.missing || 0} />
          <CountCard
            label="Dữ liệu không đồng bộ"
            value={manifest?.counts.fingerprint_mismatch || 0}
            hint="Không tự động lập chỉ mục lại"
          />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="font-medium">Nhóm vòng đời</span>
            <select
              aria-label="Nhóm vòng đời"
              value={bucket}
              onChange={(event) => setBucket(event.target.value as LifecycleBucket | '')}
              className="h-10 w-full rounded-md border bg-background px-3"
            >
              <option value="">Tất cả nhóm</option>
              {BUCKETS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Trạng thái chỉ mục tìm kiếm</span>
            <select
              aria-label="Trạng thái chỉ mục tìm kiếm"
              value={vectorState}
              onChange={(event) => setVectorState(event.target.value)}
              className="h-10 w-full rounded-md border bg-background px-3"
            >
              <option value="">Tất cả trạng thái</option>
              {['active', 'historical', 'staging', 'missing', 'orphan', 'duplicate', 'fingerprint_mismatch'].map((value) => (
                <option key={value} value={value}>{indexStateLabel(value)}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[780px] text-sm">
            <thead className="bg-muted/70 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-4 py-3">Văn bản</th>
                <th className="px-4 py-3">Vòng đời</th>
                <th className="px-4 py-3">Chỉ mục tìm kiếm</th>
                <th className="px-4 py-3">Phục vụ hiện hành</th>
                <th className="px-4 py-3">Hành động</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {documents.map((item) => (
                <tr key={item.document_id}>
                  <td className="px-4 py-3 font-medium">{item.document_id}</td>
                  <td className="px-4 py-3"><Badge variant="outline">{bucketLabel(item.bucket)}</Badge></td>
                  <td className="px-4 py-3">{indexStateLabel(item.vector_state)}</td>
                  <td className="px-4 py-3">{item.current_serving_allowed ? 'Được phép' : 'Đã chặn'}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button asChild size="sm" variant="outline">
                        <Link href={`/legal-documents/${encodeURIComponent(item.document_id)}`}>Xem hồ sơ</Link>
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => openCandidate(item.document_id)}>
                        <FilePlus2 className="mr-1.5 h-4 w-4" /> Ghi nhận thay đổi
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && documents.length === 0 && (
            <p className="p-8 text-center text-sm text-muted-foreground">Không có văn bản phù hợp bộ lọc.</p>
          )}
        </div>

        <Alert>
          <ShieldCheck className="h-4 w-4" />
          <AlertTitle>Chỉ xem trước và rà soát</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <span>Trang này không xóa dữ liệu tìm kiếm, không lập chỉ mục lại và không thay đổi kho đang phục vụ.</span>
            <span className="inline-flex items-center gap-1 text-xs"><Layers3 className="h-3.5 w-3.5" />Kho kỹ thuật: {manifest?.active_collection || 'Chưa xác định'}</span>
          </AlertDescription>
        </Alert>
      </CardContent>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Ghi nhận sự kiện pháp lý</DialogTitle>
            <p className="text-sm text-muted-foreground">Văn bản {documentId}. Bước đầu chỉ ghi nhận thay đổi chờ xác nhận, chưa ảnh hưởng đến tra cứu.</p>
          </DialogHeader>
          {!candidate ? (
            <form className="space-y-3" onSubmit={createCandidate}>
              <label className="block space-y-1 text-sm">
                <span>Loại sự kiện</span>
                <select aria-label="Loại sự kiện" value={eventType} onChange={(event) => setEventType(event.target.value)} className="h-10 w-full rounded-md border bg-background px-3">
                  {EVENT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="block space-y-1 text-sm">
                <span>Ngày áp dụng</span>
                <Input aria-label="Ngày áp dụng" type="date" required value={effectiveFrom} onChange={(event) => setEffectiveFrom(event.target.value)} />
              </label>
              <label className="block space-y-1 text-sm">
                <span>URL nguồn chính thức</span>
                <Input aria-label="URL nguồn chính thức" type="url" required value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://..." />
              </label>
              <label className="block space-y-1 text-sm">
                <span>Lý do tạo ứng viên</span>
                <Textarea aria-label="Lý do tạo ứng viên" required minLength={10} value={candidateReason} onChange={(event) => setCandidateReason(event.target.value)} />
              </label>
              <DialogFooter>
                <Button type="submit" disabled={submitting}><CalendarClock className="mr-2 h-4 w-4" />Tạo ứng viên</Button>
              </DialogFooter>
            </form>
          ) : (
            <div className="space-y-3">
              <Alert>
                <ShieldCheck className="h-4 w-4" />
                <AlertTitle>Ứng viên chờ xác nhận</AlertTitle>
                <AlertDescription className="break-all text-xs">Mã bằng chứng: {candidate.evidence_fingerprint}</AlertDescription>
              </Alert>
              <label className="block space-y-1 text-sm">
                <span>Lý do xác nhận sau đối chiếu</span>
                <Textarea aria-label="Lý do xác nhận" minLength={10} value={confirmReason} onChange={(event) => setConfirmReason(event.target.value)} />
              </label>
              <DialogFooter>
                <Button disabled={submitting || confirmReason.trim().length < 10} onClick={() => void confirmCandidate()}>Xác nhận bằng chứng</Button>
              </DialogFooter>
            </div>
          )}
          {actionMessage && <p role="status" className="text-sm">{actionMessage}</p>}
        </DialogContent>
      </Dialog>
    </Card>
  )
}
