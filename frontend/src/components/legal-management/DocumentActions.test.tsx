import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ManagedLegalDocument } from '@/lib/api/legal-management'

const mocks = vi.hoisted(() => ({ push: vi.fn(), complete: vi.fn(), replaceDocument: vi.fn(), replacementWorkflowByKey: vi.fn(), setOrganizationAssignment: vi.fn(), setSearchState: vi.fn(), previewHardDelete: vi.fn(), hardDelete: vi.fn(), extractFile: vi.fn(), extractionJob: vi.fn(), crawlPreview: vi.fn() }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('@/lib/api/legal-management', () => ({ legalManagementApi: mocks }))
vi.mock('@/lib/api/legal-import', () => ({ legalImportApi: mocks }))
import { DocumentActions } from './DocumentActions'

const document: ManagedLegalDocument = { doc_id: 42, document_title: 'Văn bản kiểm thử', law_number: 'TEST', document_type: 'Quyết định', issuing_agency: 'UBND thành phố Hải Phòng', issued_date: '2026-09-01', effective_date: '2026-09-10', article_count: 2, chunk_count: 4, quality_flags: [], state_revision: 'a'.repeat(64), organization_assignment_fingerprint: 'b'.repeat(64), organization_unit_ids: [] }
const reason = 'Kiểm thử thao tác quản trị'
function setup(overrides: Partial<ManagedLegalDocument> = {}) { render(<DocumentActions document={{ ...document, ...overrides }} units={[{ id: 'one', name: 'Phòng Một' }]} onComplete={mocks.complete} />) }
function confirm() { fireEvent.change(screen.getByLabelText('Lý do thao tác'), { target: { value: reason } }); fireEvent.click(screen.getByRole('button', { name: /^(Xác nhận|Bắt đầu thay thế)$/ })) }
beforeEach(() => {
  vi.resetAllMocks()
  mocks.complete.mockResolvedValue(undefined)
  mocks.setSearchState.mockResolvedValue({ message: 'Đã cập nhật' })
  mocks.setOrganizationAssignment.mockResolvedValue({ projection_updated: true })
  mocks.replaceDocument.mockResolvedValue({ document_id: 43 })
  mocks.replacementWorkflowByKey.mockRejectedValue(new Error('not created yet'))
  mocks.crawlPreview.mockResolvedValue({
    title: 'Quyết định thay thế', law_number: '10/2026/QĐ-UBND', document_type: 'Quyết định',
    issuing_agency: 'UBND thành phố Hải Phòng', issued_date: '2026-09-02', effective_date: '2026-09-12',
    scope: 'haiphong', content: 'Nội dung văn bản thay thế '.repeat(20),
  })
  mocks.previewHardDelete.mockResolvedValue({ eligible: true, state_revision: 'c'.repeat(64), confirmation_text: 'DELETE TEST', database: { articles: 2, chunks: 4 } })
  mocks.hardDelete.mockResolvedValue({ status: 'deleted' })
})
describe('restored document actions', () => {
  it.each([['Đưa vào tra cứu lịch sử', 'historical'], ['Loại khỏi mọi tìm kiếm', 'exclude']])('submits %s with the current revision', async (label, action) => {
    setup(); fireEvent.click(screen.getByRole('button', { name: label })); confirm()
    await waitFor(() => expect(mocks.setSearchState).toHaveBeenCalledWith(42, { action, reason, expected_revision: 'a'.repeat(64) }))
    await waitFor(() => expect(mocks.complete).toHaveBeenCalled())
  })
  it('restores an excluded document with the current revision', async () => {
    setup({ search_included: false, historical_lookup_allowed: false, serving_state: 'excluded' })
    fireEvent.click(screen.getByRole('button', { name: 'Khôi phục vào tra cứu' })); confirm()
    await waitFor(() => expect(mocks.setSearchState).toHaveBeenCalledWith(42, { action: 'restore', reason, expected_revision: 'a'.repeat(64) }))
  })
  it('does not allow an already excluded document to be excluded again', () => {
    setup({ search_included: false, historical_lookup_allowed: false, serving_state: 'excluded' })
    expect(screen.getByRole('button', { name: 'Đã loại khỏi mọi tìm kiếm' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Loại khỏi mọi tìm kiếm' })).not.toBeInTheDocument()
  })
  it('locks a state change immediately so a double click sends one request', async () => {
    let release!: (value: { message: string }) => void
    mocks.setSearchState.mockImplementation(() => new Promise(resolve => { release = resolve }))
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Loại khỏi mọi tìm kiếm' }))
    fireEvent.change(screen.getByLabelText('Lý do thao tác'), { target: { value: reason } })
    const submit = screen.getByRole('button', { name: 'Xác nhận' })
    fireEvent.click(submit); fireEvent.click(submit)
    expect(mocks.setSearchState).toHaveBeenCalledTimes(1)
    release({ message: 'Đã cập nhật' })
    await waitFor(() => expect(mocks.complete).toHaveBeenCalledTimes(1))
  })
  it('saves primary department and fingerprint', async () => {
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Phân công phòng ban' }))
    fireEvent.click(screen.getByLabelText('Phòng Một'))
    fireEvent.change(screen.getByLabelText('Phòng ban chủ trì'), { target: { value: 'one' } }); confirm()
    await waitFor(() => expect(mocks.setOrganizationAssignment).toHaveBeenCalledWith(42, { assignment_state: 'assigned', organization_unit_ids: ['one'], primary_organization_unit_id: 'one', expected_fingerprint: 'b'.repeat(64), reason }))
  })
  it('replaces from official URL and opens new document', async () => {
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Thay thế văn bản' }))
    fireEvent.change(screen.getByLabelText('URL nguồn chính thức'), { target: { value: 'https://vbpl.vn/example' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lấy nội dung từ link' }))
    expect(await screen.findByText(/Đã lấy .* ký tự/)).toBeInTheDocument()
    confirm()
    await waitFor(() => expect(mocks.replaceDocument).toHaveBeenCalledWith(42, expect.objectContaining({ source_url: 'https://vbpl.vn/example', reason, law_number: '10/2026/QĐ-UBND' }), expect.objectContaining({ idempotencyKey: expect.any(String) })))
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/legal-management/43'))
  })
  it('extracts uploaded replacement before submitting content', async () => {
    mocks.extractFile.mockResolvedValue({ filename: 'test.pdf', content: 'Nội dung '.repeat(30), complete: true })
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Thay thế văn bản' }))
    const file = new File(['test'], 'test.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText('Tệp văn bản thay thế'), { target: { files: [file] } })
    expect(await screen.findByText(/Đã đọc .* ký tự/)).toBeInTheDocument()
    confirm()
    await waitFor(() => expect(mocks.replaceDocument).toHaveBeenCalledWith(42, expect.objectContaining({ uploaded_filename: 'test.pdf', uploaded_content: 'Nội dung '.repeat(30) }), expect.objectContaining({ idempotencyKey: expect.any(String) })))
  })
  it('requires preview and exact confirmation before deleting', async () => {
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Xóa vĩnh viễn…' }))
    const input = await screen.findByLabelText('Nhập chính xác: DELETE TEST')
    confirm(); expect(mocks.hardDelete).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: 'DELETE TEST' } }); fireEvent.click(screen.getByRole('button', { name: 'Xác nhận' }))
    await waitFor(() => expect(mocks.hardDelete).toHaveBeenCalledWith(42, { reason, expected_revision: 'c'.repeat(64), confirmation_text: 'DELETE TEST' }))
    expect(mocks.push).toHaveBeenCalledWith('/legal-management')
  })
  it('cannot delete a blocked document', async () => {
    mocks.previewHardDelete.mockResolvedValue({ eligible: false, reason_code: 'immutable_release_document_cannot_be_hard_deleted' })
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Xóa vĩnh viễn…' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('không thể xóa vĩnh viễn trực tiếp')
    expect(screen.queryByText('immutable_release_document_cannot_be_hard_deleted')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Lý do thao tác')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Xác nhận' })).not.toBeInTheDocument()
    expect(mocks.hardDelete).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Thay thế văn bản' }))
    expect(screen.getByLabelText('URL nguồn chính thức')).toBeInTheDocument()
  })
  it('keeps errors visible and does not report successful completion', async () => {
    mocks.setSearchState.mockRejectedValue(new Error('Không thể lưu trạng thái.'))
    setup(); fireEvent.click(screen.getByRole('button', { name: 'Loại khỏi mọi tìm kiếm' })); confirm()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(mocks.complete).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Xác nhận' })).toBeEnabled()
  })
})
