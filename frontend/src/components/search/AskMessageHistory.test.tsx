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

  it('shows a public validity label, verification date, warning and official source', async () => {
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

    expect(await screen.findByText('Hết hiệu lực một phần')).toBeInTheDocument()
    expect(screen.getByText(/Xác minh 08\/08\/2026/)).toBeInTheDocument()
    expect(screen.getByText('Dữ liệu xác minh có thể đã cũ')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Nguồn xác minh hiệu lực/ })).toHaveAttribute(
      'href',
      'https://vbpl.vn/van-ban/example',
    )
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
