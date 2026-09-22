import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  fields: vi.fn(),
  crawlSummary: vi.fn(),
  crawlCandidates: vi.fn(),
  crawlCandidatePage: vi.fn(),
  crawlSources: vi.fn(),
  formsCatalogCandidates: vi.fn(),
  importReadiness: vi.fn(),
  extractFile: vi.fn(),
  extractionJob: vi.fn(),
  crawlPreview: vi.fn(),
  preview: vi.fn(),
  importDocument: vi.fn(),
  reviewCandidate: vi.fn(),
  resolveDuplicate: vi.fn(),
  reviewForm: vi.fn(),
}))
const settingsState = vi.hoisted(() => ({
  data: { organization_routing_mode: 'hybrid', organization_units: [], legal_domains: [] } as Record<string, unknown>,
}))

vi.mock('@/lib/api/legal-import', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/legal-import')>()
  return { ...actual, legalImportApi: api }
})

vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/lib/hooks/use-settings', () => ({
  useSettings: () => settingsState,
}))

import LegalImportPage from './page'

beforeAll(() => {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  Element.prototype.scrollIntoView = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => false)
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
})

function readyData() {
  api.fields.mockResolvedValue([{ id: 1, name: 'Hộ tịch' }])
  api.crawlSummary.mockResolvedValue({
    pending_review_count: 0,
    unread_notification_count: 0,
    source_count: 0,
    sources: [],
    import_queue: { queued: 0, running: 0, failed: 0, completed: 0 },
  })
  api.crawlCandidates.mockResolvedValue([])
  api.crawlCandidatePage.mockImplementation(async () => {
    const candidates = await api.crawlCandidates()
    return { candidates, total: candidates.length, limit: 20, offset: 0 }
  })
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
    settingsState.data = {
      organization_routing_mode: 'hybrid',
      organization_units: [],
      legal_domains: [],
    }
    readyData()
  })

  it('keeps the queue busy until the actual candidate request completes', async () => {
    let finish!: (value: unknown) => void
    api.crawlCandidatePage.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const { container } = render(<LegalImportPage />)
    expect(container.querySelector('[aria-busy]')).toHaveAttribute('aria-busy', 'true')
    await act(async () => finish({ candidates: [], total: 0, limit: 20, offset: 0 }))
    await waitFor(() => expect(container.querySelector('[aria-busy]')).toHaveAttribute('aria-busy', 'false'))
  })

  it('does not leave a perpetual checking label after the readiness request fails', async () => {
    api.importReadiness.mockRejectedValue(new Error('import_worker_timeout'))
    render(<LegalImportPage />)
    expect(await screen.findByText('Chưa kiểm tra được hệ thống nhập kho')).toBeInTheDocument()
    expect(screen.queryByText('Đang kiểm tra hệ thống')).not.toBeInTheDocument()
    expect(screen.queryByText('import_worker_timeout')).not.toBeInTheDocument()
  })

  it('starts from a compact document-only workflow and opens the add-document flow', async () => {
    render(<LegalImportPage />)

    expect(await screen.findByRole('heading', { name: 'Tiếp nhận và duyệt văn bản' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Đề xuất chờ duyệt/ })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Biểu mẫu chờ duyệt' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Hiệu lực pháp lý' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Bạn muốn làm gì?' })).not.toBeInTheDocument()

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    expect(screen.getByRole('heading', { name: 'Bước 1 — Lấy nội dung văn bản' })).toBeInTheDocument()
    expect(screen.getByText('Từ liên kết')).toBeInTheDocument()
    expect(screen.getByText('Từ tệp trên máy')).toBeInTheDocument()
    expect(screen.getByText('Dán nội dung')).toBeInTheDocument()
  })

  it('does not report the whole system ready when an enabled crawler source is failing', async () => {
    api.crawlSummary.mockResolvedValue({
      pending_review_count: 0,
      unread_notification_count: 0,
      source_count: 1,
      import_queue: { queued: 0, running: 0, failed: 0, completed: 0 },
      sources: [{
      id: 'source:vbpl', name: 'VBPL Hải Phòng', source_type: 'listing', sitemap_scope: 'listing',
      base_url: 'https://vbpl.vn/haiphong', enabled: true, interval_minutes: 60,
      lookback_days: 30, max_documents_per_run: 20, source_kind: 'web_crawler',
      last_status: 'failed', last_error: 'Không tải được danh sách',
      }],
    })

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
    expect(screen.getByRole('button', { name: 'Duyệt' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Không duyệt' })).toBeInTheDocument()
    expect(screen.queryByText(/Đánh giá/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Yêu cầu bổ sung/ })).not.toBeInTheDocument()
  })

  it('loads an official URL into the editable full-text field', async () => {
    api.crawlPreview.mockResolvedValue({
      title: 'Nghị định thử nghiệm',
      source_url: 'https://vbpl.vn/van-ban/test',
      content: 'Số ký hiệu 12/2026/NĐ-CP Ngày ban hành 09-08-2026 Ngày có hiệu lực 15-08-2026 Loại văn bản Nghị định Cơ quan ban hành Chính phủ Người ký Nguyễn Văn A Điều 1. Phạm vi điều chỉnh Nội dung chính thức.',
      characters: 211,
    })
    render(<LegalImportPage />)
    await screen.findByRole('heading', { name: 'Tiếp nhận và duyệt văn bản' })
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    fireEvent.change(screen.getByLabelText('Đường dẫn văn bản'), {
      target: { value: 'https://vbpl.vn/van-ban/test' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Lấy nội dung từ liên kết' }))

    await waitFor(() => expect(api.crawlPreview).toHaveBeenCalledWith('https://vbpl.vn/van-ban/test'))
    fireEvent.click(screen.getByRole('button', { name: /Mở toàn văn và đối chiếu bản gốc/ }))
    expect(screen.getByLabelText('Toàn bộ nội dung đã trích xuất')).toHaveValue(
      'Số ký hiệu 12/2026/NĐ-CP Ngày ban hành 09-08-2026 Ngày có hiệu lực 15-08-2026 Loại văn bản Nghị định Cơ quan ban hành Chính phủ Người ký Nguyễn Văn A Điều 1. Phạm vi điều chỉnh Nội dung chính thức.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Xong — quay lại thông tin' }))
    expect(screen.getByLabelText('Tên văn bản *')).toHaveValue('Nghị định thử nghiệm')
    expect(screen.getByLabelText('Số, ký hiệu *')).toHaveValue('12/2026/NĐ-CP')
    expect(screen.getByLabelText('Loại văn bản *')).toHaveValue('Nghị định')
    expect(screen.getByLabelText('Cơ quan ban hành *')).toHaveValue('Chính phủ')
    expect(screen.getByLabelText('Ngày ban hành')).toHaveValue('2026-08-09')
    expect(screen.getByLabelText('Ngày có hiệu lực *')).toHaveValue('2026-08-15')
  })

  it('continues PDF OCR to completion and clears metadata from the previous link', async () => {
    const nativeText = 'CHÍNH PHỦ\nSố: 346/2026/NĐ-CP\nHà Nội, ngày 09 tháng 9 năm 2026\nNGHỊ ĐỊNH\nĐiều 1. Nội dung lớp chữ.'
    api.extractFile.mockResolvedValue({
      filename: 'Nghị-định-346-2026-NĐ-CP.pdf',
      content: nativeText,
      characters: nativeText.length,
      file_fingerprint: 'a'.repeat(64),
      total_pages: 24,
      processed_pages: 6,
      coverage_percent: 25,
      native_text_pages: [1, 2, 3, 4, 5, 6],
      ocr_requested_pages: Array.from({length: 18}, (_, index) => index + 7),
      ocr_status: 'queued',
      extraction_status: 'processing',
      extraction_job_id: 'b'.repeat(32),
      complete: false,
    })
    api.extractionJob.mockResolvedValue({
      job_id: 'b'.repeat(32),
      file_id: 'c'.repeat(32),
      extraction_status: 'complete',
      extracted_text: `${nativeText}\n--- Trang 24 ---\nNội dung OCR trang cuối.`,
      total_pages: 24,
      page_count: 24,
      processed_pages: 24,
      coverage_percent: 100,
      pages_without_text: [],
      failed_pages: [],
      native_text_pages: [1, 2, 3, 4, 5, 6],
      ocr_pages: Array.from({length: 18}, (_, index) => index + 7),
      complete: true,
    })

    const { container } = render(<LegalImportPage />)
    await screen.findByRole('heading', { name: 'Tiếp nhận và duyệt văn bản' })
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })
    fireEvent.change(screen.getByLabelText('Tên văn bản *'), { target: { value: 'Nghị định số 349/2026/NĐ-CP' } })
    fireEvent.change(screen.getByLabelText('Số, ký hiệu *'), { target: { value: '349/2026/NĐ-CP' } })

    const input = container.querySelector('#legal-file') as HTMLInputElement
    const file = new File(['pdf'], 'Nghị-định-346-2026-NĐ-CP.pdf', {type: 'application/pdf'})
    fireEvent.change(input, {target: {files: [file]}})

    await waitFor(() => expect(api.extractFile).toHaveBeenCalledWith(file, 'auto'))
    expect(await screen.findByText(/Đang OCR 18 trang ảnh trong nền/)).toBeInTheDocument()
    await waitFor(() => expect(api.extractionJob).toHaveBeenCalledWith('b'.repeat(32)), {timeout: 2500})
    await waitFor(() => expect(screen.getByText(/24 \/ 24 trang đã xử lý/)).toBeInTheDocument())
    expect(screen.getByLabelText('Tên văn bản *')).toHaveValue('Nghị định số 346/2026/NĐ-CP')
    expect(screen.getByLabelText('Số, ký hiệu *')).toHaveValue('346/2026/NĐ-CP')
    expect(screen.getByLabelText('Ngày ban hành')).toHaveValue('2026-09-09')
    expect(screen.queryByDisplayValue('349/2026/NĐ-CP')).not.toBeInTheDocument()
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
    await screen.findByRole('heading', { name: 'Tiếp nhận và duyệt văn bản' })
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    fireEvent.change(screen.getByLabelText('Tên văn bản *'), { target: { value: 'Nghị quyết thử nghiệm' } })
    fireEvent.change(screen.getByLabelText('Số, ký hiệu *'), { target: { value: '22/2025/NQ-HĐND' } })
    fireEvent.change(screen.getByLabelText('Loại văn bản *'), { target: { value: 'Nghị quyết' } })
    fireEvent.change(screen.getByLabelText('Cơ quan ban hành *'), { target: { value: 'HĐND Thành phố Hải Phòng' } })
    fireEvent.change(screen.getByLabelText('Ngày có hiệu lực *'), { target: { value: '2025-10-26' } })
    fireEvent.click(screen.getByRole('button', { name: /Mở toàn văn và đối chiếu bản gốc/ }))
    fireEvent.change(screen.getByLabelText('Toàn bộ nội dung đã trích xuất'), {
      target: { value: 'Điều 1. Phạm vi điều chỉnh. Nội dung văn bản pháp luật chính thức.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Xong — quay lại thông tin' }))
    fireEvent.click(screen.getByRole('button', { name: 'Bước 3 — Kiểm tra trước khi gửi' }))

    await waitFor(() => expect(api.preview).toHaveBeenCalledTimes(1))
    expect(api.preview).toHaveBeenCalledWith(expect.objectContaining({
      law_number: '22/2025/NQ-HĐND',
      effective_date: '2025-10-26',
    }))
    expect(await screen.findByText('Văn bản đạt điều kiện')).toBeInTheDocument()
  })

  it('maps the managed economy domain to its approved storage field', async () => {
    settingsState.data = {
      organization_routing_mode: 'hybrid',
      organization_units: [],
      legal_domains: [{
        code: 'kinh_te', name: 'Kinh tế', aliases: [], is_active: true, sort_order: 1,
      }],
    }
    api.fields.mockResolvedValue([
      { id: 34, name: 'Công Thương' },
      { id: 366, name: 'Kinh tế xây dựng' },
    ])
    render(<LegalImportPage />)
    await screen.findByRole('heading', { name: 'Tiếp nhận và duyệt văn bản' })
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Thêm văn bản' }), { button: 0, ctrlKey: false })

    const domainTrigger = screen.getByRole('combobox', { name: 'Lĩnh vực' })
    const nativeDomainSelect = domainTrigger.parentElement?.querySelector('select')
    expect(nativeDomainSelect).not.toBeNull()
    fireEvent.change(nativeDomainSelect as HTMLSelectElement, { target: { value: 'kinh_te' } })

    expect(screen.queryByText(/Đây là lĩnh vực quản lý mới/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Chủ đề dữ liệu pháp lý cụ thể')).not.toBeInTheDocument()
    expect(screen.queryByText(/chưa có trường dữ liệu tương ứng/)).not.toBeInTheDocument()
  })

  it('shows form candidates in the unified list without exposing the legal-document approval actions', async () => {
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

    expect(await screen.findByText('Biểu mẫu thử nghiệm')).toBeInTheDocument()
    expect(screen.getByText('Biểu mẫu — quản lý ở mục Biểu mẫu')).toBeInTheDocument()
    expect(screen.getByText('Biểu mẫu được hiển thị để đối chiếu')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Duyệt' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Mở quản lý biểu mẫu' })).toHaveAttribute('href', '/procedure-management')
  })

  it('shows a visible retry action when operational data cannot load', async () => {
    api.crawlSummary.mockRejectedValue(new Error('offline'))
    render(<LegalImportPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được dữ liệu vận hành')
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument()
  })

  it('loads real server pages beyond 200 records', async () => {
    api.crawlCandidatePage.mockImplementation(async (_status, limit, offset) => ({
      candidates: [{ id: `legal_crawl_candidate:${offset}`, title: `Đề xuất thứ ${offset + 1}`, source_type: 'document', status: 'rejected' }],
      total: 241, limit, offset,
    }))
    render(<LegalImportPage />)
    expect(await screen.findByText('Đề xuất thứ 1')).toBeInTheDocument()
    expect(screen.getByText(/Trang 1 \/ 13/)).toHaveTextContent('tổng 241 bản ghi')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Trang sau' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }))
    expect(await screen.findByText('Đề xuất thứ 21')).toBeInTheDocument()
    expect(screen.queryByText('Đề xuất thứ 1')).not.toBeInTheDocument()
    expect(api.crawlCandidatePage).toHaveBeenLastCalledWith('all', 20, 20, 'all', 'all', 15_000)
  })

  it('keeps loaded proposals visible when the summary fails', async () => {
    api.crawlSummary.mockRejectedValue(new Error('offline'))
    api.crawlCandidates.mockResolvedValue([{ id: 'candidate:kept', title: 'Đề xuất vẫn xem được', source_type: 'document', status: 'rejected' }])
    render(<LegalImportPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được dữ liệu vận hành')
    expect(screen.getByText('Đề xuất vẫn xem được')).toBeInTheDocument()
  })
})
