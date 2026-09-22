import apiClient from './client'
import { getApiUrl } from '@/lib/config'

export interface LegalField {
  id: number
  name: string
  description?: string
}

export interface LegalImportPayload {
  uploaded_pdf_sha256?: string | null
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
  /** Stable admin-managed routing code; field_id remains a legacy storage key. */
  domain_slug?: string | null
  domain_codes?: string[]
  primary_organization_unit_id?: string | null
  organization_unit_ids?: string[]
  organization_assignment_state?: 'assigned' | 'shared' | 'unassigned'
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
  website_type?: 'mixed_official' | 'legal_documents' | 'procedures' | 'forms' | 'reference'
  link_selector?: string | null
  next_page_selector?: string | null
  include_patterns?: string[] | null
  exclude_patterns?: string[] | null
  last_checked_at?: string | null
  last_success_at?: string | null
  last_error?: string | null
  domains?: string[] | null
  default_organization_unit_id?: string | null
  unassigned_policy?: 'unassigned' | 'shared'
  rate_limit_seconds?: number | null
  content_fetch_allowed?: boolean | null
  compatibility_mode?: 'standard' | 'high'
  last_run_stats?: Record<string, number> | null
  source_freshness?: string | null
  last_status?: string | null
  source_kind?: 'web_crawler' | 'internal_queue'
  is_default?: boolean
  can_delete?: boolean
  can_scan?: boolean
  purpose?: string | null
}

export interface LegalCrawlScanError {
  source_id: string
  status: string
  message: string
}

export interface LegalCrawlScanRun {
  status: string
  source_id?: string
  statistics?: Record<string, number>
  failure_reason?: string | null
  reason?: string | null
}

export interface LegalCrawlScanResult {
  status: 'completed' | 'completed_with_warnings' | 'partial' | 'failed' | 'busy' | 'skipped'
  runs: LegalCrawlScanRun[]
  run_count: number
  created: number
  updated: number
  failed_count: number
  warning_count: number
  busy_count: number
  skipped_count: number
  errors: LegalCrawlScanError[]
}

export interface LegalImportReadiness {
  status: 'ready' | 'not_ready'
  components: Record<string, {
    healthy: boolean
    code: string
    required?: boolean
    heartbeat_age_seconds?: number
    max_heartbeat_age_seconds?: number
  }>
  embedding_device: {
    requested: string
    active: string
  }
}

export type FormWorkflowStatus =
  | 'draft' | 'submitted' | 'needs_supplement' | 'resubmitted'
  | 'source_approved' | 'legal_enrichment' | 'ready_for_attestation'
  | 'attested' | 'release_candidate' | 'released'
  | 'rejected' | 'withdrawn' | 'superseded' | 'expired' | 'quarantined'

export interface FormReviewCaseV17 {
  case_id: string
  officer_id: string
  domain: string
  procedure_id: string
  title: string
  status: FormWorkflowStatus
  revision: number
  version: number
  current_submission: { source_url: string; source_checksum?: string | null; note?: string; asset_kind?: 'file' | 'eform'; page_number?: number | null }
  legal_metadata?: Record<string, unknown>
}

export interface FormManagementEvent {
  event_id: string; actor_id: string; action: string; occurred_at: string
  from_status?: string; to_status?: string; reason_code?: string
}

export interface FormManagementAsset {
  form_id: string; canonical_name: string; form_code?: string; asset_kind: 'file' | 'eform'
  source_url: string; source_checksum: string; effective_from?: string; effective_to?: string
  audiences: string[]; download_url?: string
}

export interface FormManagementCatalog {
  release_id?: string
  procedures: FormProcedureCandidateV18[]
  assets: FormManagementAsset[]
  bindings: Array<{ procedure_id: string; form_id?: string; audience: string; condition?: string }>
  aliases: Array<{ procedure_id: string; alias: string; alias_kind: string }>
}

export interface FormCoverageV17 {
  procedure_total: number
  procedure_decided: number
  identity_total: number
  identity_decided: number
  binding_total: number
  binding_decided: number
  complete: boolean
  workflow_statuses: Record<string, number>
}

export interface FormProcedureCandidateV18 {
  procedure_id: string
  procedure_code: string
  name: string
  domain: string
  official_source_url?: string | null
  coverage_status?: string | null
  primary_organization_unit_id?: string | null
  primary_organization_unit_name?: string | null
  supporting_organization_unit_ids?: string[]
}

export interface FormSourceProposalMetadataV18 {
  steps: Array<{
    id: string
    label: string
    action: string
    public_after_step: boolean
  }>
  source_inputs: Array<'official_url' | 'pdf' | 'docx' | 'eform'>
  file_contract: {
    accepted_extensions: string[]
    accepted_mime_types: string[]
    checksum: 'sha256'
    transport: string
  }
  source_approval_changes_public_release: boolean
}

export interface FormAttestationPreviewV17 {
  case_id: string
  revision: number
  fingerprint: string
  procedure: Record<string, unknown>
  asset: Record<string, unknown>
  bindings: Array<Record<string, unknown>>
  aliases: string[]
}

export interface FormReleaseV17 {
  release_id: string
  version: number
  legal_as_of: string
  manifest_sha256: string
  status: 'candidate' | 'validated' | 'active' | 'retired' | 'blocked'
  gate_report?: {
    passed: boolean
    errors: string[]
    source_checks?: { required: number; passed: number; mode: string }
  }
  manifest?: {
    build?: { case_ids?: string[] }
    procedures?: Array<Record<string, unknown>>
    assets?: Array<Record<string, unknown>>
    bindings?: Array<Record<string, unknown>>
    exclusions?: Array<Record<string, unknown>>
  }
}

export interface CrawlSourceCreatePayload {
  name: string
  base_url: string
  sitemap_scope: 'central' | 'haiphong' | 'local'
  interval_minutes?: number
  lookback_days?: number
  max_documents_per_run?: number
  max_listing_pages_per_run?: number
  rate_limit_seconds?: number
  compatibility_mode?: 'standard' | 'high'
  filter_keyword?: string | null
  website_type?: 'mixed_official' | 'legal_documents' | 'procedures' | 'forms' | 'reference'
  link_selector?: string | null
  next_page_selector?: string | null
  include_patterns?: string[]
  exclude_patterns?: string[]
  domains?: string[]
  default_organization_unit_id?: string | null
  unassigned_policy?: 'unassigned' | 'shared'
}

export interface CrawlSourcePreview {
  status: 'ok'
  mutation_performed: false
  base_url: string
  website_type: NonNullable<LegalCrawlSource['website_type']>
  discovered_count: number
  preview_count: number
  truncated: boolean
  listing_verified?: boolean
  filtered_out_count?: number
  notice?: string | null
  candidates: Array<{
    url: string
    title: string
    context: string
    source_type: string
    law_number?: string
    document_type?: string
  }>
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
  processed_pages?: number
  total_pages?: number
  complete?: boolean
  truncated?: boolean
  failed_pages?: number[]
  pages_without_text?: number[]
  native_text_pages?: number[]
  ocr_requested_pages?: number[]
  ocr_pages?: number[]
  page_extractors?: Record<string, string>
  coverage_percent?: number
  table_count?: number
  table_extracted_count?: number
  characters?: number
  preview?: string
  text_fingerprint?: string | null
  file_fingerprint?: string | null
}

export interface CandidateReviewRecommendation {
  action?: string
  scores?: Record<string, number>
  evidence?: string[]
  hard_gate_failures?: string[]
  passed_hard_gates?: boolean
  evidence_snippets?: Array<{ kind: string; text: string }>
  generated_at?: string
}

export interface LegalDuplicateMatch {
  id: string
  target_type: 'document' | 'candidate'
  kind: 'duplicate_content' | 'identity_match' | 'content_changed' | 'metadata_conflict'
  title?: string | null
  law_number?: string | null
  issuing_agency?: string | null
  issued_date?: string | null
  source_url?: string | null
  status?: string | null
  reason_codes: string[]
  target_revision: string
  can_archive: boolean
  can_review_replacement: boolean
}

export interface LegalDuplicateResolutionRequest {
  action: 'archive_duplicate' | 'review_replacement'
  target_type: 'document' | 'candidate'
  target_id: string
  expected_revision: string
  target_revision: string
  reason: string
  confirmed_same_document: boolean
}

export interface LegalDuplicateResolution {
  action: LegalDuplicateResolutionRequest['action']
  target_type: 'document' | 'candidate'
  target_id: string
  target_title?: string
  reason: string
  resolved_at: string
  corpus_changed: false
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
  duplicate_matches?: LegalDuplicateMatch[]
  duplicate_check_status?: 'complete' | 'unavailable'
  duplicate_check_revision?: string
  duplicate_resolution?: LegalDuplicateResolution | null
  duplicate_runtime_document?: {
    id?: string | number | null
    title?: string | null
    law_number?: string | null
  } | null
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
  assignment_state?: 'assigned' | 'shared' | 'unassigned' | null
  primary_organization_unit_id?: string | null
  organization_unit_ids?: string[] | null
  suitability_recommendation?: string | null
  ai_assessment?: AiAssessment | null
  review_recommendation?: CandidateReviewRecommendation | null
  extraction_result?: CandidateExtractionResult | null
  needs_ocr?: boolean
  raw_metadata?: Record<string, unknown> | null
  review_status?: string | null
  import_status?: string | null
  import_job?: string | null
  pipeline_stage?: 'fetching_source' | 'validating' | 'blocked' | 'queued' | 'chunking_embedding' | 'active' | 'failed' | string | null
  preparation_status?: 'pending' | 'ready' | 'blocked' | string | null
  blockers?: string[] | null
  document_id?: string | null
  chunk_count?: number | null
  indexed_at?: string | null
  vector_collection?: string | null
  chatbot_ready?: boolean | null
  requested_changes_note?: string | null
  proposal_reason?: string | null
  source_type?: string | null
  content_characters?: number | null
}

export interface LegalCandidateMetadataUpdate {
  title?: string
  law_number?: string
  document_type?: string
  issuing_agency?: string
  scope?: 'central' | 'haiphong' | 'local'
  sector?: string
  issued_date?: string
  effective_date?: string
  expired_date?: string
  source_url?: string
  confirmed_official_source?: boolean
  assignment_state?: 'assigned' | 'shared' | 'unassigned'
  primary_organization_unit_id?: string | null
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
  /** Included by /crawl/summary so the page does not repeat /crawl/sources. */
  sources?: LegalCrawlSource[]
  last_checked_at?: string | null
  schedule_interval_minutes?: number
  activated_candidates?: number
  unverified_imported_candidates?: number
  import_queue?: {
    queued: number
    running: number
    failed: number
    completed: number
  }
  import_metrics?: {
    observed_job_count: number
    success_rate_percent: number | null
    average_queue_seconds: number | null
    average_processing_seconds: number | null
    validation_failed_candidates: number
    duplicate_conflict_candidates: number
    activated_candidates: number
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

export interface LegalFormCandidatePage {
  summary: {
    filtered_total: number
    returned: number
    offset: number
    limit: number
  }
  generated_at?: string | null
  records: LegalFormCandidate[]
}

export interface LegalFormReviewPayload {
  decision: 'approved' | 'rejected'
  review_note?: string
  reason?: string
  form_name?: string
  procedure_id?: string
  domain?: string
}

export interface FormLegalReviewPreviewItem {
  candidate_id?: string | null
  canonical_form_id: string
  procedure_id: string
  form_code?: string | null
  canonical_name?: string | null
  source_page_url?: string | null
  source_download_url?: string | null
  local_path?: string | null
  sha256?: string | null
  legal_basis: string[]
  effective_from?: string | null
  effective_to?: string | null
  jurisdiction?: string | null
  administrative_level?: string | null
  review_status?: string | null
  legal_review_status?: string | null
  reason_codes: string[]
  requirement_identity_id?: string | null
  issuing_instrument?: string | null
  source_domain?: string | null
  effectivity_reason_code?: string | null
  effectivity_source_url?: string | null
  exclusion_reason_codes?: string[]
  approved?: false
  runtime_eligible?: false
}

export interface FormLegalReviewPreview {
  legal_as_of: string
  preview_fingerprint: string
  attestation_id: string
  reviewer_id?: string
  automated_approval?: boolean
  summary: {
    total_forms: number
    active_catalog_forms?: number
    eligible_forms: number
    excluded_forms: number
    already_approved_forms: number
    excluded_no_official_forms?: number
  }
  reason_counts: Record<string, number>
  catalog_exclusion_reason_counts?: Record<string, number>
  eligible_items: FormLegalReviewPreviewItem[]
  excluded_items: FormLegalReviewPreviewItem[]
  catalog_excluded_items?: FormLegalReviewPreviewItem[]
}

export interface FormLegalReviewAttestationResult {
  status: 'applied' | 'already_applied'
  attestation_id: string
  reviewer_id: string
  decision: 'approved' | 'rejected'
  item_count: number
  automated_approval: false
  campaign_status?: string
  release_gate?: {
    status: string
    stage?: string | null
    launch_status?: string | null
    feature_flag_enabled: false
  }
}

export interface FormCompletionCampaignStatus {
  schema_version: 'form-completion-campaign-v1'
  run_id: string
  generated_at: string
  legal_as_of: string
  status:
    | 'not_started'
    | 'queued'
    | 'running'
    | 'waiting_for_human_attestation'
    | 'completed_fail_closed'
    | 'failed_fail_closed'
  stage: string
  counts: Record<string, number>
  reason_counts: Record<string, number>
  automated_approval: false
  human_attestation_required: true
  launch_status?: 'started' | 'already_running'
}

export interface FormResolutionCampaignStatus {
  schema_version: 'form-resolution-campaign-v1'
  run_id: string
  generated_at?: string
  legal_as_of?: string
  status:
    | 'not_started'
    | 'queued'
    | 'running'
    | 'READY_FOR_HUMAN_ATTESTATION'
    | 'VERIFIED_DATA_GAP'
    | 'BLOCKED_EXTERNAL'
    | 'BLOCKED_RELEASE'
    | 'failed_fail_closed'
  stage: string
  counts: Record<string, number>
  reason_counts: Record<string, number>
  automated_approval: false
  human_attestation_required: true
  feature_flag_enabled: false
  launch_status?: 'started' | 'resumed' | 'already_running' | 'already_available'
  manifest_sha256?: string
  source_snapshot_sha256?: string
  current_manifest_sha256?: string | null
  current_source_snapshot_sha256?: string | null
  manifest_drift?: boolean
  source_snapshot_drift?: boolean
}

export interface FormResolutionCampaignShortlist {
  run_id: string
  legal_as_of: string
  preview_fingerprint: string
  attestation_id: string
  batch_id: string
  batch_count: number
  identity_count: number
  canonical_form_count: number
  procedure_binding_count: number
  invalid_record_count: number
  records: FormLegalReviewPreviewItem[]
  feature_flag_enabled: false
  automated_approval: false
  human_attestation_required: true
  manifest_sha256: string
  source_snapshot_sha256: string
}

export interface FormPostAttestationGateStatus {
  schema_version: 'form-post-attestation-gate-v1'
  status: 'not_started' | 'queued' | 'running' | 'PASS' | 'BLOCKED_RELEASE'
  stage: string
  legal_as_of?: string
  attestation_ref?: string
  passed: number
  failed: number
  blocked: number
  checks: Array<{
    name: string
    status: 'PASS' | 'FAIL' | 'BLOCKED'
    reason_code?: string
    duration_seconds: number
  }>
  feature_flag_enabled: false
  launch_status?: 'started' | 'already_running'
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

export type LegalValidityHealth = 'healthy' | 'degraded' | 'stale' | 'missing'
export type LegalValidityDecisionAction =
  | 'confirm_mapping'
  | 'reject_match'
  | 'request_recheck'
  | 'mark_historical'
  | 'quarantine'
  | 'replace_with_candidate'

export interface LegalValiditySyncStatus {
  mode: 'observe' | 'protect' | 'strict'
  status: LegalValidityHealth
  generated_at: string | null
  last_success_at: string | null
  next_run_at: string | null
  coverage: { eligible: number; observed: number; fresh: number }
  counts: { active: number; blocked: number; warning: number; open_events: number }
  sources: Array<{
    source_kind: string
    status: 'healthy' | 'degraded'
    last_success_at?: string | null
    reason_code?: string | null
  }>
  reason_code?: string | null
  age_seconds?: number | null
  failure_phase?: string | null
  error_code?: string | null
  error_message?: string | null
  release_id?: string | null
  manifest_sha256?: string | null
}

export interface LegalValidityEvent {
  id: string
  document_id?: string | null
  document_title?: string | null
  law_number: string
  issuing_agency?: string | null
  issued_date?: string | null
  scope?: string | null
  event_type?: string | null
  severity: 'low' | 'medium' | 'high' | 'critical'
  review_status: string
  serving_action?: string | null
  source_url?: string | null
  raw_status?: string | null
  normalized_status?: string | null
  effective_from?: string | null
  effective_to?: string | null
  affected_provisions?: Array<{ article: string; clause?: string | null; point?: string | null }>
  created_at?: string | null
  updated_at?: string | null
}

export interface LegalValidityEventPage {
  items: LegalValidityEvent[]
  next_cursor: string | null
}

export interface LegalReplacementCandidate {
  law_number: string
  confidence: 'verified' | 'high' | 'possible' | 'unclear' | 'conflict'
  evidence_level: string
  relation_status: 'pending_admin_review'
  source_url: string
  source_kind: string
  observed_at: string
  basis: string
}

export interface LegalReplacementDiscovery {
  status: 'candidates_found' | 'no_explicit_candidate'
  requires_admin_review: boolean
  candidates: LegalReplacementCandidate[]
  reason_codes: string[]
}

export interface LegalVectorCleanupManifest {
  job_id: string
  state: 'blocking_applied' | 'vector_cleanup_running' | 'vector_cleanup_completed' | 'vector_cleanup_partial' | 'vector_cleanup_failed'
  blocking_applied: boolean
  document_id: string
  law_number: string
  document_title?: string
  expected_vector_count: number
  dry_run?: boolean
  already_absent?: boolean
  collections?: Record<string, { before: number; after: number; deleted: number; error_code?: string }>
}

export interface LegalValidityDocumentTimeline {
  document_id: string
  law_number?: string | null
  document_title?: string | null
  observations: Array<Record<string, unknown>>
  events: LegalValidityEvent[]
  decisions: Array<Record<string, unknown>>
  replacement_discovery: LegalReplacementDiscovery
}

export interface LegalValiditySyncClient {
  validityStatus(): Promise<LegalValiditySyncStatus>
  validityEvents(params?: {
    review_status?: string
    severity?: string
    scope?: string
    expired_within_days?: number
    current_snapshot_only?: boolean
    limit?: number
    cursor?: string
  }): Promise<LegalValidityEventPage>
  decideValidityEvent(
    eventId: string,
    payload: { action: LegalValidityDecisionAction; reason: string },
  ): Promise<{ event: Partial<LegalValidityEvent> | null; decision: Record<string, unknown>; operation?: Record<string, unknown> | null }>
  validityDocument(documentId: string): Promise<LegalValidityDocumentTimeline>
  previewValidityVectorCleanup(documentId: string): Promise<LegalVectorCleanupManifest>
  cleanupValidityVectors(documentId: string, payload: { reason: string }): Promise<LegalVectorCleanupManifest>
  extractFile?(file: File, extractor?: LegalImportExtractor): Promise<{ filename: string; characters: number; content: string }>
  createReplacementWorkflow?(documentId: string, payload: { event_id: string; source_url: string; reason: string; uploaded_content?: string; uploaded_filename?: string }): Promise<{ status: string; workflow_id?: string; replacement?: Record<string, unknown>; old_document?: Record<string, unknown>; message?: string }>
}

export const legalImportApi = {
  async formProcedureCandidates(params: {
    q?: string
    domain?: string
    organization_unit_id?: string
    limit?: number
  } = {}): Promise<{
    items: FormProcedureCandidateV18[]
    total: number
    source: 'active_release' | 'read_only_compatibility'
    read_only: boolean
  }> {
    const response = await apiClient.get('/procedures/forms-catalog/procedure-candidates', { params })
    return response.data
  },

  async formSourceProposalMetadata(): Promise<FormSourceProposalMetadataV18> {
    const response = await apiClient.get<FormSourceProposalMetadataV18>('/procedures/forms-catalog/source-proposal-metadata')
    return response.data
  },

  async submitFormGovernanceCase(payload: {
    procedure_id: string
    domain: string
    title: string
    source_url: string
    source_checksum?: string | null
    asset_kind?: 'file' | 'eform'
    note?: string
    page_number?: number | null
  }): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>('/procedures/forms-catalog/review-cases', payload)
    return response.data
  },

  async myFormGovernanceCases(status?: FormWorkflowStatus): Promise<FormReviewCaseV17[]> {
    const response = await apiClient.get<FormReviewCaseV17[]>('/procedures/forms-catalog/review-cases/mine', { params: { status } })
    return response.data
  },

  async supplementFormGovernanceCase(caseId: string, payload: {
    procedure_id: string
    domain: string
    title: string
    source_url: string
    source_checksum?: string | null
    asset_kind?: 'file' | 'eform'
    note?: string
  }): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/supplement`, payload)
    return response.data
  },

  async formGovernanceCases(status?: FormWorkflowStatus): Promise<FormReviewCaseV17[]> {
    const response = await apiClient.get<FormReviewCaseV17[]>('/procedures/forms-catalog/review-cases', { params: { status } })
    return response.data
  },

  async formManagementCatalog(): Promise<FormManagementCatalog> {
    return (await apiClient.get<FormManagementCatalog>('/procedures/forms-catalog/management-catalog')).data
  },

  async editFormDraft(item: FormReviewCaseV17, submission: FormReviewCaseV17['current_submission'] & { title: string; procedure_id: string; domain: string }): Promise<FormReviewCaseV17> {
    return (await apiClient.put<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(item.case_id)}/draft`, { version: item.version, submission })).data
  },

  async deleteFormDraft(item: FormReviewCaseV17, reason: string): Promise<FormReviewCaseV17> {
    return (await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(item.case_id)}/delete-draft`, { version: item.version, reason })).data
  },

  async replaceManagedForm(formId: string, procedureId: string): Promise<FormReviewCaseV17> {
    return (await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/managed-assets/${encodeURIComponent(formId)}/replace`, { procedure_id: procedureId })).data
  },

  async formCaseHistory(caseId: string): Promise<FormManagementEvent[]> {
    return (await apiClient.get<FormManagementEvent[]>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/history`)).data
  },

  async formAssetHistory(formId: string): Promise<FormManagementEvent[]> {
    return (await apiClient.get<FormManagementEvent[]>(`/procedures/forms-catalog/managed-assets/${encodeURIComponent(formId)}/history`)).data
  },

  async withdrawFormPreview(formId: string, reason: string): Promise<FormReleaseV17> {
    return (await apiClient.post<FormReleaseV17>(`/procedures/forms-catalog/managed-assets/${encodeURIComponent(formId)}/withdraw-preview`, { reason })).data
  },

  async testFormDownload(caseId: string): Promise<Blob> {
    return (await apiClient.get<Blob>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/test-download`, { responseType: 'blob' })).data
  },

  async formGovernanceCoverage(): Promise<FormCoverageV17> {
    const response = await apiClient.get<FormCoverageV17>('/procedures/forms-catalog/coverage')
    return response.data
  },

  async requestFormSupplement(caseId: string, reason: string): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/request-supplement`, { reason })
    return response.data
  },

  async approveFormSource(caseId: string): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/approve-source`)
    return response.data
  },

  async rejectFormSource(caseId: string, reason: string): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/reject-source`, { reason })
    return response.data
  },

  async updateFormLegalMetadata(caseId: string, payload: Record<string, unknown>): Promise<FormReviewCaseV17> {
    const response = await apiClient.put<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/legal-metadata`, payload)
    return response.data
  },

  async readyFormForAttestation(caseId: string): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/ready-for-attestation`)
    return response.data
  },

  async reopenFormForCorrection(caseId: string): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/reopen-for-correction`)
    return response.data
  },

  async formAttestationPreview(caseId: string): Promise<FormAttestationPreviewV17> {
    const response = await apiClient.get<FormAttestationPreviewV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/attestation-preview`)
    return response.data
  },

  async attestFormCase(caseId: string, fingerprint: string): Promise<FormReviewCaseV17> {
    const response = await apiClient.post<FormReviewCaseV17>(`/procedures/forms-catalog/review-cases/${encodeURIComponent(caseId)}/attest`, { fingerprint })
    return response.data
  },

  async previewFormRelease(caseIds: string[], legalAsOf: string): Promise<FormReleaseV17> {
    const response = await apiClient.post<FormReleaseV17>('/procedures/forms-catalog/releases/preview', {
      case_ids: caseIds,
      legal_as_of: legalAsOf,
    })
    return response.data
  },

  async validateFormRelease(releaseId: string): Promise<FormReleaseV17> {
    const response = await apiClient.post<FormReleaseV17>(`/procedures/forms-catalog/releases/${encodeURIComponent(releaseId)}/validate`)
    return response.data
  },

  async activateFormRelease(releaseId: string): Promise<Record<string, unknown>> {
    const response = await apiClient.post<Record<string, unknown>>(`/procedures/forms-catalog/releases/${encodeURIComponent(releaseId)}/activate`)
    return response.data
  },

  async activeFormRelease(): Promise<Pick<FormReleaseV17, 'release_id' | 'version' | 'legal_as_of' | 'manifest_sha256' | 'status'>> {
    const response = await apiClient.get<Pick<FormReleaseV17, 'release_id' | 'version' | 'legal_as_of' | 'manifest_sha256' | 'status'>>('/procedures/forms-catalog/releases/active')
    return response.data
  },
  async pendingFormRelease(): Promise<FormReleaseV17 | null> {
    try {
      const response = await apiClient.get<FormReleaseV17>('/procedures/forms-catalog/releases/pending')
      return response.data
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status
      if (status === 404) return null
      throw error
    }
  },
  async validityStatus(): Promise<LegalValiditySyncStatus> {
    const response = await apiClient.get<LegalValiditySyncStatus>('/legal/validity/status')
    return response.data
  },

  async validityEvents(params: {
    review_status?: string
    severity?: string
    scope?: string
    expired_within_days?: number
    current_snapshot_only?: boolean
    limit?: number
    cursor?: string
  } = {}): Promise<LegalValidityEventPage> {
    const response = await apiClient.get<LegalValidityEventPage>('/legal/validity/events', { params })
    return response.data
  },

  async decideValidityEvent(
    eventId: string,
    payload: { action: LegalValidityDecisionAction; reason: string },
  ): Promise<{ event: Partial<LegalValidityEvent> | null; decision: Record<string, unknown>; operation?: Record<string, unknown> | null }> {
    const response = await apiClient.post<{
      event: Partial<LegalValidityEvent> | null
      decision: Record<string, unknown>
      operation?: Record<string, unknown> | null
    }>(`/legal/validity/events/${encodeURIComponent(eventId)}/decision`, payload)
    return response.data
  },

  async validityDocument(documentId: string): Promise<LegalValidityDocumentTimeline> {
    const response = await apiClient.get<LegalValidityDocumentTimeline>(
      `/legal/validity/documents/${encodeURIComponent(documentId)}`,
    )
    return response.data
  },

  async previewValidityVectorCleanup(documentId: string): Promise<LegalVectorCleanupManifest> {
    const response = await apiClient.get<LegalVectorCleanupManifest>(
      `/legal/validity/documents/${encodeURIComponent(documentId)}/vector-cleanup/preview`,
    )
    return response.data
  },

  async cleanupValidityVectors(
    documentId: string,
    payload: { reason: string },
  ): Promise<LegalVectorCleanupManifest> {
    const response = await apiClient.post<LegalVectorCleanupManifest>(
      `/legal/validity/documents/${encodeURIComponent(documentId)}/vector-cleanup`,
      payload,
    )
    return response.data
  },

  async createReplacementWorkflow(
    documentId: string,
    payload: { event_id: string; source_url: string; reason: string; uploaded_content?: string; uploaded_filename?: string },
  ): Promise<{ status: string; workflow_id?: string; replacement?: Record<string, unknown>; old_document?: Record<string, unknown>; message?: string }> {
    const response = await apiClient.post<{ status: string; workflow_id?: string; replacement?: Record<string, unknown>; old_document?: Record<string, unknown>; message?: string }>(
      `/legal/validity/documents/${encodeURIComponent(documentId)}/replacement-workflow`,
      payload,
      { headers: { 'Idempotency-Key': `replacement-${documentId}-${payload.source_url}-${payload.uploaded_filename || ''}-${(payload.uploaded_content || '').length}` } },
    )
    return response.data
  },

  async importReadiness(timeout = 1800): Promise<LegalImportReadiness> {
    const baseUrl = (await getApiUrl()).replace(/\/$/, '')
    // The normal local deployment uses the Next.js `/api` rewrite. Readiness
    // is mounted at the FastAPI root, so keep that root route when an explicit
    // API origin exists and tunnel it through the rewrite for a relative URL.
    const readinessUrl = baseUrl ? `${baseUrl}/ready/import` : '/api/ready/import'
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), timeout)
    let response: Response
    try {
      response = await fetch(readinessUrl, {
        cache: 'no-store',
        credentials: 'include',
        signal: controller.signal,
      })
    } finally {
      window.clearTimeout(timer)
    }
    const payload = await response.json().catch(() => null)
    if (!payload || typeof payload !== 'object' || !('status' in payload)) {
      throw new Error('Không nhận được trạng thái pipeline nhập kho.')
    }
    return payload as LegalImportReadiness
  },

  async fields(timeout = 1800): Promise<LegalField[]> {
    const response = await apiClient.get<{ fields: LegalField[] }>('/legal/import/fields', { timeout })
    return response.data.fields
  },

  async preview(payload: LegalImportPayload): Promise<ImportPreview> {
    const response = await apiClient.post<ImportPreview>('/legal/import/preview', payload)
    return response.data
  },

  async importDocument(payload: LegalImportPayload, file?: File | null) {
    if (file) {
      const data = new FormData()
      data.append('metadata_json', JSON.stringify(payload))
      data.append('file', file)
      return (await apiClient.post('/legal/import/with-file', data)).data
    }
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
    file_fingerprint?: string
    total_pages?: number
    processed_pages?: number
    page_count?: number
    complete?: boolean
    truncated?: boolean
    failed_pages?: number[]
    pages_without_text?: number[]
    table_count?: number
    table_extracted_count?: number
    native_text_pages?: number[]
    ocr_requested_pages?: number[]
    ocr_pages?: number[]
    page_extractors?: Record<string, string>
    coverage_percent?: number
    ocr_status?: string
    reason?: string
    deferred_ocr?: boolean
    cache_hit?: boolean
    file_id?: string
    extraction_job_id?: string
    extraction_status?: 'processing' | 'complete' | 'partial' | 'error'
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
    law_number?: string | null
    document_type?: string | null
    issuing_agency?: string | null
    issued_date?: string | null
    effective_date?: string | null
    expired_date?: string | null
    scope?: 'central' | 'haiphong' | 'local'
    matched_domains?: string[]
  }> {
    const response = await apiClient.post('/legal/crawl/preview', { url }, { timeout: 120_000 })
    return response.data
  },

  async crawlSummary(timeout = 1800): Promise<LegalCrawlSummary> {
    const response = await apiClient.get<LegalCrawlSummary>('/legal/crawl/summary', { timeout })
    return response.data
  },

  async crawlSources(timeout = 1800): Promise<LegalCrawlSource[]> {
    const response = await apiClient.get<{ sources: LegalCrawlSource[] }>('/legal/crawl/sources', { timeout })
    return response.data.sources
  },

  async updateCrawlSource(sourceId: string, payload: Partial<Pick<LegalCrawlSource, 'name' | 'base_url' | 'sitemap_scope' | 'enabled' | 'interval_minutes' | 'lookback_days' | 'max_documents_per_run' | 'max_listing_pages_per_run' | 'rate_limit_seconds' | 'filter_keyword' | 'website_type' | 'link_selector' | 'next_page_selector' | 'include_patterns' | 'exclude_patterns' | 'content_fetch_allowed' | 'compatibility_mode'>>) {
    const response = await apiClient.patch<{ source: LegalCrawlSource }>(`/legal/crawl/sources/${sourceId}`, payload)
    return response.data.source
  },

  async createCrawlSource(payload: CrawlSourceCreatePayload) {
    const response = await apiClient.post<{ source: LegalCrawlSource }>('/legal/crawl/sources', payload)
    return response.data.source
  },

  async previewCrawlSource(payload: CrawlSourceCreatePayload): Promise<CrawlSourcePreview> {
    const response = await apiClient.post<CrawlSourcePreview>('/legal/crawl/sources/preview', payload, {
      timeout: 60_000,
    })
    return response.data
  },

  async extractionJob(jobId: string): Promise<{
    job_id: string
    file_id: string
    extraction_status: 'processing' | 'complete' | 'partial' | 'error'
    extracted_text?: string
    char_count?: number
    error?: string | null
    warnings?: string[]
    complete?: boolean
    total_pages?: number
    processed_pages?: number
    page_count?: number
    coverage_percent?: number
    pages_without_text?: number[]
    failed_pages?: number[]
    native_text_pages?: number[]
    ocr_pages?: number[]
    page_extractors?: Record<string, string>
    table_count?: number
    table_extracted_count?: number
    extractor_used?: string
    extractor_version?: string
  }> {
    const response = await apiClient.get(`/media/extraction-jobs/${encodeURIComponent(jobId)}`)
    return response.data
  },

  async deleteCrawlSource(sourceId: string) {
    const response = await apiClient.delete<{ id: string; deleted: boolean; already_deleted?: boolean; name?: string }>(`/legal/crawl/sources/${encodeURIComponent(sourceId)}`)
    return response.data
  },

  async scanNow(sourceId?: string): Promise<LegalCrawlScanResult> {
    const response = await apiClient.post<LegalCrawlScanResult>('/legal/crawl/scan', null, {
      params: sourceId ? { source_id: sourceId } : undefined,
      timeout: 180_000,
    })
    return response.data
  },

  async crawlCandidates(
    status = 'pending_review',
    limit = 100,
    timeout = 1800,
    sourceType = 'all',
  ): Promise<LegalCrawlCandidate[]> {
    const response = await apiClient.get<{ candidates: LegalCrawlCandidate[] }>('/legal/crawl/candidates', {
      params: {
        ...(status && status !== 'all' ? { status } : {}),
        ...(sourceType && sourceType !== 'all' ? { source_type: sourceType } : {}),
        limit,
      },
      timeout,
    })
    return response.data.candidates
  },

  async crawlCandidatePage(status = 'all', limit = 20, offset = 0, sourceType = 'all', origin = 'all', timeout = 1800) {
    const response = await apiClient.get<{ candidates: LegalCrawlCandidate[]; total: number; limit: number; offset: number }>('/legal/crawl/candidates', {
      params: { status, source_type: sourceType, origin, limit, offset },
      timeout,
    })
    return response.data
  },

  async reviewCandidate(candidateId: string, decision: 'approved' | 'rejected' | 'changes_requested', review_note = '') {
    const response = await apiClient.post<{ candidate: LegalCrawlCandidate }>(`/legal/crawl/candidates/${candidateId}/review`, {
      decision,
      review_note,
    })
    return response.data.candidate
  },

  async resolveDuplicate(candidateId: string, payload: LegalDuplicateResolutionRequest) {
    const response = await apiClient.post<{
      candidate: LegalCrawlCandidate
      resolution: LegalDuplicateResolution
      idempotent: boolean
    }>(`/legal/crawl/candidates/${encodeURIComponent(candidateId)}/duplicate-resolution`, payload, { timeout: 12_000 })
    return response.data
  },

  async updateCandidateMetadata(candidateId: string, payload: LegalCandidateMetadataUpdate) {
    const response = await apiClient.patch<{
      candidate: LegalCrawlCandidate
      validation_errors: string[]
    }>(`/legal/crawl/candidates/${candidateId}/metadata`, payload)
    return response.data
  },

  async importCandidate(candidateId: string) {
    const response = await apiClient.post<{
      job?: Record<string, unknown>
      candidate?: LegalCrawlCandidate
      status: 'queued' | 'duplicate_conflict'
    }>(
      `/legal/crawl/candidates/${candidateId}/import`,
    )
    return response.data
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

  async formsCatalogStatus(): Promise<LegalFormsCatalogStatus> {
    const response = await apiClient.get<LegalFormsCatalogStatus>('/procedures/forms-catalog/status')
    return response.data
  },

  async formLegalReviewPreview(legalAsOf: string): Promise<FormLegalReviewPreview> {
    const response = await apiClient.get<FormLegalReviewPreview>(
      '/procedures/forms-catalog/legal-review-attestations/preview',
      { params: { legal_as_of: legalAsOf } },
    )
    return response.data
  },

  async formCompletionCampaignStatus(): Promise<FormCompletionCampaignStatus> {
    const response = await apiClient.get<FormCompletionCampaignStatus>(
      '/procedures/forms-catalog/completion-campaign',
    )
    return response.data
  },

  async formResolutionCampaignStatus(): Promise<FormResolutionCampaignStatus> {
    const response = await apiClient.get<FormResolutionCampaignStatus>(
      '/procedures/forms-catalog/form-resolution/current',
    )
    return response.data
  },

  async runFormResolutionCampaign(
    legalAsOf: string,
    manifestSha256: string,
    sourceSnapshotSha256: string,
  ): Promise<FormResolutionCampaignStatus> {
    const response = await apiClient.post<FormResolutionCampaignStatus>(
      '/procedures/forms-catalog/form-resolution/run',
      null,
      {
        params: {
          legal_as_of: legalAsOf,
          manifest_sha256: manifestSha256,
          source_snapshot_sha256: sourceSnapshotSha256,
        },
      },
    )
    return response.data
  },

  async formResolutionCampaignGaps(runId: string) {
    const response = await apiClient.get<{
      run_id: string
      records: Array<{
        occurrence_id: string
        procedure_id: string | null
        reason_code: string
        source_attempt_count: number
      }>
      feature_flag_enabled: false
      automated_approval: false
    }>(`/procedures/forms-catalog/form-resolution/${encodeURIComponent(runId)}/gaps`)
    return response.data
  },

  async formResolutionCampaignShortlist(
    runId: string,
  ): Promise<FormResolutionCampaignShortlist> {
    const response = await apiClient.get<FormResolutionCampaignShortlist>(
      `/procedures/forms-catalog/form-resolution/${encodeURIComponent(runId)}/review-shortlist`,
    )
    return response.data
  },

  async attestFormResolutionCampaign(
    shortlist: FormResolutionCampaignShortlist,
    reviewNote: string,
    reviewedAt: string,
  ): Promise<FormLegalReviewAttestationResult> {
    const response = await apiClient.post<FormLegalReviewAttestationResult>(
      `/procedures/forms-catalog/form-resolution/${encodeURIComponent(shortlist.run_id)}/attest`,
      {
        attestation_id: shortlist.attestation_id,
        reviewed_at: reviewedAt,
        legal_as_of: shortlist.legal_as_of || reviewedAt.slice(0, 10),
        preview_fingerprint: shortlist.preview_fingerprint,
        batch_id: shortlist.batch_id,
        manifest_sha256: shortlist.manifest_sha256,
        source_snapshot_sha256: shortlist.source_snapshot_sha256,
        decision: 'approved',
        review_note: reviewNote,
        items: shortlist.records.map((item) => ({
          candidate_id: item.candidate_id,
          canonical_form_id: item.canonical_form_id,
          procedure_id: item.procedure_id,
          effective_from: item.effective_from,
          effective_to: item.effective_to,
          jurisdiction: item.jurisdiction || 'Hai Phong',
          administrative_level: item.administrative_level || 'commune',
        })),
      },
    )
    return response.data
  },

  async runFormCompletionCampaign(legalAsOf: string): Promise<FormCompletionCampaignStatus> {
    const response = await apiClient.post<FormCompletionCampaignStatus>(
      '/procedures/forms-catalog/completion-campaign/run',
      null,
      { params: { legal_as_of: legalAsOf } },
    )
    return response.data
  },

  async runPostAttestationReleaseGates(
    legalAsOf: string,
    attestationId: string,
  ): Promise<FormPostAttestationGateStatus> {
    const response = await apiClient.post<FormPostAttestationGateStatus>(
      '/procedures/forms-catalog/post-attestation-release-gates/run',
      null,
      {
        params: {
          legal_as_of: legalAsOf,
          attestation_id: attestationId,
        },
      },
    )
    return response.data
  },

  async attestLegalForms(
    preview: FormLegalReviewPreview,
    reviewNote: string,
    reviewedAt: string,
  ): Promise<FormLegalReviewAttestationResult> {
    const response = await apiClient.post<FormLegalReviewAttestationResult>(
      '/procedures/forms-catalog/legal-review-attestations',
      {
        attestation_id: preview.attestation_id,
        reviewed_at: reviewedAt,
        legal_as_of: preview.legal_as_of,
        preview_fingerprint: preview.preview_fingerprint,
        decision: 'approved',
        review_note: reviewNote,
        items: preview.eligible_items.map((item) => ({
          candidate_id: item.candidate_id,
          canonical_form_id: item.canonical_form_id,
          procedure_id: item.procedure_id,
          effective_from: item.effective_from,
          effective_to: item.effective_to,
          jurisdiction: item.jurisdiction || 'Hai Phong',
          administrative_level: item.administrative_level || 'commune',
        })),
      },
    )
    return response.data
  },

  async reviewForm(
    formId: string,
    decision: 'approved' | 'rejected',
    reviewNote = '',
    extras: Partial<Omit<LegalFormReviewPayload, 'decision' | 'review_note'>> = {},
  ) {
    // This endpoint records queue triage only. Canonical legal attestation is
    // the sole path that may publish a form to the runtime catalog.
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

  async formsCatalogCandidates(
    domain?: string,
    reviewStatus = 'candidate_pending_review',
    offset = 0,
    limit = 50,
  ): Promise<LegalFormCandidatePage> {
    // Admin triage queue. Candidates never go to citizen/officer surfaces.
    const response = await apiClient.get<LegalFormCandidatePage>('/procedures/forms-catalog/candidates-full', {
      params: { domain, review_status: reviewStatus, offset, limit },
    })
    return {
      ...response.data,
      summary: response.data.summary || { filtered_total: 0, returned: 0, offset, limit },
      records: (response.data.records || []).map((item) => ({
        ...item,
        title: item.title || item.form_title || item.detected_form_name || item.file_name || item.id,
        domain: item.domain || item.suggested_domain || null,
        procedure_id: item.procedure_id || item.suggested_procedure_id || null,
        source_url: item.source_url || item.page_url || null,
        review_note: item.review_note || item.reason || null,
      })),
    }
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

}
