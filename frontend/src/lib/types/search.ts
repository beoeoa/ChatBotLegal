// Search types
export interface AskMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  status?: 'pending' | 'complete' | 'error' | 'cancelled'
  /** True after the Ask response snapshot has been persisted to the session. */
  persisted?: boolean
  citations?: Array<{
    chunk_id: string
    law_number?: string
    document_title?: string
    article_number?: string
    article_title?: string
    source_url?: string
    internal_url?: string
    pdf_url?: string
    fallback_search_url?: string
    doc_id?: string | number
    label?: string
  }>
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
  }>
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
    }>
  } | null
  rag_trace?: {
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
  } | null
  grounding_status?: string
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
  question: string
  role: 'officer' | 'citizen' | 'admin'
  strategy_model: string
  answer_model: string
  final_answer_model: string
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
  /** Transport-only; never serialized into the Ask request body. */
  signal?: AbortSignal
}

export interface AskResponse {
  answer: string
  question: string
  rag_trace?: RagTrace | null
  grounding_status?: string
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
    }>
  } | null
  citations?: Array<{ chunk_id: string; law_number?: string; document_title?: string; article_number?: string; article_title?: string; source_url?: string; internal_url?: string; pdf_url?: string; fallback_search_url?: string; doc_id?: string | number; label?: string; link_status?: 'internal_indexed' | 'external_recorded' | 'missing' | 'dead' }>
  recommended_forms?: Array<{ name: string; file_type: string; download_url?: string | null; official_level: string; review_status: string; has_official_file?: boolean; needs_official_file?: boolean; procedure_id?: string; procedure_name?: string; form_id?: string | null }>
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
  trace_id?: string | null
  error?: { code?: string; message?: string; suggestion?: string } | null
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
}

export interface AnswerSection {
  issue_id: string
  title: string
  status: 'sufficiently_evidenced' | 'partially_evidenced' | 'insufficiently_evidenced'
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

