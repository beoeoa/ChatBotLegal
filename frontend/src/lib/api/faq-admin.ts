/**
 * FAQ Admin API Client
 *
 * Wraps /api/faq endpoints for admin CRUD operations.
 * Backend already enforces admin-only access on POST/PUT/DELETE.
 */

import { apiClient } from './client'

export interface FaqItem {
  id: string
  question: string
  answer: string
  submission_place: string
  legal_basis: string[]
  guidance_label: string
  requires_forms: boolean
  steps: string[]
  form_ids: string[]
  forms: Array<{
    id: string
    name: string
    file_type: string
    download_url: string
    official_level: string
    review_status: string
  }>
  forms_unavailable: boolean
  domain: string
  ward_scope: string | null
  review_status: string
  created_at: string
  updated_at: string
  approved_by: string | null
}

export interface FaqListResponse {
  total: number
  items: FaqItem[]
}

export interface FaqCreatePayload {
  question: string
  answer: string
  submission_place?: string
  legal_basis?: string[]
  guidance_label?: string
  requires_forms?: boolean
  steps?: string[]
  form_ids?: string[]
  domain: string
  ward_scope?: string | null
  review_status?: 'draft' | 'approved' | 'rejected'
}

export interface FaqUpdatePayload {
  question?: string
  answer?: string
  submission_place?: string
  legal_basis?: string[]
  guidance_label?: string
  requires_forms?: boolean
  steps?: string[]
  form_ids?: string[]
  domain?: string
  ward_scope?: string | null
  review_status?: 'draft' | 'approved' | 'rejected'
}

export const DOMAIN_OPTIONS: Record<string, string> = {
  ho_tich_chung_thuc: 'Hộ tịch - Chứng thực',
  dat_dai_xay_dung: 'Đất đai - Xây dựng',
  an_sinh_y_te_giao_duc: 'An sinh - Y tế - Giáo dục',
  cu_tru_an_ninh: 'Cư trú - An ninh',
  khieu_nai_to_cao_xu_phat: 'Khiếu nại - Tố cáo - Xử phạt',
  hanh_chinh_cong: 'Hành chính công',
  trat_tu_do_thi: 'Trật tự đô thị',
}

export const REVIEW_STATUS_OPTIONS: Record<string, string> = {
  draft: 'Nháp',
  approved: 'Đã duyệt',
  rejected: 'Từ chối',
}

export const faqAdminApi = {
  /** List FAQs with optional filters (admin sees all statuses) */
  list: async (params?: {
    domain?: string
    review_status?: string
    q?: string
    limit?: number
    offset?: number
  }): Promise<FaqListResponse> => {
    const query = new URLSearchParams()
    if (params?.domain) query.set('domain', params.domain)
    if (params?.review_status) query.set('review_status', params.review_status)
    if (params?.q) query.set('q', params.q)
    query.set('limit', String(params?.limit ?? 200))
    query.set('offset', String(params?.offset ?? 0))
    const response = await apiClient.get<FaqListResponse>(`/faq?${query.toString()}`)
    return response.data
  },

  /** Get a single FAQ by ID */
  get: async (id: string): Promise<FaqItem> => {
    const response = await apiClient.get<FaqItem>(`/faq/${id}`)
    return response.data
  },

  /** Create a new FAQ (admin only) */
  create: async (data: FaqCreatePayload): Promise<FaqItem> => {
    const response = await apiClient.post<FaqItem>('/faq', data)
    return response.data
  },

  /** Update an existing FAQ (admin only) */
  update: async (id: string, data: FaqUpdatePayload): Promise<FaqItem> => {
    const response = await apiClient.put<FaqItem>(`/faq/${id}`, data)
    return response.data
  },

  /** Delete a FAQ (admin only) */
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/faq/${id}`)
  },

  /** Seed FAQs from default data (admin only) */
  seed: async (): Promise<{ imported: number; total: number }> => {
    const response = await apiClient.post<{ imported: number; total: number }>('/faq/seed')
    return response.data
  },
}
