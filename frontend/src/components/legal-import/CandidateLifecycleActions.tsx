'use client'

import Link from 'next/link'
import { AlertTriangle, Archive, ArrowRightLeft } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { LegalCrawlCandidate, LegalDuplicateResolutionRequest } from '@/lib/api/legal-import'
import { CandidateDuplicateReview } from './CandidateDuplicateReview'
import { crawlerErrorCopy, extractionReasonCopy } from '@/lib/utils/crawler-copy'

type ReviewDecision = 'approved' | 'rejected'

interface CandidateLifecycleActionsProps {
  candidate: LegalCrawlCandidate
  busy: boolean
  onReview: (candidateId: string, decision: ReviewDecision) => void
  onRetryImport?: (candidateId: string) => void
  onResolveDuplicate?: (candidateId: string, payload: LegalDuplicateResolutionRequest) => Promise<void>
}

function importedValue(candidate: LegalCrawlCandidate, key: string): string | number | null {
  const value = candidate.imported_document?.[key]
  return typeof value === 'string' || typeof value === 'number' ? value : null
}

const pipelineLabels: Record<string, string> = {
  fetching_source: 'Đang chuẩn bị văn bản',
  validating: 'Đang kiểm tra thông tin',
  queued: 'Đang nhập kho',
  chunking_embedding: 'Đang lập chỉ mục',
  active: 'Sẵn sàng trả lời',
  blocked: 'Cần xử lý',
  failed: 'Nhập kho thất bại',
}

export function CandidateLifecycleActions({
  candidate,
  busy,
  onReview,
  onRetryImport,
  onResolveDuplicate,
}: CandidateLifecycleActionsProps) {
  const status = candidate.status
  const documentId = candidate.document_id ?? importedValue(candidate, 'document_id')
  const chunkCount = candidate.chunk_count ?? importedValue(candidate, 'chunk_count')
  const activationStatus = importedValue(candidate, 'activation_status')
  const retrievalIsActive =
    (candidate.pipeline_stage === 'active' || candidate.import_status === 'completed') &&
    activationStatus === 'active' &&
    documentId != null &&
    Number(chunkCount) > 0 &&
    Boolean(candidate.vector_collection) &&
    candidate.chatbot_ready === true
  const existingDocumentId = candidate.duplicate_runtime_document?.id

  if (status === 'duplicate_archived' || status === 'replacement_review') {
    const receipt = candidate.duplicate_resolution
    const archived = status === 'duplicate_archived'
    return (
      <div className="w-full space-y-3 rounded-xl border bg-muted/30 p-4 text-sm">
        <p className="flex items-center gap-2 font-semibold">
          {archived ? <Archive className="h-4 w-4" aria-hidden="true" /> : <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />}
          {archived ? 'Đã lưu trữ bản trùng' : 'Chờ đối chiếu thay thế'}
        </p>
        <p className="leading-relaxed text-muted-foreground">{archived
          ? 'Bản đề xuất đã rời hàng chờ. Toàn văn và dấu vết đối chiếu vẫn được giữ; văn bản và dữ liệu tra cứu trong kho không bị xóa.'
          : 'Chưa thay đổi văn bản trong kho. Mở bản đang có và hoàn tất thao tác thay thế bằng link hoặc PDF.'}</p>
        <p className="break-words">{receipt?.reason || candidate.review_note}</p>
        {receipt?.target_type === 'document' && <Button asChild variant="outline" className="min-h-11">
          <Link href={`/legal-management/${encodeURIComponent(receipt.target_id)}`}>{archived ? 'Xem bản giữ lại' : 'Mở văn bản để thay thế'}</Link>
        </Button>}
      </div>
    )
  }

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
        <Button asChild type="button" size="sm" variant="outline" className="mt-3 min-h-11">
          <Link href={`/legal-management/${encodeURIComponent(String(documentId))}`}>
            Xem trong kho văn bản
          </Link>
        </Button>
        <Button asChild type="button" size="sm" variant="outline" className="ml-2 mt-3 min-h-11">
          <Link href={`/search?mode=ask&q=${encodeURIComponent(candidate.law_number || candidate.title)}`}>
            Thử hỏi chatbot
          </Link>
        </Button>
      </div>
    )
  }

  if (status === 'imported') {
    return (
      <div className="w-full rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-sm">
        <Badge variant="secondary">Chưa xác nhận kích hoạt tra cứu</Badge>
        <p className="mt-2 text-xs text-amber-950">
          Đang hoàn tất kích hoạt dữ liệu tra cứu. Bản ghi chỉ được xác nhận đã nhập kho khi văn bản và toàn bộ dữ liệu tìm kiếm đã sẵn sàng.
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

  if (candidate.duplicate_matches?.length && onResolveDuplicate) {
    return <CandidateDuplicateReview candidate={candidate} busy={busy} onResolve={onResolveDuplicate} />
  }

  if (candidate.comparison_status === 'duplicate' && existingDocumentId != null) {
    return <div className="space-y-3 rounded-xl border p-4 text-sm">
      <p className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4" aria-hidden="true" />Cùng số hiệu — cần đối chiếu</p>
      <p>Chưa đủ thông tin kết luận trùng toàn văn. Làm mới danh sách để đối chiếu trước khi xử lý.</p>
      <Button asChild variant="outline" className="min-h-11"><Link href={`/legal-management/${encodeURIComponent(String(existingDocumentId))}`}>Xem bản trong kho</Link></Button>
    </div>
  }

  if (status === 'rejected') {
    return (
      <div className="w-full rounded-lg border bg-muted/30 p-3 text-sm">
        <Badge variant="outline">Không duyệt</Badge>
        <p className="mt-2 text-xs text-muted-foreground">
          Bản ghi được giữ để kiểm toán và không được đưa vào kho tra cứu.
        </p>
      </div>
    )
  }

  if (status === 'approved' || status === 'import_failed' || status === 'changes_requested') {
    return (
      <div className="w-full space-y-3 rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
        <p className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4" aria-hidden="true" />Cần xử lý trước khi nhập kho</p>
        <p className="break-words leading-relaxed">{crawlerErrorCopy(candidate.review_note || candidate.requested_changes_note, 'Kiểm tra và bổ sung thông tin, sau đó thử nhập lại. Văn bản chưa được xác nhận sẵn sàng tra cứu.')}</p>
        {candidate.blockers?.map((blocker, index) => <p key={`${index}:${blocker}`} className="break-words text-muted-foreground">{extractionReasonCopy(blocker)}</p>)}
        <div className="flex flex-wrap gap-2">
          {status === 'changes_requested' ? <Button className="min-h-11" disabled={busy} onClick={() => onReview(candidate.id, 'approved')}>Duyệt lại sau khi bổ sung</Button>
            : onRetryImport && <Button className="min-h-11" disabled={busy} onClick={() => onRetryImport(candidate.id)}>Thử nhập lại</Button>}
          {(status === 'changes_requested' || status === 'import_failed') && (
            <Button
              type="button"
              variant="outline"
              className="min-h-11"
              disabled={busy}
              onClick={() => onReview(candidate.id, 'rejected')}
            >
              Không đưa vào kho
            </Button>
          )}
        </div>
      </div>
    )
  }

  if (status !== 'pending') {
    return (
      <div className="w-full rounded-lg border bg-muted/30 p-3 text-sm">
        <Badge variant="secondary">Trạng thái cần kiểm tra</Badge>
        <p className="mt-2 text-xs text-muted-foreground">
          Chưa xác nhận được bước xử lý hiện tại. Hãy làm mới hoặc kiểm tra dấu vết tác vụ.
        </p>
      </div>
    )
  }

  return (
    <div className="w-full space-y-2">
      <Badge variant="outline">Duyệt</Badge>
      {candidate.duplicate_check_status === 'unavailable' && <p className="flex items-start gap-2 text-sm text-muted-foreground">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />Chưa kiểm tra được trùng. Làm mới để đối chiếu; hệ thống vẫn kiểm tra lại trước khi nhập kho.
      </p>}
      <p className="text-xs text-muted-foreground">
        Quản trị viên là người chốt cuối. Bấm Duyệt để ghi nhận quyết định và chuyển ngay sang hàng nhập kho; bấm Không duyệt để giữ bản ghi ngoài tra cứu.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          onClick={() => onReview(candidate.id, 'rejected')}
          disabled={busy}
        >
          Không duyệt
        </Button>
        <Button
          type="button"
          className="min-h-11"
          onClick={() => onReview(candidate.id, 'approved')}
          disabled={busy}
        >
          Duyệt
        </Button>
      </div>
    </div>
  )
}
