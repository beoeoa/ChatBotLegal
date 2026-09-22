import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  capabilities: vi.fn(), list: vi.fn(), detail: vi.fn(), create: vi.fn(),
  update: vi.fn(), transition: vi.fn(), review: vi.fn(), activate: vi.fn(), activationPreview: vi.fn(),
}))

vi.mock('@/lib/api/legal-lifecycle', () => ({
  legalLifecycleApi: api,
  lifecycleIdempotencyKey: (action: string) => `${action}:test-idempotency`,
}))
vi.mock('@/lib/hooks/use-settings', () => ({ useSettings: () => ({ data: { organization_units: [] } }) }))
vi.mock('@/lib/api/legal-import', () => ({ legalImportApi: { fields: async () => [] } }))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import LegalLifecycleDraftPage from './page'

const submitted = {
  id: 'legal_document_draft:draft001', state: 'submitted', revision: 3,
  title: 'Quyết định thử nghiệm', law_number: '01/2026/QĐ-TEST',
  document_type: 'Quyết định', issuing_agency: 'UBND Hải Phòng',
  source_url: 'https://vbpl.vn/example', content: 'Điều 1. Nội dung thử nghiệm.',
  submitted_by: 'user:submitter', created_by: 'user:submitter',
}

describe('LegalLifecycleDraftPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.list.mockResolvedValue({ items: [], limit: 50, offset: 0 })
  })

  it('shows the closed write gate and does not fetch protected drafts for an unmapped Admin', async () => {
    api.capabilities.mockResolvedValue({
      actor_id: 'user:admin', authenticated_admin: true, writes_enabled: false,
      editor: false, reviewer: false, activation_enabled: false,
    })
    render(<LegalLifecycleDraftPage />)
    expect(await screen.findByText('Chức năng biên tập đang tạm khóa')).toBeInTheDocument()
    expect(screen.getByText('Tài khoản chưa được phân quyền')).toBeInTheDocument()
    expect(screen.queryByText(/Phase 2|workflow|migration|editor\/reviewer/i)).not.toBeInTheDocument()
    expect(api.list).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /kích hoạt/i })).not.toBeInTheDocument()
  })

  it('lets a configured editor create a staging draft with a reason', async () => {
    api.capabilities.mockResolvedValue({
      actor_id: 'user:editor', authenticated_admin: true, writes_enabled: true,
      editor: true, reviewer: false, activation_enabled: false,
    })
    api.create.mockResolvedValue({ id: 'legal_document_draft:new001', state: 'draft', revision: 1, title: 'Văn bản thử nghiệm' })
    render(<LegalLifecycleDraftPage />)
    await screen.findByRole('heading', { name: 'Bản nháp và phiên bản' })
    fireEvent.change(screen.getByLabelText('Tiêu đề bản nháp'), { target: { value: 'Văn bản thử nghiệm' } })
    fireEvent.change(screen.getByLabelText('Số ký hiệu bản nháp'), { target: { value: '01/2026/QĐ-TEST' } })
    fireEvent.change(screen.getByLabelText('URL nguồn chính thức'), { target: { value: 'https://vbpl.vn/example' } })
    fireEvent.change(screen.getByLabelText('Nội dung bản nháp'), { target: { value: 'Điều 1. Nội dung thử nghiệm.' } })
    fireEvent.change(screen.getByLabelText('Lý do thay đổi'), { target: { value: 'Tạo bản nháp thử nghiệm để rà soát.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lưu bản nháp' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Văn bản thử nghiệm', reason: 'Tạo bản nháp thử nghiệm để rà soát.' }),
      'create:test-idempotency',
    ))
    expect(await screen.findByText('Đã tạo bản nháp trong khu vực chuẩn bị.')).toBeInTheDocument()
  })

  it('disables same-actor approval in the UI', async () => {
    api.capabilities.mockResolvedValue({
      actor_id: 'user:submitter', authenticated_admin: true, writes_enabled: true,
      editor: true, reviewer: true, activation_enabled: false,
    })
    api.list.mockResolvedValue({ items: [submitted], limit: 50, offset: 0 })
    api.detail.mockResolvedValue(submitted)
    render(<LegalLifecycleDraftPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Quyết định thử nghiệm/ }))
    await screen.findByText('Kiểm tra và quyết định')
    fireEvent.change(screen.getByLabelText('Lý do kiểm tra hoặc quyết định'), { target: { value: 'Không được tự duyệt hồ sơ của mình.' } })
    expect(screen.getByRole('button', { name: 'Phê duyệt' })).toBeDisabled()
    expect(api.review).not.toHaveBeenCalled()
  })

  it('allows a distinct reviewer decision but never renders live activation', async () => {
    api.capabilities.mockResolvedValue({
      actor_id: 'user:reviewer', authenticated_admin: true, writes_enabled: true,
      editor: false, reviewer: true, activation_enabled: false,
    })
    api.list.mockResolvedValue({ items: [submitted], limit: 50, offset: 0 })
    api.detail.mockResolvedValue(submitted)
    api.review.mockResolvedValue({ ...submitted, state: 'approved', revision: 4 })
    render(<LegalLifecycleDraftPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Quyết định thử nghiệm/ }))
    await screen.findByText('Kiểm tra và quyết định')
    fireEvent.change(screen.getByLabelText('Lý do kiểm tra hoặc quyết định'), { target: { value: 'Đã đối chiếu đầy đủ nguồn thử nghiệm.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Phê duyệt' }))
    await waitFor(() => expect(api.review).toHaveBeenCalledWith(
      submitted.id, 'approved', 'Đã đối chiếu đầy đủ nguồn thử nghiệm.', 3,
      'review-approved:test-idempotency',
    ))
    expect(screen.queryByRole('button', { name: /kích hoạt/i })).not.toBeInTheDocument()
  })
})
