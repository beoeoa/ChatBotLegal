'use client'

import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CalendarClock, Layers3, ScanSearch, ShieldCheck } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  legalManagementApi,
  type LegalImpactCaseProjection,
  type LifecycleTimeline,
} from '@/lib/api/legal-management'

const EVENT_LABELS: Record<string, string> = {
  effective: 'Có hiệu lực',
  expiry: 'Hết hiệu lực',
  amend: 'Sửa đổi',
  supplement: 'Bổ sung',
  replace: 'Bị thay thế',
  repeal: 'Bị bãi bỏ',
  suspend: 'Đình chỉ',
  restore: 'Khôi phục',
  correct: 'Đính chính',
  extend: 'Gia hạn',
  consolidate: 'Hợp nhất',
}

export function DocumentLifecyclePanel({
  documentId,
  isAdmin,
}: {
  documentId: string
  isAdmin: boolean
}) {
  const [timeline, setTimeline] = useState<LifecycleTimeline | null>(null)
  const [impacts, setImpacts] = useState<LegalImpactCaseProjection[]>([])
  const [manifestGate, setManifestGate] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<string>('')
  const [loadingPreview, setLoadingPreview] = useState(false)

  useEffect(() => {
    if (!isAdmin || !documentId) return
    let active = true
    const load = async () => {
      const [timelineResult, impactsResult, manifestResult] = await Promise.allSettled([
        legalManagementApi.lifecycleTimeline(documentId),
        legalManagementApi.impactCases('needs_review'),
        legalManagementApi.activeIndexManifest(),
      ])
      if (!active) return
      if (timelineResult.status === 'fulfilled') {
        setTimeline(timelineResult.value)
        const eventIds = new Set(timelineResult.value.events.map((item) => item.id))
        if (impactsResult.status === 'fulfilled') {
          setImpacts(impactsResult.value.filter((item) => eventIds.has(item.change_event_id)))
        }
      } else {
        setError('Chưa thể đọc timeline Feature 018 cho văn bản này.')
      }
      if (manifestResult.status === 'fulfilled') setManifestGate(manifestResult.value.gate_passed)
    }
    void load()
    return () => {
      active = false
    }
  }, [documentId, isAdmin])

  const targetProvisions = useMemo(
    () => (timeline?.provisions || []).map((item) => item.provision_identity || item.article_identity || '').filter(Boolean),
    [timeline?.provisions],
  )

  if (!isAdmin) return null

  const previewIndex = async () => {
    setLoadingPreview(true)
    try {
      const result = await legalManagementApi.previewIndexJob({
        document_id: documentId,
        provisions: targetProvisions,
      })
      setPreview(
        result.mode === 'incremental'
          ? 'Preview tăng dần đã tạo; chưa ghi vector và chưa đổi active pointer.'
          : 'Preview toàn văn đã tạo; chưa ghi vector và chưa đổi active pointer.',
      )
    } catch {
      setPreview('Không thể tạo preview chỉ mục ở thời điểm này.')
    } finally {
      setLoadingPreview(false)
    }
  }

  return (
    <Card className="mb-6 border-primary/20">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><CalendarClock className="h-5 w-5" />Timeline, ảnh hưởng và chỉ mục</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">Chỉ Admin thấy projection vận hành; nội dung văn bản vẫn dùng ACL hiện có.</p>
          </div>
          <Badge variant={manifestGate ? 'default' : 'destructive'}>
            {manifestGate ? 'Manifest đạt gate' : 'Chỉ mục cần rà soát'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert><AlertTriangle className="h-4 w-4" /><AlertTitle>Projection chưa sẵn sàng</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border p-4"><p className="text-sm text-muted-foreground">Sự kiện đã ghi</p><p className="mt-1 text-2xl font-semibold">{timeline?.events.length || 0}</p></div>
          <div className="rounded-lg border p-4"><p className="text-sm text-muted-foreground">Việc cần rà soát</p><p className="mt-1 text-2xl font-semibold">{impacts.length}</p></div>
          <div className="rounded-lg border p-4"><p className="text-sm text-muted-foreground">Trạng thái vector</p><p className="mt-1 font-semibold">{timeline?.vector_state || 'Chưa xác định'}</p></div>
        </div>
        {(timeline?.events || []).length > 0 && (
          <ol className="space-y-3" aria-label="Timeline pháp lý">
            {timeline?.events.map((event) => (
              <li key={event.id} className="rounded-lg border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{EVENT_LABELS[event.event_type] || event.event_type}</span>
                  <Badge variant="outline">{event.status}</Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">Áp dụng: {event.effective_from || 'Chưa có ngày'}</p>
              </li>
            ))}
          </ol>
        )}
        {impacts.length > 0 && (
          <Alert>
            <ScanSearch className="h-4 w-4" />
            <AlertTitle>{impacts.length} dependency đang chờ rà soát</AlertTitle>
            <AlertDescription>Không tự động sao chép nội dung hoặc phát hành revision mới.</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 p-4">
          <div className="flex items-start gap-2 text-sm">
            <ShieldCheck className="mt-0.5 h-4 w-4 text-primary" />
            <span>Preview chỉ xác định phạm vi; execution cần gate và phê duyệt riêng.</span>
          </div>
          <Button variant="outline" onClick={() => void previewIndex()} disabled={loadingPreview || !timeline}>
            <Layers3 className="mr-2 h-4 w-4" />Xem trước tái lập chỉ mục
          </Button>
        </div>
        {preview && <p role="status" className="text-sm">{preview}</p>}
      </CardContent>
    </Card>
  )
}
