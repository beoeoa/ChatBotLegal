import apiClient from './client'

export type QuickChatProcedureDetail = {
  procedure_id?: string | null
  name?: string | null
  domain?: string | null
  submission_place?: string | null
  documents_required?: string[]
  steps?: string[]
  duration?: string | null
  fee?: string | null
  guidance?: string | null
  source_url?: string | null
  source_status?: string | null
  procedure_revision_sha256?: string | null
  forms?: string[]
  legal_basis?: string[]
}

export type QuickChatSection = {
  id: string
  title: string
  kind: 'checklist' | 'steps' | 'note' | 'notice'
  items?: string[]
  body?: string | null
}

export type QuickChatResponse = {
  answer: string
  question: string
  answer_mode?: string | null
  answer_route?: string | null
  answer_status?: string | null
  grounding_status?: string | null
  outcome?: string | null
  reason_code?: string | null
  procedure_summary?: string | null
  procedure_detail?: QuickChatProcedureDetail | null
  citations?: Array<{ document_title?: string; source_url?: string | null; label?: string | null }>
  source_gap?: string[]
  guest?: boolean
  intent_id?: string | null
  release_id?: string | null
  answer_facet?: string | null
  quick_facts?: Array<{ id: string; label: string; value: string }>
  answer_sections?: QuickChatSection[]
  data_quality_notice?: string | null
  suggested_questions?: string[]
  action_chips?: Array<{ id: string; label: string; question: string }>
  verification?: { label: string; release_id?: string | null; procedure_revision_sha256?: string | null; llm_used: boolean }
  quota_remaining?: number | null
  retry_after_seconds?: number | null
  llm_used?: boolean
}

export type QuickChatRequest = {
  question: string
  idempotency_key: string
  context?: Array<{ question: string }>
}

export async function askQuickChat(payload: QuickChatRequest): Promise<QuickChatResponse> {
  const response = await apiClient.post<QuickChatResponse>('/public/quick-chat', payload)
  return response.data
}
