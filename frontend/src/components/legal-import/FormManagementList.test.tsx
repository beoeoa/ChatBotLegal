import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FormManagementList } from './FormManagementList'
import { legalImportApi, type FormReviewCaseV17 } from '@/lib/api/legal-import'

vi.mock('@/lib/hooks/use-settings', () => ({ useSettings: () => ({ data: {
  legal_domains: [
    { code: 'cu_tru', name: 'Cư trú', aliases: [], is_active: true, sort_order: 1 },
    { code: 'dat_dai_xay_dung', name: 'Đất đai, xây dựng', aliases: [], is_active: true, sort_order: 2 },
  ],
  organization_units: [
    { id: 'a', code: 'phong_a', name: 'Phòng A', domain_codes: ['cu_tru'], support_enabled: true, is_active: true, sort_order: 1 },
    { id: 'b', code: 'phong_b', name: 'Phòng B', domain_codes: ['dat_dai_xay_dung'], support_enabled: true, is_active: true, sort_order: 2 },
  ],
} }) }))

vi.mock('@/lib/api/legal-import', () => ({ legalImportApi: {
  formManagementCatalog: vi.fn(), formProcedureCandidates: vi.fn(),
  editFormDraft: vi.fn(), deleteFormDraft: vi.fn(), formCaseHistory: vi.fn(),
  replaceManagedForm: vi.fn(), formAssetHistory: vi.fn(),
} }))

const draft: FormReviewCaseV17 = { case_id: 'c1', officer_id: 'admin', domain: 'cu_tru', procedure_id: 'p1', title: 'Mẫu cư trú nháp', status: 'submitted', revision: 1, version: 4, current_submission: { source_url: 'https://vbpl.vn/old.pdf', asset_kind: 'file' } }
const onDone = vi.fn().mockResolvedValue(undefined)
const show = () => render(<FormManagementList cases={[draft]} onDone={onDone} renderCase={item => <p>Chi tiết {item.title}</p>} />)

describe('FormManagementList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(legalImportApi.formProcedureCandidates).mockResolvedValue({ items: [
      { procedure_id: 'p1', procedure_code: 'p1', name: 'Đăng ký cư trú', domain: 'cu_tru', primary_organization_unit_id: 'a', primary_organization_unit_name: 'Phòng A' },
      { procedure_id: 'p2', procedure_code: 'p2', name: 'Thủ tục đất đai', domain: 'dat_dai_xay_dung', primary_organization_unit_id: 'b', primary_organization_unit_name: 'Phòng B' },
    ], total: 2, source: 'active_release', read_only: true })
    vi.mocked(legalImportApi.formManagementCatalog).mockResolvedValue({ procedures: [], assets: [{ form_id: 'f2', canonical_name: 'Mẫu đất đai', asset_kind: 'file', source_url: 'https://vbpl.vn/f2.pdf', source_checksum: 'a'.repeat(64), audiences: ['citizen'] }], bindings: [{ procedure_id: 'p2', form_id: 'f2', audience: 'citizen' }], aliases: [] })
    vi.mocked(legalImportApi.editFormDraft).mockResolvedValue(draft)
    vi.mocked(legalImportApi.deleteFormDraft).mockResolvedValue({ ...draft, status: 'withdrawn' })
  })
  it('filters department then domain then procedure and resets dependent choices', async () => {
    show()
    await screen.findByText('Mẫu đất đai')
    fireEvent.change(screen.getByLabelText('Phòng ban'), { target: { value: 'a' } })
    expect(screen.queryByText('Mẫu đất đai')).not.toBeInTheDocument()
    expect(screen.getByText('Mẫu cư trú nháp')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Lĩnh vực'), { target: { value: 'cu_tru' } })
    fireEvent.change(screen.getByLabelText('Thủ tục'), { target: { value: 'p1' } })
    fireEvent.change(screen.getByLabelText('Phòng ban'), { target: { value: 'b' } })
    expect(screen.getByLabelText('Lĩnh vực')).toHaveValue('')
    expect(screen.getByLabelText('Thủ tục')).toHaveValue('')
    expect(screen.getByText('Mẫu đất đai')).toBeInTheDocument()
  })
  it('edits with version and resets checksum for source verification', async () => {
    show(); await screen.findByText('Mẫu đất đai')
    fireEvent.click(screen.getByText('Mẫu cư trú nháp'))
    fireEvent.click(screen.getByRole('button', { name: 'Sửa thông tin' }))
    fireEvent.change(screen.getByLabelText('Tên biểu mẫu'), { target: { value: 'Mẫu đã sửa' } })
    fireEvent.click(screen.getByRole('button', { name: 'Lưu và kiểm tra lại' }))
    await waitFor(() => expect(legalImportApi.editFormDraft).toHaveBeenCalledWith(draft, expect.objectContaining({ title: 'Mẫu đã sửa', source_checksum: null, procedure_id: 'p1' })))
  })
  it('requires a reason before deleting a draft and retains confirmation', async () => {
    show(); await screen.findByText('Mẫu đất đai'); fireEvent.click(screen.getByText('Mẫu cư trú nháp'))
    fireEvent.click(screen.getByRole('button', { name: 'Xóa bản nháp' }))
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('lý do')
    expect(legalImportApi.deleteFormDraft).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Lý do'), { target: { value: 'Nhập trùng mẫu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận' }))
    await waitFor(() => expect(legalImportApi.deleteFormDraft).toHaveBeenCalledWith(draft, 'Nhập trùng mẫu'))
  })
})
