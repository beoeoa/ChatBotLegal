import apiClient from '@/lib/api/client'

export type AdminActivityItem = {
  id: string
  occurred_at: string
  actor: string
  actor_label?: string
  actor_role: string
  activity_type?: string
  module: string
  result: 'success' | 'failed'
  action: string
  action_label?: string
  resource_type: string
  resource_label?: string
  resource_id: string
  sensitive_detail_available: boolean
}

export type AdminActivityFilters = {
  role?: string
  module?: string
  result?: string
  actor?: string
  search?: string
  activity_type?: string
  date_from?: string
  date_to?: string
  cursor?: string
  limit?: number
}

export type AdminActivityPage = {
  items: AdminActivityItem[]
  total: number
  next_cursor: string | null
  content_policy: 'metadata_only'
}

export type AdminActivityExport = {
  blob: Blob
  filename: string
}

function clean(filters: AdminActivityFilters): Record<string, string | number> {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined && value !== '')) as Record<string, string | number>
}

function exportFilters(filters: AdminActivityFilters): Record<string, string | number> {
  const supported = { ...filters }
  delete supported.cursor
  delete supported.limit
  return clean(supported)
}

function exportFilename(contentDisposition: unknown): string {
  if (typeof contentDisposition !== 'string') return 'nhat-ky-quan-tri.csv'
  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try { return decodeURIComponent(encoded) } catch { return encoded }
  }
  return contentDisposition.match(/filename="?([^";]+)"?/i)?.[1] || 'nhat-ky-quan-tri.csv'
}

export const adminActivityApi = {
  list: async (filters: AdminActivityFilters = {}): Promise<AdminActivityPage> => {
    const response = await apiClient.get<AdminActivityPage>('/admin/activity', { params: clean(filters) })
    return response.data
  },

  sensitiveView: async (eventId: string, reason: string): Promise<Record<string, unknown>> => {
    const response = await apiClient.post(`/admin/activity/${encodeURIComponent(eventId)}/sensitive-view`, { reason })
    return response.data
  },

  exportCsv: async (filters: AdminActivityFilters = {}): Promise<AdminActivityExport> => {
    const response = await apiClient.get<Blob>('/admin/activity/export', {
      params: { format: 'csv', ...exportFilters(filters) },
      responseType: 'blob',
    })
    return {
      blob: response.data,
      filename: exportFilename(response.headers['content-disposition']),
    }
  },
}
