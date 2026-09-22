import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  domains: vi.fn(),
  communeCatalog: vi.fn(),
  exportXlsx: vi.fn(),
}))

vi.mock('@/lib/api/legal-documents', () => ({ legalDocumentsApi: api }))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import SourcesPage from './page'

function item(id: number) {
  return {
    doc_id: id,
    document_title: `Văn bản ${id}`,
    law_number: `${id}/2026/QĐ-UBND`,
    issuing_agency: 'UBND Thành phố Hải Phòng',
    effective_status: 'active',
    effective_date: '2026-08-16',
    field_name: 'Hộ tịch',
    domain_name: 'Hộ tịch và chứng thực',
    retrieval_tier: 'core',
    article_count: 3,
    source_url: 'https://vbpl.vn/example',
  }
}

describe('SourcesPage', () => {
  beforeEach(() => {
    api.list.mockReset()
    api.domains.mockReset()
    api.communeCatalog.mockReset()
    api.exportXlsx.mockReset()
    api.domains.mockResolvedValue([
      { slug: 'ho_tich_chung_thuc', name: 'Hộ tịch và chứng thực', field_count: 1 },
    ])
    api.communeCatalog.mockResolvedValue([{
      id: 'van-phong-hdnd-ubnd',
      name: 'Văn phòng HĐND và UBND',
      code: 'van_phong',
      fields: [{
        code: 'ho_tich_chung_thuc',
        name: 'Hộ tịch - Chứng thực',
        domains: ['ho_tich_chung_thuc'],
      }],
    }])
    api.list.mockImplementation(async (params: { offset?: number; limit?: number }) => {
      const offset = params.offset || 0
      const limit = params.limit || 20
      return {
        items: Array.from({ length: Math.min(limit, 45 - offset) }, (_, index) => item(offset + index + 1)),
        total: 45,
        limit,
        offset,
        as_of: '2026-08-16',
        tier: 'all',
      }
    })
    api.exportXlsx.mockResolvedValue({
      blob: new Blob(['xlsx']),
      filename: 'danh-sach-van-ban-20260816-120000.xlsx',
    })
    Object.defineProperty(window.URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:test') })
    Object.defineProperty(window.URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      value: (callback: FrameRequestCallback) => {
        callback(0)
        return 1
      },
    })
    Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  })

  it('loads exactly 20 documents per server page and replaces page content', async () => {
    render(<SourcesPage />)

    expect(await screen.findByText('Văn bản 1')).toBeInTheDocument()
    expect(screen.getByText('Văn bản 20')).toBeInTheDocument()
    expect(screen.queryByText('Tải thêm văn bản')).not.toBeInTheDocument()
    expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 20, offset: 0 }),
      expect.any(AbortSignal),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }))
    expect(await screen.findByText('Văn bản 21')).toBeInTheDocument()
    expect(screen.getByText('Văn bản 40')).toBeInTheDocument()
    expect(screen.queryByText('Văn bản 1')).not.toBeInTheDocument()
    expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 20, offset: 20 }),
      expect.any(AbortSignal),
    )
    expect(screen.getByRole('button', { name: 'Trang 2', current: 'page' })).toBeInTheDocument()
  })

  it('resets to page one and maps the selected validity milestone to API parameters', async () => {
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')
    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }))
    await screen.findByText('Văn bản 21')

    fireEvent.change(screen.getByLabelText('Lọc theo tình trạng hiệu lực'), { target: { value: 'expiring_30' } })

    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        validity_status: 'expiring_30',
        limit: 20,
        offset: 0,
      }),
      expect.any(AbortSignal),
    ))
    expect(await screen.findByRole('button', { name: 'Trang 1', current: 'page' })).toBeInTheDocument()
  })

  it('shows only the requested validity milestones and removes date-range controls', async () => {
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')

    const milestone = screen.getByLabelText('Lọc theo tình trạng hiệu lực')
    expect(milestone).toHaveTextContent('Còn hiệu lực')
    expect(milestone).toHaveTextContent('Hết hiệu lực')
    expect(milestone).toHaveTextContent('Sắp hết hiệu lực trong 30 ngày')
    expect(milestone).not.toHaveTextContent('Sắp có hiệu lực')
    expect(milestone).not.toHaveTextContent('Chưa xác minh hiệu lực')
    expect(screen.queryByLabelText('Từ ngày')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Đến ngày')).not.toBeInTheDocument()
  })

  it('exports every result with the currently applied filters', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')

    fireEvent.change(screen.getByLabelText('Lọc theo phòng ban'), { target: { value: 'van-phong-hdnd-ubnd' } })
    fireEvent.change(screen.getByLabelText('Lọc theo tình trạng hiệu lực'), { target: { value: 'expired' } })
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        domain: 'ho_tich_chung_thuc',
        validity_status: 'expired',
      }),
      expect.any(AbortSignal),
    ))

    fireEvent.click(screen.getByRole('button', { name: /Xuất Excel \(45\)/ }))
    await waitFor(() => expect(api.exportXlsx).toHaveBeenCalledWith(expect.objectContaining({
      domain: 'ho_tich_chung_thuc',
      validity_status: 'expired',
      sort_by: 'effective_date',
      sort_order: 'desc',
    })))
    expect(click).toHaveBeenCalled()
    click.mockRestore()
  })

  it('shows only the fields configured for the selected department', async () => {
    api.communeCatalog.mockResolvedValueOnce([
      {
        id: 'kinh-te-ha-tang',
        name: 'Phòng Kinh tế - Hạ tầng - Đô thị',
        code: 'kinh_te',
        fields: [
          { code: 'dat_dai_xay_dung', name: 'Đất đai - Xây dựng', domains: ['dat_dai_xay_dung'] },
          { code: 'trat_tu_do_thi', name: 'Trật tự đô thị', domains: ['trat_tu_do_thi'] },
        ],
      },
    ])
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')

    fireEvent.change(screen.getByLabelText('Lọc theo phòng ban'), { target: { value: 'kinh-te-ha-tang' } })

    expect(screen.getByRole('option', { name: 'Đất đai - Xây dựng' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Trật tự đô thị' })).toBeInTheDocument()
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ domain: 'dat_dai_xay_dung', offset: 0 }),
      expect.any(AbortSignal),
    ))
  })
})
