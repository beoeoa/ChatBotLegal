import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatMemoryContinuations } from './ChatMemoryContinuations'

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }))

vi.mock('@/lib/api/client', () => ({
  apiClient: { get: apiGet },
}))

describe('ChatMemoryContinuations', () => {
  beforeEach(() => {
    apiGet.mockReset()
  })

  it('does not expose saved procedures until runtime and user opt-in are both enabled', async () => {
    apiGet.mockImplementation((url: string) => Promise.resolve({
      data: url.endsWith('/settings')
        ? { enabled: false, runtime_available: true }
        : [{
            id: 'memory-1',
            memory_key: 'tracked_procedure',
            value: { id: '1.004194', name: 'Đăng ký tạm trú' },
          }],
    }))

    render(<ChatMemoryContinuations onSelect={vi.fn()} />)

    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('saved-memory-continuations')).not.toBeInTheDocument()
  })

  it('prefills through the callback and never submits by itself', async () => {
    const onSelect = vi.fn()
    apiGet.mockImplementation((url: string) => Promise.resolve({
      data: url.endsWith('/settings')
        ? { enabled: true, runtime_available: true }
        : [{
            id: 'memory-1',
            memory_key: 'tracked_procedure',
            value: { id: '1.004194', name: 'Đăng ký tạm trú' },
          }],
    }))

    render(<ChatMemoryContinuations onSelect={onSelect} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Tiếp tục: Đăng ký tạm trú' }))
    expect(onSelect).toHaveBeenCalledOnce()
    expect(onSelect).toHaveBeenCalledWith(
      'Tôi muốn tiếp tục hỏi về Đăng ký tạm trú.',
      'memory-1',
    )
  })
})
