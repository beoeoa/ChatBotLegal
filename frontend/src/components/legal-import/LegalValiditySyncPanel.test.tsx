import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { LegalValidityEvent, LegalValiditySyncClient } from '@/lib/api/legal-import'
import { LegalValiditySyncPanel } from './LegalValiditySyncPanel'

function event(index: number): LegalValidityEvent {
  return {
    id: `legal_validity_event:event-${index}`,
    document_id: `doc-${index}`,
    document_title: `Văn bản thứ ${index}`,
    law_number: `${index}/2026/TT-TEST`,
    severity: 'critical',
    review_status: 'open',
    source_url: `https://vbpl.vn/van-ban/old-${index}`,
    normalized_status: 'expired',
    effective_to: '2026-08-01',
    affected_provisions: [],
    created_at: '2026-08-08T01:00:00Z',
  }
}

function client(overrides: Partial<LegalValiditySyncClient> = {}): LegalValiditySyncClient {
  return {
    validityStatus: vi.fn(),
    validityEvents: vi.fn().mockResolvedValue({ items: [event(1)], next_cursor: null }),
    decideValidityEvent: vi.fn().mockImplementation(async (_eventId, payload) => ({
      event: null,
      decision: {},
      operation: {
        status: 'applied',
        document_id: 'doc-1',
        serving_action: payload.action === 'quarantine' ? 'block' : 'historical_only',
        current_answer_eligible: false,
        historical_lookup_allowed: payload.action === 'mark_historical',
      },
    })),
    validityDocument: vi.fn().mockResolvedValue({
      document_id: 'doc-1', observations: [], events: [], decisions: [],
      replacement_discovery: {
        status: 'candidates_found', requires_admin_review: true, reason_codes: [],
        candidates: [{
          law_number: '99/2026/TT-TEST', confidence: 'verified',
          evidence_level: 'explicit_official_relationship', relation_status: 'pending_admin_review',
          source_url: 'https://vbpl.vn/van-ban/replacement', source_kind: 'vbpl',
          observed_at: '2026-08-08T01:00:00Z', basis: 'official_affecting_document_number',
        }],
      },
    }),
    previewValidityVectorCleanup: vi.fn(),
    cleanupValidityVectors: vi.fn(),
    extractFile: vi.fn().mockResolvedValue({ filename: 'replacement.pdf', characters: 1200, content: 'Nội dung toàn văn '.repeat(100) }),
    createReplacementWorkflow: vi.fn().mockResolvedValue({
      status: 'activated',
      replacement: { document_id: 99, chunk_count: 12, activation_status: 'active' },
      old_document: { document_id: 'doc-1', serving_action: 'historical_only' },
    }),
    ...overrides,
  }
}

afterEach(() => vi.restoreAllMocks())

describe('LegalValiditySyncPanel', () => {
  it('loads only the 30-day queue with exactly 20 records per page and shows only three business actions', async () => {
    const api = client()
    render(<LegalValiditySyncPanel client={api} />)

    expect(await screen.findByText('Văn bản hết hiệu lực cần xử lý')).toBeInTheDocument()
    expect(api.validityEvents).toHaveBeenCalledWith({
      review_status: 'open', current_snapshot_only: true, limit: 20,
    })
    const actions = within(screen.getByLabelText('Ba thao tác xử lý văn bản')).getAllByRole('button')
    expect(actions.map((button) => button.textContent)).toEqual([
      'Thay bằng văn bản thay thế',
      'Đưa vào tra cứu lịch sử',
      'Cách ly khỏi tra cứu',
    ])
    expect(screen.queryByText(/5229|12186|5000/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Xác nhận nguồn khớp' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Đưa vào kiểm tra lại' })).not.toBeInTheDocument()
  })

  it('uses cursor pagination for 20 records per page', async () => {
    const first = { items: Array.from({ length: 20 }, (_, index) => event(index + 1)), next_cursor: '2026-08-01T00:00:00Z' }
    const second = { items: [event(21)], next_cursor: null }
    const api = client({ validityEvents: vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second) })
    render(<LegalValiditySyncPanel client={api} />)
    expect(await screen.findByText('Trang 1 · 20/20 văn bản')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }))
    expect(await screen.findByText('Trang 2 · 1/20 văn bản')).toBeInTheDocument()
    expect(api.validityEvents).toHaveBeenLastCalledWith({
      review_status: 'open', current_snapshot_only: true, limit: 20,
      cursor: '2026-08-01T00:00:00Z',
    })
  })

  it('activates the official replacement in current Q&A and moves the old document to history', async () => {
    const api = client()
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('Văn bản thứ 1')
    fireEvent.change(screen.getByLabelText('Lý do xử lý'), { target: { value: 'Admin xác nhận văn bản thay thế chính thức.' } })
    fireEvent.change(screen.getByLabelText('URL nguồn chính thức của văn bản thay thế'), { target: { value: 'https://vbpl.vn/van-ban/replacement' } })
    const file = new File(['pdf-content'], 'replacement.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText('Tệp PDF bổ sung (không bắt buộc)'), { target: { files: [file] } })
    expect(await screen.findByText(/Đã trích xuất replacement.pdf: 1.200 ký tự/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Thay bằng văn bản thay thế' }))

    await waitFor(() => expect(api.createReplacementWorkflow).toHaveBeenCalledWith('doc-1', {
      event_id: 'legal_validity_event:event-1',
      source_url: 'https://vbpl.vn/van-ban/replacement',
      reason: 'Admin xác nhận văn bản thay thế chính thức.',
      uploaded_content: 'Nội dung toàn văn '.repeat(100),
      uploaded_filename: 'replacement.pdf',
    }))
    expect(await screen.findByRole('status')).toHaveTextContent('đưa vào hỏi đáp hiện tại')
    expect(screen.getByRole('status')).toHaveTextContent('văn bản cũ đã chuyển sang tra cứu lịch sử')
  })

  it('moves an expired document to historical lookup', async () => {
    const api = client()
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('Văn bản thứ 1')
    fireEvent.change(screen.getByLabelText('Lý do xử lý'), { target: { value: 'Giữ văn bản để tra cứu lịch sử.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Đưa vào tra cứu lịch sử' }))

    await waitFor(() => expect(api.decideValidityEvent).toHaveBeenCalledWith(
      'legal_validity_event:event-1',
      { action: 'mark_historical', reason: 'Giữ văn bản để tra cứu lịch sử.' },
    ))
    expect(await screen.findByRole('status')).toHaveTextContent('Đã chuyển sang tra cứu lịch sử')
    expect(screen.getByRole('status')).not.toHaveTextContent('historical_only')
    expect(screen.getByRole('link', { name: 'Mở văn bản trong tra cứu lịch sử' })).toHaveAttribute('href', '/legal-documents/doc-1')
  })

  it('requires confirmation and quarantines the document from current Q&A', async () => {
    const api = client()
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('Văn bản thứ 1')
    fireEvent.change(screen.getByLabelText('Lý do xử lý'), { target: { value: 'Nguồn có sai lệch cần cách ly ngay.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cách ly khỏi tra cứu' }))

    expect(screen.getByRole('dialog', { name: 'Cách ly 1/2026/TT-TEST khỏi tra cứu?' })).toHaveTextContent('không xuất hiện trong tra cứu lịch sử')
    expect(screen.getByRole('dialog')).not.toHaveTextContent('serving_action')
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận cách ly' }))
    await waitFor(() => expect(api.decideValidityEvent).toHaveBeenCalledWith(
      'legal_validity_event:event-1',
      { action: 'quarantine', reason: 'Nguồn có sai lệch cần cách ly ngay.' },
    ))
    expect(await screen.findByRole('status')).toHaveTextContent('Đã xác nhận loại khỏi mọi tìm kiếm')
    expect(screen.getByRole('status')).not.toHaveTextContent('serving_action')
    expect(screen.getByRole('link', { name: 'Mở hồ sơ còn lưu trong kho quản trị' })).toHaveAttribute('href', '/legal-management/doc-1')
  })

  it('allows URL-only replacement and treats an attached PDF as optional', async () => {
    const api = client({
      validityDocument: vi.fn().mockResolvedValue({
        document_id: 'doc-1', observations: [], events: [], decisions: [],
        replacement_discovery: { status: 'no_explicit_candidate', requires_admin_review: false, candidates: [], reason_codes: [] },
      }),
    })
    render(<LegalValiditySyncPanel client={api} />)
    await screen.findAllByText('Văn bản thứ 1')
    expect(screen.getByRole('button', { name: 'Thay bằng văn bản thay thế' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Lý do xử lý'), { target: { value: 'Admin xác nhận tải tệp thay thế chính thức.' } })
    fireEvent.change(screen.getByLabelText('URL nguồn chính thức của văn bản thay thế'), { target: { value: 'https://vbpl.vn/van-ban/replacement' } })
    expect(screen.getByRole('button', { name: 'Thay bằng văn bản thay thế' })).toBeEnabled()
  })
})
