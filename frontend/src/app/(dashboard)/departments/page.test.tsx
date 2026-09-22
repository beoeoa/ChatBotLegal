import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/lib/api/client', () => ({ apiClient: api }))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import Page from './page'

const unit = {
  id: 'custom',
  code: 'custom',
  name: 'Phòng địa phương',
  short_name: null,
  aliases: [],
  parent_id: null,
  domain_codes: ['ho_tich_chung_thuc', 'dat_dai_xay_dung'],
  domain_assignments: [
    { domain_code: 'ho_tich_chung_thuc', responsibility: 'primary' },
    { domain_code: 'dat_dai_xay_dung', responsibility: 'support' },
  ],
  is_active: true,
  support_enabled: true,
  sort_order: 0,
}

const seededDomains = [
  {
    code: 'ho_tich_chung_thuc',
    name: 'Tư pháp - Hộ tịch & Chứng thực',
    aliases: [],
    is_active: true,
    sort_order: 1,
  },
  {
    code: 'dat_dai_xay_dung',
    name: 'Đất đai - Xây dựng - Môi trường',
    aliases: [],
    is_active: true,
    sort_order: 2,
  },
]

describe('Dynamic department management', () => {
  let units: Array<typeof unit>
  let domains: typeof seededDomains

  beforeEach(() => {
    vi.clearAllMocks()
    units = [{ ...unit, domain_codes: [...unit.domain_codes], domain_assignments: [...unit.domain_assignments] }]
    domains = seededDomains.map((domain) => ({ ...domain }))

    api.get.mockImplementation(async (path: string) => ({
      data:
        path === '/settings/legal-domains'
          ? domains.map((domain) => ({ ...domain }))
          : units.map((item) => ({ ...item })),
    }))
    api.post.mockImplementation(async (path: string, data) => {
      if (path === '/settings/legal-domains') domains.push({ ...data })
      else units.push({ ...data })
      return { data }
    })
    api.put.mockImplementation(async (path: string, data) => {
      if (path.startsWith('/settings/legal-domains/')) {
        domains = domains.map((domain) => (domain.code === data.code ? { ...data } : domain))
      } else {
        units = units.map((item) => (item.id === data.id ? { ...data } : item))
      }
      return { data }
    })
    api.delete.mockImplementation(async (path: string) => {
      const id = decodeURIComponent(path.split('/').at(-1) ?? '')
      if (path.startsWith('/settings/legal-domains/')) {
        const current = domains.find((domain) => domain.code === id)!
        const data = { ...current, is_active: false }
        domains = domains.map((domain) => (domain.code === id ? data : domain))
        return { data }
      }
      const current = units.find((item) => item.id === id)!
      const data = { ...current, is_active: false, support_enabled: false }
      units = units.map((item) => (item.id === id ? data : item))
      return { data }
    })
  })

  it('creates using human fields with generated identity and explicit supporting assignment', async () => {
    render(<Page />)
    await screen.findByText(unit.name)
    fireEvent.click(screen.getByRole('button', { name: 'Thêm phòng ban' }))
    fireEvent.change(screen.getByLabelText('Tên phòng ban *'), {
      target: { value: 'Phòng Kinh tế xã mới' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /Tư pháp - Hộ tịch/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Tạo phòng ban' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/settings/organization-units',
        expect.objectContaining({
          name: 'Phòng Kinh tế xã mới',
          id: expect.stringMatching(/^pb_/),
          code: expect.stringMatching(/^pb_/),
          domain_assignments: [
            { domain_code: 'ho_tich_chung_thuc', responsibility: 'support' },
          ],
        }),
      ),
    )
    await screen.findByText('Phòng Kinh tế xã mới')
  })

  it('persists a newly added domain and reuses it after reloading the page data', async () => {
    render(<Page />)
    await screen.findByText(unit.name)
    fireEvent.click(screen.getByRole('button', { name: 'Thêm phòng ban' }))
    fireEvent.click(screen.getByRole('button', { name: 'Thêm lĩnh vực' }))
    fireEvent.change(screen.getByLabelText('Tên lĩnh vực *'), {
      target: { value: 'Chuyển đổi số' },
    })
    expect(screen.getByLabelText('Mã ổn định *')).toHaveValue('chuyen_doi_so')
    fireEvent.click(screen.getByRole('button', { name: 'Lưu lĩnh vực' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/settings/legal-domains',
        expect.objectContaining({ code: 'chuyen_doi_so', name: 'Chuyển đổi số' }),
      ),
    )
    expect(screen.getByRole('checkbox', { name: /Chuyển đổi số/ })).toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(4))
    fireEvent.click(screen.getByRole('button', { name: 'Thêm phòng ban' }))
    expect(await screen.findByRole('checkbox', { name: /Chuyển đổi số/ })).toBeInTheDocument()
  })

  it('renames a domain everywhere while retaining its stable linked code', async () => {
    render(<Page />)
    await screen.findByText(unit.name)
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Sửa lĩnh vực Tư pháp - Hộ tịch & Chứng thực',
      }),
    )
    fireEvent.change(screen.getByLabelText('Tên lĩnh vực *'), {
      target: { value: 'Tư pháp và Hộ tịch số' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Lưu lĩnh vực' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/settings/legal-domains/ho_tich_chung_thuc',
        expect.objectContaining({
          code: 'ho_tich_chung_thuc',
          name: 'Tư pháp và Hộ tịch số',
        }),
      ),
    )
    expect(await screen.findByText('Tư pháp và Hộ tịch số')).toBeVisible()
    expect(
      within(screen.getByRole('article', { name: unit.name })).getByText(
        /Tư pháp và Hộ tịch số/,
      ),
    ).toBeVisible()
  })

  it('retires an unused domain without deleting history and restores it', async () => {
    domains.push({
      code: 'chuyen_doi_so',
      name: 'Chuyển đổi số',
      aliases: [],
      is_active: true,
      sort_order: 3,
    })
    render(<Page />)
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Xóa lĩnh vực Chuyển đổi số khỏi sử dụng',
      }),
    )
    expect(api.delete).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận xóa khỏi sử dụng' }))

    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith('/settings/legal-domains/chuyen_doi_so'),
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Khôi phục' }))
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/settings/legal-domains/chuyen_doi_so',
        expect.objectContaining({ code: 'chuyen_doi_so', is_active: true }),
      ),
    )
  })

  it('removes stale assignments when deselecting a field', async () => {
    render(<Page />)
    fireEvent.click(
      await screen.findByRole('button', { name: `Sửa phòng ban ${unit.name}` }),
    )
    fireEvent.click(screen.getByRole('checkbox', { name: /Đất đai - Xây dựng/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1))
    expect(api.put.mock.calls[0][1].domain_codes).toEqual(['ho_tich_chung_thuc'])
    expect(api.put.mock.calls[0][1].domain_assignments).toEqual([
      { domain_code: 'ho_tich_chung_thuc', responsibility: 'primary' },
    ])
  })

  it('confirms reversible retirement and supports restoration', async () => {
    render(<Page />)
    fireEvent.click(
      await screen.findByRole('button', {
        name: `Ngừng hoạt động phòng ban ${unit.name}`,
      }),
    )
    expect(api.delete).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận ngừng hoạt động' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Khôi phục' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/settings/organization-units/custom',
        expect.objectContaining({ is_active: true }),
      ),
    )
  })

  it('retains input and displays the backend error', async () => {
    api.put.mockRejectedValue({
      response: { data: { detail: 'Lĩnh vực phải thuộc danh mục phường/xã.' } },
    })
    render(<Page />)
    fireEvent.click(
      await screen.findByRole('button', { name: `Sửa phòng ban ${unit.name}` }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Lĩnh vực phải thuộc danh mục')
    expect(within(screen.getByRole('dialog')).getByLabelText('Tên phòng ban *')).toHaveValue(
      unit.name,
    )
  })
})
