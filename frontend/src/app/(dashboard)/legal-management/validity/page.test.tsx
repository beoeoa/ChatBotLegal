import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock('@/components/legal-import/LegalValiditySyncPanel', () => ({
  LegalValiditySyncPanel: () => <div>Giao diện kiểm tra hiệu lực</div>,
}))

import LegalValidityManagementPage from './page'

describe('LegalValidityManagementPage', () => {
  it('places validity operations inside the legal management area', () => {
    render(<LegalValidityManagementPage />)

    expect(screen.getByRole('heading', { name: 'Văn bản cần kiểm tra hiệu lực' })).toBeInTheDocument()
    expect(screen.getByText('Giao diện kiểm tra hiệu lực')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Quay lại kho văn bản' })).toHaveAttribute('href', '/legal-management')
  })
})
