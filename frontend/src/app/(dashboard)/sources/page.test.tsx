import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  domains: vi.fn(),
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
    api.exportXlsx.mockReset()
    api.domains.mockResolvedValue([
      { slug: 'ho_tich_chung_thuc', name: 'Hộ tịch và chứng thực', field_count: 1 },
    ])
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

  it('resets to page one and maps the selected date range to API parameters', async () => {
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')
    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }))
    await screen.findByText('Văn bản 21')

    fireEvent.change(screen.getByLabelText('Chọn loại ngày'), { target: { value: 'issued' } })
    fireEvent.change(screen.getByLabelText('Từ ngày'), { target: { value: '2026-01-01' } })
    fireEvent.change(screen.getByLabelText('Đến ngày'), { target: { value: '2026-06-30' } })

    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        issued_from: '2026-01-01',
        issued_to: '2026-06-30',
        limit: 20,
        offset: 0,
      }),
      expect.any(AbortSignal),
    ))
    expect(await screen.findByRole('button', { name: 'Trang 1', current: 'page' })).toBeInTheDocument()
  })

  it('does not call the API for a reversed date range', async () => {
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')

    fireEvent.change(screen.getByLabelText('Từ ngày'), { target: { value: '2026-08-17' } })
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ effective_from: '2026-08-17' }),
      expect.any(AbortSignal),
    ))
    const callsBeforeInvalidRange = api.list.mock.calls.length
    fireEvent.change(screen.getByLabelText('Đến ngày'), { target: { value: '2026-08-16' } })

    expect(await screen.findByText('Ngày bắt đầu không được sau ngày kết thúc.')).toBeInTheDocument()
    await new Promise((resolve) => window.setTimeout(resolve, 0))
    expect(api.list).toHaveBeenCalledTimes(callsBeforeInvalidRange)
  })

  it('exports every result with the currently applied filters', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<SourcesPage />)
    await screen.findByText('Văn bản 1')

    fireEvent.change(screen.getByLabelText('Lọc theo lĩnh vực'), { target: { value: 'ho_tich_chung_thuc' } })
    fireEvent.change(screen.getByLabelText('Từ ngày'), { target: { value: '2026-01-01' } })
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ domain: 'ho_tich_chung_thuc', effective_from: '2026-01-01' }),
      expect.any(AbortSignal),
    ))

    fireEvent.click(screen.getByRole('button', { name: 'Xuất Excel' }))
    await waitFor(() => expect(api.exportXlsx).toHaveBeenCalledWith(expect.objectContaining({
      domain: 'ho_tich_chung_thuc',
      effective_from: '2026-01-01',
      sort_by: 'effective_date',
      sort_order: 'desc',
    })))
    expect(click).toHaveBeenCalled()
    click.mockRestore()
  })
})
