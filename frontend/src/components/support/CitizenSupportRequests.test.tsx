import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CitizenSupportRequests } from './CitizenSupportRequests'

describe('CitizenSupportRequests', () => {
  it('shows owned queue state and explicit open/cancel actions without content', () => {
    const onOpen = vi.fn()
    const onCancel = vi.fn()
    render(
      <CitizenSupportRequests
        loading={false}
        tickets={[{
          id: 'ticket-private-1',
          canonical_domain: 'ho_tich_chung_thuc',
          status: 'queued',
          created_at: '2026-08-13T08:00:00Z',
          position: 2,
        }]}
        onRefresh={() => undefined}
        onOpen={onOpen}
        onCancel={onCancel}
      />,
    )

    expect(screen.getByText('Yêu cầu của tôi')).toBeInTheDocument()
    expect(screen.getByText('Đang chờ cán bộ')).toBeInTheDocument()
    expect(screen.getByText('Vị trí 2')).toBeInTheDocument()
    expect(screen.queryByText(/ticket-private-1/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Xem yêu cầu' }))
    fireEvent.click(screen.getByRole('button', { name: 'Huỷ yêu cầu' }))
    expect(onOpen).toHaveBeenCalledWith('ticket-private-1')
    expect(onCancel).toHaveBeenCalledWith('ticket-private-1')
  })

  it('does not offer cancellation for a resolved request', () => {
    render(
      <CitizenSupportRequests
        loading={false}
        tickets={[{
          id: 'resolved-1',
          status: 'resolved',
          created_at: '2026-08-13T08:00:00Z',
        }]}
        onRefresh={() => undefined}
        onOpen={() => undefined}
        onCancel={() => undefined}
      />,
    )
    expect(screen.getByText('Đã xử lý')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Huỷ yêu cầu' })).not.toBeInTheDocument()
  })
})
