import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiPut = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api/client', () => ({
  apiClient: { put: apiPut },
}))

import { AccountProfileForm } from './AccountProfileForm'

describe('AccountProfileForm', () => {
  beforeEach(() => apiPut.mockReset())

  it('allows every role to save editable profile fields', async () => {
    apiPut.mockResolvedValue({
      data: { profile: { full_name: 'Nguyễn Văn A', phone: '0912345678', ward: 'Phường Lê Chân' } },
    })
    const onSaved = vi.fn()

    render(
      <AccountProfileForm
        initialProfile={{ full_name: 'Nguyễn Văn A', phone: '', ward: 'Phường Lê Chân' }}
        roleLabel="Quản trị viên"
        onSaved={onSaved}
      />,
    )

    fireEvent.change(screen.getByLabelText('Số điện thoại'), { target: { value: '0912345678' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    await waitFor(() => expect(apiPut).toHaveBeenCalledWith('/users/me', {
      full_name: 'Nguyễn Văn A',
      phone: '0912345678',
      ward: 'Phường Lê Chân',
    }))
    expect(onSaved).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent('Đã lưu thông tin hồ sơ')
  })

  it('explains invalid phone input without sending a request', () => {
    render(
      <AccountProfileForm
        initialProfile={{ full_name: '', phone: '', ward: '' }}
        roleLabel="Công dân"
        onSaved={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('Số điện thoại'), { target: { value: '123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    expect(apiPut).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('8 đến 15 chữ số')
  })
})
