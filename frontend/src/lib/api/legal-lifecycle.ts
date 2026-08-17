import { apiClient } from './client'

export type LifecycleDraftState =
  | 'draft'
  | 'duplicate_review'
  | 'submitted'
  | 'changes_requested'
  | 'rejected'
  | 'approved'

export interface LifecycleCapabilities {
  actor_id: string | null
  authenticated_admin: boolean
  writes_enabled: boolean
  editor: boolean
  reviewer: boolean
  activation_enabled: boolean
}

export interface LifecycleValidation {
  blocking: string[]
  warnings: string[]
  duplicate_candidates: Array<Record<string, unknown>>
  validated_revision?: number
  checked_at?: string
}

export interface LifecycleDraft {
  id: string
  logical_document_id?: number | null
  state: LifecycleDraftState
  title?: string | null
  law_number?: string | null
  document_type?: string | null
  issuing_agency?: string | null
  scope?: string | null
  sector?: string | null
  issued_date?: string | null
  effective_date?: string | null
  expired_date?: string | null
  source_url?: string | null
  content?: string
  revision: number
  validation?: LifecycleValidation | null
  created_by?: string | null
  submitted_by?: string | null
  reviewed_by?: string | null
  updated?: string
  version?: { version_key?: string; activation_state?: string } | null
}

export interface LifecycleDraftInput {
  reason: string
  logical_document_id?: number | null
  base_fingerprint?: string | null
  title?: string
  law_number?: string
  document_type?: string
  issuing_agency?: string
  scope?: string
  sector?: string
  issued_date?: string
  effective_date?: string
  expired_date?: string
  source_url?: string
  content?: string
}

export interface ActivationPreview {
  draft_id: string
  logical_document_id?: number | null
  version_key?: string | null
  manifest_ready: boolean
  provision_count: number
  chunk_count: number
  required_collections: string[]
  live_activation_enabled: boolean
  status: 'blocked' | 'ready'
}

function mutationHeaders(idempotencyKey: string, revision?: number) {
  return {
    'Idempotency-Key': idempotencyKey,
    ...(revision ? { 'If-Match': `"${revision}"` } : {}),
  }
}

export const legalLifecycleApi = {
  async capabilities(): Promise<LifecycleCapabilities> {
    return (await apiClient.get<LifecycleCapabilities>('/legal/lifecycle/capabilities')).data
  },

  async list(state?: string): Promise<{ items: LifecycleDraft[]; limit: number; offset: number }> {
    return (await apiClient.get('/legal/lifecycle/drafts', { params: { state: state || undefined } })).data
  },

  async detail(id: string): Promise<LifecycleDraft> {
    return (await apiClient.get<LifecycleDraft>(`/legal/lifecycle/drafts/${encodeURIComponent(id)}`)).data
  },

  async create(payload: LifecycleDraftInput, idempotencyKey: string): Promise<LifecycleDraft> {
    return (await apiClient.post('/legal/lifecycle/drafts', payload, { headers: mutationHeaders(idempotencyKey) })).data
  },

  async update(id: string, payload: LifecycleDraftInput, revision: number, idempotencyKey: string): Promise<LifecycleDraft> {
    return (await apiClient.put(`/legal/lifecycle/drafts/${encodeURIComponent(id)}`, payload, { headers: mutationHeaders(idempotencyKey, revision) })).data
  },

  async transition(id: string, action: 'validate' | 'submit', reason: string, revision: number, idempotencyKey: string): Promise<LifecycleDraft> {
    return (await apiClient.post(`/legal/lifecycle/drafts/${encodeURIComponent(id)}/${action}`, { reason }, { headers: mutationHeaders(idempotencyKey, revision) })).data
  },

  async review(id: string, decision: 'approved' | 'rejected' | 'changes_requested', reason: string, revision: number, idempotencyKey: string): Promise<LifecycleDraft> {
    return (await apiClient.post(`/legal/lifecycle/drafts/${encodeURIComponent(id)}/review`, { decision, reason }, { headers: mutationHeaders(idempotencyKey, revision) })).data
  },

  async activationPreview(id: string): Promise<ActivationPreview> {
    return (await apiClient.get<ActivationPreview>(`/legal/lifecycle/drafts/${encodeURIComponent(id)}/activation-preview`)).data
  },
}

export function lifecycleIdempotencyKey(action: string): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${action}:${random}`
}
