import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api/client', () => ({ default: { get }, apiClient: { get } }))

import { adminActivityApi } from './admin-activity'

describe('adminActivityApi', () => {
  beforeEach(() => get.mockReset())

  it('downloads Excel through the authenticated API client with active filters', async () => {
    const blob = new Blob(['xlsx'])
    get.mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': "attachment; filename*=UTF-8''nhat-ky-quan-tri.xlsx" },
    })

    await expect(adminActivityApi.exportXlsx({
      activity_type: 'data_ingestion', result: 'success', date_from: '2026-08-10', limit: 50,
    })).resolves.toEqual({ blob, filename: 'nhat-ky-quan-tri.xlsx' })

    expect(get).toHaveBeenCalledWith('/admin/activity/export', {
      params: {
        format: 'xlsx', activity_type: 'data_ingestion', result: 'success', date_from: '2026-08-10',
      },
      responseType: 'blob',
    })
  })
})
