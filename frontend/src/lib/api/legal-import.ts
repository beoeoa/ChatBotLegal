import apiClient from './client'

export interface LegalField {
  id: number
  name: string
  description?: string
}

export interface LegalImportPayload {
  title: string
  law_number: string
  document_type: string
  issuing_agency: string
  scope: string
  sector: string
  field_id: number
  issued_date: string | null
  effective_date: string
  expired_date: string | null
  source_url: string
  applicability_info: string
  content: string
  confirmed_official_source: boolean
}

export interface LegalDomain {
  slug: string
  name: string
  field_count: number
}

export interface LegalCrawlSource {
  id: string
  name: string
  source_type: string
  sitemap_scope: string
  base_url: string
  enabled: boolean
  interval_minutes: number
  lookback_days: number
  max_documents_per_run: number
  max_listing_pages_per_run?: number | null
  filter_keyword?: string | null
  last_checked_at?: string | null
  last_success_at?: string | null
  last_error?: string | null
  domains?: string[] | null
  rate_limit_seconds?: number | null
  content_fetch_allowed?: boolean | null
  last_run_stats?: Record<string, number> | null
  source_freshness?: string | null
  last_status?: string | null
}

export interface AiAssessment {
  domain: string
  scope: string
  official_level: string
  effective_status: string
  duplicate_risk: string
  confidence: number
  reasons: string[]
}

export interface CandidateExtractionResult {
  status?: string
  extractor_used?: string
  pdf_kind?: 'text_based' | 'scan' | 'unknown' | 'not_pdf' | string
  ocr_status?: string
  ocr_confidence?: number | null
  reason?: string
  language?: string
  page_count?: number
  characters?: number
  preview?: string
  text_fingerprint?: string | null
  file_fingerprint?: string | null
}

export interface CandidateReviewRecommendation {
  action?: string
  scores?: Record<string, number>
  evidence?: string[]
  evidence_snippets?: Array<{ kind: string; text: string }>
  generated_at?: string
}

export interface LegalCrawlCandidate {
  id: string
  external_id: string
  title: string
  law_number?: string | null
  description?: string | null
  detail_url: string
  source_url: string
  sitemap_lastmod?: string | null
  document_type?: string | null
  issuing_agency?: string | null
  scope?: string | null
  status: string
  suggested_action: string
  comparison_status: string
  detected_changes: string[]
  detected_change_details?: Array<{
    field: string
    label: string
    old_value?: unknown
    new_value?: unknown
  }>
  imported_document?: Record<string, unknown> | null
  source?: LegalCrawlSource | null
  review_note?: string | null
  reviewed_at?: string | null
  reviewed_by?: string | null
  reviewed_role?: string | null
  inferred_domain?: string | null
  suitability_recommendation?: string | null
  ai_assessment?: AiAssessment | null
  review_recommendation?: CandidateReviewRecommendation | null
  extraction_result?: CandidateExtractionResult | null
  needs_ocr?: boolean
  raw_metadata?: Record<string, unknown> | null
  review_status?: string | null
  requested_changes_note?: string | null
  proposal_reason?: string | null
  source_type?: string | null
}

export interface LegalCrawlNotification {
  id: string
  type: string
  title: string
  message: string
  read_at?: string | null
  created?: string | null
  candidate?: LegalCrawlCandidate | null
}

export interface LegalCrawlSummary {
  pending_review_count: number
  unread_notification_count: number
  source_count: number
  last_checked_at?: string | null
  schedule_interval_minutes?: number
}

export interface RuntimeTelemetryEvent {
  at: string
  category: string
  duration_ms: number
  outcome?: string
  status_code?: number
  route?: string
}

export interface LegalQualitySummary {
  generated_at: string
  runtime?: {
    event_count?: number
    by_category?: Record<string, { count: number; avg_ms: number; max_ms: number; error_count: number; cancelled_count?: number }>
    slow_requests?: RuntimeTelemetryEvent[]
    issue_counts?: Record<string, number>
    privacy?: string
  }
  quality_issues?: {
    citation_dead?: number
    pdf_export_failed?: number
    broken_form_url?: number
    slow_request?: number
    ocr_failed?: number
    import_failed?: number
    unanswered_question?: number
  }
  role_evaluation: {
    available: boolean
    generated_at?: string
    total?: number
    completed?: number
    hallucination_flags?: number
    fallback_count?: number
    by_role?: Record<string, { count: number; grounded: number; role_style: number }>
    sample?: Array<Record<string, unknown>>
  }
  retrieval_evaluation: {
    available: boolean
    generated_at?: string
    total?: number
    with_sources?: number
    avg_sources?: number
    top_status_distribution?: Record<string, number>
    sample?: Array<Record<string, unknown>>
  }
}

export interface LegalFormsCatalogStatus {
  summary: {
    manifest_total: number
    domain_counts: Record<string, number>
    status_counts: Record<string, number>
    verified_official_files: number
    quarantined_synthetic_files: number
    generated_at?: string | null
    hai_phong_official?: {
      articles_discovered?: number
      attachments_found?: number
      official_source_packages?: number
      form_source_references?: number
      unique_form_title_groups?: number
      duplicate_source_references?: number
      needs_title_review?: number
      total_source_references?: number
      available_canonical_forms?: number
      duplicate_sources?: number
      blocked_effectivity_flags?: number
      review_required_title?: number
      status_counts?: Record<string, number>
      domain_counts?: Record<string, number>
      errors?: number
      generated_at?: string | null
      priority_supplement?: {
        total_available?: number
        standalone_files?: number
        verbatim_pdf_slices?: number
        errors?: number
      }
      priority_200?: {
        target_count?: number
        selected_count?: number
        target_met?: boolean
        available_pool?: number
        domain_counts?: Record<string, number>
        tier_counts?: Record<string, number>
        supplement_count?: number
        warning?: string
      }
    }
  }
  records: Array<{
    id: string
    title?: string | null
    domain?: string | null
    detail_url?: string | null
    status: string
    http_status?: number | null
    download_url?: string | null
    local_path?: string | null
    file_type?: string | null
    checked_at?: string | null
  }>
}

export interface LegalFormCandidate {
  id: string
  title?: string | null
  form_title?: string | null
  detected_form_name?: string | null
  file_name?: string | null
  file_path?: string | null
  official_level?: string | null
  ward_scope?: boolean | null
  version?: string | null
  domain?: string | null
  suggested_domain?: string | null
  department?: string | null
  procedure_id?: string | null
  suggested_procedure_id?: string | null
  source_url?: string | null
  page_url?: string | null
  review_note?: string | null
  review_status?: string | null
  confidence?: number | null
  reason?: string | null
  sha256?: string | null
  source_name?: string | null
  is_approved?: boolean | null
  priority_path?: string | null
  local_path?: string | null
  download_url?: string | null
}

export interface LegalFormReviewPayload {
  decision: 'approved' | 'rejected'
  review_note?: string
  reason?: string
  form_name?: string
  procedure_id?: string
  domain?: string
}

export interface ImportPreview {
  valid: boolean
  errors: string[]
  warnings: string[]
  article_count: number
  chunk_count: number
  field: LegalField | null
  sample_articles: Array<{
    article_number: string
    title: string
    characters: number
  }>
}

export type LegalImportExtractor = 'auto' | 'basic' | 'rag_anything'

export const legalImportApi = {
  async fields(): Promise<LegalField[]> {
    const response = await apiClient.get<{ fields: LegalField[] }>('/legal/import/fields')
    return response.data.fields
  },

  async preview(payload: LegalImportPayload): Promise<ImportPreview> {
    const response = await apiClient.post<ImportPreview>('/legal/import/preview', payload)
    return response.data
  },

  async importDocument(payload: LegalImportPayload) {
    const response = await apiClient.post('/legal/import', payload)
    return response.data
  },

  async domains(): Promise<LegalDomain[]> {
    const response = await apiClient.get<{ domains: LegalDomain[] }>('/legal/domains')
    return response.data.domains
  },

  async extractFile(
    file: File,
    extractor: LegalImportExtractor = 'auto',
  ): Promise<{
    filename: string
    characters: number
    content: string
    extractor_used?: string
    extractor_fallback_reason?: string
    content_blocks?: number
    repo_path?: string
  }> {
    const formData = new FormData()
    formData.append('file', file)
    const response = await apiClient.post('/legal/import/extract', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180_000,
      params: { extractor },
    })
    return response.data
  },

  async crawlPreview(url: string): Promise<{
    source_url: string
    title: string
    characters: number
    content: string
    crawler: string
    requires_manual_review: boolean
  }> {
    const response = await apiClient.post('/legal/crawl/preview', { url }, { timeout: 120_000 })
    return response.data
  },

  async crawlSummary(): Promise<LegalCrawlSummary> {
    const response = await apiClient.get<LegalCrawlSummary>('/legal/crawl/summary')
    return response.data
  },

  async crawlSources(): Promise<LegalCrawlSource[]> {
    const response = await apiClient.get<{ sources: LegalCrawlSource[] }>('/legal/crawl/sources')
    return response.data.sources
  },

  async updateCrawlSource(sourceId: string, payload: Partial<Pick<LegalCrawlSource, 'enabled' | 'interval_minutes' | 'lookback_days' | 'max_documents_per_run' | 'max_listing_pages_per_run' | 'rate_limit_seconds' | 'filter_keyword'>>) {
    const response = await apiClient.patch<{ source: LegalCrawlSource }>(`/legal/crawl/sources/${sourceId}`, payload)
    return response.data.source
  },

  async scanNow(sourceId?: string) {
    const response = await apiClient.post('/legal/crawl/scan', null, {
      params: sourceId ? { source_id: sourceId } : undefined,
      timeout: 180_000,
    })
    return response.data
  },

  async crawlCandidates(status = 'pending_review', limit = 100): Promise<LegalCrawlCandidate[]> {
    const response = await apiClient.get<{ candidates: LegalCrawlCandidate[] }>('/legal/crawl/candidates', {
      params: { status, limit },
    })
    return response.data.candidates
  },

  async reviewCandidate(candidateId: string, decision: 'approved' | 'rejected' | 'changes_requested', review_note = '') {
    const response = await apiClient.post<{ candidate: LegalCrawlCandidate }>(`/legal/crawl/candidates/${candidateId}/review`, {
      decision,
      review_note,
    })
    return response.data.candidate
  },

  async assessCandidate(candidateId: string): Promise<{ candidate: LegalCrawlCandidate; ai_assessment: AiAssessment | null; warning?: string }> {
    const response = await apiClient.post<{ candidate: LegalCrawlCandidate; ai_assessment: AiAssessment | null; warning?: string }>(
      `/legal/crawl/candidates/${candidateId}/assess`,
      null,
      { timeout: 120_000 },
    )
    return response.data
  },

  async notifications(unreadOnly = false): Promise<LegalCrawlNotification[]> {
    const response = await apiClient.get<{ notifications: LegalCrawlNotification[] }>('/legal/crawl/notifications', {
      params: { unread_only: unreadOnly },
    })
    return response.data.notifications
  },

  async markNotificationRead(notificationId: string) {
    const response = await apiClient.post<{ notification: LegalCrawlNotification }>(`/legal/crawl/notifications/${notificationId}/read`)
    return response.data.notification
  },

  async qualitySummary(): Promise<LegalQualitySummary> {
    const response = await apiClient.get<LegalQualitySummary>('/legal/quality/summary')
    return response.data
  },

  async formsCatalogStatus(): Promise<LegalFormsCatalogStatus> {
    const response = await apiClient.get<LegalFormsCatalogStatus>('/procedures/forms-catalog/status')
    return response.data
  },

  async reviewForm(
    formId: string,
    decision: 'approved' | 'rejected',
    reviewNote = '',
    extras: Partial<Omit<LegalFormReviewPayload, 'decision' | 'review_note'>> = {},
  ) {
    // Prefer full classified candidate pipeline (copy file + update index).
    const response = await apiClient.post(`/procedures/forms-catalog/candidates-full/${formId}/review`, {
      decision,
      review_note: reviewNote,
      reason: extras.reason ?? reviewNote,
      form_name: extras.form_name,
      procedure_id: extras.procedure_id,
      domain: extras.domain,
    })
    return response.data
  },

  async formsCatalogCandidates(domain?: string, reviewStatus = 'candidate_pending_review'): Promise<LegalFormCandidate[]> {
    // Admin queue from crawl+classify pipeline. Pending candidates never go to citizen/officer surfaces.
    const response = await apiClient.get<{ records: LegalFormCandidate[] }>('/procedures/forms-catalog/candidates-full', {
      params: { domain, review_status: reviewStatus, limit: 200 },
    })
    return (response.data.records || []).map((item) => ({
      ...item,
      title: item.title || item.form_title || item.detected_form_name || item.file_name || item.id,
      domain: item.domain || item.suggested_domain || null,
      procedure_id: item.procedure_id || item.suggested_procedure_id || null,
      source_url: item.source_url || item.page_url || null,
      review_note: item.review_note || item.reason || null,
    }))
  },

  async listOfficialForms(domain?: string, query?: string): Promise<LegalFormCandidate[]> {
    const response = await apiClient.get<{ records: LegalFormCandidate[] }>('/procedures/forms-catalog/official', {
      params: { domain, query, limit: 200 },
    })
    return response.data.records || []
  },

  async extractCandidate(candidateId: string) {
    const response = await apiClient.post<{ job: Record<string, unknown>; status: string; message: string }>(
      `/legal/crawl/candidates/${candidateId}/extract`,
    )
    return response.data
  },

  async getExtractJobStatus(jobId: string) {
    const response = await apiClient.get<{ job: Record<string, unknown> }>(`/legal/crawl/extract-jobs/${jobId}`)
    return response.data.job
  },

  async runtimeQuality() {
    const response = await apiClient.get<Record<string, unknown>>('/legal/quality/runtime')
    return response.data
  },

  async runQualityEvaluation(type: 'retrieval' | 'role') {
    const response = await apiClient.post<{ started: boolean; evaluation_type: string }>(`/legal/quality/run/${type}`)
    return response.data
  },
}
