import { apiClient } from './client'

export type AvailabilityStatus = 'available' | 'degraded' | 'unavailable'

export interface AvailabilitySection {
  status: AvailabilityStatus
  observed_at?: string
  reason_code?: string | null
  message?: string | null
  [key: string]: unknown
}

export interface LegalValidityProjection {
  status: string
  serving_action: string
  verified_at?: string | null
  source_url?: string | null
  effective_from?: string | null
  effective_to?: string | null
  warning_code?: string | null
  reason_code?: string | null
  would_block: boolean
  current_answer_eligible: boolean
  historical_lookup_allowed: boolean
  display_label: string
}

export interface LegalManagementSummary {
  observed_at: string
  as_of: string
  documents: {
    total: number
    active: number
    inactive: number
    not_yet_effective?: number
    expired_by_date?: number
    missing_source?: number
    missing_metadata?: number
  }
  structure: { articles: number; chunks: number }
  tiers: { core: number; expanded: number }
  validity: AvailabilitySection & {
    counts?: Record<string, number>
    coverage?: Record<string, number>
  }
  operations: AvailabilitySection & {
    pending_document_candidates?: number
    import_queue?: Record<string, number>
  }
  vectors: AvailabilitySection & {
    database_chunks?: number
    collections?: Record<string, number | null>
  }
  faq_impacts: AvailabilitySection
  serving_release?: {
    release_id?: string
    legal_as_of?: string
    manifest_sha256?: string
    current_collection?: string
    temporal_collection?: string
    exact_lexical_index?: string
    state_counts?: Record<string, number>
    cards?: {
      total_retrievable?: number | null
      current_effective?: number | null
      expired_total?: number | null
      unknown_total?: number | null
      not_yet_effective_total?: number | null
      expiring_30?: number | null
      effective_30?: number | null
    }
  }
}

export interface ManagedLegalDocument {
  doc_id: string | number
  document_title: string
  law_number?: string | null
  document_type?: string | null
  issuing_agency?: string | null
  scope?: string | null
  sector?: string | null
  stored_status?: string | null
  as_of_status?: string | null
  validity_status?: string | null
  validity_sync?: LegalValidityProjection | null
  serving_status?: string | null
  current_answer_eligible?: boolean
  historical_lookup_allowed?: boolean
  search_included?: boolean
  search_reason?: string | null
  state_revision?: string | null
  metadata_revision?: string | null
  metadata_editable?: boolean
  serving_state?: 'current_retrievable' | 'historical_only' | 'quarantined' | 'future_effective' | string
  issued_date?: string | null
  effective_date?: string | null
  expired_date?: string | null
  source_url?: string | null
  version?: string | null
  field_id?: number | null
  field_name?: string | null
  domain?: string | null
  domain_name?: string | null
  retrieval_tier?: 'core' | 'expanded'
  article_count: number
  chunk_count: number
  quality_flags: string[]
  primary_organization_unit_id?: string | null
  organization_unit_ids?: string[]
  organization_assignment_state?: 'assigned' | 'shared' | 'unassigned' | string
  organization_assignment_status?: 'confirmed' | 'needs_confirmation' | string
  organization_assignment_fingerprint?: string | null
  created_at?: string | null
  source_id?: string | null
  collection_source?: string | null
  gazette_date?: string | null
  signer_title?: string | null
  signer_name?: string | null
  applicability_info?: string | null
}

export interface LegalManagementMetadataPayload {
  issued_date?: string | null
  effective_date?: string | null
  expired_date?: string | null
  source_url?: string | null
  gazette_date?: string | null
  signer_title?: string | null
  signer_name?: string | null
  applicability_info?: string | null
  version?: string | null
  reason: string
  expected_revision: string
  confirm_validity: boolean
}

export interface LegalManagementListResponse {
  items: ManagedLegalDocument[]
  total: number
  limit: number
  offset: number
  as_of: string
  observed_at: string
  serving_release?: LegalManagementSummary['serving_release']
  organization_units_available?: boolean
}

export interface LegalManagementListParams {
  q?: string
  document_type?: string
  issuing_agency?: string
  scope?: string
  domain?: string
  organization_unit_id?: string
  organization_responsibility?: 'all' | 'primary' | 'support'
  include_organization_units?: boolean
  stored_status?: string
  validity_status?: 'active' | 'not_yet_effective' | 'expired' | 'unknown'
  include_expired_history?: boolean
  tier?: 'all' | 'core' | 'expanded'
  data_quality?: 'missing_source' | 'missing_metadata' | 'zero_chunks' | 'unknown_status' | 'unclassified'
  source_presence?: 'all' | 'present' | 'missing'
  issued_from?: string
  issued_to?: string
  effective_from?: string
  effective_to?: string
  expired_from?: string
  expired_to?: string
  as_of?: string
  limit?: number
  offset?: number
  sort_by?: 'effective_date' | 'issued_date' | 'expired_date' | 'title' | 'law_number' | 'stored_status' | 'article_count' | 'chunk_count'
  sort_order?: 'asc' | 'desc'
}

export interface MetadataRequestOptions {
  timeout?: number
}

export interface LegalManagementDetail {
  observed_at: string
  document: ManagedLegalDocument
  organization_units_available?: boolean
  structure: {
    article_count: number
    chunk_count: number
    articles: Array<Record<string, unknown>>
  }
  relationships: AvailabilitySection & { items?: Array<Record<string, unknown>> }
  validity: AvailabilitySection & {
    observations?: Array<Record<string, unknown>>
    events?: Array<Record<string, unknown>>
    decisions?: Array<Record<string, unknown>>
    replacement_discovery?: Record<string, unknown>
  }
  vectors: AvailabilitySection & {
    expected?: number
    collections?: Record<string, { present?: number; missing?: number; reason_code?: string }>
    complete_collections?: string[]
    retrieval_ready?: boolean
    current_retrieval_ready?: boolean
    historical_retrieval_ready?: boolean
    verification_source?: string
    checked_at?: string
  }
  faq_impacts: AvailabilitySection & { items?: Array<Record<string, unknown>> }
  versions: AvailabilitySection & { legacy_version?: string | null; items?: Array<Record<string, unknown>> }
  audit: AvailabilitySection & {
    items?: Array<{
      id?: string
      actor_user?: string
      actor_role?: string
      action?: string
      entity_type?: string
      entity_id?: string
      reason?: string
      created?: string
    }>
  }
}

export interface LegalManagementReplacementPayload {
  source_url?: string
  reason: string
  uploaded_content?: string
  uploaded_filename?: string
  title?: string
  law_number?: string
  document_type?: string
  issuing_agency?: string
  issued_date?: string
  effective_date?: string
  expired_date?: string
  scope?: string
  sector?: string
  applicability_info?: string
}

export interface LegalManagementReplacementWorkflow {
  workflow_id?: string
  status?: 'prepared' | 'processing' | 'activated' | 'failed' | 'pending_retry' | string
  steps?: Record<string, string>
  hard_gates?: Record<string, boolean>
  error?: string | null
  updated_at?: string
}

export interface LegalManagementReplacementResult {
  status: string
  workflow_id?: string
  document_id?: string | number
  chunk_count?: number
  vector_collection?: string
  new_document?: Record<string, unknown>
  old_document?: Record<string, unknown>
  chatbot_ready?: boolean
  audit_sync?: { status?: 'recorded' | 'pending'; error_class?: string }
  workflow_sync?: { status?: 'recorded' | 'pending'; error_class?: string }
  message?: string
}

export interface LegalManagementHardDeletePreview {
  status: 'eligible' | 'blocked'
  eligible: boolean
  reason_code?: string | null
  document_id: string
  law_number: string
  document_title: string
  state_revision: string
  release_member: boolean
  confirmation_text: string
  database: {
    articles: number
    chunks: number
    scope_rows: number
    relationships: number
    workspace_links: number
    quality_rows: number
  }
  vectors: LegalManagementDetail['vectors']
  audit_preserved: true
}

export type LifecycleBucket =
  | 'active'
  | 'future'
  | 'expiring_90'
  | 'expiring_30'
  | 'expiring_7'
  | 'expiring_1'
  | 'expired'
  | 'partially_expired'
  | 'replaced'
  | 'repealed'
  | 'suspended'
  | 'corrected'
  | 'consolidated'
  | 'unknown'

export interface Feature018LifecycleSummary {
  legal_as_of: string
  counts: Record<LifecycleBucket, number>
  alerts: Array<{
    document_id: string
    bucket: LifecycleBucket
    threshold_days: number
    effective_to?: string | null
  }>
  observed_at: string
}

export interface Feature018LifecycleDocument {
  document_id: string
  legal_as_of: string
  bucket: LifecycleBucket
  vector_state: string
  effective_from?: string | null
  effective_to?: string | null
  current_serving_allowed: boolean
  effective_provisions: string[]
  inactive_provisions: string[]
  applied_event_ids: string[]
  ignored_event_ids: string[]
}

export interface Feature018VectorManifest {
  schema_version: string
  generated_at?: string
  active_collection: string
  active_pointer_unchanged: boolean
  gate_passed: boolean
  counts: Record<string, number>
  expected_fingerprints: Record<string, string>
  manifest_fingerprint: string
  inventory_sha256: string
  reason_codes: string[]
  source_warnings: string[]
  read_only: true
  vectors_mutated: false
  corpus_mutated: false
}

export interface ChangeEventCandidateInput {
  document_id: string
  event_type: string
  effective_from: string
  effective_to?: string
  source_url: string
  scope: 'whole_document' | 'provisions'
  provisions: string[]
  provenance: Record<string, unknown>
  reason: string
}

export interface ChangeEventCandidate extends ChangeEventCandidateInput {
  id: string
  status: 'candidate'
  evidence_fingerprint: string
}

export interface LifecycleTimeline {
  document_id: string
  events: Array<{
    id: string
    event_type: string
    effective_from?: string | null
    effective_to?: string | null
    source_url?: string | null
    status: string
    scope: string
    provisions: string[]
  }>
  provisions: Array<{
    provision_identity?: string
    article_identity?: string
    effective_from?: string | null
    effective_to?: string | null
    status: string
  }>
  vector_state: string
}

export interface LegalImpactCaseProjection {
  id: string
  change_event_id: string
  dependent_type: string
  dependent_id: string
  detected_reason: string
  status: string
  evidence_sha256: string
}

export const legalManagementApi = {
  async summary(options?: MetadataRequestOptions): Promise<LegalManagementSummary> {
    const response = await apiClient.get<LegalManagementSummary>('/legal/management/summary', options)
    return response.data
  },

  async list(params: LegalManagementListParams, options?: MetadataRequestOptions): Promise<LegalManagementListResponse> {
    const response = await apiClient.get<LegalManagementListResponse>('/legal/management/documents', { params, ...options })
    return response.data
  },

  async detail(documentId: string | number): Promise<LegalManagementDetail> {
    const response = await apiClient.get<LegalManagementDetail>(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}`,
    )
    return response.data
  },

  async setOrganizationAssignment(
    documentId: string | number,
    payload: {
      assignment_state: 'assigned' | 'shared' | 'unassigned'
      primary_organization_unit_id?: string | null
      organization_unit_ids: string[]
      reason: string
      expected_fingerprint?: string | null
    },
  ): Promise<{
    document_id: string
    assignment: Pick<ManagedLegalDocument,
      'organization_assignment_state' | 'primary_organization_unit_id' | 'organization_unit_ids' | 'organization_assignment_status'>
    fingerprint: string
    projection_updated: boolean
  }> {
    const response = await apiClient.post(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}/organization-assignment`,
      payload,
      { timeout: 15_000 },
    )
    return response.data
  },

  async updateMetadata(
    documentId: string | number,
    payload: LegalManagementMetadataPayload,
  ): Promise<{
    document_id: string
    metadata_revision?: string
    projection_updated: boolean
    validity?: { status?: string; verified_at?: string } | null
    message?: string
  }> {
    const response = await apiClient.post(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}/metadata`,
      payload,
      { timeout: 45_000 },
    )
    return response.data
  },

  async replaceDocument(
    documentId: string | number,
    payload: LegalManagementReplacementPayload,
    options: { idempotencyKey?: string } = {},
  ): Promise<LegalManagementReplacementResult> {
    const response = await apiClient.post<LegalManagementReplacementResult>(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}/replacement`,
      payload,
      {
        timeout: 900_000,
        headers: options.idempotencyKey
          ? { 'Idempotency-Key': options.idempotencyKey }
          : undefined,
      },
    )
    return response.data
  },

  async replacementWorkflowByKey(
    idempotencyKey: string,
  ): Promise<LegalManagementReplacementWorkflow> {
    const response = await apiClient.get<{
      status: string
      workflow: LegalManagementReplacementWorkflow
    }>('/legal/validity/replacement-workflows/by-idempotency-key', {
      params: { key: idempotencyKey },
      timeout: 5_000,
    })
    return response.data.workflow
  },

  async setSearchState(
    documentId: string | number,
    payload: {
      action: 'exclude' | 'restore' | 'historical' | 'quarantine'
      reason: string
      expected_revision: string
    },
  ): Promise<{
    status: string
    action: string
    document_id: string
    search_included: boolean
    state_revision?: string
    chatbot_ready?: boolean
    operation_verification?: {
      status?: 'passed' | 'degraded'
      passed?: boolean
      state_authority_verified?: boolean
      vector_ready?: boolean | null
    }
    message?: string
  }> {
    const response = await apiClient.post(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}/search-state`,
      payload,
      { timeout: 30_000 },
    )
    return response.data
  },

  async previewHardDelete(
    documentId: string | number,
  ): Promise<LegalManagementHardDeletePreview> {
    const response = await apiClient.get<LegalManagementHardDeletePreview>(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}/hard-delete/preview`,
      { timeout: 30_000 },
    )
    return response.data
  },

  async hardDelete(
    documentId: string | number,
    payload: {
      reason: string
      expected_revision: string
      confirmation_text: string
    },
  ): Promise<{
    status: 'deleted'
    document_id: string
    law_number: string
    database_deleted: boolean
    vectors_deleted: boolean
    message?: string
  }> {
    const response = await apiClient.post(
      `/legal/management/documents/${encodeURIComponent(String(documentId))}/hard-delete`,
      payload,
      { timeout: 120_000 },
    )
    return response.data
  },

  async lifecycleSummary(legalAsOf: string): Promise<Feature018LifecycleSummary> {
    const response = await apiClient.get<Feature018LifecycleSummary>(
      '/legal-management/lifecycle/summary',
      { params: { legal_as_of: legalAsOf } },
    )
    return response.data
  },

  async lifecycleDocuments(params: {
    legal_as_of: string
    bucket?: LifecycleBucket
    vector_state?: string
  }): Promise<Feature018LifecycleDocument[]> {
    const response = await apiClient.get<Feature018LifecycleDocument[]>(
      '/legal-management/documents',
      { params },
    )
    return response.data
  },

  async lifecycleTimeline(documentId: string): Promise<LifecycleTimeline> {
    const response = await apiClient.get<LifecycleTimeline>(
      `/legal-management/documents/${encodeURIComponent(documentId)}/timeline`,
    )
    return response.data
  },

  async impactCases(status = 'needs_review'): Promise<LegalImpactCaseProjection[]> {
    const response = await apiClient.get<LegalImpactCaseProjection[]>(
      '/legal-management/impact-cases',
      { params: { status } },
    )
    return response.data
  },

  async activeIndexManifest(): Promise<Feature018VectorManifest> {
    const response = await apiClient.get<Feature018VectorManifest>(
      '/legal-management/index-manifests/active',
    )
    return response.data
  },

  async createChangeEventCandidate(
    input: ChangeEventCandidateInput,
  ): Promise<ChangeEventCandidate> {
    const response = await apiClient.post<ChangeEventCandidate>(
      '/legal-management/change-events/candidates',
      input,
    )
    return response.data
  },

  async confirmChangeEvent(
    eventId: string,
    input: { evidence_fingerprint: string; reason: string },
  ): Promise<{ event_id: string; status: 'confirmed'; impact_case_count: number }> {
    const response = await apiClient.post(
      `/legal-management/change-events/${encodeURIComponent(eventId)}/confirm`,
      input,
    )
    return response.data
  },

  async previewIndexJob(input: { document_id: string; provisions: string[] }) {
    const response = await apiClient.post<{
      document_id: string
      target_provisions: string[]
      mode: 'incremental' | 'full'
      state: 'preview'
      mutation_performed: false
      active_pointer_change: false
    }>('/legal-management/index-jobs/preview', input)
    return response.data
  },
}
