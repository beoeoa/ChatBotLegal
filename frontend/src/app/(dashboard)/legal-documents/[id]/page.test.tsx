import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.hoisted(() => vi.fn())

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: '31285' }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('@/lib/api/client', () => ({ apiClient: { get: apiGet } }))
vi.mock('@/lib/config', () => ({ getApiUrl: vi.fn() }))
vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (state: { role: string }) => unknown) => selector({ role: 'citizen' }),
}))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import LegalDocumentViewerPage from './page'

describe('LegalDocumentViewerPage validity history label', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    apiGet.mockResolvedValue({
      data: {
        doc_id: '31285',
        document_title: 'Văn bản lịch sử',
        law_number: '96/2014/TT-BQP',
        effective_status: 'active',
        validity_status: 'expired',
        current_answer_eligible: false,
        historical_lookup_allowed: true,
        validity_sync: {
          status: 'expired',
          serving_action: 'historical_only',
          current_answer_eligible: false,
          historical_lookup_allowed: true,
          display_label:
            'H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh',
        },
        article_index: [],
        articles: [],
      },
    })
  })

  it('keeps the document readable while warning it is historical-only', async () => {
    render(<LegalDocumentViewerPage />)

    const labels = await screen.findAllByText(
      'H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh',
    )
    expect(labels.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Ch\u1ec9 d\u00f9ng \u0111\u1ec3 tra c\u1ee9u l\u1ecbch s\u1eed/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Văn bản lịch sử' })).toBeInTheDocument()
  })
})
