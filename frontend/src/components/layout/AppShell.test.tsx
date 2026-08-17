import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

const setCollapsed = vi.fn()

vi.mock('@/lib/stores/sidebar-store', () => ({
  useSidebarStore: (selector: (state: { setCollapsed: typeof setCollapsed }) => unknown) => (
    selector({ setCollapsed })
  ),
}))

vi.mock('./SetupBanner', () => ({ SetupBanner: () => null }))
vi.mock('./AppSidebar', () => ({
  AppSidebar: ({ onNavigate }: { onNavigate?: () => void }) => (
    <button type="button" onClick={onNavigate}>Đi đến mục</button>
  ),
}))

describe('AppShell mobile navigation', () => {
  beforeEach(() => setCollapsed.mockClear())

  it('opens an overlay drawer and closes it after navigation', () => {
    render(<AppShell><div>Nội dung chat</div></AppShell>)

    fireEvent.click(screen.getByRole('button', { name: 'Mở điều hướng' }))
    expect(setCollapsed).toHaveBeenCalledWith(false)
    expect(screen.getByRole('dialog', { name: 'Điều hướng' })).toBeInTheDocument()

    const navigationButtons = screen.getAllByRole('button', { name: 'Đi đến mục' })
    fireEvent.click(navigationButtons[navigationButtons.length - 1])
    expect(screen.queryByRole('dialog', { name: 'Điều hướng' })).not.toBeInTheDocument()
  })
})
