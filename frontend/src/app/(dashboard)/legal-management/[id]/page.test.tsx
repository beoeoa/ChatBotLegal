import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ detail: vi.fn(), updateMetadata: vi.fn() }))
const settings = vi.hoisted(() => ({
  organization_units: [
    { id: 'active-unit', code: 'ACTIVE', name: 'Phòng đang hoạt động', domain_codes: [], support_enabled: true, is_active: true, sort_order: 1 },
    { id: 'stopped-unit', code: 'STOPPED', name: 'Phòng đã tạm ngưng', domain_codes: [], support_enabled: true, is_active: false, sort_order: 2 },
  ],
}))

vi.mock('next/navigation', () => ({ useParams: () => ({ id: '42' }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/lib/api/legal-management', () => ({ legalManagementApi: api }))
vi.mock('@/lib/hooks/use-settings', () => ({ useSettings: () => ({ data: settings }) }))
vi.mock('@/components/layout/AppShell', () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }))

import LegalManagementDetailPage from './page'

function detailFixture() {
  return {
    observed_at: '2026-09-14T00:00:00Z',
    document: {
      doc_id: 42,
      document_title: 'Quyết định thử nghiệm',
      law_number: '42/2026/QĐ-UBND',
      document_type: 'Quyết định',
      issuing_agency: 'UBND thành phố Hải Phòng',
      stored_status: 'active',
      metadata_revision: 'e'.repeat(64),
      metadata_editable: true,
      issued_date: '2026-08-01',
      effective_date: '2026-09-01',
      source_url: 'https://vbpl.vn/example',
      validity_status: 'unknown',
      validity_sync: { status: 'unknown', display_label: 'Chưa xác minh hiệu lực' },
      article_count: 2,
      chunk_count: 4,
      quality_flags: [],
      organization_unit_ids: [],
    },
    structure: { article_count: 2, chunk_count: 4, articles: [] },
    relationships: { status: 'available', items: [] },
    validity: { status: 'available', observations: [], events: [], decisions: [] },
    vectors: { status: 'available' },
    faq_impacts: { status: 'unavailable' },
    versions: { status: 'unavailable' },
    audit: { status: 'available', items: [] },
  }
}

beforeAll(() => {
  class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
})

describe('LegalManagementDetailPage management workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.detail.mockResolvedValue(detailFixture())
    api.updateMetadata.mockResolvedValue({ document_id: '42', metadata_revision: 'f'.repeat(64), projection_updated: false, message: 'Đã cập nhật thông tin văn bản.' })
  })

  it('restores the management actions and detail tabs', async () => {
    render(<LegalManagementDetailPage />)
    expect(await screen.findByText('Quyết định thử nghiệm')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Thông tin chung/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Dữ liệu tra cứu/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Câu hỏi thường gặp ảnh hưởng/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Lịch sử thao tác/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Thay thế văn bản/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Phân công phòng ban/i })).toBeInTheDocument()
  })

  it('only offers active departments when assigning a document', async () => {
    render(<LegalManagementDetailPage />)
    await screen.findByText('Quyết định thử nghiệm')
    fireEvent.click(screen.getByRole('button', { name: 'Phân công phòng ban' }))
    expect(screen.getByLabelText('Phòng đang hoạt động')).toBeInTheDocument()
    expect(screen.queryByText('Phòng đã tạm ngưng')).not.toBeInTheDocument()
  })

  it('saves general information without confirming validity', async () => {
    render(<LegalManagementDetailPage />)
    await screen.findByText('Quyết định thử nghiệm')
    fireEvent.click(screen.getByRole('button', { name: 'Sửa thông tin' }))
    fireEvent.change(screen.getByLabelText('Người ký'), { target: { value: 'Nguyễn Văn A' } })
    fireEvent.change(screen.getByLabelText('Lý do cập nhật hoặc căn cứ đối chiếu'), { target: { value: 'Bổ sung thông tin người ký theo văn bản.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thông tin' }))
    await waitFor(() => expect(api.updateMetadata).toHaveBeenCalledWith('42', expect.objectContaining({ signer_name: 'Nguyễn Văn A', confirm_validity: false, expected_revision: 'e'.repeat(64) })))
  })

})
