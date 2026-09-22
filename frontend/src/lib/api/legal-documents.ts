import { apiClient } from './client'

export type LegalRetrievalTier = 'all' | 'core' | 'expanded'
export type LegalValidityStatusFilter = 'active' | 'expiring_30' | 'not_yet_effective' | 'expired' | 'unknown'

export interface LegalDocumentListItem {
  doc_id: string | number
  document_title: string
  law_number?: string | null
  document_type?: string | null
  issuing_agency?: string | null
  scope?: string | null
  sector?: string | null
  effective_status: string
  validity_status?: string | null
  validity_sync?: {
    status?: string | null
    display_label?: string | null
    effective_from?: string | null
    effective_to?: string | null
    current_answer_eligible?: boolean
    historical_lookup_allowed?: boolean
  } | null
  issued_date?: string | null
  effective_date?: string | null
  expired_date?: string | null
  source_url?: string | null
  field_id?: number | null
  field_name?: string | null
  domain?: string | null
  domain_name?: string | null
  retrieval_tier: Exclude<LegalRetrievalTier, 'all'>
  article_count: number
}

export interface LegalDocumentListResponse {
  items: LegalDocumentListItem[]
  total: number
  limit: number
  offset: number
  as_of: string
  tier: LegalRetrievalTier
}

export interface LegalDomain {
  slug: string
  name: string
  field_count: number
}

export interface LegalDepartmentField {
  code: string
  name: string
  domains: string[]
}

export interface LegalDepartment {
  id: string
  name: string
  code: string
  fields: LegalDepartmentField[]
}

export interface LegalDocumentFilterParams {
  q?: string
  domain?: string
  tier?: LegalRetrievalTier
  validity_status?: LegalValidityStatusFilter
  issued_from?: string
  issued_to?: string
  effective_from?: string
  effective_to?: string
  expired_from?: string
  expired_to?: string
  sort_by?: 'effective_date' | 'issued_date' | 'title' | 'law_number'
  sort_order?: 'asc' | 'desc'
}

export interface LegalDocumentListParams extends LegalDocumentFilterParams {
  limit?: number
  offset?: number
}

export interface LegalDocumentExport {
  blob: Blob
  filename: string
}

function exportFilename(disposition?: string): string {
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) return decodeURIComponent(encoded)
  return disposition?.match(/filename="?([^";]+)"?/i)?.[1] || 'danh-sach-van-ban.xlsx'
}

export const legalDocumentsApi = {
  async list(
    params: LegalDocumentListParams,
    signal?: AbortSignal,
  ): Promise<LegalDocumentListResponse> {
    const response = await apiClient.get<LegalDocumentListResponse>('/legal/documents', { params, signal })
    return response.data
  },

  async exportXlsx(params: LegalDocumentFilterParams): Promise<LegalDocumentExport> {
    const response = await apiClient.get<Blob>('/legal/documents/export.xlsx', {
      params,
      responseType: 'blob',
    })
    return {
      blob: response.data,
      filename: exportFilename(response.headers['content-disposition']),
    }
  },

  async domains(): Promise<LegalDomain[]> {
    const response = await apiClient.get<{ domains: LegalDomain[] }>('/legal/domains')
    return response.data.domains || []
  },

  async communeCatalog(): Promise<LegalDepartment[]> {
    const response = await apiClient.get<{ departments: LegalDepartment[] }>('/legal/commune-catalog')
    return response.data.departments || []
  },
}
