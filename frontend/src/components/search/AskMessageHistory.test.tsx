import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent } from '@testing-library/react'
import type { AskMessage } from '@/lib/types/search'
import { AskMessageHistory } from './AskMessageHistory'

vi.mock('./StreamingResponse', () => ({
  StreamingResponse: ({ finalAnswer, answerSections, formsUnavailable, ragTrace, citations }: {
    finalAnswer: string | null
    answerSections?: Array<unknown>
    formsUnavailable?: boolean
    ragTrace?: unknown
    citations?: Array<unknown>
  }) => (
    <article
      data-testid="polished-answer-card"
      data-section-count={answerSections?.length || 0}
      data-forms-unavailable={formsUnavailable ? 'true' : 'false'}
      data-has-trace={ragTrace ? 'true' : 'false'}
      data-citation-count={citations?.length || 0}
    >
      {finalAnswer}
    </article>
  ),
}))

const now = '2026-07-11T12:00:00.000Z'
const message = (id: string, role: AskMessage['role'], content: string, status?: AskMessage['status']): AskMessage => ({
  id,
  role,
  content,
  status,
  created_at: now,
})

describe('AskMessageHistory', () => {
  it('shows a persisted image attachment above the user message bubble', () => {
    render(<AskMessageHistory role="citizen" showRagTrace={false} messages={[
      {
        ...message('user-image', 'user', 'Bạn đọc giúp tôi ảnh này'),
        attachments: [{
          kind: 'uploaded_document_context_v1',
          value: {
            name: 'van-ban.png',
            size: 258477,
            type: 'image/png',
            file_id: '5813aa4e611d4558a254b86ad645bef1',
            status: 'complete',
          },
        }],
      },
    ]} />)

    expect(screen.getByTestId('sent-image-attachment')).toHaveAttribute(
      'href',
      '/api/media/files/5813aa4e611d4558a254b86ad645bef1',
    )
    expect(screen.getByRole('img', { name: 'Ảnh đính kèm: van-ban.png' })).toBeInTheDocument()
    expect(screen.getByText('van-ban.png · 253 KB')).toBeInTheDocument()
    expect(screen.getByText('Bạn đọc giúp tôi ảnh này')).toBeInTheDocument()
  })

  it('shows a document attachment from history without requiring a preview URL', () => {
    render(<AskMessageHistory role="citizen" showRagTrace={false} messages={[
      {
        ...message('user-file', 'user', 'Tóm tắt tài liệu'),
        attachments: [{
          kind: 'uploaded_document_context_v1',
          value: {
            name: 'quyet-dinh.docx',
            size: 4096,
            type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            status: 'complete',
          },
        }],
      },
    ]} />)

    expect(screen.getByTestId('sent-file-attachment')).toHaveTextContent('quyet-dinh.docx')
    expect(screen.getByTestId('sent-file-attachment')).toHaveTextContent('4 KB')
  })

  it('does not label a partial model answer as independently verified', () => {
    render(<AskMessageHistory role="citizen" showRagTrace={false} messages={[
      {...message('partial', 'assistant', 'Phần đã trả lời', 'complete'), outcome:'partial'},
    ]} />)
    expect(screen.getByText(/nội dung kết luận chưa được kiểm chứng độc lập/)).toBeInTheDocument()
    expect(screen.queryByText(/chỉ bao gồm phần đã được kiểm chứng/)).not.toBeInTheDocument()
  })
  it('renders exactly one polished card per completed answer and preserves the first after a second question', async () => {
    const { rerender } = render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[
          message('u1', 'user', 'Câu hỏi thứ nhất'),
          message('a1', 'assistant', 'Câu trả lời đẹp thứ nhất', 'complete'),
        ]}
      />,
    )

    expect(await screen.findAllByTestId('polished-answer-card')).toHaveLength(1)
    expect(screen.getByTestId('polished-answer-card')).toHaveTextContent('Câu trả lời đẹp thứ nhất')
    expect(screen.queryByText('Câu trả lời đẹp thứ nhất', { selector: 'p' })).not.toBeInTheDocument()

    rerender(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[
          message('u1', 'user', 'Câu hỏi thứ nhất'),
          message('a1', 'assistant', 'Câu trả lời đẹp thứ nhất', 'complete'),
          message('u2', 'user', 'Câu hỏi thứ hai'),
          message('a2', 'assistant', 'Câu trả lời đẹp thứ hai', 'complete'),
        ]}
      />,
    )

    const cards = await screen.findAllByTestId('polished-answer-card')
    expect(cards).toHaveLength(2)
    expect(cards[0]).toHaveTextContent('Câu trả lời đẹp thứ nhất')
    expect(cards[1]).toHaveTextContent('Câu trả lời đẹp thứ hai')
    expect(screen.getAllByTestId('ask-assistant-message')).toHaveLength(2)
  })

  it('shows only a pending skeleton before the final answer exists', () => {
    render(
      <AskMessageHistory
        role="officer"
        showRagTrace={false}
        pendingStageLabel="Đang kiểm tra căn cứ pháp lý"
        messages={[message('u1', 'user', 'Câu hỏi'), message('a1', 'assistant', '', 'pending')]}
      />,
    )

    expect(screen.getByTestId('ask-assistant-pending')).toBeInTheDocument()
    expect(screen.getByText('Đang kiểm tra căn cứ pháp lý')).toBeInTheDocument()
    expect(screen.queryByTestId('polished-answer-card')).not.toBeInTheDocument()
  })

  it('shows early sources as being checked without calling them final evidence', () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        pendingStageLabel="Đang soạn câu trả lời"
        pendingCitations={[
          {
            law_number: '62/2020/QH14',
            article_number: '1',
            source_url: 'https://vbpl.vn/62',
          },
        ]}
        messages={[message('u1', 'user', 'Điều 1?'), message('a1', 'assistant', '', 'pending')]}
      />,
    )

    expect(screen.getByText('Nguồn đang được đối chiếu (1)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Điều 1 Luật số 62/2020/QH14' })).toHaveAttribute(
      'href',
      'https://vbpl.vn/62#:~:text=%C4%90i%E1%BB%81u%201',
    )
    expect(screen.getByText('Chỉ được gắn làm căn cứ khi câu trả lời cuối cùng hoàn tất.')).toBeInTheDocument()
    expect(screen.queryByTestId('polished-answer-card')).not.toBeInTheDocument()
  })

  it('forwards optional structured sections while keeping the legacy content', async () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[{
          ...message('a1', 'assistant', 'Câu trả lời phẳng', 'complete'),
          answer_sections: [{
            issue_id: 'issue-1',
            title: 'Thẩm quyền',
            status: 'sufficiently_evidenced',
            answer: 'Nội dung đã xác minh',
            citations: [],
          }],
        }]}
      />,
    )

    expect(await screen.findByTestId('polished-answer-card')).toHaveAttribute('data-section-count', '1')
  })

  it('restores forms_unavailable and never forwards a stored admin trace to a citizen view', async () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace
        messages={[{
          ...message('a1', 'assistant', 'Chưa có mẫu chính thức', 'complete'),
          forms_unavailable: true,
          rag_trace: {
            private_chunk_preview: 'must-not-render',
          } as unknown as AskMessage['rag_trace'],
        }]}
      />,
    )

    const card = await screen.findByTestId('polished-answer-card')
    expect(card).toHaveAttribute('data-forms-unavailable', 'true')
    expect(card).toHaveAttribute('data-has-trace', 'false')
  })

  it('keeps citation metadata for inline links but hides the separate validity boxes', async () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[{
          ...message('a1', 'assistant', 'Nội dung trả lời', 'complete'),
          citations: [{
            chunk_id: 'safe-1',
            law_number: '31/2024/QH15',
            validity_sync: {
              status: 'expired_partial',
              serving_action: 'allow',
              verified_at: '2026-08-08T01:00:00Z',
              source_url: 'https://vbpl.vn/van-ban/example',
              warning_code: 'validity_snapshot_stale',
            },
          }],
        }]}
      />,
    )

    expect(await screen.findByTestId('polished-answer-card')).toHaveAttribute('data-citation-count', '1')
    expect(screen.queryByTestId('citation-validity-notice')).not.toBeInTheDocument()
    expect(screen.queryByText('Hết hiệu lực một phần')).not.toBeInTheDocument()
  })

  it('does not forward a blocked validity source to the answer renderer', async () => {
    render(
      <AskMessageHistory
        role="officer"
        showRagTrace={false}
        messages={[{
          ...message('a1', 'assistant', 'Nội dung trả lời', 'complete'),
          citations: [{
            chunk_id: 'blocked-1',
            law_number: '10/2020/NĐ-CP',
            validity_sync: {
              status: 'expired',
              serving_action: 'historical_only',
              verified_at: '2026-08-08T01:00:00Z',
            },
          }],
        }]}
      />,
    )

    expect(await screen.findByTestId('polished-answer-card')).toHaveAttribute('data-citation-count', '0')
    expect(screen.queryByTestId('citation-validity-notice')).not.toBeInTheDocument()
    expect(screen.queryByText('Hết hiệu lực')).not.toBeInTheDocument()
  })

  it('shows suggestions only below the latest answer and clicking only delegates text', async () => {
    const onSuggestionClick = vi.fn()
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        onSuggestionClick={onSuggestionClick}
        messages={[
          {
            ...message('a1', 'assistant', 'Câu trả lời cũ', 'complete'),
            suggested_questions: [{ text: 'Gợi ý cũ?', issue_id: 'issue-1', facet: 'documents' }],
          },
          {
            ...message('a2', 'assistant', 'Câu trả lời mới', 'complete'),
            suggested_questions: [{ text: 'Cần chuẩn bị giấy tờ gì?', issue_id: 'issue-1', facet: 'documents' }],
          },
        ]}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Gợi ý cũ?' })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Cần chuẩn bị giấy tờ gì?' }))
    expect(onSuggestionClick).toHaveBeenCalledOnce()
    expect(onSuggestionClick).toHaveBeenCalledWith('Cần chuẩn bị giấy tờ gì?')
  })

  it('shows at most three backend-projected related documents under the latest answer', async () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[
          {
            ...message('a1', 'assistant', 'Câu trả lời', 'complete'),
            related_documents: Array.from({ length: 4 }, (_, index) => ({
              document_id: `doc-${index + 1}`,
              title: `Văn bản ${index + 1}`,
              law_number: `${index + 1}/2026/NĐ-CP`,
              source_url: `https://example.test/${index + 1}`,
            })),
          },
        ]}
      />,
    )

    expect(await screen.findByTestId('related-documents')).toBeInTheDocument()
    expect(screen.getByText('Nghị định số 1/2026/NĐ-CP')).toBeInTheDocument()
    expect(screen.getByText('Nghị định số 3/2026/NĐ-CP')).toBeInTheDocument()
    expect(screen.queryByText('Nghị định số 4/2026/NĐ-CP')).not.toBeInTheDocument()
  })

  it('uses backend validation reason and never offers retry for rejected claims', async () => {
    const onRetryClick = vi.fn()
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        onRetryClick={onRetryClick}
        messages={[
          message('u1', 'user', 'Điều 1?'),
          {
            ...message('a1', 'assistant', 'Chưa thể kết luận', 'complete'),
            outcome: 'partial',
            reason_code: 'CLAIM_VALIDATION_FAILED',
            retryable: false,
          },
        ]}
      />,
    )

    expect(await screen.findByTestId('answer-delivery-notice')).toHaveTextContent(
      'chưa thể tạo kết luận đã kiểm chứng',
    )
    expect(screen.queryByRole('button', { name: 'Thử lại' })).not.toBeInTheDocument()
    expect(onRetryClick).not.toHaveBeenCalled()
  })

  it('does not claim a source was found when source-only has no public citation', async () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[{
          ...message('a1', 'assistant', 'Không tìm thấy văn bản trong kho.', 'complete'),
          outcome: 'source_only',
          evidence_count: 1,
          citations: [],
        }]}
      />,
    )

    expect(await screen.findByTestId('answer-delivery-notice')).toHaveTextContent(
      'Chưa tìm thấy nguồn pháp luật phù hợp',
    )
    expect(screen.queryByText('Đã tìm thấy nguồn nhưng chưa thể tạo kết luận pháp lý an toàn.')).not.toBeInTheDocument()
  })

  it('does not claim retained legal sources for a source-free conversation timeout', async () => {
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        messages={[{
          ...message('a1', 'assistant', 'Xin lỗi, vui lòng thử lại.', 'complete'),
          outcome: 'failed',
          reason_code: 'PROVIDER_TIMEOUT',
          evidence_count: 0,
        }]}
      />,
    )

    expect(await screen.findByTestId('answer-delivery-notice')).toHaveTextContent(
      'lượt này không sử dụng nguồn pháp luật',
    )
    expect(screen.queryByText(/giữ lại phần nguồn đã tìm được/)).not.toBeInTheDocument()
  })

  it('offers full-text and detail actions for an article outline', async () => {
    const onSuggestionClick = vi.fn()
    render(
      <AskMessageHistory
        role="citizen"
        showRagTrace={false}
        onSuggestionClick={onSuggestionClick}
        messages={[
          message('u1', 'user', 'Điều 1 quy định gì?'),
          {
            ...message('a1', 'assistant', 'Tổng quan Điều 1', 'complete'),
            outcome: 'answered',
            scope: 'article_outline',
            citations: [{ law_number: '62/2020/QH14', source_url: 'https://vbpl.vn/62' }],
          },
        ]}
      />,
    )

    expect(screen.getByTestId('view-full-article')).toHaveAttribute('href', 'https://vbpl.vn/62')
    fireEvent.click(screen.getByTestId('analyze-article-detail'))
    expect(onSuggestionClick).toHaveBeenCalledWith('Điều 1 quy định gì? Hãy phân tích chi tiết từng nhóm nội dung của Điều này.')
  })
})

describe('AskMessageHistory latest-message behavior', () => {
  function viewportRef(distanceFromBottom: number) {
    const viewport = document.createElement('div')
    Object.defineProperties(viewport, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 700 - distanceFromBottom },
    })
    viewport.scrollTo = vi.fn()
    return { current: viewport }
  }

  it('follows a new message when the reader is already near the bottom', () => {
    const ref = viewportRef(20)
    const { rerender } = render(
      <AskMessageHistory messages={[message('u1', 'user', 'Câu hỏi')]} role="citizen" showRagTrace={false} scrollViewportRef={ref} />,
    )
    expect(ref.current.scrollTo).toHaveBeenCalled()

    vi.mocked(ref.current.scrollTo).mockClear()
    rerender(
      <AskMessageHistory messages={[message('u1', 'user', 'Câu hỏi'), message('a1', 'assistant', 'Câu trả lời', 'complete')]} role="citizen" showRagTrace={false} scrollViewportRef={ref} />,
    )
    expect(ref.current.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' })
    expect(screen.queryByRole('button', { name: 'Xuống tin mới nhất' })).not.toBeInTheDocument()
  })

  it('does not force scroll while reading older messages and offers an explicit latest action', () => {
    const ref = viewportRef(300)
    const { rerender } = render(
      <AskMessageHistory messages={[message('u1', 'user', 'Câu hỏi')]} role="citizen" showRagTrace={false} scrollViewportRef={ref} />,
    )
    vi.mocked(ref.current.scrollTo).mockClear()
    act(() => fireEvent.scroll(ref.current))

    rerender(
      <AskMessageHistory messages={[message('u1', 'user', 'Câu hỏi'), message('a1', 'assistant', 'Câu trả lời', 'complete')]} role="citizen" showRagTrace={false} scrollViewportRef={ref} />,
    )

    expect(ref.current.scrollTo).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Xuống tin mới nhất' }))
    expect(ref.current.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' })
  })
})
