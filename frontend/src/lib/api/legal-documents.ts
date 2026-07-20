import { apiClient } from './client'

export type LegalRetrievalTier = 'all' | 'core' | 'expanded'

export interface LegalDocumentListItem {
  doc_id: string | number
  document_title: string
  law_number?: string | null
  document_type?: string | null
  issuing_agency?: string | null
  scope?: string | null
  sector?: string | null
  effective_status: string
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

export interface LegalDocumentListParams {
  q?: string
  domain?: string
  tier?: LegalRetrievalTier
  limit?: number
  offset?: number
  sort_by?: 'effective_date' | 'issued_date' | 'title' | 'law_number'
  sort_order?: 'asc' | 'desc'
}

export const legalDocumentsApi = {
  async list(params: LegalDocumentListParams): Promise<LegalDocumentListResponse> {
    const response = await apiClient.get<LegalDocumentListResponse>('/legal/documents', { params })
    return response.data
  },

  async domains(): Promise<LegalDomain[]> {
    const response = await apiClient.get<{ domains: LegalDomain[] }>('/legal/domains')
    return response.data.domains || []
  },
}
