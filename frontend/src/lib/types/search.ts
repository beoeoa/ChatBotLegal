// Search types
export interface PublicValiditySync {
  status?: 'not_yet_effective' | 'active' | 'expired' | 'expired_partial' | 'suspended' | 'suspended_partial' | 'amended' | 'replaced' | 'repealed' | 'unknown' | string
  serving_action?: 'allow' | 'warn' | 'block' | 'historical_only' | 'block_provisions' | string
  verified_at?: string | null
  source_url?: string | null
  effective_from?: string | null
  effective_to?: string | null
  warning_code?: string | null
}

export interface LegalCitation {
  /** Request-local answer markers resolved to this verified source. */
  evidence_ids?: string[]
  chunk_id?: string
  law_number?: string
  document_title?: string
  article_number?: string
  article_title?: string
  clause_number?: string
  point_number?: string
  effective_status?: string
  source_url?: string
  internal_url?: string
  viewer_url?: string
  pdf_url?: string
  fallback_search_url?: string
  doc_id?: string | number
  label?: string
  link_status?: 'internal_indexed' | 'external_recorded' | 'missing' | 'dead'
  validity_sync?: PublicValiditySync
  authority_level?: string
  authority_label?: string
}

export interface AnswerCompleteness {
  status: 'complete' | 'incomplete' | 'not_assessed'
  coverage_ratio: number
  source_unit_count: number
  covered_unit_count: number
  required_checks: Array<'coverage' | 'order' | 'plain_language' | 'practical_meaning'>
  checks: Record<string, boolean>
  reason_codes: string[]
}

export type AnswerMode = 'normal' | 'verified_source_condensed' | 'source_view_only' | 'procedure_fast_path'
export type AnswerStatus = 'verified' | 'unverified' | 'partial' | 'cannot_verify'
export type FallbackTier = 'exact' | 'domain' | 'expanded' | 'full_corpus' | 'catalog' | 'clarification' | 'support'
export type AnswerRoute = 'exact_article' | 'procedure_form' | 'general_legal' | 'historical'
export type ConversationRoute = 'chat_meta' | 'document_followup' | 'legal_query' | 'out_of_scope'

export interface ConversationDocumentRefV1 {
  document_id: string
  title: string
  law_number?: string | null
  source_url?: string | null
  article_refs?: string[]
  pinned?: boolean
  active?: boolean
}

export interface LegalAnswerPresentationSections {
  short_answer?: string | null
  actions: string[]
  dossier: string[]
  procedure?: Record<string, unknown> | null
  recommended_forms: Array<Record<string, unknown>>
  legal_bases: LegalCitation[]
  caveats: string[]
  clarifying_questions: string[]
  unverified_explanations?: Array<Record<string, unknown>>
}

export interface UploadedDocumentContext {
  name: string
  size: number
  type: string
  file_id?: string
  sha256?: string
  status?: 'processing' | 'complete' | 'partial' | 'error'
}

export interface AskMessageAttachment {
  kind: string
  value?: UploadedDocumentContext | Record<string, unknown> | null
}

export interface AskMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  attachments?: AskMessageAttachment[] | null
  status?: 'pending' | 'complete' | 'error' | 'cancelled'
  /** True after the Ask response snapshot has been persisted to the session. */
  persisted?: boolean
  citations?: LegalCitation[]
  answer_sections?: AnswerSection[]
  recommended_forms?: Array<{
    name: string
    file_type: string
    download_url?: string | null
    official_level: string
    review_status: string
    has_official_file?: boolean
    needs_official_file?: boolean
    procedure_id?: string
    procedure_name?: string
    form_id?: string | null
    form_code?: string | null
    audience?: 'citizen' | 'officer' | 'both'
    usage?: 'applicant_form' | 'officer_internal' | 'conditional_form'
    required_or_conditional?: 'required' | 'conditional'
    condition?: string | null
    source_url?: string | null
    source_classification?: string
    effective_from?: string | null
    effective_to?: string | null
  }>
  forms_unavailable?: boolean
  faq_refs?: string[]
  faqs?: Array<{
    id: string
    question: string
    answer: string
    steps?: string[]
    form_ids?: string[]
    domain?: string
    ward_scope?: string | null
    review_status?: string
  }>
  procedure_detail?: {
    id: string
    name: string
    department: string
    domain_slug: string
    steps: string[]
    documents_required: string[]
    duration: string
    fee: string
    forms: Array<{
      name: string
      file_type: string
      download_url?: string | null
      official_level: string
      review_status: string
      has_official_file?: boolean
      needs_official_file?: boolean
      form_code?: string | null
      procedure_id?: string
      procedure_name?: string
      audience?: 'citizen' | 'officer' | 'both'
      usage?: 'applicant_form' | 'officer_internal' | 'conditional_form'
      source_url?: string | null
      effective_from?: string | null
      effective_to?: string | null
    }>
  } | null
  suggested_questions?: SuggestedQuestionV1[]
  conversation_route?: ConversationRoute | null
  active_document?: ConversationDocumentRefV1 | null
  related_documents?: ConversationDocumentRefV1[]
  memory_usage?: MemoryUsageV1 | null
  model_option_id?: string | null
  model_display_name?: string | null
  model_locked?: boolean
  generation_provenance?: Record<string, unknown> | null
  timing_summary?: AskTimingSummary | null
  /** Canonical Direct RAG timing contract; timing_summary is legacy-compatible. */
  timing?: AskTimingSummary | null
  rag_trace?: RagTrace | null
  grounding_status?: string
  answer_completeness?: AnswerCompleteness
  answer_mode?: AnswerMode
  answer_status?: AnswerStatus
  fallback_tier?: FallbackTier
  canonical_domain?: string | null
  evidence_count?: number
  coverage_warning?: string | null
  unverified_explanations?: Array<Record<string, unknown>>
  blocked_reason?: string | null
  outcome?: AnswerOutcome
  reason_code?: string | null
  retryable?: boolean
  scope?: AnswerScope | null
  persistence_degraded?: boolean
  quality?: DirectAnswerQuality | null
  presentation_version?: 'legal-answer-v1' | 'direct-rag-v1'
  answer_route?: AnswerRoute | null
  pipeline_version?: string | null
  data_release_id?: string | null
  release_id?: string | null
  index_fingerprint?: string | null
  manifest_hash?: string | null
  validity_snapshot?: string | null
  verification_label?: string | null
  historical_label?: string | null
  sections?: LegalAnswerPresentationSections | null
  created_at: string
}

export interface SearchRequest {
  query: string
  type: 'text' | 'vector'
  limit: number
  search_sources: boolean
  search_notes: boolean
  minimum_score: number
}

export interface SearchResult {
  id: string
  title: string
  parent_id: string
  final_score: number
  matches?: string[]
  relevance?: number
  similarity?: number
  score?: number
  type?: string
  source_type?: string
  created?: string
  updated?: string
  // Legal document fields (primary search results)
  law_number?: string
  document_title?: string
  article_number?: string
  article_title?: string
  snippet?: string
  content?: string
  source_url?: string
  domain?: string
  domain_slug?: string
  domain_name?: string
  chunk_id?: string
  document_id?: string | number
  scope?: string
  document_status?: string
}

export interface SearchResponse {
  results: SearchResult[]
  total_count: number
  search_type: string
}

// Ask types
export interface AskRequest {
  answer_depth?: 'quick' | 'balanced' | 'deep'
  attachment_id?: string
  attachment_text?: string
  attachment_name?: string
  attachment_sha256?: string
  attachment_status?: 'processing' | 'complete' | 'partial' | 'error'
  question: string
  role: 'officer' | 'citizen' | 'admin'
  strategy_model: string
  answer_model: string
  final_answer_model: string
  model_option_id?: string
  offline_mode?: boolean
  offline_model?: string
  domain?: string | null
  ward_scope?: string
  uploaded_context_ids?: string[]
  show_rag_trace?: boolean
  session_id?: string
  conversation_id?: string
  event_date?: string
  legal_as_of?: string
  idempotency_key?: string
  pre_persisted_user_message?: boolean
  memory_item_ids?: string[]
  active_document_id?: string
  /** Transport-only; never serialized into the Ask request body. */
  signal?: AbortSignal
}

export interface AskResponse {
  request_items?: Array<{ item_id: string; question: string; action: string; facets?: string[] }>
  item_results?: Array<{
    item_id: string
    question: string
    status: string
    facets: Record<string, string>
    facet_sources?: Record<string, string[]>
    missing_reasons?: Record<string, string>
    assessment_kind?: 'model_reported'
    support_status?: 'not_assessed'
    retrieval_observation?: Record<string, unknown>
  }>
  answer: string
  question: string
  rag_trace?: RagTrace | null
  grounding_status?: string
  answer_status?: AnswerStatus
  fallback_tier?: FallbackTier
  canonical_domain?: string | null
  evidence_count?: number
  coverage_warning?: string | null
  blocked_reason?: string | null
  outcome?: AnswerOutcome
  reason_code?: string | null
  retryable?: boolean
  scope?: AnswerScope | null
  persistence_degraded?: boolean
  quality?: DirectAnswerQuality | null
  answer_completeness?: AnswerCompleteness
  answer_mode?: AnswerMode
  procedure_detail?: {
    id: string
    name: string
    department: string
    domain_slug: string
    steps: string[]
    documents_required: string[]
    duration: string
    fee: string
    forms: Array<{
      name: string
      file_type: string
      download_url?: string | null
      official_level: string
      review_status: string
      has_official_file?: boolean
      needs_official_file?: boolean
      form_code?: string | null
      procedure_id?: string
      procedure_name?: string
      audience?: 'citizen' | 'officer' | 'both'
      usage?: 'applicant_form' | 'officer_internal' | 'conditional_form'
      source_url?: string | null
      effective_from?: string | null
      effective_to?: string | null
    }>
  } | null
  citations?: LegalCitation[]
  recommended_forms?: Array<{ name: string; file_type: string; download_url?: string | null; official_level: string; review_status: string; has_official_file?: boolean; needs_official_file?: boolean; procedure_id?: string; procedure_name?: string; form_id?: string | null; form_code?: string | null; audience?: 'citizen' | 'officer' | 'both'; usage?: 'applicant_form' | 'officer_internal' | 'conditional_form'; required_or_conditional?: 'required' | 'conditional'; condition?: string | null; source_url?: string | null; source_classification?: string; effective_from?: string | null; effective_to?: string | null }>
  procedure_summary?: string
  faqs?: Array<{
    id: string
    question: string
    answer: string
    steps?: string[]
    form_ids?: string[]
    domain?: string
    ward_scope?: string | null
    review_status?: string
  }>
  domain_mismatch?: boolean
  selected_domain?: string | null
  suggested_domain?: string | null
  suggested_agency?: string | null
  conversation_id?: string | null
  latency_ms?: number | null
  timing_summary?: AskTimingSummary | null
  timing?: AskTimingSummary | null
  trace_id?: string | null
  error?: { code?: string; message?: string; suggestion?: string; reason?: string; retryable?: boolean } | null
  question_type?: string | null
  detected_domain?: string | null
  required_sections?: string[]
  forms_unavailable?: boolean
  source_gap?: string[]
  evidence_coverage?: Record<string, { status?: 'verified' | 'missing' | 'conflicting' | 'not_applicable'; evidence_ids?: string[]; reason?: string }>
  claim_validation?: Array<Record<string, unknown>>
  authority_status?: string | null
  legal_as_of?: string | null
  clarifying_questions?: string[]
  quality_flags?: string[]
  answer_score_preview?: number | null
  answer_sections?: AnswerSection[]
  presentation_version?: 'legal-answer-v1' | 'direct-rag-v1' | null
  answer_route?: AnswerRoute | null
  pipeline_version?: string | null
  data_release_id?: string | null
  release_id?: string | null
  index_fingerprint?: string | null
  manifest_hash?: string | null
  validity_snapshot?: string | null
  verification_label?: string | null
  historical_label?: string | null
  sections?: LegalAnswerPresentationSections | null
  suggested_questions?: SuggestedQuestionV1[]
  memory_usage?: MemoryUsageV1 | null
  conversation_route?: ConversationRoute | null
  active_document?: ConversationDocumentRefV1 | null
  related_documents?: ConversationDocumentRefV1[]
  model_option_id?: string | null
  model_display_name?: string | null
  model_locked?: boolean
  generation_provenance?: Record<string, unknown> | null
}

export interface AskTimingSummary {
  retrieval_ms?: number | null
  routing_ms?: number | null
  exact_retrieval_ms?: number | null
  vector_retrieval_ms?: number | null
  overlay_retrieval_ms?: number | null
  hydrate_ms?: number | null
  context_build_ms?: number | null
  prompt_build_ms?: number | null
  model_provision_ms?: number | null
  provisioning_ms?: number | null
  generation_ms?: number | null
  verification_ms?: number | null
  validation_ms?: number | null
  persistence_ms?: number | null
  end_to_end_ms?: number | null
  timeout_stage?: string | null
  fallback_used?: boolean
  persistence_degraded?: boolean
  evidence_candidates?: number
  evidence_units?: number
  evidence_sent?: number
  router_latency_ms?: number | null
  router_fallback?: boolean
  ollama_metrics?: Record<string, unknown> | null
}

export interface DirectAnswerQuality {
  contract_version?: string
  accepted_claim_count?: number
  rejected_claim_count?: number
  citation_count?: number
  citation_assessment?: 'packet_membership_only' | string
  support_status?: 'not_assessed' | string
  support_reference_count?: number
  excluded_reference_count?: number
  support_reference_kind?: 'candidate_mentions_only' | string
  coverage?: number | null
  coverage_status?: 'not_assessed' | 'partial' | 'insufficient_evidence' | string
  validity_status?: string | null
  validity_assessment?: 'registry_observations_only' | string
  validity_unknown_source_count?: number
  validity_observed_source_count?: number
  validity_scope?: 'displayed_sources' | string
  citation_status?: 'valid' | 'invalid' | 'missing' | 'present' | string | null
  packet_coverage?: {
    requested_issue_ids?: string[]
    represented_issue_ids?: string[]
    missing_issue_ids?: string[]
    partial_issue_ids?: string[]
    dropped_evidence_count?: number
    truncated_evidence_count?: number
    coverage_kind?: 'retrieval_presence_only' | string
  }
}

export type AnswerOutcome =
  | 'answered'
  | 'partial'
  | 'source_only'
  | 'clarification_required'
  | 'failed'

export type AnswerScope =
  | 'full_article'
  | 'article_outline'
  | 'bounded_window'
  | 'multi_source'

export interface SuggestedQuestionV1 {
  id?: string
  text: string
  issue_id: string
  facet: string
}

export interface MemoryUsageV1 {
  used: boolean
  inherited_fields: string[]
  source?: 'conversation' | 'conversation+long_term' | null
  state_revision: number
  history_mode?: 'empty' | 'full' | 'compacted'
  messages_considered?: number
  messages_included?: number
  history_tokens?: number
  input_token_budget?: number
  history_token_budget?: number
  older_messages_compacted?: number
  digest_revision?: number
  digest_through_message_id?: string | null
  router_latency_ms?: number
  router_fallback?: boolean
  context_checksum?: string
}

export interface CitationDisplayItem {
  document_title?: string
  law_number?: string
  article_number?: string
  clause_number?: string
  point_number?: string
  effective_status?: string
  legal_as_of?: string
  source_url?: string
  label?: string
  validity_sync?: PublicValiditySync
  authority_level?: string
  authority_label?: string
}

export interface AnswerSection {
  issue_id: string
  title: string
  status: 'sufficiently_evidenced' | 'partially_evidenced' | 'insufficiently_evidenced'
  facet?: 'rule' | 'condition' | 'authority' | 'documents' | 'procedure' | 'verification' | 'recording' | 'deadline' | 'fee' | 'dispute' | 'form' | 'unknown' | null
  priority?: 'critical' | 'high' | 'normal' | null
  claim_types?: Array<'rule' | 'condition' | 'authority' | 'documents' | 'procedure' | 'next_action' | 'deadline' | 'fee' | 'form' | 'exception' | 'warning'>
  answer?: string | null
  guidance?: string | null
  limitation?: string | null
  citations: CitationDisplayItem[]
  clarifying_question?: string | null
}

export interface RagTrace {
  legal_as_of?: string
  evidence_coverage?: Record<string, unknown>
  claim_validation?: Array<Record<string, unknown>>
  question_classification?: Record<string, unknown>
  latency_by_stage?: Record<string, number>
  form_provenance?: {
    requested?: boolean
    forms_unavailable?: boolean
    accepted?: Array<Record<string, unknown>>
    rejected?: Array<Record<string, unknown>>
  }
  input_question?: string
  selected_domain?: string | null
  detected_domain?: {
    slug: string
    name: string
    count: number
  } | null
  retrieved_chunks?: Array<{
    chunk_id: number
    score?: number
    domain?: string
    field?: string
    law_number?: string
    document_title?: string
    article_number?: string
    article_title?: string
    content_preview?: string
  }>
  filtered_candidates?: Array<Record<string, unknown>>
  llm_sources?: Array<Record<string, unknown>>
}
