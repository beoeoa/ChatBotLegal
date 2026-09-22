import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

const setCollapsed = vi.fn()
const useAuthStore = vi.fn()
const pathname = vi.hoisted(() => ({ value: '/procedures' }))

vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (state: { role: 'citizen' | 'officer' | 'admin' }) => unknown) => (
    selector(useAuthStore())
  ),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => pathname.value,
}))

vi.mock('@/lib/stores/sidebar-store', () => ({
  useSidebarStore: (selector: (state: { setCollapsed: typeof setCollapsed }) => unknown) => (
    selector({ setCollapsed })
  ),
}))

vi.mock('./SetupBanner', () => ({ SetupBanner: () => null }))
vi.mock('./AppSidebar', () => ({
  AppSidebar: ({ onNavigate }: { onNavigate?: () => void }) => (
    <button type="button" data-testid="primary-sidebar" onClick={onNavigate}>Đi đến mục</button>
  ),
}))

describe('AppShell mobile navigation', () => {
  beforeEach(() => {
    setCollapsed.mockClear()
    pathname.value = '/procedures'
    useAuthStore.mockReturnValue({ role: 'citizen' })
  })

  it('opens an overlay drawer and closes it after navigation', () => {
    render(<AppShell><div>Nội dung chat</div></AppShell>)

    fireEvent.click(screen.getByRole('button', { name: 'Mở điều hướng' }))
    expect(setCollapsed).toHaveBeenCalledWith(false)
    expect(screen.getByRole('dialog', { name: 'Điều hướng' })).toBeInTheDocument()

    const navigationButtons = screen.getAllByRole('button', { name: 'Đi đến mục' })
    fireEvent.click(navigationButtons[navigationButtons.length - 1])
    expect(screen.queryByRole('dialog', { name: 'Điều hướng' })).not.toBeInTheDocument()
  })

  it('uses the primary sidebar for feature pages instead of the legacy topbar shell', () => {
    render(<AppShell><div>Nội dung thủ tục</div></AppShell>)

    expect(screen.getByRole('button', { name: 'Mở điều hướng' })).toBeInTheDocument()
    expect(screen.getByText('Nội dung thủ tục')).toBeInTheDocument()
  })

  it('keeps the same primary shell for officer feature pages', () => {
    useAuthStore.mockReturnValue({ role: 'officer' })

    render(<AppShell><div>Nội dung cán bộ</div></AppShell>)

    expect(screen.getByTestId('primary-sidebar')).toBeInTheDocument()
    expect(screen.getByText('Nội dung cán bộ')).toBeInTheDocument()
  })

  it('keeps the admin primary shell unchanged', () => {
    useAuthStore.mockReturnValue({ role: 'admin' })

    render(<AppShell><div>Nội dung quản trị</div></AppShell>)

    expect(screen.getByTestId('primary-sidebar')).toBeInTheDocument()
    expect(screen.getByText('Nội dung quản trị')).toBeInTheDocument()
  })

  it.each(['citizen', 'officer'])('leaves the chat route to its conversation sidebar for %s', (role) => {
    pathname.value = '/search'
    useAuthStore.mockReturnValue({ role })
    const { container } = render(<AppShell><div>Hỏi đáp</div></AppShell>)
    expect(screen.queryByTestId('primary-sidebar')).not.toBeInTheDocument()
    expect(container.querySelector('.user-workspace')).not.toBeNull()
  })

  it('leaves admin chat navigation owned by the conversation sidebar', () => {
    pathname.value = '/search'
    useAuthStore.mockReturnValue({ role: 'admin' })
    const { container } = render(<AppShell><div>Hỏi đáp</div></AppShell>)
    expect(screen.queryByTestId('primary-sidebar')).not.toBeInTheDocument()
    expect(container.querySelector('.user-workspace')).toBeNull()
  })
})
