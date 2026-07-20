/* eslint-disable @typescript-eslint/no-explicit-any */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { AppSidebar } from './AppSidebar'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useAuthStore } from '@/lib/stores/auth-store'

vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

describe('AppSidebar', () => {
  beforeEach(() => {
    vi.mocked(useSidebarStore).mockReturnValue({
      isCollapsed: false,
      toggleCollapse: vi.fn(),
    } as any)
    useAuthStore.setState({
      isAuthenticated: true,
      role: 'admin',
      username: 'admin',
      token: 'test',
    } as any)
  })

  it('shows Hồ sơ pháp lý for admin', () => {
    render(<AppSidebar />)
    expect(screen.getByText('navigation.notebooks')).toBeDefined()
  })

  it('hides Hồ sơ pháp lý from citizen navigation', () => {
    useAuthStore.setState({ role: 'citizen', username: 'citizen' } as any)
    render(<AppSidebar />)
    expect(screen.queryByText('navigation.notebooks')).toBeNull()
  })

  it('shows frequently asked questions and hides legal documents from citizen navigation', () => {
    useAuthStore.setState({ role: 'citizen', username: 'citizen' } as any)
    render(<AppSidebar />)
    expect(document.querySelector('a[href="/procedures"]')).not.toBeNull()
    expect(document.querySelector('a[href="/sources"]')).toBeNull()
  })

  it('shows Hồ sơ pháp lý for officer navigation', () => {
    useAuthStore.setState({ role: 'officer', username: 'officer' } as any)
    render(<AppSidebar />)
    expect(screen.getByText('navigation.notebooks')).toBeDefined()
  })

  it('toggles collapse state when clicking handle', () => {
    const toggleCollapse = vi.fn()
    vi.mocked(useSidebarStore).mockReturnValue({
      isCollapsed: false,
      toggleCollapse,
    } as any)
    render(<AppSidebar />)
    fireEvent.click(screen.getByTestId('sidebar-toggle'))
    expect(toggleCollapse).toHaveBeenCalled()
  })
})
