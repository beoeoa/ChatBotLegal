import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  it('blocks approval when the exact law number already exists in the serving warehouse', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          comparison_status: 'duplicate',
          duplicate_runtime_document: { id: 127430, law_number: '1654/QĐ-UBND' },
        })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByText('Cùng số hiệu — cần đối chiếu')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Xem bản trong kho' })).toHaveAttribute(
      'href',
      '/legal-management/127430',
    )
    expect(screen.queryByRole('button', { name: 'Duyệt và nhập kho' })).not.toBeInTheDocument()
  })

  it('shows review decisions only while a candidate is pending', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate()}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Không duyệt' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Duyệt' })).toBeInTheDocument()
    expect(screen.queryByText(/Đánh giá/)).not.toBeInTheDocument()
  })

  it('shows an approved validation failure as actionable, not processing', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({ status: 'approved', import_status: 'validation_failed' })}
        busy={false}
        onReview={vi.fn()}
      />,
    )

    expect(screen.getByText('Cần xử lý trước khi nhập kho')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Không duyệt' })).not.toBeInTheDocument()
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

  it('exposes the real failure reason instead of claiming the item is processing', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          status: 'import_failed',
          import_status: 'failed',
          review_note: 'Import/embed failed: nguồn chính thức chưa xác minh được.',
        })}
        busy={false}
        onReview={vi.fn()}
      />,
    )

    expect(screen.getByText('Cần xử lý trước khi nhập kho')).toBeInTheDocument()
    expect(screen.getByText(/nguồn chính thức chưa xác minh được/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Không đưa vào kho' })).toBeInTheDocument()
  })

  it('renders imported records read-only and hides reassessment', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          status: 'imported',
          import_status: 'completed',
          vector_collection: 'legal_chunks_admin_approved_local_v1',
          chatbot_ready: true,
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
    expect(screen.getByRole('link', { name: 'Xem trong kho văn bản' })).toHaveAttribute('href', '/legal-management/109385')
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

    expect(screen.getByText('Chưa xác nhận kích hoạt tra cứu')).toBeInTheDocument()
    expect(screen.queryByText('Đã nhập kho và kích hoạt tra cứu')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('translates persisted blockers and hides raw technical failures', () => {
    render(<CandidateLifecycleActions candidate={candidate({
      status: 'changes_requested', review_note: 'TypeError: request failed',
      blockers: ['document_type_missing|issued_date_missing', 'document_not_effective_for_current_search'],
    })} busy={false} onReview={vi.fn()} />)
    expect(screen.getByText('Thiếu loại văn bản. Thiếu ngày ban hành.')).toBeInTheDocument()
    expect(screen.getByText(/Văn bản chưa đến ngày có hiệu lực/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/TypeError|document_type_missing|document_not_effective/)
  })

  it('lets an administrator close an import failure without retrying forever', () => {
    const onReview = vi.fn()
    render(
      <CandidateLifecycleActions
        candidate={candidate({ status: 'import_failed', review_note: 'Import job failed: Thiếu ngày ban hành.' })}
        busy={false}
        onReview={onReview}
        onRetryImport={vi.fn()}
      />,
    )

    expect(document.body.textContent).not.toContain('Import job failed')
    fireEvent.click(screen.getByRole('button', { name: 'Không đưa vào kho' }))
    expect(onReview).toHaveBeenCalledWith('legal_crawl_candidate:test', 'rejected')
  })

  it('does not claim chatbot readiness without a confirmed serving collection', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          status: 'imported',
          import_status: 'completed',
          chatbot_ready: false,
          imported_document: {
            document_id: '109385',
            chunk_count: 42,
            activation_status: 'active',
          },
        })}
        busy={false}
        onReview={vi.fn()}
        onRetryImport={vi.fn()}
      />,
    )

    expect(screen.getByText('Chưa xác nhận kích hoạt tra cứu')).toBeInTheDocument()
    expect(screen.queryByText('Đã nhập kho và kích hoạt tra cứu')).not.toBeInTheDocument()
  })

  it('requires reason and explicit source comparison before archiving identity-only match', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined)
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          duplicate_check_revision: 'a'.repeat(64),
          comparison_status: 'identity_match',
          duplicate_matches: [{
            id: '127430', target_type: 'document', kind: 'identity_match',
            title: 'Quyết định đang có', law_number: '1654/QĐ-UBND',
            reason_codes: ['same_number', 'full_content_not_compared'],
            target_revision: 'b'.repeat(64), can_archive: true, can_review_replacement: true,
          }],
        })}
        busy={false}
        onReview={vi.fn()}
        onResolveDuplicate={onResolve}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Lưu trữ bản trùng' }))
    const confirmButton = screen.getByRole('button', { name: 'Xác nhận lưu trữ' })
    expect(confirmButton).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Lý do đối chiếu (ít nhất 10 ký tự)'), {
      target: { value: 'Đã đối chiếu toàn văn với nguồn gốc.' },
    })
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(confirmButton)

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(
      'legal_crawl_candidate:test',
      expect.objectContaining({
        action: 'archive_duplicate', target_id: '127430', confirmed_same_document: true,
      }),
    ))
  })

  it('announces an inline reason error after the field loses focus', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          duplicate_check_revision: 'a'.repeat(64),
          duplicate_matches: [{
            id: '127430', target_type: 'document', kind: 'identity_match',
            title: 'Quyết định đang có', law_number: '1654/QĐ-UBND',
            reason_codes: ['same_number', 'full_content_not_compared'],
            target_revision: 'b'.repeat(64), can_archive: true, can_review_replacement: true,
          }],
        })}
        busy={false}
        onReview={vi.fn()}
        onResolveDuplicate={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Lưu trữ bản trùng' }))
    const reason = screen.getByLabelText('Lý do đối chiếu (ít nhất 10 ký tự)')
    fireEvent.blur(reason)

    expect(reason).toHaveAttribute('aria-invalid', 'true')
    expect(reason).toHaveAccessibleDescription(/0\/2000 ký tự/)
    expect(screen.getByRole('alert')).toHaveTextContent('Nhập ít nhất 10 ký tự')

    fireEvent.change(reason, { target: { value: 'Đã đối chiếu bản gốc.' } })
    expect(reason).toHaveAttribute('aria-invalid', 'false')
    expect(screen.queryByText('Nhập ít nhất 10 ký tự cho lý do đối chiếu.')).not.toBeInTheDocument()
  })

  it('does not offer archive when the fetched content changed', () => {
    render(
      <CandidateLifecycleActions
        candidate={candidate({
          duplicate_check_revision: 'a'.repeat(64),
          comparison_status: 'content_changed',
          duplicate_matches: [{
            id: '127430', target_type: 'document', kind: 'content_changed',
            title: 'Quyết định đang có', reason_codes: ['same_number'],
            target_revision: 'b'.repeat(64), can_archive: false, can_review_replacement: true,
          }],
        })}
        busy={false}
        onReview={vi.fn()}
        onResolveDuplicate={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Lưu trữ bản trùng' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Đối chiếu để thay thế' })).toBeInTheDocument()
  })
})
