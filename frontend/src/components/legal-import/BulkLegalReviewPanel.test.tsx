import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BulkLegalReviewPanel } from './BulkLegalReviewPanel'
import type { FormLegalReviewPreview } from '@/lib/api/legal-import'

const preview: FormLegalReviewPreview = {
  legal_as_of: '2026-07-27',
  preview_fingerprint: 'preview-fingerprint',
  attestation_id: 'form-batch-preview-fingerprint',
  summary: {
    total_forms: 3,
    active_catalog_forms: 3,
    eligible_forms: 1,
    excluded_forms: 2,
    already_approved_forms: 0,
    excluded_no_official_forms: 0,
  },
  reason_counts: {
    EFFECTIVITY_UNKNOWN: 1,
    SEED_DEMO_QUARANTINED: 1,
  },
  eligible_items: [
    {
      candidate_id: 'candidate-1',
      canonical_form_id: 'form-birth',
      procedure_id: 'dang_ky_khai_sinh',
      form_code: '01',
      canonical_name: 'Tờ khai đăng ký khai sinh',
      source_page_url: 'https://sotp.haiphong.gov.vn/thu-tuc/khai-sinh',
      source_download_url: 'https://cdn.haiphong.gov.vn/forms/khai-sinh.pdf',
      local_path: 'data/uploads/forms/priority_official/birth.pdf',
      sha256: 'a'.repeat(64),
      legal_basis: ['60/2014/QH13'],
      effective_from: '2025-07-01',
      effective_to: null,
      jurisdiction: 'Hai Phong',
      administrative_level: 'commune',
      review_status: 'approved',
      legal_review_status: 'candidate_pending_review',
      reason_codes: [],
    },
  ],
  excluded_items: [
    {
      canonical_form_id: 'form-no-effectivity',
      procedure_id: 'xac_nhan_tinh_trang_hon_nhan',
      form_code: null,
      canonical_name: 'Tờ khai xác nhận tình trạng hôn nhân',
      source_page_url: null,
      source_download_url: null,
      local_path: null,
      sha256: null,
      legal_basis: [],
      effective_from: null,
      effective_to: null,
      jurisdiction: 'Hai Phong',
      administrative_level: 'commune',
      review_status: 'candidate_pending_review',
      legal_review_status: 'not_reviewed',
      reason_codes: ['EFFECTIVITY_UNKNOWN'],
    },
    {
      canonical_form_id: 'form-demo',
      procedure_id: 'demo',
      form_code: null,
      canonical_name: '[DEMO] Biểu mẫu',
      source_page_url: null,
      source_download_url: null,
      local_path: null,
      sha256: null,
      legal_basis: [],
      effective_from: null,
      effective_to: null,
      jurisdiction: 'Hai Phong',
      administrative_level: 'commune',
      review_status: 'approved',
      legal_review_status: 'candidate_pending_review',
      reason_codes: ['SEED_DEMO_QUARANTINED'],
    },
  ],
  catalog_excluded_items: [],
  catalog_exclusion_reason_counts: {},
}

describe('BulkLegalReviewPanel', () => {
  it('shows totals, hard-gate reasons and official metadata before confirmation', () => {
    render(
      <BulkLegalReviewPanel
        preview={preview}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={() => undefined}
      />,
    )

    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('Tờ khai đăng ký khai sinh')).toBeInTheDocument()
    expect(screen.getByText('dang_ky_khai_sinh')).toBeInTheDocument()
    expect(screen.getByText('60/2014/QH13')).toBeInTheDocument()
    expect(screen.getAllByText('Cần kiểm tra thêm dữ liệu nguồn').length).toBeGreaterThan(0)
    expect(screen.queryByText('EFFECTIVITY_UNKNOWN')).not.toBeInTheDocument()
    expect(screen.queryByText('SEED_DEMO_QUARANTINED')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Nguồn chính thức/i })).toHaveAttribute(
      'href',
      preview.eligible_items[0].source_page_url,
    )
  })

  it('requires one explicit confirmation and never auto-submits', () => {
    const onConfirm = vi.fn()
    const { rerender } = render(
      <BulkLegalReviewPanel
        preview={preview}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={onConfirm}
      />,
    )

    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole('button', { name: 'Xác nhận pháp lý hàng loạt' }),
    )
    expect(onConfirm).toHaveBeenCalledTimes(1)

    rerender(
      <BulkLegalReviewPanel
        preview={{ ...preview, eligible_items: [], summary: { ...preview.summary, eligible_forms: 0 } }}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={onConfirm}
      />,
    )
    expect(
      screen.queryByRole('button', { name: 'Xác nhận pháp lý hàng loạt' }),
    ).not.toBeInTheDocument()
  })

  it('shows no-official-form records separately from the review queue', () => {
    render(
      <BulkLegalReviewPanel
        preview={{
          ...preview,
          summary: {
            ...preview.summary,
            active_catalog_forms: 2,
            excluded_forms: 1,
            excluded_no_official_forms: 1,
          },
          excluded_items: preview.excluded_items.slice(0, 1),
          catalog_excluded_items: [
            {
              ...preview.excluded_items[1],
              canonical_form_id: 'form-no-official-state-form',
              canonical_name: 'Biểu mẫu không có trong thủ tục chính thức',
              reason_codes: ['NO_OFFICIAL_STATE_FORM_CONFIRMED'],
            },
          ],
          catalog_exclusion_reason_counts: {
            NO_OFFICIAL_STATE_FORM_CONFIRMED: 1,
          },
        }}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={() => undefined}
      />,
    )

    expect(screen.getByText('Đã loại khỏi danh mục phục vụ')).toBeInTheDocument()
    expect(
      screen.getByText('Biểu mẫu không có trong thủ tục chính thức'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('Cần kiểm tra thêm dữ liệu nguồn').length).toBeGreaterThan(0)
  })

  it('starts the automated completion campaign without approving forms', () => {
    const onRunCampaign = vi.fn()
    render(
      <BulkLegalReviewPanel
        preview={preview}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={() => undefined}
        onRunCampaign={onRunCampaign}
        campaignRunning={false}
        campaignStatus={{
          schema_version: 'form-completion-campaign-v1',
          run_id: 'opaque-run',
          generated_at: '2026-07-27T10:00:00Z',
          legal_as_of: '2026-07-27',
          status: 'completed_fail_closed',
          stage: 'complete',
          counts: {
            ready_for_attestation: 0,
            remaining_unresolved: 55,
          },
          reason_counts: {},
          automated_approval: false,
          human_attestation_required: true,
        }}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Tự động xử lý biểu mẫu còn lại' }),
    )
    expect(onRunCampaign).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('form-campaign-status')).toHaveTextContent(
      'Còn thiếu bằng chứng: 55',
    )
  })

  it('keeps the technical runner hidden until its campaign status is available', () => {
    render(
      <BulkLegalReviewPanel
        preview={preview}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={() => undefined}
        onRunCampaign={() => undefined}
      />,
    )

    expect(
      screen.queryByRole('button', { name: 'Tự động xử lý biểu mẫu còn lại' }),
    ).not.toBeInTheDocument()
  })

  it('requires a fresh campaign before approving a checksum-drifted shortlist', () => {
    const onConfirm = vi.fn()
    const onRunCampaign = vi.fn()
    render(
      <BulkLegalReviewPanel
        preview={preview}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={onConfirm}
        onRunCampaign={onRunCampaign}
        campaignStatus={{
          schema_version: 'form-resolution-campaign-v1',
          run_id: 'run-stale',
          status: 'READY_FOR_HUMAN_ATTESTATION',
          stage: 'complete',
          counts: {},
          reason_counts: {},
          automated_approval: false,
          human_attestation_required: true,
          feature_flag_enabled: false,
          manifest_drift: true,
        }}
      />,
    )

    expect(screen.getByTestId('form-manifest-drift')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Cần tạo lại lô duyệt' }),
    ).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole('button', { name: 'Tạo lại lô từ dữ liệu nguồn mới' }),
    )
    expect(onRunCampaign).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('shows the campaign shortlist when the legacy preview has zero eligible forms', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(
      <BulkLegalReviewPanel
        preview={{
          ...preview,
          eligible_items: [],
          summary: { ...preview.summary, eligible_forms: 0 },
        }}
        loading={false}
        submitting={false}
        reviewNote=""
        onReviewNoteChange={() => undefined}
        onConfirm={() => undefined}
        campaignStatus={{
          schema_version: 'form-resolution-campaign-v1',
          run_id: 'run-1',
          status: 'READY_FOR_HUMAN_ATTESTATION',
          stage: 'complete',
          counts: {
            ready_for_attestation: 107,
            remaining_unresolved: 281,
            target_identities: 278,
            approved_runtime_identities: 51,
            pending_identities: 227,
            ready_for_human_attestation_identities: 83,
          },
          reason_counts: {},
          automated_approval: false,
          human_attestation_required: true,
          feature_flag_enabled: false,
        }}
        campaignShortlist={{
          run_id: 'run-1',
          legal_as_of: '2026-07-27',
          preview_fingerprint: 'a'.repeat(64),
          attestation_id: 'form-resolution-run-1-a',
          batch_id: 'form-review-001-test',
          batch_count: 2,
          identity_count: 1,
          canonical_form_count: 1,
          procedure_binding_count: 2,
          invalid_record_count: 0,
          feature_flag_enabled: false,
          automated_approval: false,
          human_attestation_required: true,
          manifest_sha256: 'c'.repeat(64),
          source_snapshot_sha256: 'd'.repeat(64),
          records: [
            {
              candidate_id: 'candidate-campaign',
              canonical_form_id: 'form-campaign',
              procedure_id: '1.000001',
              form_code: '04',
              canonical_name: 'Gi\u1ea5y \u0111\u1ec1 ngh\u1ecb',
              source_page_url: 'https://vbpl.vn/document',
              source_download_url: 'https://vbpl.vn/form.pdf',
              sha256: 'b'.repeat(64),
              legal_basis: ['25/2025/TT-BYT'],
              effective_from: '2025-06-30',
              effective_to: null,
              jurisdiction: 'Hai Phong',
              administrative_level: 'commune',
              review_status: 'candidate_pending_review',
              legal_review_status: 'candidate_pending_review',
              reason_codes: [],
            },
            {
              candidate_id: 'candidate-campaign',
              canonical_form_id: 'form-campaign',
              procedure_id: '1.000001',
              form_code: '04',
              canonical_name: 'Gi\u1ea5y \u0111\u1ec1 ngh\u1ecb',
              source_page_url: 'https://vbpl.vn/document',
              source_download_url: 'https://vbpl.vn/form.pdf',
              sha256: 'b'.repeat(64),
              legal_basis: ['25/2025/TT-BYT'],
              effective_from: '2025-06-30',
              effective_to: null,
              jurisdiction: 'Hai Phong',
              administrative_level: 'commune',
              review_status: 'candidate_pending_review',
              legal_review_status: 'candidate_pending_review',
              reason_codes: [],
            },
          ],
        }}
      />,
    )

    expect(
      screen.getByText(
        'Lô form-review-001-test (2 lô): 1 danh tính biểu mẫu / 2 liên kết thủ tục / 1 biểu mẫu chuẩn',
      ),
    ).toBeInTheDocument()
    expect(screen.getAllByText('Gi\u1ea5y \u0111\u1ec1 ngh\u1ecb')).toHaveLength(2)
    expect(
      consoleError.mock.calls.some((call) => call.join(' ').includes('same key')),
    ).toBe(false)
    consoleError.mockRestore()
    expect(
      screen.getByRole('button', {
        name: 'X\u00e1c nh\u1eadn ph\u00e1p l\u00fd h\u00e0ng lo\u1ea1t',
      }),
    ).toBeEnabled()
    expect(screen.getByTestId('form-campaign-status')).toHaveTextContent('Chờ người duyệt xác nhận')
  })
})
