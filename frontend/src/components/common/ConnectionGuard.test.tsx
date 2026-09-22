import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const config = vi.hoisted(() => ({ getConfig: vi.fn(), resetConfig: vi.fn() }))
vi.mock('@/lib/config', () => config)
vi.mock('@/components/errors/ConnectionErrorOverlay', () => ({
  ConnectionErrorOverlay: ({ onRetry }: { onRetry: () => void }) => <button onClick={onRetry}>Thử lại</button>,
}))
import { ConnectionGuard } from './ConnectionGuard'

describe('ConnectionGuard shared startup configuration', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders children and joins startup config without invalidating it', async () => {
    let finish!: (value: { dbStatus: string }) => void
    config.getConfig.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    render(<ConnectionGuard><main>Nội dung</main></ConnectionGuard>)
    expect(screen.getByText('Nội dung')).toBeInTheDocument()
    expect(config.resetConfig).not.toHaveBeenCalled()
    await act(async () => finish({ dbStatus: 'online' }))
    expect(config.getConfig).toHaveBeenCalledTimes(1)
  })

  it('only clears config for an explicit retry and coalesces repeated clicks', async () => {
    config.getConfig.mockResolvedValueOnce({ dbStatus: 'offline' })
    render(<ConnectionGuard><main>Nội dung</main></ConnectionGuard>)
    const retry = await screen.findByRole('button', { name: 'Thử lại' })
    let finish!: (value: { dbStatus: string }) => void
    config.getConfig.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    act(() => {
      fireEvent.click(retry)
      fireEvent.click(retry)
    })
    expect(config.resetConfig).toHaveBeenCalledTimes(1)
    expect(config.getConfig).toHaveBeenCalledTimes(2)
    await act(async () => finish({ dbStatus: 'online' }))
    await waitFor(() => expect(screen.getByText('Nội dung')).toBeInTheDocument())
  })
})
