import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { CatalogFormPicker } from './CatalogFormPicker'
import { legalImportApi } from '@/lib/api/legal-import'

vi.mock('@/lib/api/legal-import', () => ({ legalImportApi: { formManagementCatalog: vi.fn() } }))

it('selects a catalog identity with its procedure instead of accepting a copied URL', async () => {
  vi.mocked(legalImportApi.formManagementCatalog).mockResolvedValue({ release_id: 'r1', procedures: [{ procedure_id: 'p1', procedure_code: 'p1', name: 'Đăng ký khai sinh', domain: 'ho_tich' }], assets: [{ form_id: 'f1', canonical_name: 'Tờ khai khai sinh', source_url: 'https://vbpl.vn/form.pdf', source_checksum: 'a'.repeat(64), asset_kind: 'file', audiences: ['citizen'] }], bindings: [{ procedure_id: 'p1', form_id: 'f1', audience: 'citizen' }], aliases: [] })
  const change = vi.fn()
  render(<CatalogFormPicker values={[]} onChange={change} />)
  fireEvent.click(screen.getByRole('button', { name: 'Chọn biểu mẫu có sẵn' }))
  await screen.findByRole('option', { name: 'Đăng ký khai sinh' })
  fireEvent.change(screen.getByLabelText('Thủ tục trong kho biểu mẫu'), { target: { value: 'p1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Chọn mẫu' }))
  await waitFor(() => expect(change).toHaveBeenCalledWith([expect.objectContaining({ form_id: 'f1', catalog_procedure_id: 'p1' })]))
  expect(screen.queryByLabelText('Đường dẫn tải')).not.toBeInTheDocument()
})
