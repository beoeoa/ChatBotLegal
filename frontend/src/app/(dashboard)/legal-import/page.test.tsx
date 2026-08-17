import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  fields: vi.fn(),
  crawlSummary: vi.fn(),
  crawlCandidates: vi.fn(),
  crawlSources: vi.fn(),
  formsCatalogCandidates: vi.fn(),
  importReadiness: vi.fn(),
  crawlPreview: vi.fn(),
  preview: vi.fn(),
  importDocument: vi.fn(),
  reviewCandidate: vi.fn(),
  reviewForm: vi.fn(),
}))

vi.mock('@/lib/api/legal-import', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/legal-import')>()
  return { ...actual, legalImportApi: api }
})

import LegalImportPage from './page'

beforeAll(() => {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
})

function readyData() {
  api.fields.mockResolvedValue([{ id: 1, name: 'Hộ tịch' }])
  api.crawlSummary.mockResolvedValue({
    pending_review_count: 0,
    unread_notification_count: 0,
    source_count: 0,
    import_queue: { queued: 0, running: 0, failed: 0, completed: 0 },
  })
  api.crawlCandidates.mockResolvedValue([])
  api.crawlSources.mockResolvedValue([])
  api.formsCatalogCandidates.mockResolvedValue({
    records: [],
    summary: { filtered_total: 0, limit: 50, offset: 0 },
  })
  api.importReadiness.mockResolvedValue({
    status: 'ready',
    embedding_device: { active: 'cpu' },
    components: { import_worker: { code: 'ready' } },
  })
}

describe('LegalImportPage user journey', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readyData()
  })

  it('starts from a compact three-tab workflow and opens the add-document flow', async () => {
    render(<LegalImportPage />)

    expect(await screen.findByRole('heading', { name: 'Trung tâm dữ liệu pháp luật' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Đề xuất chờ duyệt/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Biểu mẫu chờ duyệt' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Hiệu lực pháp lý' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Bạn muốn làm gì?' })).not.toBeInTheDocument()

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    expect(screen.getByRole('heading', { name: 'Bước 1 — Lấy nội dung văn bản' })).toBeInTheDocument()
    expect(screen.getByText('Từ liên kết chính thức')).toBeInTheDocument()
    expect(screen.getByText('Từ tệp trên máy')).toBeInTheDocument()
    expect(screen.getByText('Dán nội dung')).toBeInTheDocument()
  })

  it('does not report the whole system ready when an enabled crawler source is failing', async () => {
    api.crawlSources.mockResolvedValue([{
      id: 'source:vbpl', name: 'VBPL Hải Phòng', source_type: 'listing', sitemap_scope: 'listing',
      base_url: 'https://vbpl.vn/haiphong', enabled: true, interval_minutes: 60,
      lookback_days: 30, max_documents_per_run: 20, source_kind: 'web_crawler',
      last_status: 'error', last_error: 'Không tải được danh sách',
    }])

    render(<LegalImportPage />)

    expect(await screen.findByText('1 nguồn thu thập đang lỗi')).toBeInTheDocument()
    expect(screen.queryByText('Hệ thống sẵn sàng')).not.toBeInTheDocument()
  })

  it('shows only approve and reject for a pending proposal and hides AI assessment', async () => {
    api.crawlSummary.mockResolvedValue({
      pending_review_count: 1,
      unread_notification_count: 0,
      source_count: 1,
      import_queue: { queued: 0, running: 0, failed: 0, completed: 0 },
    })
    api.crawlCandidates.mockResolvedValue([{
      id: 'legal_crawl_candidate:one',
      external_id: 'one',
      title: 'Nghị quyết thử nghiệm',
      law_number: '11/2026/NQ-HĐND',
      document_type: 'Nghị quyết',
      issuing_agency: 'HĐND thành phố Hải Phòng',
      scope: 'haiphong',
      detail_url: 'https://vbpl.vn/test',
      source_url: 'https://vbpl.vn/test',
      status: 'pending',
      suggested_action: 'review',
      comparison_status: 'new',
      detected_changes: [],
      raw_metadata: { candidate_origin: 'vbpl_listing_scan' },
      ai_assessment: { confidence: 0.9, domain: 'dat_dai_xay_dung' },
      review_recommendation: { action: 'manual_review_required', scores: { nguon_chinh_thuc: 100 } },
    }])

    render(<LegalImportPage />)

    expect(await screen.findByText('Nghị quyết thử nghiệm')).toBeInTheDocument()
    expect(screen.getByText(/HĐND thành phố Hải Phòng/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Duyệt và nhập kho' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Từ chối' })).toBeInTheDocument()
    expect(screen.queryByText(/Đánh giá/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Yêu cầu bổ sung/ })).not.toBeInTheDocument()
  })

  it('uses only the canonical form-governance workflow and hides the legacy approve queue', async () => {
    render(<LegalImportPage />)
    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Biểu mẫu chờ duyệt' }), { button: 0, ctrlKey: false })

    expect(await screen.findByText('Quản trị thủ tục và biểu mẫu')).toBeInTheDocument()
    expect(screen.getByText(/Duyệt nguồn trước; hoàn thiện và xác nhận pháp lý sau/)).toBeInTheDocument()
    expect(screen.queryByText(/Khi bấm Duyệt:/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Duyệt' })).not.toBeInTheDocument()
  })

  it('loads an official URL into the editable full-text field', async () => {
    api.crawlPreview.mockResolvedValue({
      title: 'Nghị định thử nghiệm',
      source_url: 'https://vbpl.vn/van-ban/test',
      content: 'Số ký hiệu 12/2026/NĐ-CP Ngày ban hành 09-08-2026 Ngày có hiệu lực 15-08-2026 Loại văn bản Nghị định Cơ quan ban hành Chính phủ Người ký Nguyễn Văn A Điều 1. Phạm vi điều chỉnh Nội dung chính thức.',
      characters: 211,
    })
    render(<LegalImportPage />)
    await screen.findByRole('heading', { name: 'Trung tâm dữ liệu pháp luật' })
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    fireEvent.change(screen.getByLabelText('Đường dẫn văn bản'), {
      target: { value: 'https://vbpl.vn/van-ban/test' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Lấy nội dung từ liên kết' }))

    await waitFor(() => expect(api.crawlPreview).toHaveBeenCalledWith('https://vbpl.vn/van-ban/test'))
    expect(screen.getByLabelText('Toàn văn *')).toHaveValue(
      'Số ký hiệu 12/2026/NĐ-CP Ngày ban hành 09-08-2026 Ngày có hiệu lực 15-08-2026 Loại văn bản Nghị định Cơ quan ban hành Chính phủ Người ký Nguyễn Văn A Điều 1. Phạm vi điều chỉnh Nội dung chính thức.',
    )
    expect(screen.getByLabelText('Tên văn bản *')).toHaveValue('Nghị định thử nghiệm')
    expect(screen.getByLabelText('Số, ký hiệu *')).toHaveValue('12/2026/NĐ-CP')
    expect(screen.getByLabelText('Loại văn bản *')).toHaveValue('Nghị định')
    expect(screen.getByLabelText('Cơ quan ban hành *')).toHaveValue('Chính phủ')
    expect(screen.getByLabelText('Ngày ban hành')).toHaveValue('2026-08-09')
    expect(screen.getByLabelText('Ngày có hiệu lực *')).toHaveValue('2026-08-15')
  })

  it('submits the completed add-document form to preview', async () => {
    api.preview.mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      article_count: 1,
      chunk_count: 2,
    })
    render(<LegalImportPage />)
    await screen.findByRole('heading', { name: 'Trung tâm dữ liệu pháp luật' })
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    fireEvent.change(screen.getByLabelText('Tên văn bản *'), { target: { value: 'Nghị quyết thử nghiệm' } })
    fireEvent.change(screen.getByLabelText('Số, ký hiệu *'), { target: { value: '22/2025/NQ-HĐND' } })
    fireEvent.change(screen.getByLabelText('Loại văn bản *'), { target: { value: 'Nghị quyết' } })
    fireEvent.change(screen.getByLabelText('Cơ quan ban hành *'), { target: { value: 'HĐND Thành phố Hải Phòng' } })
    fireEvent.change(screen.getByLabelText('Ngày có hiệu lực *'), { target: { value: '2025-10-26' } })
    fireEvent.change(screen.getByLabelText('Toàn văn *'), {
      target: { value: 'Điều 1. Phạm vi điều chỉnh. Nội dung văn bản pháp luật chính thức.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Bước 3 — Kiểm tra trước khi gửi' }))

    await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(1))
    expect(api.preview).toHaveBeenCalledWith(expect.objectContaining({
      law_number: '22/2025/NQ-HĐND',
      effective_date: '2025-10-26',
    }))
    expect(await screen.findByText('Văn bản đạt điều kiện')).toBeInTheDocument()
  })

  it('does not claim a source filter is hiding documents when only a form candidate exists', async () => {
    api.crawlCandidates.mockResolvedValue([
      {
        id: 'legal_crawl_candidate:form-only',
        title: 'Biểu mẫu thử nghiệm',
        source_type: 'form',
        status: 'pending',
        raw_metadata: {},
      },
    ])

    render(<LegalImportPage />)

    expect(await screen.findByText('Không có văn bản trong mục này')).toBeInTheDocument()
    expect(screen.queryByText(/Bộ lọc nguồn đang ẩn/)).not.toBeInTheDocument()
    expect(screen.getByText('Bạn có thể kiểm tra nguồn để tìm văn bản mới hoặc tự thêm một văn bản.')).toBeInTheDocument()
  })

  it('shows a visible retry action when operational data cannot load', async () => {
    api.crawlSummary.mockRejectedValue(new Error('offline'))
    render(<LegalImportPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được dữ liệu vận hành')
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument()
  })
})
