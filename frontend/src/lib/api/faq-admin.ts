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
  documents_required: string[]
  duration: string
  fee: string
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
  revision_id: string
  revision_number: number
  confirmed_procedure_id: string
  primary_organization_unit_id?: string | null
  public_state: 'pending' | 'needs_review' | 'confirmed' | 'released' | 'dismissed' | 'blocked'
  /** True only when this revision belongs to the currently active public release. */
  in_active_release?: boolean
}

export interface FaqListResponse {
  state_counts?: Record<string, number>
  total: number
  items: FaqItem[]
  active_release_id?: string | null
  active_release_count?: number
  historical_released_count?: number
}

export interface FaqCreatePayload {
  organization_unit_id?: string
  faq_key?: string
  question: string
  answer: string
  submission_place?: string
  legal_basis?: string[]
  guidance_label?: string
  requires_forms?: boolean
  steps?: string[]
  documents_required?: string[]
  duration?: string
  fee?: string
  domain: string
  confirmed_procedure_id: string
  ward_scope?: string | null
}

export interface FaqUpdatePayload {
  organization_unit_id?: string
  question?: string
  answer?: string
  submission_place?: string
  legal_basis?: string[]
  guidance_label?: string
  requires_forms?: boolean
  steps?: string[]
  documents_required?: string[]
  duration?: string
  fee?: string
  domain?: string
  confirmed_procedure_id?: string
  ward_scope?: string | null
}

export interface FaqRelease {
  id: string
  release_id: string
  version: number
  status: 'candidate' | 'validated' | 'active' | 'retired' | 'blocked'
  form_release_id: string
  manifest: {
    gate_report?: { passed: boolean; errors: string[] }
    withdrawal?: { revision_ids: string[]; reason: string }
  }
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
  pending: 'Chờ xác nhận',
  needs_review: 'Cần rà soát',
  confirmed: 'Đã xác nhận · chưa công khai',
  released: 'Đã phát hành',
  dismissed: 'Đã loại',
  blocked: 'Đang bị chặn',
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
    const response = await apiClient.get<FaqListResponse>('/faq/governance/revisions', { params })
    return response.data
  },

  /** Get a single FAQ by ID */
  get: async (id: string): Promise<FaqItem> => {
    const response = await apiClient.get<FaqItem>(`/faq/${id}`)
    return response.data
  },

  /** Create a new FAQ (admin only) */
  create: async (data: FaqCreatePayload): Promise<FaqItem> => {
    const response = await apiClient.post<FaqItem>('/faq/governance/revisions', {
      faq_key: data.faq_key,
      question: data.question,
      answer: data.answer,
      canonical_domain: data.domain,
      organization_unit_id: data.organization_unit_id,
      confirmed_procedure_id: data.confirmed_procedure_id,
      requires_forms: data.requires_forms,
      submission_place: data.submission_place,
      legal_basis: data.legal_basis,
      guidance_label: data.guidance_label,
      steps: data.steps,
      documents_required: data.documents_required,
      duration: data.duration,
      fee: data.fee,
      ward_scope: data.ward_scope,
      evidence: {},
    })
    return response.data
  },

  /** Update an existing FAQ (admin only) */
  update: async (id: string, data: FaqUpdatePayload): Promise<FaqItem> => {
    if (!data.confirmed_procedure_id || !data.domain || !data.question || !data.answer) {
      throw new Error('FAQ_REVISION_INCOMPLETE')
    }
    return faqAdminApi.create({ ...data, faq_key: id } as FaqCreatePayload)
  },

  confirm: async (revisionId: string): Promise<FaqItem> => {
    const response = await apiClient.post<FaqItem>(`/faq/governance/revisions/${encodeURIComponent(revisionId)}/confirm`)
    return response.data
  },

  deleteRevision: async (revisionId: string): Promise<void> => {
    await apiClient.delete(`/faq/governance/revisions/${encodeURIComponent(revisionId)}`)
  },

  previewRelease: async (revisionIds: string[]): Promise<FaqRelease> => {
    const response = await apiClient.post<FaqRelease>('/faq/governance/releases/preview', { revision_ids: revisionIds })
    return response.data
  },

  validateRelease: async (releaseId: string): Promise<FaqRelease> => {
    const response = await apiClient.post<FaqRelease>(`/faq/governance/releases/${encodeURIComponent(releaseId)}/validate`)
    return response.data
  },

  previewWithdrawal: async (revisionIds: string[], reason: string, activeReleaseId: string): Promise<FaqRelease> => {
    const response = await apiClient.post<FaqRelease>('/faq/governance/releases/preview', {
      revision_ids: [], withdraw_revision_ids: revisionIds,
      withdrawal_reason: reason, expected_active_release_id: activeReleaseId,
    })
    return response.data
  },

  activateRelease: async (releaseId: string): Promise<FaqRelease> => {
    const response = await apiClient.post<FaqRelease>(`/faq/governance/releases/${encodeURIComponent(releaseId)}/activate`)
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
