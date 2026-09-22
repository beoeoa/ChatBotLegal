import apiClient from '@/lib/api/client'

export type DashboardSectionStatus = 'available' | 'healthy' | 'degraded' | 'unavailable' | 'no_data'

export type AdminDashboardSection = Record<string, unknown> & {
  status: DashboardSectionStatus | string
  observed_at?: string | null
  reason_code?: string | null
}

export type AdminDashboardAttentionItem = {
  code: string
  severity: 'critical' | 'warning' | 'info'
  title: string
  description: string
  count: number
  href?: string | null
  observed_at: string
}

export type AdminOperationalAlert = {
  id: string
  code: string
  module: 'support' | 'lifecycle' | 'vector' | 'import' | 'forms' | 'faq' | 'provider' | 'security' | 'service'
  severity: 'critical' | 'warning' | 'info'
  priority: number
  state_label: string
  title: string
  impact: string
  count: number
  primary_action: { label: string; href: string }
  worklist_filters: Record<string, string>
  observed_at: string
  technical_detail: {
    collapsed_by_default: boolean
    code: string
    source_href?: string | null
  }
}

export type AdminDashboardAuditRow = {
  id?: string
  action?: string
  resource_type?: string
  actor_role?: string
  created_at?: string
}

export type AdminDashboardSnapshot = {
  observed_at: string
  freshness: {
    generated_at: string
    cache_ttl_seconds: number
    cached: boolean
    refreshing?: boolean
    stale?: boolean
    age_seconds?: number | null
  }
  attention_items: AdminDashboardAttentionItem[]
  operational_alerts?: AdminOperationalAlert[]
  health: AdminDashboardSection & {
    components?: Record<string, { status?: string; code?: string; checked_at?: string }>
  }
  legal_repository: AdminDashboardSection & {
    documents?: Record<string, unknown> & {
      expired?: number
      expired_by_date?: number
      by_primary_domain?: Record<string, number>
      classified_total?: number
      unclassified?: number
      classification_coverage_percent?: number
      domain_denominator?: number
      by_primary_organization_unit?: Record<string, number>
      organization_assignment_states?: Record<string, number>
      organization_projection_available?: boolean
    }
    structure?: Record<string, unknown>
    tiers?: Record<string, unknown>
    vectors?: Record<string, unknown>
    validity?: Record<string, unknown>
    faq_impacts?: Record<string, unknown>
    serving_release?: {
      cards?: Record<string, unknown>
      release_id?: string
      legal_as_of?: string
    }
  }
  crawl_import: AdminDashboardSection & {
    import_queue?: Record<string, unknown>
    import_metrics?: Record<string, unknown>
    by_domain?: Record<string, unknown>
    by_source?: Record<string, unknown>
    by_organization_unit?: Record<string, number>
    by_assignment_state?: Record<string, number>
  }
  knowledge: AdminDashboardSection & {
    candidate_status?: Record<string, unknown>
    procedures?: Record<string, unknown>
    forms?: Record<string, unknown>
    faqs?: Record<string, unknown>
    ocr_failures?: number
  }
  users: AdminDashboardSection & {
    total?: number
    active?: number
    locked?: number
    deleted?: number
    must_change_password?: number
    by_role?: Record<string, number>
    active_by_role?: Record<string, number>
    officers_by_domain?: Record<string, number>
    officers_by_ward?: Record<string, number>
    officers_by_organization_unit?: Record<string, number>
    officers_without_organization_unit?: number
  }
  organization: AdminDashboardSection & {
    units?: Array<{
      id: string
      code: string
      name: string
      short_name?: string | null
      is_active: boolean
      support_enabled: boolean
    }>
    domains?: Array<{
      code: string
      name: string
      is_active: boolean
      sort_order: number
    }>
    readiness?: Record<string, unknown>
    ready_for_unit_primary?: boolean | null
  }
  models: AdminDashboardSection & {
    credential_count?: number
    model_count?: number
    providers?: string[]
    models_by_type?: Record<string, number>
    defaults?: Record<string, string | null>
    resolved_defaults?: {
      chat?: { id?: string | null; display_name?: string; provider?: string | null; modality?: string; configured?: boolean; available?: boolean; source?: string }
      embedding?: { id?: string | null; display_name?: string; provider?: string | null; modality?: string; configured?: boolean; available?: boolean; source?: string }
    }
    active_vector_embedding?: { id?: string | null; display_name?: string; provider?: string | null; modality?: string; configured?: boolean; available?: boolean; source?: string; fingerprint?: string | null }
    available_providers?: string[]
    config_revision?: string | null
    observed_at?: string | null
    embedding_index_warning?: boolean
    ready_for_answers?: boolean
  }
  support: AdminDashboardSection & {
    total?: number
    waiting?: number
    active?: number
    closed?: number
    unassigned?: number
    overdue?: number
    by_domain?: Record<string, number>
    by_organization_unit?: Record<string, number>
    without_organization_unit?: number
  }
  runtime_metrics: AdminDashboardSection & {
    event_count?: number
    by_category?: Record<string, Record<string, number>>
    error_statuses?: Record<string, number>
    issue_counts?: Record<string, number>
    privacy?: string
  }
  audit_7d: AdminDashboardSection & {
    timezone?: string
    total?: number
    daily?: Array<{ date: string; count: number }>
    by_group?: Record<string, number>
    truncated?: boolean
  }
  recent_audit: AdminDashboardAuditRow[] | AdminDashboardSection
  legal_cases: { total: number; open: number }
  metrics: Record<string, unknown>
}

export const adminDashboardApi = {
  snapshot: async (forceRefresh = false): Promise<AdminDashboardSnapshot> => {
    const response = await apiClient.get<AdminDashboardSnapshot>('/admin/control/dashboard', {
      params: forceRefresh ? { force_refresh: true } : undefined,
      timeout: 10000,
    })
    return response.data
  },
}
