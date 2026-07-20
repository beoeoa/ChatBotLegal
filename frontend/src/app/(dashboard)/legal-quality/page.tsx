'use client'

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, BarChart3, Clock3, FileWarning, Play, RefreshCcw, ShieldAlert } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { legalImportApi, LegalQualitySummary } from '@/lib/api/legal-import'

const ISSUE_LABELS: Array<[keyof NonNullable<LegalQualitySummary['quality_issues']>, string]> = [
  ['citation_dead', 'Citation không mở được'],
  ['pdf_export_failed', 'Xuất PDF thất bại'],
  ['broken_form_url', 'Link biểu mẫu lỗi'],
  ['slow_request', 'Yêu cầu chậm'],
  ['ocr_failed', 'OCR/trích xuất lỗi'],
  ['import_failed', 'Import/embedding lỗi'],
  ['unanswered_question', 'Câu hỏi chưa có câu trả lời'],
]

export default function LegalQualityPage() {
  const [summary, setSummary] = useState<LegalQualitySummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<'retrieval' | 'role' | null>(null)

  const loadSummary = useCallback(async () => {
    setLoading(true)
    try {
      setSummary(await legalImportApi.qualitySummary())
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Không tải được dashboard chất lượng')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void loadSummary()
    return () => controller.abort()
  }, [loadSummary])

  const runEvaluation = async (type: 'retrieval' | 'role') => {
    setRunning(type)
    try {
      await legalImportApi.runQualityEvaluation(type)
      toast.success(`Đã khởi chạy đánh giá ${type === 'retrieval' ? 'truy xuất' : 'câu trả lời theo vai trò'}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Không chạy được đánh giá')
    } finally {
      setRunning(null)
    }
  }

  const roleEval = summary?.role_evaluation
  const retrievalEval = summary?.retrieval_evaluation
  const issues = summary?.quality_issues
  const runtime = summary?.runtime

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6 pb-16">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-semibold"><BarChart3 className="h-8 w-8 text-primary" />Dashboard chất lượng pháp lý</h1>
          <p className="mt-2 text-muted-foreground">Theo dõi bám nguồn, tốc độ, job lỗi và kết quả đánh giá. Telemetry không lưu nội dung hay danh tính người dùng.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => void loadSummary()} disabled={loading}><RefreshCcw className="mr-2 h-4 w-4" />Làm mới</Button>
          <Button variant="outline" onClick={() => void runEvaluation('retrieval')} disabled={running !== null}><Play className="mr-2 h-4 w-4" />{running === 'retrieval' ? 'Đang chạy...' : 'Chạy truy xuất'}</Button>
          <Button onClick={() => void runEvaluation('role')} disabled={running !== null}><Play className="mr-2 h-4 w-4" />{running === 'role' ? 'Đang chạy...' : 'Chạy câu trả lời theo vai trò'}</Button>
        </div>
      </div>

      {loading && !summary ? <DashboardSkeleton /> : <>
        <div className="grid gap-4 md:grid-cols-4">
          <MetricCard title="Đánh giá vai trò" value={roleEval?.completed ?? 0} caption="câu đã chấm" />
          <MetricCard title="Cờ ảo giác" value={roleEval?.hallucination_flags ?? 0} caption="cờ đỏ phát hiện" />
          <MetricCard title="Yêu cầu runtime" value={runtime?.event_count ?? 0} caption="trong bộ đệm tạm" />
          <MetricCard title="Yêu cầu chậm" value={issues?.slow_request ?? 0} caption="từ 5 giây trở lên" />
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><ShieldAlert className="h-5 w-5" />Sự cố cần theo dõi</CardTitle><CardDescription>Chỉ lưu loại lỗi, trạng thái và thời gian; không lưu câu hỏi hay nội dung.</CardDescription></CardHeader>
            <CardContent className="space-y-2">
              {ISSUE_LABELS.map(([key, label]) => <div key={key} className="flex items-center justify-between rounded-lg border p-3 text-sm"><span>{label}</span><span className="font-semibold">{issues?.[key] ?? 0}</span></div>)}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Clock3 className="h-5 w-5" />Độ trễ theo luồng</CardTitle><CardDescription>Các phép đo thời gian trong bộ đệm runtime.</CardDescription></CardHeader>
            <CardContent className="space-y-2">
              {Object.entries(runtime?.by_category || {}).length === 0 ? <p className="text-sm text-muted-foreground">Chưa có dữ liệu runtime trong phiên máy chủ này.</p> : Object.entries(runtime?.by_category || {}).map(([category, stat]) => <div key={category} className="rounded-lg border p-3 text-sm"><div className="font-medium">{category}</div><div className="mt-1 text-muted-foreground">{stat.count} lần · trung bình {stat.avg_ms} ms · cao nhất {stat.max_ms} ms · lỗi {stat.error_count}{stat.cancelled_count ? ` · huỷ ${stat.cancelled_count}` : ''}</div></div>)}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <EvaluationCard title="Chất lượng theo vai trò" available={roleEval?.available} empty="Chưa có file đánh giá câu trả lời theo vai trò.">{Object.entries(roleEval?.by_role || {}).map(([role, stats]) => <div key={role} className="rounded-lg border p-4 text-sm"><div className="font-medium">{role}</div><div className="mt-2 text-muted-foreground">{stats.count} câu · bám nguồn {stats.grounded} · đúng văn phong {stats.role_style}</div></div>)}</EvaluationCard>
          <EvaluationCard title="Chất lượng truy xuất" available={retrievalEval?.available} empty="Chưa có file đánh giá truy xuất.">{Object.entries(retrievalEval?.top_status_distribution || {}).map(([status, count]) => <div key={status} className="rounded-lg border p-4 text-sm"><div className="font-medium">{status}</div><div className="mt-2 text-muted-foreground">{count} câu có nguồn đầu tiên thuộc trạng thái này</div></div>)}</EvaluationCard>
        </div>

        {(runtime?.slow_requests || []).length > 0 && <Card><CardHeader><CardTitle className="flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-amber-600" />Yêu cầu chậm gần đây</CardTitle><CardDescription>Không bao gồm query, nội dung hay danh tính.</CardDescription></CardHeader><CardContent className="space-y-2">{runtime?.slow_requests?.slice(0, 10).map((item, index) => <div key={`${item.at}-${index}`} className="flex flex-wrap justify-between gap-2 rounded border p-3 text-sm"><span>{item.category} {item.route ? `· ${item.route}` : ''}</span><span className="font-medium">{item.duration_ms} ms</span></div>)}</CardContent></Card>}
      </>}
    </div>
  )
}

function MetricCard({ title, value, caption }: { title: string; value: string | number; caption: string }) { return <Card><CardHeader className="pb-2"><CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold">{value}</div><p className="mt-1 text-xs text-muted-foreground">{caption}</p></CardContent></Card> }
function EvaluationCard({ title, available, empty, children }: { title: string; available?: boolean; empty: string; children: React.ReactNode }) { return <Card><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent className="space-y-4">{!available ? <p className="text-sm text-muted-foreground">{empty}</p> : children}</CardContent></Card> }
function DashboardSkeleton() { return <><div className="grid gap-4 md:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <Card key={index}><CardContent className="h-24 animate-pulse bg-muted/40" /></Card>)}</div><Card><CardContent className="h-56 animate-pulse bg-muted/40" /></Card></> }
