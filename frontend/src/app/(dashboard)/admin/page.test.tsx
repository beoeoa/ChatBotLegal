import { render, screen } from '@testing-library/react'
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
      documents: { total: 12, active: 10, expired: 1, unknown_status: 1, by_primary_organization_unit: { 'tu-phap': 4 } },
      structure: { articles: 40, chunks: 80 }, tiers: {},
      vectors: { collections: { legal_core: 70 }, database_chunks: 80 }, validity: {}, faq_impacts: {},
    },
    crawl_import: { status: 'available', observed_at: observed, by_organization_unit: { 'tu-phap': 2 } },
    knowledge: { status: 'available', observed_at: observed },
    users: { status: 'available', observed_at: observed, officers_by_organization_unit: { 'tu-phap': 3 } },
    organization: { status: 'available', observed_at: observed, ready_for_unit_primary: false, units: [{ id: 'tu-phap', code: 'tu_phap', name: 'Tư pháp - Hộ tịch', short_name: 'Tư pháp', is_active: true, support_enabled: true }] },
    support: { status: 'available', observed_at: observed, total: 5, waiting: 2, active: 1, closed: 2, unassigned: 1, overdue: 1, by_organization_unit: { 'tu-phap': 1 } },
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
    expect(screen.getByText('Kho văn bản pháp luật')).toBeInTheDocument()
    expect(screen.getByText('Độ phủ dữ liệu tra cứu')).toBeInTheDocument()
    expect(screen.getByText('Người dùng & Hỗ trợ')).toBeInTheDocument()
    expect(screen.getByText('Trạng thái 7 Dịch vụ cốt lõi')).toBeInTheDocument()
    expect(screen.getByText('Phòng ban & Dữ liệu')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Đến quản lý kho văn bản' })).toBeInTheDocument()
    expect(screen.getByText(/Văn bản mới chưa vào hàng rà soát/)).toBeInTheDocument()
  })

  it('keeps the overview usable when support data is unavailable', async () => {
    const snapshot = dashboardSnapshot()
    snapshot.support = {
      status: 'unavailable', observed_at: snapshot.observed_at,
      total: 0, waiting: 0, active: 0, closed: 0, unassigned: 0, overdue: 0,
      by_organization_unit: { 'tu-phap': 0 },
    }
    api.snapshot.mockResolvedValue(snapshot)

    render(<AdminDashboardPage />)

    await screen.findByRole('heading', { name: 'Tổng quan hệ thống' })
    expect(screen.getByText('Người dùng & Hỗ trợ')).toBeInTheDocument()
    expect(screen.queryByText('Phiên hỗ trợ quá SLA')).not.toBeInTheDocument()
  })

  it('does not turn missing legal statistics into an empty repository or claim search readiness', async () => {
    const snapshot = dashboardSnapshot()
    api.snapshot.mockResolvedValue({ ...snapshot, legal_repository: { status: 'unavailable' } })
    render(<AdminDashboardPage />)
    await screen.findByRole('heading', { name: 'Tổng quan hệ thống' })
    expect(screen.getByText('— hiệu lực')).toBeInTheDocument()
    expect(screen.getByText('— hết hiệu lực')).toBeInTheDocument()
    expect(screen.getByText('— đoạn văn bản')).toBeInTheDocument()
    expect(screen.getByText('Chưa có số liệu xác nhận')).toBeInTheDocument()
    expect(screen.queryByText('Sẵn sàng tra cứu')).not.toBeInTheDocument()
  })

  it('uses the inventory expiry total and does not estimate coverage from overlapping collections', async () => {
    const snapshot = dashboardSnapshot()
    api.snapshot.mockResolvedValue({
      ...snapshot,
      freshness: { ...snapshot.freshness, stale: true },
      legal_repository: {
        ...snapshot.legal_repository,
        serving_release: {
          cards: { total_retrievable: 11, current_effective: 9, expired_total: 27 },
        },
      },
    })
    render(<AdminDashboardPage />)
    await screen.findByRole('heading', { name: 'Tổng quan hệ thống' })
    expect(screen.getByText('11')).toBeInTheDocument()
    expect(screen.getByText('9 hiệu lực')).toBeInTheDocument()
    expect(screen.getByText('27 hết hiệu lực')).toBeInTheDocument()
    expect(screen.getByText('Chưa có số liệu xác nhận')).toBeInTheDocument()
    expect(screen.getByText(/Đang hiển thị số liệu đã lưu/)).toBeInTheDocument()
  })
})
