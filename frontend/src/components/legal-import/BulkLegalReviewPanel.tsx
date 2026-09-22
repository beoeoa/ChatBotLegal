'use client'

import { RefreshCcw, ShieldCheck, TriangleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type {
  FormCompletionCampaignStatus,
  FormLegalReviewPreview,
  FormLegalReviewPreviewItem,
  FormResolutionCampaignShortlist,
  FormResolutionCampaignStatus,
} from '@/lib/api/legal-import'
import { systemStatusLabel } from '@/lib/utils/system-labels'

interface BulkLegalReviewPanelProps {
  preview: FormLegalReviewPreview | null
  loading: boolean
  submitting: boolean
  reviewNote: string
  onReviewNoteChange: (value: string) => void
  onConfirm: () => void
  campaignStatus?: FormCompletionCampaignStatus | FormResolutionCampaignStatus | null
  campaignShortlist?: FormResolutionCampaignShortlist | null
  campaignRunning?: boolean
  onRunCampaign?: () => void
}

const REVIEW_REASON_LABELS: Record<string, string> = {
  NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH: 'Chưa xác định được một nguồn Nhà nước duy nhất',
  OFFICIAL_FILE_MAPPING_UNRESOLVED: 'Chưa ghép được tệp nguồn với đúng thủ tục',
  OFFICIAL_PACKAGE_FILE_RETRY_REQUIRED: 'Cần tải lại tệp từ nguồn chính thức',
  OFFICIAL_EFORM_REQUIRES_SEPARATE_GATE: 'Biểu mẫu điện tử cần được kiểm tra riêng',
  VERIFIED_DATA_GAP: 'Thiếu dữ liệu đã xác minh',
  NOT_EFFECTIVE: 'Biểu mẫu chưa có hiệu lực',
  EXPIRED: 'Biểu mẫu đã hết hiệu lực',
}

function reviewReasonLabel(value: string) {
  return REVIEW_REASON_LABELS[value] || 'Cần kiểm tra thêm dữ liệu nguồn'
}

function campaignStageLabel(value?: string | null) {
  const labels: Record<string, string> = {
    discovery: 'Tìm nguồn chính thức',
    download: 'Tải tệp nguồn',
    validation: 'Kiểm tra dữ liệu',
    mapping: 'Ghép biểu mẫu với thủ tục',
    attestation: 'Chờ xác nhận pháp lý',
    release: 'Kiểm tra trước khi phát hành',
    completed: 'Đã hoàn tất',
  }
  return labels[String(value || '').trim().toLowerCase()] || 'Đang xử lý'
}

function reviewItemKey(
  item: FormLegalReviewPreviewItem,
  scope: string,
  index: number,
) {
  return [
    scope,
    item.candidate_id || item.requirement_identity_id || item.sha256 || item.canonical_form_id,
    item.requirement_identity_id || '',
    item.procedure_id,
    index,
  ].join(':')
}

function MetadataRow({
  item,
  excluded = false,
}: {
  item: FormLegalReviewPreviewItem
  excluded?: boolean
}) {
  return (
    <div
      className={`space-y-3 rounded-lg border p-4 ${
        excluded ? 'border-amber-300 bg-amber-50/50' : 'border-green-300 bg-green-50/40'
      }`}
      data-testid={excluded ? 'legal-review-excluded-item' : 'legal-review-eligible-item'}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="font-medium">{item.canonical_name || item.canonical_form_id}</h4>
        {item.form_code && <Badge variant="outline">Mã: {item.form_code}</Badge>}
        <Badge variant="outline">{systemStatusLabel(item.review_status)}</Badge>
        {excluded && <Badge variant="destructive">Không được duyệt</Badge>}
      </div>
      <dl className="grid gap-2 text-sm md:grid-cols-2">
        {item.requirement_identity_id && (
          <div>
            <dt className="text-muted-foreground">Định danh yêu cầu</dt>
            <dd className="font-mono text-xs">{item.requirement_identity_id}</dd>
          </div>
        )}
        <div>
            <dt className="text-muted-foreground">Mã thủ tục</dt>
          <dd className="font-mono text-xs">{item.procedure_id || 'Thiếu'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Hiệu lực</dt>
          <dd>
            {item.effective_from || 'Chưa xác minh'}
            {item.effective_to ? ` đến ${item.effective_to}` : ''}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Phạm vi</dt>
          <dd>
            {item.jurisdiction || 'Chưa xác minh'} ·{' '}
            {item.administrative_level || 'Chưa xác minh'}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Trạng thái pháp lý</dt>
          <dd>{systemStatusLabel(item.legal_review_status, 'Chưa kiểm tra')}</dd>
        </div>
        {item.issuing_instrument && (
          <div>
            <dt className="text-muted-foreground">Văn bản ban hành</dt>
            <dd>{item.issuing_instrument}</dd>
          </div>
        )}
        {item.source_domain && (
          <div>
            <dt className="text-muted-foreground">Miền nguồn</dt>
            <dd>{item.source_domain}</dd>
          </div>
        )}
      </dl>
      <div className="space-y-1 text-xs">
        <p>
          <span className="text-muted-foreground">Tệp lưu trữ: </span>
          <span className="break-all">{item.local_path || 'Chưa có tệp'}</span>
        </p>
        <p>
          <span className="text-muted-foreground">Mã kiểm tra tệp: </span>
          <span className="break-all font-mono">{item.sha256 || 'Chưa có mã kiểm tra'}</span>
        </p>
        <p>
          <span className="text-muted-foreground">Căn cứ pháp lý: </span>
          {item.legal_basis.length ? item.legal_basis.join(', ') : 'Chưa xác minh'}
        </p>
      </div>
      <div className="flex flex-wrap gap-3 text-sm">
        {item.source_page_url && (
          <a
            className="text-primary underline"
            href={item.source_page_url}
            target="_blank"
            rel="noreferrer"
          >
            Nguồn chính thức
          </a>
        )}
        {item.source_download_url && (
          <a
            className="text-primary underline"
            href={item.source_download_url}
            target="_blank"
            rel="noreferrer"
          >
            File nguồn
          </a>
        )}
      </div>
      {item.reason_codes.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {item.reason_codes.map((reason) => (
            <Badge key={reason} variant="outline">
              {reviewReasonLabel(reason)}
            </Badge>
          ))}
        </div>
      )}
      {item.effectivity_reason_code && (
        <p className="text-xs text-muted-foreground">
          Hiệu lực: {reviewReasonLabel(item.effectivity_reason_code)}
        </p>
      )}
      {item.exclusion_reason_codes?.length ? (
        <div className="flex flex-wrap gap-2">
          {item.exclusion_reason_codes.map((reason) => (
            <Badge key={reason} variant="destructive">{reviewReasonLabel(reason)}</Badge>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function BulkLegalReviewPanel({
  preview,
  loading,
  submitting,
  reviewNote,
  onReviewNoteChange,
  onConfirm,
  campaignStatus,
  campaignShortlist,
  campaignRunning = false,
  onRunCampaign,
}: BulkLegalReviewPanelProps) {
  const campaignPreviewInvalid = Boolean(
    campaignStatus?.schema_version === 'form-resolution-campaign-v1'
    && (campaignStatus.manifest_drift || campaignStatus.source_snapshot_drift),
  )
  const isResolutionCampaign = campaignStatus?.schema_version === 'form-resolution-campaign-v1'
  const hasReviewItems = isResolutionCampaign
    ? Boolean(campaignShortlist?.records.length)
    : Boolean(preview?.eligible_items.length)
  const eligibleItems = isResolutionCampaign
    ? campaignShortlist?.records ?? []
    : preview?.eligible_items ?? []
  const eligible = hasReviewItems && campaignShortlist?.records.length
    ? campaignShortlist.canonical_form_count
    : preview?.summary.eligible_forms ?? 0
  const canRunCampaign = Boolean(
    onRunCampaign
    && campaignStatus
    && (
      campaignPreviewInvalid
      || campaignStatus.status === 'not_started'
      || campaignStatus.status === 'VERIFIED_DATA_GAP'
      || campaignStatus.status === 'completed_fail_closed'
    ),
  )
  const releaseGatePending = String(campaignStatus?.status || '') === 'ATTESTED_PENDING_RELEASE_GATES'
  const activeCatalog =
    preview?.summary.active_catalog_forms ?? preview?.summary.total_forms ?? 0
  const catalogExcluded = preview?.summary.excluded_no_official_forms ?? 0
  const workQueues = [
    {
      label: 'Cần tìm đúng nguồn',
      count: preview?.reason_counts.NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH ?? 0,
    },
    {
      label: 'Có file, cần ghép đúng thủ tục',
      count: preview?.reason_counts.OFFICIAL_FILE_MAPPING_UNRESOLVED ?? 0,
    },
    {
      label: 'Trang nguồn đang thử tải lại',
      count: preview?.reason_counts.OFFICIAL_PACKAGE_FILE_RETRY_REQUIRED ?? 0,
    },
    {
      label: 'Biểu mẫu điện tử cần kiểm tra riêng',
      count: preview?.reason_counts.OFFICIAL_EFORM_REQUIRES_SEPARATE_GATE ?? 0,
    },
  ].filter((item) => item.count > 0)

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-green-700" />
            Xác nhận pháp lý hàng loạt
          </CardTitle>
          <CardDescription>
            Đây là quyết định của người đang đăng nhập. Hệ thống chỉ đồng bộ những
            biểu mẫu đáp ứng đầy đủ điều kiện bắt buộc và không tự động phê duyệt pháp lý.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {canRunCampaign ? (
          <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-medium text-blue-950">Tự động hoàn thiện nguồn biểu mẫu</p>
              <p className="mt-1 text-sm text-blue-800">
                Hệ thống tự tìm nguồn Nhà nước, tải và kiểm tra tệp, tính toàn vẹn, thủ tục và hiệu lực.
                Biểu mẫu chỉ được chuyển tới danh sách xác nhận; không có quyết định pháp lý tự động.
              </p>
              {campaignStatus && campaignStatus.status !== 'not_started' ? (
                <p className="mt-2 text-xs text-blue-800" data-testid="form-campaign-status">
                  Trạng thái: {systemStatusLabel(campaignStatus.status)} · Công đoạn: {campaignStageLabel(campaignStatus.stage)}
                  {campaignStatus.schema_version === 'form-resolution-campaign-v1' ? (
                    <>
                      {' · '}Bản ghi liên kết đạt điều kiện:{' '}
                      {campaignStatus.counts.ready_for_attestation ?? 0}
                      {' · '}Bản ghi liên kết còn vướng:{' '}
                      {campaignStatus.counts.remaining_unresolved
                        ?? campaignStatus.counts.verified_data_gap
                        ?? 0}
                    </>
                  ) : (
                    <>
                      {' · '}Sẵn sàng xác nhận: {campaignStatus.counts.ready_for_attestation ?? 0}
                      {' · '}Còn thiếu bằng chứng:{' '}
                      {campaignStatus.counts.remaining_unresolved
                        ?? campaignStatus.counts.verified_data_gap
                        ?? 0}
                    </>
                  )}
                </p>
              ) : null}
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={onRunCampaign}
              disabled={campaignRunning}
            >
              <RefreshCcw
                className={`mr-2 h-4 w-4 ${campaignRunning ? 'animate-spin' : ''}`}
              />
              {campaignRunning
                ? 'Đang tự động xử lý...'
                : campaignPreviewInvalid
                  ? 'Tạo lại lô từ dữ liệu nguồn mới'
                  : 'Tự động xử lý biểu mẫu còn lại'}
            </Button>
          </div>
          </div>
        ) : null}
        {campaignStatus?.schema_version === 'form-resolution-campaign-v1' ? (
          <details open={hasReviewItems} className="rounded-lg border bg-muted/10 p-3">
            <summary className="cursor-pointer font-medium">
              Tình trạng xử lý: <span data-testid="form-campaign-status">{systemStatusLabel(campaignStatus.status)} · {campaignStageLabel(campaignStatus.stage)}</span>
            </summary>
            <div className="mt-3 space-y-3">
            {(campaignStatus.manifest_drift || campaignStatus.source_snapshot_drift) ? (
              <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900" data-testid="form-manifest-drift">
                Dữ liệu nguồn đã thay đổi. Lô xử lý bị khóa cho đến khi tạo lại từ dữ liệu mới.
              </div>
            ) : (
              <div className="rounded-lg border border-green-300 bg-green-50 p-3 text-sm text-green-900" data-testid="form-manifest-stable">
                Dữ liệu kiểm tra khớp với nguồn; có thể tiếp tục xử lý các biểu mẫu đề xuất.
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ['Tổng định danh cần có', campaignStatus.counts.target_identities ?? 0],
                ['Đã duyệt để phục vụ', campaignStatus.counts.approved_runtime_identities ?? 0],
                ['Còn phải hoàn thiện', campaignStatus.counts.pending_identities ?? 0],
                ['Sẵn sàng chờ người duyệt', campaignStatus.counts.ready_for_human_attestation_identities ?? 0],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-lg border bg-muted/20 p-3">
                  <p className="text-sm text-muted-foreground">{label}</p>
                  <p className="text-xl font-semibold">{value}</p>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              Tính toàn vẹn của lô: {campaignStatus.manifest_sha256 ? 'Đã xác nhận' : 'Chưa xác nhận'}
            </p>
            </div>
          </details>
        ) : null}
        {hasReviewItems && campaignShortlist?.records.length ? (
          <p className="text-sm font-medium" data-testid="campaign-shortlist-counts">
            Lô {campaignShortlist.batch_id} ({campaignShortlist.batch_count} lô):{' '}
            {campaignShortlist.identity_count} danh tính biểu mẫu /{' '}
            {campaignShortlist.procedure_binding_count} liên kết thủ tục /{' '}
            {campaignShortlist.canonical_form_count} biểu mẫu chuẩn
          </p>
        ) : null}
        {hasReviewItems && campaignStatus?.schema_version === 'form-resolution-campaign-v1'
          && Object.keys(campaignStatus.reason_counts || {}).length > 0 ? (
          <div className="rounded-lg border border-amber-300 bg-amber-50/50 p-4">
            <div className="flex items-center gap-2 font-medium text-amber-900">
              <TriangleAlert className="h-4 w-4" />
              Lý do kỹ thuật chưa thể đưa vào lô duyệt
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(campaignStatus.reason_counts).map(([reason, count]) => (
                <Badge key={reason} variant="outline">{reviewReasonLabel(reason)}: {count}</Badge>
              ))}
            </div>
          </div>
        ) : null}
        {hasReviewItems ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ['Biểu mẫu đang quản lý', activeCatalog],
            ['Đạt điều kiện bắt buộc', eligible],
            ['Cần bổ sung', preview?.summary.excluded_forms ?? 0],
            ['Đã xác nhận trước đó', preview?.summary.already_approved_forms ?? 0],
            ['Không có mẫu nhà nước', catalogExcluded],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-lg border p-3">
              <p className="text-sm text-muted-foreground">{label}</p>
              <p className="text-2xl font-semibold">{value}</p>
            </div>
          ))}
          </div>
        ) : null}

        {hasReviewItems && workQueues.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {workQueues.map((queue) => (
              <div key={queue.label} className="rounded-lg border bg-muted/30 p-3">
                <p className="text-sm text-muted-foreground">{queue.label}</p>
                <p className="mt-1 text-xl font-semibold">{queue.count}</p>
              </div>
            ))}
          </div>
        )}

        {hasReviewItems && Object.keys(preview?.reason_counts || {}).length > 0 && (
          <div className="rounded-lg border border-amber-300 bg-amber-50/50 p-4">
            <div className="flex items-center gap-2 font-medium text-amber-900">
              <TriangleAlert className="h-4 w-4" />
              Lý do bị loại
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(preview?.reason_counts || {}).map(([reason, count]) => (
                <Badge key={reason} variant="outline">
                  {reviewReasonLabel(reason)}: {count}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {hasReviewItems ? (
          <div className="space-y-3">
            <h3 className="font-medium">Biểu mẫu sẽ được xác nhận</h3>
            {eligibleItems.map((item, index) => (
              <MetadataRow
                key={reviewItemKey(item, 'eligible', index)}
                item={item}
              />
            ))}
          </div>
        ) : null}

        {hasReviewItems && preview?.excluded_items.length ? (
          <details>
            <summary className="cursor-pointer font-medium">
              Xem {preview.excluded_items.length} biểu mẫu bị loại
            </summary>
            <div className="mt-3 space-y-3">
              {preview.excluded_items.map((item, index) => (
                <MetadataRow
                  key={reviewItemKey(item, 'excluded', index)}
                  item={item}
                  excluded
                />
              ))}
            </div>
          </details>
        ) : null}

        {hasReviewItems && preview?.catalog_excluded_items?.length ? (
          <details>
            <summary className="cursor-pointer font-medium">
              Đã loại khỏi danh mục phục vụ
            </summary>
            <p className="mt-2 text-sm text-muted-foreground">
              Các bản ghi này không có biểu mẫu được liệt kê tại nguồn thủ tục chính thức.
              Hệ thống giữ thông tin mô tả để kiểm tra lịch sử nhưng không đưa vào hàng chờ duyệt hoặc trả cho người dùng.
            </p>
            <div className="mt-3 space-y-3">
              {preview.catalog_excluded_items.map((item, index) => (
                <MetadataRow
                  key={reviewItemKey(item, 'catalog-excluded', index)}
                  item={item}
                  excluded
                />
              ))}
            </div>
          </details>
        ) : null}

        {!hasReviewItems ? (
          <div
            className="rounded-lg border border-dashed bg-muted/20 p-4 text-sm text-muted-foreground"
            data-testid="form-review-no-pending-batch"
          >
            <p className="font-medium text-foreground">
              {releaseGatePending
                ? 'Không còn lô biểu mẫu cần xác nhận.'
                : 'Hiện chưa có lô biểu mẫu cần xác nhận.'}
            </p>
            <p className="mt-1">
              {releaseGatePending
                ? 'Các quyết định đã ghi nhận đang chờ kiểm tra phát hành; không cần thao tác tại đây.'
                : 'Khi có lô đạt điều kiện kỹ thuật, hệ thống sẽ hiển thị lại phần xác nhận.'}
            </p>
          </div>
        ) : null}

        {hasReviewItems ? (
        <>
        <div className="space-y-2">
          <Label htmlFor="bulk-legal-review-note">Ghi chú quyết định</Label>
          <Textarea
            id="bulk-legal-review-note"
            value={reviewNote}
            onChange={(event) => onReviewNoteChange(event.target.value)}
            rows={3}
            maxLength={2000}
            placeholder="Nêu phạm vi tài liệu đã đối chiếu; không nhập dữ liệu cá nhân."
          />
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            Người xác nhận được lấy từ tài khoản quản trị viên đang đăng nhập và không thể sửa tại đây.
          </p>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={
              loading
              || submitting
              || campaignPreviewInvalid
              || eligibleItems.length === 0
            }
          >
            {submitting
              ? 'Đang đồng bộ...'
              : campaignPreviewInvalid
                ? 'Cần tạo lại lô duyệt'
                : 'Xác nhận pháp lý hàng loạt'}
          </Button>
        </div>
        </>
        ) : null}
      </CardContent>
    </Card>
  )
}
