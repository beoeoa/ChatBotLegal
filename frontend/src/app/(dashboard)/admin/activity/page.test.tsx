import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ list: vi.fn(), sensitiveView: vi.fn(), exportXlsx: vi.fn(), exportCsv: vi.fn(), exportUrl: vi.fn() }))
vi.mock('@/lib/api/admin-activity', () => ({ adminActivityApi: api }))
vi.mock('@/components/layout/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }))

import AdminActivityPage from './page'

const item = {
  id: 'audit:1', occurred_at: new Date().toISOString(), actor: 'user:admin',
  actor_label: 'Admin', actor_role: 'admin', activity_type: 'data_ingestion',
  module: 'legal_data', result: 'success', action: 'admin.legal.import',
  action_label: 'Đã nhập văn bản pháp luật', resource_type: 'legal_document',
  resource_label: 'Văn bản pháp luật', resource_id: 'doc-1', sensitive_detail_available: true,
}

describe('AdminActivityPage', () => {
  beforeEach(() => {
    api.list.mockReset()
    api.sensitiveView.mockReset()
    api.exportXlsx.mockReset()
    api.exportCsv.mockReset()
    api.exportUrl.mockReset()
    api.list.mockResolvedValue({
      items: [item], total: 1, next_cursor: null, content_policy: 'metadata_only',
    })
    api.sensitiveView.mockResolvedValue({
      ...item,
      details: { result: 'success', reason: 'Bổ sung văn bản theo hồ sơ đã duyệt' },
      explanation: 'Văn bản đã được đưa vào hàng chờ để kiểm tra metadata.',
      impact: 'Chưa được dùng để trả lời cho đến khi hoàn tất kiểm tra.',
      next_action: 'Mở hàng chờ nhập văn bản để tiếp tục kiểm tra.',
      workflow_status: 'queued',
        detail_rows: [
          { label: 'Mã công việc', value: 'job-1' },
          { label: 'Trạng thái xử lý', value: 'Đang chờ' },
          { label: 'Lý do', value: 'Bổ sung văn bản theo hồ sơ đã duyệt' },
          { label: 'Lý do truy cập', value: 'Rà soát sự cố nhập dữ liệu' },
        ],
      ip_address: '[REDACTED]', user_agent: '[REDACTED]',
      access_reason: 'Rà soát sự cố nhập dữ liệu',
    })
    api.exportXlsx.mockResolvedValue({ blob: new Blob(['xlsx']), filename: 'nhat-ky-quan-tri.xlsx' })
    api.exportUrl.mockReturnValue('/api/admin/activity/export?format=csv')
  })

  it('renders a compact Vietnamese audit table and four filters', async () => {
    render(<AdminActivityPage />)

    expect(await screen.findByRole('heading', { name: 'Nhật ký quản trị' })).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Danh sách sự kiện quản trị' })
    for (const heading of ['Thời gian', 'Người thực hiện', 'Hoạt động', 'Kết quả']) {
      expect(within(table).getByRole('columnheader', { name: heading })).toBeInTheDocument()
    }
    expect(screen.getByText('Đã nhập văn bản pháp luật')).toBeInTheDocument()
    expect(screen.queryByText('admin.legal.import')).not.toBeInTheDocument()

    expect(screen.getByLabelText('Tìm kiếm')).toBeInTheDocument()
    expect(screen.getByLabelText('Loại hoạt động')).toBeInTheDocument()
    expect(screen.getByLabelText('Kết quả')).toBeInTheDocument()
    expect(screen.getByLabelText('Thời gian')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Xuất Excel' })).toBeInTheDocument()
  })

  it('renders legacy API events instead of leaving actor and activity blank', async () => {
    api.list.mockResolvedValue({
      items: [{
        id: 'audit:legacy', occurred_at: '2026-08-16T06:37:00Z', actor: 'system',
        actor_role: 'system', module: 'system', result: 'success',
        action: 'retention.purge.completed', resource_type: 'retention_job',
        resource_id: 'scheduled', sensitive_detail_available: true,
      }],
      total: 1, next_cursor: null, content_policy: 'metadata_only',
    })

    render(<AdminActivityPage />)

    const table = await screen.findByRole('table', { name: 'Danh sách sự kiện quản trị' })
    fireEvent.change(screen.getByLabelText('Thời gian'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lọc nhật ký' }))
    expect(await within(table).findByText('Hệ thống')).toBeInTheDocument()
    expect(within(table).getByText('Đã hoàn tất xóa dữ liệu hết hạn')).toBeInTheDocument()
    expect(within(table).getByText('Tác vụ lưu trữ')).toBeInTheDocument()
  })

  it('applies search, activity type and result filters', async () => {
    render(<AdminActivityPage />)
    await screen.findByText('Đã nhập văn bản pháp luật')

    fireEvent.change(screen.getByLabelText('Tìm kiếm'), { target: { value: 'văn bản' } })
    fireEvent.change(screen.getByLabelText('Loại hoạt động'), { target: { value: 'data_ingestion' } })
    fireEvent.change(screen.getByLabelText('Kết quả'), { target: { value: 'success' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lọc nhật ký' }))

    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({
      search: 'văn bản', activity_type: 'data_ingestion', result: 'success', limit: 50,
    })))
  })

  it('filters legacy API results locally when the running API ignores new filters', async () => {
    api.list.mockResolvedValue({
      items: [
        { ...item, actor_label: undefined, activity_type: undefined, action_label: undefined, resource_label: undefined },
        {
          ...item, id: 'audit:form', action: 'form.source.approve', resource_type: 'form_review_case',
          actor_label: undefined, activity_type: undefined, action_label: undefined, resource_label: undefined,
        },
      ],
      total: 2, next_cursor: null, content_policy: 'metadata_only',
    })
    render(<AdminActivityPage />)
    expect(await screen.findByText('Đã duyệt nguồn biểu mẫu')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Loại hoạt động'), { target: { value: 'data_ingestion' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lọc nhật ký' }))

    await waitFor(() => expect(screen.queryByText('Đã duyệt nguồn biểu mẫu')).not.toBeInTheDocument())
    expect(screen.getByText('Đã nhập văn bản pháp luật')).toBeInTheDocument()
    expect(screen.getByText('1 sự kiện')).toBeInTheDocument()
  })

  it('exports Excel through the authenticated API client', async () => {
    const createObjectURL = vi.fn(() => 'blob:activity-xlsx')
    const revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })

    render(<AdminActivityPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Xuất Excel' }))

    await waitFor(() => expect(api.exportXlsx).toHaveBeenCalledWith(expect.objectContaining({ limit: 50 })))
    expect(createObjectURL).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:activity-xlsx'), { timeout: 1500 })
    click.mockRestore()
  })

  it('rejects meaningless access reasons and renders structured detail in a side dialog', async () => {
    render(<AdminActivityPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Đã nhập văn bản pháp luật/ }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Chi tiết sự kiện')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Lý do truy cập'), { target: { value: 'dddddddddddddddd' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Xem chi tiết' }))

    expect(within(dialog).getByText(/ít nhất 3 từ có nghĩa/i)).toBeInTheDocument()
    expect(api.sensitiveView).not.toHaveBeenCalled()

    fireEvent.change(within(dialog).getByLabelText('Lý do truy cập'), { target: { value: 'Rà soát sự cố nhập dữ liệu' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Xem chi tiết' }))

    await waitFor(() => expect(api.sensitiveView).toHaveBeenCalledWith('audit:1', 'Rà soát sự cố nhập dữ liệu'))
    expect(await within(dialog).findByText('Bổ sung văn bản theo hồ sơ đã duyệt')).toBeInTheDocument()
    expect(within(dialog).getByText('Văn bản đã được đưa vào hàng chờ để kiểm tra thông tin mô tả.')).toBeInTheDocument()
    expect(within(dialog).getByText('Mở hàng chờ nhập văn bản để tiếp tục kiểm tra.')).toBeInTheDocument()
    expect(within(dialog).queryByText('Thông tin kỹ thuật')).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/metadata/i)).not.toBeInTheDocument()
  })
})
