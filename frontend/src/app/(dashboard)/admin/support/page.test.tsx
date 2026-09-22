import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/api/client', () => ({ apiClient: api }))
vi.mock('@/components/layout/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }))

import AdminSupportPage from './page'

describe('AdminSupportPage', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
    api.get.mockResolvedValue({ data: [{
      id: 'private-ticket-id',
      canonical_domain: 'ho_tich_chung_thuc',
      status: 'queued',
      priority: 'high',
      assigned_officer_id: null,
      assignment_generation: 0,
      created_at: '2026-08-13T08:00:00Z',
      updated_at: '2026-08-13T08:00:00Z',
      first_response_due_at: '2026-08-13T08:30:00Z',
      resolution_due_at: '2026-08-13T16:00:00Z',
      overdue: true,
    }] })
    api.post.mockResolvedValue({ data: {
      grant: { expires_at: '2026-08-13T09:15:00Z', audit_event_id: 'audit-1' },
      ticket: { id: 'private-ticket-id', status: 'queued' },
      messages: [{ id: 'm1', sender_role: 'citizen', content: 'Nội dung chỉ hiện sau audit', created_at: '2026-08-13T08:00:00Z' }],
    } })
  })

  it('shows response deadlines but protects private content behind a recorded reason', async () => {
    render(<AdminSupportPage />)
    expect(await screen.findByText('Quá hạn phản hồi')).toBeInTheDocument()
    expect(screen.queryByText('Nội dung chỉ hiện sau audit')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Mở nội dung có kiểm soát/ }))
    const open = screen.getByRole('button', { name: 'Ghi lịch sử và mở nội dung' })
    expect(open).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Lý do truy cập'), { target: { value: 'Kiểm tra khiếu nại SLA 123' } })
    expect(open).toBeEnabled()
    fireEvent.click(open)

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/support/admin/tickets/private-ticket-id/view-content',
      { reason: 'Kiểm tra khiếu nại SLA 123' },
    ))
    expect(await screen.findByText('Nội dung chỉ hiện sau audit')).toBeInTheDocument()
  })
})
