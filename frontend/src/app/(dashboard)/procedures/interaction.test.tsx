import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ProcedureCatalogPage } from './page'

vi.mock('@/components/layout/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
vi.mock('@/lib/api/client', () => ({ apiClient: { get: vi.fn(async (url: string) => {
  if (url === '/procedures/forms-catalog/public-catalog?audience=citizen') return { data: {
    release_id: 'release-test', version: 2, legal_as_of: '2026-09-15', total: 1,
    items: [{
      procedure_id: 'test-procedure', name: 'Thủ tục thử nghiệm', domain: 'cu_tru_an_ninh',
      form_status: 'source_gap', forms_unavailable: true, forms: [],
    }],
  } }
  if (url === '/procedures/directory') return { data: { departments: [{
    id: 'office', name: 'Văn phòng thử nghiệm',
    fields: [
      { code: 'cu_tru_an_ninh', name: 'cu_tru_an_ninh' },
      { code: 'quoc_phong_quan_su', name: 'quoc_phong_quan_su' },
    ],
  }] } }
  if (url === '/procedures') return { data: [{
    id: 'test-procedure', name: 'Thủ tục thử nghiệm', domain_slug: 'cu_tru_an_ninh',
    steps: ['Bước thử nghiệm'], documents_required: [], forms: [],
  }] }
  return { data: { items: [] } }
}) } }))

afterEach(cleanup)

it('handles the real directory shape without domains and opens a procedure', async () => {
  render(<ProcedureCatalogPage />)
  fireEvent.click(await screen.findByText('Văn phòng thử nghiệm'))
  expect(await screen.findByText('Thủ tục thử nghiệm')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Quốc phòng - Nghĩa vụ quân sự' }))
  expect(screen.getByText('Không tìm thấy thủ tục phù hợp.')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Cư trú - Căn cước - ANTT' }))
  fireEvent.click(screen.getByRole('button', { name: 'Thủ tục thử nghiệm' }))
  await waitFor(() => expect(screen.getByText('Bước thử nghiệm')).toBeVisible())
})
