'use client'

import Link from 'next/link'
import { RefreshCcw } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { LegalCrawlCandidate } from '@/lib/api/legal-import'

type ReviewDecision = 'approved' | 'rejected'

interface CandidateLifecycleActionsProps {
  candidate: LegalCrawlCandidate
  busy: boolean
  importReady?: boolean
  onReview: (candidateId: string, decision: ReviewDecision) => void
  onRetryImport: (candidateId: string) => void
}

function importedValue(candidate: LegalCrawlCandidate, key: string): string | number | null {
  const value = candidate.imported_document?.[key]
  return typeof value === 'string' || typeof value === 'number' ? value : null
}

const pipelineLabels: Record<string, string> = {
  fetching_source: 'Đang tải nguồn chính thức',
  validating: 'Đang kiểm tra dữ liệu pháp lý',
  queued: 'Đang chờ xử lý',
  chunking_embedding: 'Đang chia đoạn và tạo chỉ mục',
  active: 'Đã kích hoạt tra cứu',
  blocked: 'Cần bổ sung dữ liệu',
  failed: 'Xử lý thất bại',
}

export function CandidateLifecycleActions({
  candidate,
  busy,
  importReady = true,
  onReview,
  onRetryImport,
}: CandidateLifecycleActionsProps) {
  const status = candidate.status
  const documentId = candidate.document_id ?? importedValue(candidate, 'document_id')
  const chunkCount = candidate.chunk_count ?? importedValue(candidate, 'chunk_count')
  const activationStatus = importedValue(candidate, 'activation_status')
  const retrievalIsActive =
    (candidate.pipeline_stage === 'active' || candidate.import_status === 'completed') &&
    activationStatus === 'active' &&
    documentId != null &&
    Number(chunkCount) > 0

  if (status === 'imported' && retrievalIsActive) {
    return (
      <div className="w-full rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge className="bg-emerald-700">Đã nhập kho và kích hoạt tra cứu</Badge>
          <span className="text-emerald-900">Chỉ đọc — không duyệt hoặc đánh giá lại.</span>
        </div>
        <p className="mt-2 text-xs text-emerald-900">
          Có thể sử dụng trong tra cứu và hỏi đáp
          {chunkCount != null ? ` · ${chunkCount} đoạn nội dung` : ''}
        </p>
        <Button asChild type="button" size="sm" variant="outline" className="mt-3">
          <Link href={`/legal-management?document_id=${encodeURIComponent(String(documentId))}`}>
            Xem trong kho văn bản
          </Link>
        </Button>
      </div>
    )
  }

  if (status === 'imported') {
    return (
      <div className="w-full rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-sm">
        <Badge variant="secondary">Nhập kho chưa xác nhận kích hoạt</Badge>
        <p className="mt-2 text-xs text-amber-950">
          Văn bản đã có bản ghi nhưng chưa xác nhận được khả năng tra cứu. Cần kiểm tra lại trước khi kích hoạt.
        </p>
      </div>
    )
  }

  if (status === 'import_queued' || ['fetching_source', 'validating', 'queued', 'chunking_embedding'].includes(candidate.pipeline_stage || '')) {
    const running = candidate.import_status === 'running' || candidate.pipeline_stage === 'chunking_embedding'
    return (
      <div className="w-full rounded-lg border border-blue-200 bg-blue-50/60 p-3 text-sm">
        <Badge variant="secondary">{pipelineLabels[candidate.pipeline_stage || ''] || (running ? 'Đang chuẩn hóa và tạo chỉ mục' : 'Đang chờ đưa vào kho')}</Badge>
        <p className="mt-2 text-xs text-muted-foreground">
          {running
            ? 'Hệ thống đang xử lý. Văn bản sẽ dùng được sau khi hoàn tất và được kích hoạt.'
            : 'Đã xếp hàng; hệ thống sẽ chuẩn hóa và tạo chỉ mục theo thứ tự. Không thể đổi quyết định trong lúc xử lý.'}
        </p>
      </div>
    )
  }

  if (status === 'rejected') {
    return (
      <div className="w-full rounded-lg border bg-muted/30 p-3 text-sm">
        <Badge variant="outline">Đã bỏ qua</Badge>
        <p className="mt-2 text-xs text-muted-foreground">
          Bản ghi được giữ để kiểm toán và không được đưa vào kho tra cứu.
        </p>
      </div>
    )
  }

  if (status === 'approved' || status === 'import_failed') {
    const failed = status === 'import_failed' || candidate.import_status === 'failed'
    const importFailureReason = failed || candidate.import_status === 'validation_failed'
      ? candidate.review_note?.trim()
      : ''
    return (
      <div className="w-full space-y-2 rounded-lg border border-amber-200 bg-amber-50/50 p-3 text-sm">
        <Badge variant={failed ? 'destructive' : 'secondary'}>
          {failed ? 'Nhập kho thất bại' : 'Đã duyệt – chưa nhập kho'}
        </Badge>
        <p className="text-xs text-muted-foreground">
          {failed
            ? 'Văn bản chưa được kích hoạt tra cứu. Kiểm tra lỗi rồi thử nhập lại.'
            : candidate.import_status === 'validation_failed'
              ? 'Dữ liệu chưa đạt kiểm tra bắt buộc; văn bản chưa được đưa vào kho.'
              : 'Quyết định duyệt đã được ghi nhận nhưng văn bản chưa được đưa vào kho.'}
        </p>
        {importFailureReason && (
          <p className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-950">
            <span className="font-medium">Lý do gần nhất: </span>
            {importFailureReason}
          </p>
        )}
        <Button type="button" size="sm" onClick={() => onRetryImport(candidate.id)} disabled={busy || !importReady}>
          <RefreshCcw className="mr-1 h-3 w-3" />
          Thử nhập lại
        </Button>
      </div>
    )
  }

  const awaitingSupplement = status === 'changes_requested'
  if (status !== 'pending' && !awaitingSupplement) {
    return (
      <div className="w-full rounded-lg border bg-muted/30 p-3 text-sm">
        <Badge variant="outline">Trạng thái cần kiểm tra</Badge>
        <p className="mt-2 text-xs text-muted-foreground">
          Hệ thống đã khóa hành động duyệt để tránh chuyển sai vòng đời dữ liệu.
        </p>
      </div>
    )
  }

  return (
    <div className="w-full space-y-2">
      <Badge variant={awaitingSupplement ? 'secondary' : 'outline'}>
        {awaitingSupplement ? 'Cần bổ sung' : 'Chờ duyệt'}
      </Badge>
      {awaitingSupplement && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-3 text-xs text-amber-900">
          <p>Chưa duyệt và chưa tạo job nhập kho. Hãy bổ sung các dữ liệu sau:</p>
          {(candidate.blockers || []).length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {candidate.blockers?.map((blocker) => <li key={blocker}>{blocker}</li>)}
            </ul>
          ) : (
            <p className="mt-2">Đối chiếu lại metadata và toàn văn từ nguồn chính thức.</p>
          )}
        </div>
      )}
      {!importReady && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-3 text-xs text-amber-900">
          Hệ thống nhập kho đang tạm thời chưa sẵn sàng. Bạn có thể từ chối đề xuất, nhưng chưa thể duyệt và đưa văn bản vào kho.
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Duyệt sẽ tự kiểm tra nguồn, dữ liệu trùng và xếp hàng nhập kho. Từ chối sẽ giữ bản ghi để truy vết nhưng không đưa vào tra cứu.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => onReview(candidate.id, 'rejected')}
          disabled={busy}
        >
          Từ chối
        </Button>
        <Button
          type="button"
          onClick={() => onReview(candidate.id, 'approved')}
          disabled={busy || !importReady}
        >
          Duyệt và nhập kho
        </Button>
      </div>
    </div>
  )
}
