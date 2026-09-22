import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LegalDocumentViewer } from './LegalDocumentViewer'
import { apiClient } from '@/lib/api/client'

vi.mock('@/lib/api/client', () => ({ apiClient: { get: vi.fn(async () => ({ data: {
  doc_id: 'dvc:1.001193', document_title: 'Đăng ký khai sinh', articles: [],
  content: 'Thành phần hồ sơ đã lưu trong bản công bố thủ tục.', source_file_available: false,
} })) } }))
vi.mock('@/components/legal-management/DocumentLifecyclePanel', () => ({ DocumentLifecyclePanel: () => null }))

describe('locally stored publication viewer', () => {
  it('loads full text inside the same viewer instead of navigating away', async () => {
    render(<LegalDocumentViewer docId="42" articleParam="16" embedded />)
    fireEvent.click(await screen.findByRole('button', { name: 'Xem toàn văn' }))
    await waitFor(() => expect(apiClient.get).toHaveBeenLastCalledWith('/legal/docs/42?include_content=true'))
    expect(screen.queryByRole('link', { name: 'Xem toàn văn' })).not.toBeInTheDocument()
  })
  it('renders full text when publication has no numbered articles', async () => {
    render(<LegalDocumentViewer docId="dvc:1.001193" />)
    expect(await screen.findByText('Thành phần hồ sơ đã lưu trong bản công bố thủ tục.')).toBeInTheDocument()
    expect(screen.queryByText('Không có chi tiết các điều khoản trích xuất cho văn bản này.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tải PDF/ })).toBeDisabled()
  })
})
