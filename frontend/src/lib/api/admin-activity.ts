import apiClient from '@/lib/api/client'

export type AdminActivityItem = {
  id: string
  occurred_at: string
  actor: string
  actor_label?: string
  actor_role: string
  actor_role_label?: string | null
  actor_username?: string | null
  actor_department?: string | null
  actor_job_title?: string | null
  activity_type?: string
  module: string
  result: 'success' | 'failed'
  workflow_status?: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'blocked' | 'cancelled' | 'retrying'
  workflow_status_label?: string
  severity?: 'info' | 'warning' | 'critical'
  severity_label?: string
  action: string
  action_label?: string
  resource_type: string
  resource_label?: string
  resource_id: string
  explanation?: string
  impact?: string
  next_action?: string
  sensitive_detail_available: boolean
}

export type AdminActivityFilters = {
  role?: string
  module?: string
  result?: string
  status?: string
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

function exportFilename(contentDisposition: unknown, fallback: string): string {
  if (typeof contentDisposition !== 'string') return fallback
  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try { return decodeURIComponent(encoded) } catch { return encoded }
  }
  return contentDisposition.match(/filename="?([^";]+)"?/i)?.[1] || fallback
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

  exportXlsx: async (filters: AdminActivityFilters = {}): Promise<AdminActivityExport> => {
    const response = await apiClient.get<Blob>('/admin/activity/export', {
      params: { format: 'xlsx', ...exportFilters(filters) },
      responseType: 'blob',
    })
    return {
      blob: response.data,
      filename: exportFilename(response.headers['content-disposition'], 'nhat-ky-quan-tri.xlsx'),
    }
  },

  // Compatibility adapter for older callers; the Activity Center UI uses Excel.
  exportCsv: async (filters: AdminActivityFilters = {}): Promise<AdminActivityExport> => {
    const response = await apiClient.get<Blob>('/admin/activity/export', {
      params: { format: 'csv', ...exportFilters(filters) },
      responseType: 'blob',
    })
    return {
      blob: response.data,
      filename: exportFilename(response.headers['content-disposition'], 'nhat-ky-quan-tri.csv'),
    }
  },
}
