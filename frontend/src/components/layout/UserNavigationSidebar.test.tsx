import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { UserNavigationSidebar } from './UserNavigationSidebar'

const scope = vi.hoisted(() => ({ can_receive_support: false }))
vi.mock('@/lib/hooks/use-operating-scope', () => ({
  useOperatingScope: () => ({ scope }),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => '/sources',
}))

vi.mock('next/image', () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}))

vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (state: { username: string }) => unknown) => selector({ username: 'officer_test' }),
}))

vi.mock('@/lib/hooks/use-auth', () => ({
  useAuth: () => ({ logout: vi.fn() }),
}))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ language: 'vi-VN', setLanguage: vi.fn() }),
}))

vi.mock('@/lib/stores/theme-store', () => ({
  useTheme: () => ({ theme: 'light', setTheme: vi.fn() }),
}))

vi.mock('@/lib/stores/sidebar-store', () => ({
  useSidebarStore: () => ({ isCollapsed: false, toggleCollapse: vi.fn() }),
}))

vi.mock('@/lib/config', () => ({
  getConfig: () => Promise.resolve({ systemName: 'Pháp luật Hải Phòng' }),
}))

describe('UserNavigationSidebar', () => {
  it('uses the light shared navigation frame for officers', async () => {
    render(<UserNavigationSidebar role="officer" />)

    expect(screen.getByText('Chức năng')).toBeInTheDocument()
    expect(screen.getByText('Dành cho cán bộ')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Hỏi đáp pháp luật' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Kho tra cứu công khai' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Thủ tục hành chính' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Hồ sơ pháp lý' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cài đặt và tài khoản' })).toBeInTheDocument()
  })

  it('keeps only the citizen primary functions in the visible navigation', () => {
    render(<UserNavigationSidebar role="citizen" />)

    expect(screen.getByText('Dành cho người dân')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Hỏi đáp pháp luật' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Thủ tục hành chính' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Kho tra cứu công khai' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Hỗ trợ trực tuyến' })).toBeInTheDocument()
  })

  it('keeps support hidden from officers without reception capability', () => {
    scope.can_receive_support = false
    render(<UserNavigationSidebar role="officer" />)
    expect(screen.queryByRole('link', { name: 'Hỗ trợ trực tuyến' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Đề xuất văn bản' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'FAQ phòng ban' })).not.toBeInTheDocument()
  })

  it('shows support in primary navigation for eligible officers', () => {
    scope.can_receive_support = true
    render(<UserNavigationSidebar role="officer" />)
    expect(screen.getByRole('link', { name: 'Hỗ trợ trực tuyến' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Kho tra cứu công khai' })).toHaveAttribute('aria-current', 'page')
  })
})
