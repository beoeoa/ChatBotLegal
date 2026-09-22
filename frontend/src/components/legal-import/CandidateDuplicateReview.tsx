'use client'

import { useId, useState } from 'react'
import Link from 'next/link'
import { Archive, AlertTriangle, ArrowRightLeft, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { formatApiError } from '@/lib/utils/error-handler'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { LegalCrawlCandidate, LegalDuplicateResolutionRequest } from '@/lib/api/legal-import'

export const duplicateLabels: Record<string, string> = {
  duplicate_content: 'Trùng toàn văn',
  identity_match: 'Cùng định danh — cần đối chiếu',
  content_changed: 'Toàn văn có thay đổi',
  metadata_conflict: 'Metadata mâu thuẫn — cần kiểm tra',
  unchecked: 'Chưa kiểm tra được trùng',
}

interface Props {
  candidate: LegalCrawlCandidate
  busy: boolean
  onResolve: (id: string, payload: LegalDuplicateResolutionRequest) => Promise<void>
}

export function CandidateDuplicateReview({ candidate, busy, onResolve }: Props) {
  const fieldId = useId()
  const matches = candidate.duplicate_matches || []
  const [selectedKey, setSelectedKey] = useState('')
  const selected = matches.find(match => `${match.target_type}:${match.id}` === selectedKey) || matches[0]
  const [action, setAction] = useState<LegalDuplicateResolutionRequest['action'] | null>(null)
  const [reason, setReason] = useState('')
  const [reasonTouched, setReasonTouched] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const processing = busy || submitting
  const normalizedReasonLength = reason.trim().length
  const reasonInvalid = reasonTouched && normalizedReasonLength < 10
  if (!selected) return null

  const openConfirmation = (nextAction: LegalDuplicateResolutionRequest['action']) => {
    setError('')
    setReason('')
    setReasonTouched(false)
    setConfirmed(false)
    setAction(nextAction)
  }
  const submit = async () => {
    if (!action || !candidate.duplicate_check_revision || processing) return
    setSubmitting(true)
    setError('')
    try {
      await onResolve(candidate.id, {
        action, target_type: selected.target_type, target_id: selected.id,
        expected_revision: candidate.duplicate_check_revision, target_revision: selected.target_revision,
        reason: reason.trim(), confirmed_same_document: confirmed,
      })
      setAction(null)
    } catch (caught) {
      setError(formatApiError(caught, 'Không lưu được kết quả đối chiếu. Hãy thử lại.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div aria-busy={processing} className="w-full min-w-0 space-y-3 rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
      <p className="flex items-start gap-2 font-semibold">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        {duplicateLabels[selected.kind]}
      </p>
      {matches.length > 1 && (
        <div className="space-y-1">
          <Label htmlFor={`${fieldId}-target`}>Bản cần đối chiếu ({matches.length})</Label>
          <select id={`${fieldId}-target`} className="min-h-11 w-full min-w-0 rounded-md border bg-background px-2" disabled={processing || action !== null}
            value={`${selected.target_type}:${selected.id}`} onChange={event => setSelectedKey(event.target.value)}>
            {matches.map(match => <option key={`${match.target_type}:${match.id}`} value={`${match.target_type}:${match.id}`}>
              {match.target_type === 'document' ? 'Kho' : 'Hàng chờ'} · {match.law_number || match.title} · #{match.id}
            </option>)}
          </select>
        </div>
      )}
      <p className="break-words font-medium">{selected.title || selected.law_number}</p>
      <p className="text-muted-foreground">{selected.issuing_agency || 'Chưa rõ cơ quan'}{selected.issued_date ? ` · ${selected.issued_date}` : ''}</p>
      <p className="leading-relaxed text-muted-foreground">
        {selected.kind === 'duplicate_content'
          ? 'Toàn văn trích xuất giống nhau. Lưu trữ chỉ bỏ bản đề xuất khỏi hàng chờ, không xóa văn bản hoặc dữ liệu tra cứu.'
          : selected.kind === 'content_changed'
            ? 'Cùng định danh nhưng nội dung khác. Cần đối chiếu và dùng luồng thay thế; không coi là bản trùng.'
            : 'Chưa có bằng chứng hai toàn văn giống nhau. Hãy đối chiếu số hiệu, cơ quan, ngày ban hành và nội dung trước khi quyết định.'}
      </p>
      <div className="flex flex-wrap gap-2">
        {selected.target_type === 'document' && <Button asChild variant="outline" className="min-h-11">
          <Link href={`/legal-management/${encodeURIComponent(selected.id)}`}>Xem bản trong kho</Link>
        </Button>}
        {selected.source_url?.startsWith('https://') && <Button asChild variant="ghost" className="min-h-11">
          <a href={selected.source_url} target="_blank" rel="noopener noreferrer">Nguồn gốc <ExternalLink className="ml-1 h-4 w-4" aria-hidden="true" /></a>
        </Button>}
        {selected.can_archive && <Button type="button" variant="outline" className="min-h-11" disabled={processing || !candidate.duplicate_check_revision}
          onClick={() => openConfirmation('archive_duplicate')}><Archive className="mr-2 h-4 w-4" aria-hidden="true" />Lưu trữ bản trùng</Button>}
        {selected.can_review_replacement && <Button type="button" variant="outline" className="min-h-11" disabled={processing || !candidate.duplicate_check_revision}
          onClick={() => openConfirmation('review_replacement')}><ArrowRightLeft className="mr-2 h-4 w-4" aria-hidden="true" />Đối chiếu để thay thế</Button>}
      </div>
      <Dialog open={action !== null} onOpenChange={open => { if (!open && !submitting) setAction(null) }}>
        <DialogContent aria-busy={processing} className="max-h-[85dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{action === 'archive_duplicate' ? 'Lưu trữ bản đề xuất trùng?' : 'Chuyển sang đối chiếu thay thế?'}</DialogTitle>
            <DialogDescription>
              {action === 'archive_duplicate'
                ? 'Bản đề xuất sẽ rời hàng chờ duyệt và vẫn xem được ở mục Đã lưu trữ trùng. Văn bản trong kho và dữ liệu tìm kiếm không bị xóa.'
                : 'Thao tác này chỉ ghi nhận yêu cầu đối chiếu. Văn bản hiện tại vẫn giữ nguyên; bạn cần hoàn tất luồng thay thế trong kho văn bản.'}
            </DialogDescription>
          </DialogHeader>
          <p className="break-words rounded-md bg-muted p-3 text-sm">Bản giữ lại: {selected.title || selected.law_number} · #{selected.id}</p>
          <div className="space-y-2">
            <Label htmlFor={`${fieldId}-reason`}>Lý do đối chiếu (ít nhất 10 ký tự)</Label>
            <Textarea
              id={`${fieldId}-reason`}
              value={reason}
              onChange={event => setReason(event.target.value)}
              onBlur={() => setReasonTouched(true)}
              maxLength={2000}
              disabled={processing}
              aria-invalid={reasonInvalid}
              aria-describedby={`${fieldId}-reason-help${reasonInvalid ? ` ${fieldId}-reason-error` : ''}`}
            />
            <p id={`${fieldId}-reason-help`} className="text-xs text-muted-foreground">
              {normalizedReasonLength}/2000 ký tự · Nêu căn cứ đối chiếu để người kiểm tra sau hiểu quyết định.
            </p>
            {reasonInvalid && (
              <p id={`${fieldId}-reason-error`} role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                Nhập ít nhất 10 ký tự cho lý do đối chiếu.
              </p>
            )}
          </div>
          {action === 'archive_duplicate' && selected.kind === 'identity_match' && (
            <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border p-3 text-sm">
              <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={processing} />
              Tôi đã mở bản gốc, đối chiếu toàn văn và xác nhận đây là cùng một văn bản, không phải bản sửa đổi.
            </label>
          )}
          {error && <p role="alert" className="flex items-start gap-2 text-sm text-destructive"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" className="min-h-11" disabled={processing} onClick={() => setAction(null)}>Hủy</Button>
            <Button type="button" className="min-h-11" onClick={() => void submit()} disabled={processing || normalizedReasonLength < 10 || (action === 'archive_duplicate' && selected.kind === 'identity_match' && !confirmed)}>
              {processing ? 'Đang xác nhận…' : action === 'archive_duplicate' ? 'Xác nhận lưu trữ' : 'Xác nhận đối chiếu'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
