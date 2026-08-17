import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { LegalValiditySyncClient } from '@/lib/api/legal-import'
import { LegalValiditySyncPanel } from './LegalValiditySyncPanel'


function client(overrides: Partial<LegalValiditySyncClient> = {}): LegalValiditySyncClient {
  return {
    validityStatus: vi.fn().mockResolvedValue({
      mode: 'protect',
      status: 'healthy',
      generated_at: '2026-08-08T01:00:00Z',
      last_success_at: '2026-08-08T01:00:00Z',
      next_run_at: '2026-08-08T01:30:00Z',
      coverage: { eligible: 10, observed: 9, fresh: 9 },
      counts: { active: 8, blocked: 1, warning: 0, open_events: 1 },
      sources: [{ source_kind: 'vbpl', status: 'healthy', last_success_at: '2026-08-08T01:00:00Z', reason_code: null }],
    }),
    validityEvents: vi.fn().mockResolvedValue({
      items: [{
        id: 'legal_validity_event:event-1',
        document_id: 'doc-1',
        document_title: 'Luật thử nghiệm',
        law_number: '31/2024/QH15',
        severity: 'critical',
        review_status: 'open',
        serving_action: 'historical_only',
        source_url: 'https://vbpl.vn/van-ban/example',
        raw_status: 'Hết hiệu lực',
        normalized_status: 'expired',
        affected_provisions: [],
        created_at: '2026-08-08T01:00:00Z',
      }],
      next_cursor: null,
    }),
    runValiditySync: vi.fn().mockResolvedValue({ status: 'completed', events_created: 1 }),
    decideValidityEvent: vi.fn().mockResolvedValue({
      event: { id: 'legal_validity_event:event-1', review_status: 'confirmed' },
      decision: { id: 'legal_validity_decision:1', action: 'confirm_mapping', new_review_status: 'confirmed' },
    }),
    validityDocument: vi.fn().mockResolvedValue({
      document_id: 'doc-1', observations: [], events: [], decisions: [],
      replacement_discovery: { status: 'no_explicit_candidate', requires_admin_review: false, candidates: [], reason_codes: ['replacement_relation_not_observed'] },
    }),
    previewValidityVectorCleanup: vi.fn().mockResolvedValue({
      job_id: 'job-1', state: 'blocking_applied', blocking_applied: true,
      document_id: 'doc-1', law_number: '31/2024/QH15', expected_vector_count: 12, dry_run: true,
    }),
    cleanupValidityVectors: vi.fn().mockResolvedValue({
      job_id: 'job-1', state: 'vector_cleanup_completed', blocking_applied: true,
      document_id: 'doc-1', law_number: '31/2024/QH15', expected_vector_count: 12,
    }),
    ...overrides,
  }
}


describe('LegalValiditySyncPanel', () => {
  it('shows health, coverage and the review queue', async () => {
    render(<LegalValiditySyncPanel client={client()} />)

    expect(await screen.findByText('Đồng bộ hiệu lực đang ổn định')).toBeInTheDocument()
    expect(screen.getByText('9/10 văn bản')).toBeInTheDocument()
    expect(screen.getAllByText('31/2024/QH15')).toHaveLength(2)
    expect(screen.getByRole('table', { name: 'Danh sách văn bản cần kiểm tra hiệu lực' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chi tiết đối chiếu' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Mở bằng chứng nguồn' })).toHaveAttribute(
      'href',
      'https://vbpl.vn/van-ban/example',
    )
  })

  it('requires an operational reason and runs a bounded manual sync', async () => {
    const api = client()
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('31/2024/QH15')

    fireEvent.click(screen.getByText('Kiểm tra lại dữ liệu từ nguồn chính thức'))
    fireEvent.change(screen.getByLabelText('Lý do kiểm tra ngay'), {
      target: { value: 'Kiểm tra ngay sau thông báo văn bản mới' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra ngay' }))

    await waitFor(() => expect(api.runValiditySync).toHaveBeenCalledWith({
      reason: 'Kiểm tra ngay sau thông báo văn bản mới',
      scope: ['central', 'haiphong', 'local'],
      limit: 100,
    }))
  })

  it('records an explicit reason with the Admin decision', async () => {
    const api = client()
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('31/2024/QH15')

    fireEvent.change(screen.getByLabelText('Lý do xử lý 31/2024/QH15'), {
      target: { value: 'Đã đối chiếu chính xác số, cơ quan và ngày ban hành.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận kết quả' }))

    await waitFor(() => expect(api.decideValidityEvent).toHaveBeenCalledWith(
      'legal_validity_event:event-1',
      {
        action: 'confirm_mapping',
        reason: 'Đã đối chiếu chính xác số, cơ quan và ngày ban hành.',
      },
    ))
  })

  it('previews replacement evidence and exact vector cleanup before mutation', async () => {
    const api = client({
      validityDocument: vi.fn().mockResolvedValue({
        document_id: 'doc-1', observations: [], events: [], decisions: [],
        replacement_discovery: {
          status: 'candidates_found', requires_admin_review: true, reason_codes: [],
          candidates: [{
            law_number: '34/2026/N\u0110-CP', confidence: 'verified',
            evidence_level: 'explicit_official_relationship', relation_status: 'pending_admin_review',
            source_url: 'https://vbpl.vn/van-ban/replacement', source_kind: 'vbpl',
            observed_at: '2026-08-08T01:00:00Z', basis: 'official_affecting_document_number',
          }],
        },
      }),
    })
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('31/2024/QH15')

    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra quan hệ thay thế' }))

    expect(await screen.findByText('34/2026/N\u0110-CP')).toBeInTheDocument()
    expect(screen.getByText(/12 đoạn dữ liệu/)).toBeInTheDocument()
    expect(api.cleanupValidityVectors).not.toHaveBeenCalled()
  })

  it('does not display an impossible coverage ratio', async () => {
    const api = client({
      validityStatus: vi.fn().mockResolvedValue({
        mode: 'protect', status: 'healthy', generated_at: '2026-08-08T01:00:00Z',
        last_success_at: '2026-08-08T01:00:00Z', next_run_at: '2026-08-08T01:30:00Z',
        coverage: { eligible: 10, observed: 12, fresh: 12 },
        counts: { active: 8, blocked: 1, warning: 0, open_events: 1 }, sources: [],
      }),
    })
    render(<LegalValiditySyncPanel client={api} />)

    expect(await screen.findByText('10/10 văn bản')).toBeInTheDocument()
    expect(screen.getByText('Số liệu kiểm tra từ máy chủ chưa đồng nhất.')).toBeInTheDocument()
    expect(screen.queryByText('12/10 văn bản')).not.toBeInTheDocument()
  })

  it('shows stale and degraded states without claiming a fresh sync', async () => {
    const api = client({
      validityStatus: vi.fn().mockResolvedValue({
        mode: 'protect',
        status: 'stale',
        generated_at: '2026-08-07T00:00:00Z',
        last_success_at: '2026-08-07T00:00:00Z',
        next_run_at: '2026-08-08T01:30:00Z',
        coverage: { eligible: 10, observed: 9, fresh: 0 },
        counts: { active: 8, blocked: 1, warning: 0, open_events: 1 },
        sources: [{ source_kind: 'vbpl', status: 'degraded', last_success_at: '2026-08-07T00:00:00Z', reason_code: 'OFFICIAL_SOURCE_CONNECT_TIMEOUT' }],
      }),
    })
    render(<LegalValiditySyncPanel client={api} />)

    expect(await screen.findByText('Dữ liệu hiệu lực đã cũ')).toBeInTheDocument()
    expect(screen.getByText(/Nguồn VBPL đang gián đoạn/)).toBeInTheDocument()
    expect(screen.queryByText('Đồng bộ hiệu lực đang ổn định')).not.toBeInTheDocument()
  })

  it('offers retry when status loading fails', async () => {
    const validityStatus = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({
        mode: 'protect', status: 'healthy', generated_at: null, last_success_at: null,
        next_run_at: null, coverage: { eligible: 0, observed: 0, fresh: 0 },
        counts: { active: 0, blocked: 0, warning: 0, open_events: 0 }, sources: [],
      })
    render(<LegalValiditySyncPanel client={client({ validityStatus })} />)

    expect(await screen.findByText('Không tải được trạng thái đồng bộ hiệu lực.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Thử lại' }))
    await waitFor(() => expect(validityStatus).toHaveBeenCalledTimes(2))
  })
})
