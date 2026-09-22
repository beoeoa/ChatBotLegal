import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.hoisted(() => vi.fn())
const settings = vi.hoisted(() => ({ config_revision: 33, organization_routing_mode: 'hybrid', organization_units: [] }))
vi.mock('@/lib/hooks/use-settings', () => ({ useSettings: () => ({ data: settings, isLoading: false, refetch: vi.fn() }) }))
vi.mock('@/lib/api/client', () => ({ apiClient: { get: apiGet } }))
vi.mock('@/lib/config', () => ({ resetConfig: vi.fn() }))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { SystemSettingsForm } from './SystemSettingsForm'

describe('Department cutover readiness', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows full-corpus backlog in Vietnamese and blocks full cutover', async () => {
    apiGet.mockResolvedValue({ data: { ready_for_unit_primary: false, legacy_documents_unassigned: 12000, hybrid_observation_days_remaining: 0 } })
    render(<SystemSettingsForm />)
    expect(await screen.findByText('Văn bản trong toàn bộ kho chưa phân công')).toBeInTheDocument()
    expect(screen.getByText('12000')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Phòng ban là dữ liệu chính' })).toBeDisabled()
    expect(screen.getByLabelText('Chế độ chuyển đổi')).toHaveValue('hybrid')
  })

  it('does not enable cutover if readiness cannot be read', async () => {
    apiGet.mockRejectedValue(new Error('offline'))
    render(<SystemSettingsForm />)
    await waitFor(() => expect(screen.getByText(/Chưa đọc được trạng thái chuyển đổi/)).toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'Phòng ban là dữ liệu chính' })).toBeDisabled()
  })

  it('exposes cross-store disagreements instead of hiding the blocking condition', async () => {
    apiGet.mockResolvedValue({ data: { ready_for_unit_primary: false, corpus_assignment_disagreements: 1 } })
    render(<SystemSettingsForm />)
    expect(await screen.findByText('Phân công không khớp giữa kho quản trị và kho tìm kiếm')).toBeInTheDocument()
    expect(screen.getByText(/không phải tổng văn bản riêng biệt/)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Phòng ban là dữ liệu chính' })).toBeDisabled()
  })

  it('does not describe data reconciliation as completed workflow acceptance', async () => {
    apiGet.mockResolvedValue({ data: { ready_for_unit_primary: true } })
    render(<SystemSettingsForm />)
    expect(await screen.findByText(/sau khi hoàn tất nghiệm thu các luồng nghiệp vụ/)).toBeInTheDocument()
  })
})
