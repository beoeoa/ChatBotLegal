import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api/client', () => ({ default: { get }, apiClient: { get } }))

import { adminActivityApi } from './admin-activity'

describe('adminActivityApi', () => {
  beforeEach(() => get.mockReset())

  it('downloads CSV through the authenticated API client with active filters', async () => {
    const blob = new Blob(['Thời điểm,Người thực hiện'])
    get.mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="activity.csv"' },
    })

    await expect(adminActivityApi.exportCsv({
      activity_type: 'data_ingestion', result: 'success', date_from: '2026-08-10', limit: 50,
    })).resolves.toEqual({ blob, filename: 'activity.csv' })

    expect(get).toHaveBeenCalledWith('/admin/activity/export', {
      params: {
        format: 'csv', activity_type: 'data_ingestion', result: 'success', date_from: '2026-08-10',
      },
      responseType: 'blob',
    })
  })
})
