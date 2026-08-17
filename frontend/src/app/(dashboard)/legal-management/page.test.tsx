import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  summary: vi.fn(),
  list: vi.fn(),
  detail: vi.fn(),
  lifecycleSummary: vi.fn(),
  lifecycleDocuments: vi.fn(),
  activeIndexManifest: vi.fn(),
  createChangeEventCandidate: vi.fn(),
  confirmChangeEvent: vi.fn(),
}))

vi.mock('@/lib/api/legal-management', () => ({ legalManagementApi: api }))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import LegalManagementPage from './page'

function readyData() {
  api.lifecycleSummary.mockResolvedValue({
    legal_as_of: '2026-08-13',
    counts: {
      active: 1, future: 0, expiring_90: 0, expiring_30: 1, expiring_7: 0,
      expiring_1: 0, expired: 0, partially_expired: 0, replaced: 0,
      repealed: 0, suspended: 0, corrected: 0, consolidated: 0, unknown: 0,
    },
    alerts: [],
    observed_at: '2026-08-13T00:00:00Z',
  })
  api.lifecycleDocuments.mockResolvedValue([
    {
      document_id: 'doc-42', legal_as_of: '2026-08-13', bucket: 'expiring_30',
      vector_state: 'missing', current_serving_allowed: true,
      effective_provisions: [], inactive_provisions: [], applied_event_ids: [], ignored_event_ids: [],
    },
  ])
  api.activeIndexManifest.mockResolvedValue({
    schema_version: 'feature018.vector-serving-manifest.v1',
    active_collection: 'legal-active', active_pointer_unchanged: true, gate_passed: false,
    counts: { missing: 1, fingerprint_mismatch: 2 }, expected_fingerprints: {},
    manifest_fingerprint: 'm'.repeat(64), inventory_sha256: 'i'.repeat(64),
    reason_codes: ['missing_detected'], source_warnings: [], read_only: true,
    vectors_mutated: false, corpus_mutated: false,
  })
  api.summary.mockResolvedValue({
    observed_at: '2026-08-09T12:00:00Z',
    as_of: '2026-08-09',
    documents: {
      total: 100,
      active: 80,
      inactive: 20,
      missing_source: 3,
      missing_metadata: 5,
    },
    structure: { articles: 500, chunks: 1200 },
    tiers: { core: 25, expanded: 75 },
    validity: {
      status: 'available',
      counts: { blocked: 4, warning: 2, open_events: 7 },
      coverage: { eligible: 100, observed: 90, fresh: 85 },
    },
    operations: {
      status: 'available',
      pending_document_candidates: 6,
      import_queue: { queued: 2, running: 1, failed: 1, completed: 9 },
    },
    vectors: {
      status: 'unavailable',
      reason_code: 'vector_store_not_ready',
      message: 'Kho vector chưa sẵn sàng để kiểm đếm chỉ đọc.',
      database_chunks: 1200,
      collections: {},
    },
    faq_impacts: {
      status: 'unavailable',
      reason_code: 'faq_dependency_not_configured',
      message: 'Chưa có schema phụ thuộc FAQ được phê duyệt.',
    },
  })
  api.list.mockResolvedValue({
    items: [
      {
        doc_id: 42,
        document_title: 'Nghị định thử nghiệm',
        law_number: '42/2026/NĐ-CP',
        document_type: 'Nghị định',
        issuing_agency: 'Chính phủ',
        stored_status: 'active',
        as_of_status: 'active',
        retrieval_tier: 'core',
        article_count: 2,
        chunk_count: 4,
        source_url: 'https://vbpl.vn/example',
        quality_flags: [],
      },
    ],
    total: 1,
    limit: 30,
    offset: 0,
    as_of: '2026-08-09',
    observed_at: '2026-08-09T12:00:00Z',
  })
}

describe('LegalManagementPage', () => {
  it('labels an expired document as historical and not usable for current answers', async () => {
    api.list.mockResolvedValue({
      items: [
        {
          doc_id: 31285,
          document_title: 'Historical legal document',
          law_number: '96/2014/TT-BQP',
          stored_status: 'active',
          validity_status: 'expired',
          serving_status: 'historical_only',
          current_answer_eligible: false,
          historical_lookup_allowed: true,
          validity_sync: {
            status: 'expired',
            serving_action: 'historical_only',
            current_answer_eligible: false,
            historical_lookup_allowed: true,
            display_label: 'H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh',
          },
          article_count: 26,
          chunk_count: 74,
          quality_flags: [],
        },
      ],
      total: 1,
      limit: 30,
      offset: 0,
      as_of: '2026-08-10',
      observed_at: '2026-08-10T00:00:00Z',
    })
    api.summary.mockResolvedValue({
      observed_at: '2026-08-10T00:00:00Z',
      as_of: '2026-08-10',
      documents: { total: 1, active: 1 },
      structure: { articles: 26, chunks: 74 },
      tiers: { core: 1, expanded: 0 },
      validity: { status: 'available', counts: { blocked: 1 }, coverage: {} },
      operations: { status: 'available', pending_document_candidates: 0, import_queue: {} },
      vectors: { status: 'available' },
      faq_impacts: { status: 'unavailable' },
    })

    render(<LegalManagementPage />)

    expect(
      await screen.findByText(
        'H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Ch\u1ec9 d\u00f9ng \u0111\u1ec3 tra c\u1ee9u l\u1ecbch s\u1eed')).toBeInTheDocument()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    readyData()
  })

  it('shows repository health and a metadata-only inventory', async () => {
    render(<LegalManagementPage />)

    expect(await screen.findByRole('heading', { name: 'Kho văn bản pháp luật' })).toBeInTheDocument()
    expect(screen.getByText('80 / 100')).toBeInTheDocument()
    expect(screen.getByText('Nghị định thử nghiệm')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Văn bản cần kiểm tra hiệu lực' })).toHaveAttribute('href', '/legal-management/validity')
    expect(screen.queryByRole('link', { name: 'Bản nháp và phiên bản' })).not.toBeInTheDocument()
    expect(screen.getByText('Đang phục vụ tra cứu')).toBeInTheDocument()
    expect(screen.getByText('Cần kiểm tra hiệu lực')).toBeInTheDocument()
    expect(screen.getByText('Lỗi xử lý dữ liệu')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Nghị định thử nghiệm/ })).toHaveAttribute(
      'href',
      '/legal-management/42',
    )
    expect(screen.getByRole('columnheader', { name: 'Tình trạng dữ liệu' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Điều / chunk' })).not.toBeInTheDocument()
    expect(screen.queryByText('must-not-leak')).not.toBeInTheDocument()
  })

  it('applies server-side filters and resets pagination', async () => {
    render(<LegalManagementPage />)
    await screen.findByText('Nghị định thử nghiệm')

    fireEvent.change(screen.getByLabelText('Tìm văn bản'), {
      target: { value: 'hộ tịch' },
    })
    fireEvent.change(screen.getByLabelText('Tình trạng sử dụng'), {
      target: { value: 'expired' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Áp dụng bộ lọc' }))

    await waitFor(() => {
      expect(api.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'hộ tịch', validity_status: 'expired', offset: 0 }),
      )
    })
  })

  it('filters lifecycle rows and uses two-step evidence confirmation', async () => {
    api.createChangeEventCandidate.mockResolvedValue({
      id: 'change-1', document_id: 'doc-42', event_type: 'replace',
      effective_from: '2026-08-13', source_url: 'https://vbpl.vn/replacement',
      scope: 'whole_document', provisions: [], provenance: {},
      reason: 'Ghi nhận văn bản thay thế chính thức.', status: 'candidate',
      evidence_fingerprint: 'a'.repeat(64),
    })
    api.confirmChangeEvent.mockResolvedValue({
      event_id: 'change-1', status: 'confirmed', impact_case_count: 3,
    })
    render(<LegalManagementPage />)
    await screen.findByText('Quản trị vòng đời nâng cao')
    fireEvent.click(screen.getByText('Quản trị vòng đời nâng cao'))
    await screen.findByText('Vòng đời và trạng thái chỉ mục')

    fireEvent.change(screen.getByLabelText('Nhóm vòng đời'), {
      target: { value: 'expiring_30' },
    })
    await waitFor(() => {
      expect(api.lifecycleDocuments).toHaveBeenLastCalledWith(
        expect.objectContaining({ bucket: 'expiring_30' }),
      )
    })

    fireEvent.click(screen.getByRole('button', { name: /Ghi nhận thay đổi/ }))
    fireEvent.change(screen.getByLabelText('Loại sự kiện'), { target: { value: 'replace' } })
    fireEvent.change(screen.getByLabelText('URL nguồn chính thức'), {
      target: { value: 'https://vbpl.vn/replacement' },
    })
    fireEvent.change(screen.getByLabelText('Lý do tạo ứng viên'), {
      target: { value: 'Ghi nhận văn bản thay thế từ nguồn chính thức.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Tạo ứng viên' }))
    expect(await screen.findByText('Ứng viên chờ xác nhận')).toBeInTheDocument()
    expect(api.createChangeEventCandidate).toHaveBeenCalledWith(
      expect.objectContaining({ document_id: 'doc-42', event_type: 'replace' }),
    )

    fireEvent.change(screen.getByLabelText('Lý do xác nhận'), {
      target: { value: 'Đã đối chiếu nguồn và ngày áp dụng hợp lệ.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận bằng chứng' }))
    await waitFor(() => {
      expect(api.confirmChangeEvent).toHaveBeenCalledWith('change-1', {
        evidence_fingerprint: 'a'.repeat(64),
        reason: 'Đã đối chiếu nguồn và ngày áp dụng hợp lệ.',
      })
    })
  })

  it('distinguishes an empty result from a failed dashboard read', async () => {
    api.list.mockResolvedValue({
      items: [], total: 0, limit: 30, offset: 0, as_of: '2026-08-09', observed_at: '2026-08-09T12:00:00Z',
    })
    render(<LegalManagementPage />)
    expect(await screen.findByText('Không có văn bản phù hợp')).toBeInTheDocument()
    expect(screen.queryByText('Không tải được tổng quan')).not.toBeInTheDocument()
  })
})
