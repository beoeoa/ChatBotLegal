import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock('@/components/legal-import/FormGovernancePanel', () => ({
  FormGovernancePanel: () => <div>Luồng quản trị biểu mẫu chuẩn</div>,
}))

import ProcedureManagementPage from './page'

describe('ProcedureManagementPage', () => {
  it('owns form governance separately from legal-document intake', () => {
    render(<ProcedureManagementPage />)
    expect(screen.getByRole('heading', { name: 'Thủ tục và biểu mẫu' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Thủ tục và biểu mẫu' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Kho thủ tục công khai/ })).toHaveAttribute('href', '/procedures')
    expect(screen.getByRole('link', { name: /Quản lý thủ tục hành chính/ })).toHaveAttribute('href', '/faq-management')
  })
})
