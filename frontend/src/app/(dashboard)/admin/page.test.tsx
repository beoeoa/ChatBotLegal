import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ snapshot: vi.fn() }))

vi.mock('@/lib/api/admin-dashboard', () => ({ adminDashboardApi: api }))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import AdminDashboardPage from './page'

function dashboardSnapshot() {
  const observed = '2026-08-11T03:00:00Z'
  return {
    observed_at: observed,
    freshness: { generated_at: observed, cache_ttl_seconds: 30, cached: false },
    attention_items: [],
    operational_alerts: [{
      id: 'alert-1', code: 'import.failed', module: 'import', severity: 'critical', priority: 380,
      state_label: 'Cần xử lý ngay', title: 'Tác vụ nạp dữ liệu bị lỗi',
      impact: 'Văn bản mới chưa vào hàng rà soát.', count: 2,
      primary_action: { label: 'Mở tác vụ cần xử lý', href: '/legal-import?tab=proposals&status=needs_attention' },
      worklist_filters: { tab: 'proposals', status: 'needs_attention' }, observed_at: observed,
      technical_detail: { collapsed_by_default: true, code: 'import.failed' },
    }],
    health: {
      status: 'degraded', observed_at: observed,
      components: {
        api: { status: 'healthy', code: 'ready' },
        crawler: { status: 'degraded', code: 'stale' },
      },
    },
    legal_repository: {
      status: 'available', observed_at: observed,
      documents: { total: 12, active: 10, expired: 1, unknown_status: 1 },
      structure: { articles: 40, chunks: 80 }, tiers: {},
      vectors: { collections: { legal_core: 70 }, database_chunks: 80 }, validity: {}, faq_impacts: {},
    },
    crawl_import: { status: 'available', observed_at: observed },
    knowledge: { status: 'available', observed_at: observed },
    users: { status: 'available', observed_at: observed },
    support: { status: 'available', observed_at: observed, total: 5, waiting: 2, active: 1, closed: 2, unassigned: 1, overdue: 1 },
    models: { status: 'available', observed_at: observed },
    runtime_metrics: { status: 'available', observed_at: observed },
    audit_7d: { status: 'available', observed_at: observed },
    recent_audit: [],
    legal_cases: { total: 0, open: 0 }, metrics: {},
  }
}

describe('AdminDashboardPage', () => {
  beforeEach(() => {
    api.snapshot.mockReset()
    api.snapshot.mockResolvedValue(dashboardSnapshot())
  })

  it('shows only the essential read-only overview', async () => {
    render(<AdminDashboardPage />)

    expect(await screen.findByRole('heading', { name: 'Tổng quan hệ thống' })).toBeInTheDocument()
    expect(screen.getByText('Tác vụ nạp dữ liệu bị lỗi')).toBeInTheDocument()
    expect(screen.getByText('Văn bản pháp lý')).toBeInTheDocument()
    expect(screen.getByText('Độ phủ tra cứu')).toBeInTheDocument()
    expect(screen.getByText('Hỗ trợ quá hạn')).toBeInTheDocument()
    expect(screen.getByText('Tình trạng dịch vụ')).toBeInTheDocument()

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByText('Chi tiết kỹ thuật')).not.toBeInTheDocument()
    expect(screen.queryByText(/Văn bản mới chưa vào hàng rà soát/)).not.toBeInTheDocument()
  })

  it('shows missing section data explicitly instead of displaying zero', async () => {
    const snapshot = dashboardSnapshot()
    snapshot.support = {
      status: 'unavailable', observed_at: snapshot.observed_at,
      total: 0, waiting: 0, active: 0, closed: 0, unassigned: 0, overdue: 0,
    }
    api.snapshot.mockResolvedValue(snapshot)

    render(<AdminDashboardPage />)

    await screen.findByRole('heading', { name: 'Tổng quan hệ thống' })
    const label = screen.getByText('Hỗ trợ quá hạn')
    expect(within(label.parentElement as HTMLElement).getByText('—')).toBeInTheDocument()
    expect(screen.getByText('Chưa đọc được dữ liệu hỗ trợ')).toBeInTheDocument()
  })
})
