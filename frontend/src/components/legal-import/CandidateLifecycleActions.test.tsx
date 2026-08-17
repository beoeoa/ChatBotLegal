import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { LegalCrawlCandidate } from '@/lib/api/legal-import'
import { CandidateLifecycleActions } from './CandidateLifecycleActions'

function candidate(overrides: Partial<LegalCrawlCandidate> = {}): LegalCrawlCandidate {
  return {
    id: 'legal_crawl_candidate:test',
    external_id: 'test',
    title: 'Văn bản thử nghiệm',
    detail_url: 'https://vbpl.vn/test',
    source_url: 'https://vbpl.vn/test',
    status: 'pending',
    suggested_action: 'import_new',
    comparison_status: 'new',
    detected_changes: [],
    ...overrides,
  }
}

describe('CandidateLifecycleActions', () => {
  it('shows review decisions only while a candidate is pending', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate()}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Từ chối' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Duyệt và nhập kho' })).toBeInTheDocument()
    expect(screen.queryByText(/Đánh giá/)).not.toBeInTheDocument()
  })

  it('shows retry import without review decisions after approval', () => {
    const onRetryImport = vi.fn()
    render(
      <CandidateLifecycleActions
        candidate={candidate({ status: 'approved', import_status: 'validation_failed' })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={onRetryImport}
      />,
    )

    expect(screen.getByText('Đã duyệt – chưa nhập kho')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Từ chối' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Thử nhập lại' }))
    expect(onRetryImport).toHaveBeenCalledWith('legal_crawl_candidate:test')
  })

  it('distinguishes a queued job from an embedding job in progress', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({ status: 'import_queued', import_status: 'running' })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByText('Đang chuẩn hóa và tạo chỉ mục')).toBeInTheDocument()
    expect(screen.getByText(/Văn bản sẽ dùng được sau khi hoàn tất/)).toBeInTheDocument()
  })

  it('shows the latest failure reason before offering a retry', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          status: 'import_failed',
          import_status: 'failed',
          review_note: 'Import/embed failed: nguồn chính thức chưa xác minh được.',
        })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByText('Lý do gần nhất:')).toBeInTheDocument()
    expect(screen.getByText(/nguồn chính thức chưa xác minh được/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Thử nhập lại' })).toBeInTheDocument()
  })

  it('renders imported records read-only and hides reassessment', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          status: 'imported',
          import_status: 'completed',
          imported_document: {
            document_id: '109385',
            chunk_count: 42,
            activation_status: 'active',
            model: 'VNLegal-LAL',
          },
        })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByText('Đã nhập kho và kích hoạt tra cứu')).toBeInTheDocument()
    expect(screen.getByText(/Có thể sử dụng trong tra cứu và hỏi đáp/)).toBeInTheDocument()
    expect(screen.getByText(/42 đoạn/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByText(/Đánh giá lại/)).not.toBeInTheDocument()
  })

  it('does not claim retrieval is active when a legacy imported record lacks activation evidence', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          status: 'imported',
          import_status: 'completed',
          imported_document: {
            document_id: 'legacy-document',
            chunk_count: 0,
          },
        })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByText('Nhập kho chưa xác nhận kích hoạt')).toBeInTheDocument()
    expect(screen.queryByText('Đã nhập kho và kích hoạt tra cứu')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
