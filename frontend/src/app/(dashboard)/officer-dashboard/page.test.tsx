import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api/client', () => ({ apiClient: api }))
vi.mock('@/lib/stores/auth-store', () => ({ useAuthStore: () => ({ username: 'Cán bộ QA' }) }))
vi.mock('@/lib/hooks/use-operating-scope', () => ({
  useOperatingScope: () => ({
    scope: {
      primary_organization_unit_name: 'Phòng QA',
      domains: ['chuyen_doi_so'],
      domain_labels: { chuyen_doi_so: 'Chuyển đổi số' },
      can_manage_content: true,
    },
    error: '',
  }),
}))
vi.mock('@/components/layout/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }))
import OfficerDashboardPage from './page'

describe('Officer dashboard truthful metrics', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.get.mockImplementation(async (url: string) => ({ data:
      url === '/notebooks' ? Array.from({ length: 8 }, (_, i) => ({ id: `qa-${i}`, name: `Hồ sơ QA ${i}` })) :
        url.startsWith('/faq') ? { total: 0 } : url.endsWith('/summary') ? { total: 0, by_status: {} } : { candidates: [] },
    }))
  })

  it('shows a real zero FAQ count and counts all notebooks, not just five recent ones', async () => {
    render(<OfficerDashboardPage />)
    await screen.findByText('8')
    expect(screen.queryByText('10')).not.toBeInTheDocument()
    expect(screen.getAllByText('0')).toHaveLength(2)
    expect(screen.getAllByRole('link', { name: /Hồ sơ QA/ })).toHaveLength(5)
    expect(screen.getByText('Hàng chờ phòng ban')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Phòng ban chính: Phòng QA')
    expect(screen.queryByText('Sẵn sàng tiếp nhận')).not.toBeInTheDocument()
  })

  it('announces unavailable data without fake totals or a false empty-state', async () => {
    api.get.mockRejectedValue(new Error('database_connection_failed'))
    render(<OfficerDashboardPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Chưa tải được')
    expect(screen.queryByText(/database_connection_failed/)).not.toBeInTheDocument()
    expect(screen.queryByText('10')).not.toBeInTheDocument()
    expect(screen.queryByText('Bạn chưa gửi đề xuất văn bản nào')).not.toBeInTheDocument()
  })

  it('does not send duplicate refresh requests from repeated clicks', async () => {
    render(<OfficerDashboardPage />)
    await screen.findByText('8')
    api.get.mockImplementation(() => new Promise(() => {}))
    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }))
    fireEvent.click(screen.getByRole('button', { name: 'Đang cập nhật...' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(8))
  })

  it('uses full summary counts instead of a truncated proposal preview', async () => {
    api.get.mockImplementation(async (url: string) => ({ data: url.endsWith('/summary') ? { total: 123, by_status: { pending: 102, imported: 21 } } : url.includes('/candidates') ? { candidates: [{ id: 'preview', status: 'pending' }] } : url.startsWith('/faq') ? { total: 0 } : [] }))
    render(<OfficerDashboardPage />)
    expect(await screen.findByText('102 chờ duyệt')).toBeInTheDocument()
    expect(screen.getByText('123')).toBeInTheDocument()
  })

  it('renders newly configured domains instead of a fixed seven-domain catalog', async () => {
    render(<OfficerDashboardPage />)
    const link = await screen.findByRole('link', { name: /Chuyển đổi số/ })
    expect(link).toHaveAttribute('href', '/procedures?domain=chuyen_doi_so')
    expect(screen.getByText('Lĩnh vực được phân công')).toBeInTheDocument()
    expect(screen.queryByText('Danh mục 7 Lĩnh vực hành chính')).not.toBeInTheDocument()
  })
})
