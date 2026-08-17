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
  }
  attention_items: AdminDashboardAttentionItem[]
  operational_alerts?: AdminOperationalAlert[]
  health: AdminDashboardSection & {
    components?: Record<string, { status?: string; code?: string; checked_at?: string }>
  }
  legal_repository: AdminDashboardSection & {
    documents?: Record<string, unknown>
    structure?: Record<string, unknown>
    tiers?: Record<string, unknown>
    vectors?: Record<string, unknown>
    validity?: Record<string, unknown>
    faq_impacts?: Record<string, unknown>
  }
  crawl_import: AdminDashboardSection & {
    import_queue?: Record<string, unknown>
    import_metrics?: Record<string, unknown>
    by_domain?: Record<string, unknown>
    by_source?: Record<string, unknown>
  }
  knowledge: AdminDashboardSection & {
    candidate_status?: Record<string, unknown>
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
    officers_by_domain?: Record<string, number>
    officers_by_ward?: Record<string, number>
  }
  models: AdminDashboardSection & {
    credential_count?: number
    model_count?: number
    providers?: string[]
    models_by_type?: Record<string, number>
    defaults?: Record<string, string | null>
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
