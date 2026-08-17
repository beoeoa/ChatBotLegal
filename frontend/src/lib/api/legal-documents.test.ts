import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('./client', () => ({ apiClient: { get } }))

import { legalDocumentsApi } from './legal-documents'

describe('legalDocumentsApi', () => {
  beforeEach(() => get.mockReset())

  it('forwards pagination, date filters and an abort signal', async () => {
    const signal = new AbortController().signal
    get.mockResolvedValue({ data: { items: [], total: 0, limit: 20, offset: 20 } })

    await legalDocumentsApi.list({
      limit: 20,
      offset: 20,
      effective_from: '2026-01-01',
      effective_to: '2026-08-16',
    }, signal)

    expect(get).toHaveBeenCalledWith('/legal/documents', {
      params: {
        limit: 20,
        offset: 20,
        effective_from: '2026-01-01',
        effective_to: '2026-08-16',
      },
      signal,
    })
  })

  it('downloads xlsx and reads its server-provided filename', async () => {
    const blob = new Blob(['xlsx'])
    get.mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="danh-sach-van-ban-20260816.xlsx"' },
    })

    await expect(legalDocumentsApi.exportXlsx({ tier: 'core' })).resolves.toEqual({
      blob,
      filename: 'danh-sach-van-ban-20260816.xlsx',
    })
    expect(get).toHaveBeenCalledWith('/legal/documents/export.xlsx', {
      params: { tier: 'core' },
      responseType: 'blob',
    })
  })
})
