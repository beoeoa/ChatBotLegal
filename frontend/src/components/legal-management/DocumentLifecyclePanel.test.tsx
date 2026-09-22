import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  lifecycleTimeline: vi.fn(),
  impactCases: vi.fn(),
  activeIndexManifest: vi.fn(),
  previewIndexJob: vi.fn(),
}))

vi.mock('@/lib/api/legal-management', () => ({ legalManagementApi: api }))

import { DocumentLifecyclePanel } from './DocumentLifecyclePanel'

describe('DocumentLifecyclePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.lifecycleTimeline.mockResolvedValue({
      document_id: 'doc-1',
      events: [
        {
          id: 'event-1', event_type: 'replace', effective_from: '2026-08-13',
          status: 'confirmed', scope: 'whole_document', provisions: [],
        },
      ],
      provisions: [{ provision_identity: 'Điều 1', status: 'confirmed' }],
      vector_state: 'missing',
    })
    api.impactCases.mockResolvedValue([
      {
        id: 'impact-1', change_event_id: 'event-1', dependent_type: 'faq',
        dependent_id: 'faq-1', detected_reason: 'replacement_content_differs',
        status: 'needs_review', evidence_sha256: 'a'.repeat(64),
      },
    ])
    api.activeIndexManifest.mockResolvedValue({ gate_passed: false })
    api.previewIndexJob.mockResolvedValue({
      document_id: 'doc-1', target_provisions: ['Điều 1'], mode: 'incremental',
      state: 'preview', mutation_performed: false, active_pointer_change: false,
    })
  })

  it('does not fetch or expose operations projection to non-admin roles', () => {
    render(<DocumentLifecyclePanel documentId="doc-1" isAdmin={false} />)
    expect(screen.queryByText('Timeline, ảnh hưởng và chỉ mục')).not.toBeInTheDocument()
    expect(api.lifecycleTimeline).not.toHaveBeenCalled()
  })

  it('shows timeline, impact and a mutation-free incremental preview to Admin', async () => {
    render(<DocumentLifecyclePanel documentId="doc-1" isAdmin />)

    expect(await screen.findByText('Bị thay thế')).toBeInTheDocument()
    expect(screen.getByText('1 nội dung liên quan đang chờ rà soát')).toBeInTheDocument()
    expect(screen.getByText('Thiếu dữ liệu tra cứu')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Xem trước tái lập chỉ mục' }))

    await waitFor(() => {
      expect(api.previewIndexJob).toHaveBeenCalledWith({
        document_id: 'doc-1', provisions: ['Điều 1'],
      })
    })
    expect(await screen.findByText(/Đã xác định phần dữ liệu cần cập nhật/)).toBeInTheDocument()
  })
})
