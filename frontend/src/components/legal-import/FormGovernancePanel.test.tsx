import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { FormGovernancePanel } from './FormGovernancePanel'
import { legalImportApi } from '@/lib/api/legal-import'

vi.mock('@/lib/api/legal-import', () => ({
  legalImportApi: {
    formGovernanceCases: vi.fn(),
    formGovernanceCoverage: vi.fn(),
    formProcedureCandidates: vi.fn(),
    formSourceProposalMetadata: vi.fn(),
    submitFormGovernanceCase: vi.fn(),
  },
}))

describe('FormGovernancePanel', () => {
  beforeEach(() => {
    vi.mocked(legalImportApi.formGovernanceCases).mockResolvedValue([
      {
        case_id: 'c1', officer_id: 'o1', domain: 'cu_tru_an_ninh', procedure_id: 'p1',
        title: 'Tờ khai cư trú', status: 'submitted', revision: 1, version: 1,
        current_submission: { source_url: 'https://vbpl.vn/form.pdf' },
      },
      {
        case_id: 'c2', officer_id: 'o1', domain: 'cu_tru_an_ninh', procedure_id: 'p2',
        title: 'Mẫu đã xác nhận', status: 'attested', revision: 1, version: 5,
        current_submission: { source_url: 'https://vbpl.vn/form-2.pdf', source_checksum: 'a'.repeat(64) },
      },
    ])
    vi.mocked(legalImportApi.formGovernanceCoverage).mockResolvedValue({
      procedure_total: 191, procedure_decided: 42, identity_total: 131,
      identity_decided: 31, binding_total: 229, binding_decided: 53,
      complete: false, workflow_statuses: { submitted: 1 },
    })
    vi.mocked(legalImportApi.formProcedureCandidates).mockResolvedValue({
      items: [{
        procedure_id: '1.000280', procedure_code: '1.000280',
        name: 'Công nhận trường tiểu học đạt chuẩn quốc gia',
        domain: 'an_sinh_y_te_giao_duc',
      }],
      total: 1, source: 'read_only_compatibility', read_only: true,
    })
    vi.mocked(legalImportApi.formSourceProposalMetadata).mockResolvedValue({
      steps: [
        { id: 'procedure_selected', label: 'Chọn đúng thủ tục', action: 'Xác nhận thủ tục', public_after_step: false },
        { id: 'source_verified', label: 'Xác minh nguồn chính thức', action: 'Xác minh nguồn chính thức', public_after_step: false },
        { id: 'legal_metadata_completed', label: 'Hoàn thiện dữ liệu pháp lý', action: 'Lưu dữ liệu pháp lý', public_after_step: false },
        { id: 'legal_attested', label: 'Xác nhận pháp lý', action: 'Xác nhận bản khóa checksum', public_after_step: false },
        { id: 'release_validated', label: 'Kiểm tra bản phát hành', action: 'Chạy Release Gate', public_after_step: false },
        { id: 'released', label: 'Phát hành', action: 'Phát hành cho người dân', public_after_step: true },
      ],
      source_inputs: ['official_url', 'pdf', 'docx', 'eform'],
      file_contract: {
        accepted_extensions: ['.pdf', '.docx'], accepted_mime_types: ['application/pdf'],
        checksum: 'sha256', transport: 'client_metadata_only_until_secure_upload_gate',
      },
      source_approval_changes_public_release: false,
    })
  })

  it('shows workflow-specific source actions, current step and release separation', async () => {
    render(<FormGovernancePanel />)
    await waitFor(() => expect(screen.getByText('Tờ khai cư trú')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Yêu cầu bổ sung bằng chứng nguồn' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Xác minh nguồn chính thức' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Từ chối nguồn không hợp lệ' })).toBeInTheDocument()
    expect(screen.getByText((_, node) => node?.textContent === 'Bước hiện tại: Xác minh nguồn chính thức')).toBeInTheDocument()
    expect(screen.getByText((_, node) => node?.textContent === 'Bước kế tiếp: Hoàn thiện dữ liệu pháp lý')).toBeInTheDocument()
    expect(screen.getByText(/chỉ bước Phát hành mới/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tạo bản phát hành thử/ })).toBeInTheDocument()
    expect(screen.getByText(/tạo bản thử → kiểm tra Release Gate → phát hành/)).toBeInTheDocument()
  })

  it('offers name/code/domain procedure search and URL/PDF/DOCX/e-form proposal inputs', async () => {
    render(<FormGovernancePanel />)
    await waitFor(() => expect(screen.getByText('Đề xuất nguồn biểu mẫu')).toBeInTheDocument())

    expect(screen.getByLabelText('Tên hoặc mã thủ tục')).toBeInTheDocument()
    expect(screen.getByLabelText('Lĩnh vực')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'URL chính thức' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'PDF' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'DOCX' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'E-form' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Tên hoặc mã thủ tục'), { target: { value: 'tiểu học' } })
    fireEvent.click(screen.getByRole('button', { name: 'Tìm thủ tục' }))
    await waitFor(() => expect(legalImportApi.formProcedureCandidates).toHaveBeenCalledWith({
      q: 'tiểu học', domain: undefined, limit: 20,
    }))
    expect(await screen.findByRole('option', { name: /Công nhận trường tiểu học/ })).toBeInTheDocument()
  })

  it('shows validation beside each missing proposal field instead of one generic error', async () => {
    render(<FormGovernancePanel />)
    await waitFor(() => expect(screen.getByText('Đề xuất nguồn biểu mẫu')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Gửi đề xuất để xác minh nguồn' }))

    expect(await screen.findByText('Hãy tìm và chọn một thủ tục trong danh sách kết quả.')).toBeInTheDocument()
    expect(screen.getByText('Tên biểu mẫu không được để trống.')).toBeInTheDocument()
    expect(screen.getByText('URL nguồn chính thức không được để trống.')).toBeInTheDocument()
    expect(legalImportApi.submitFormGovernanceCase).not.toHaveBeenCalled()
  })
})
