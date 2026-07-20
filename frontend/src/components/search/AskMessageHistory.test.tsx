import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { AskMessage } from '@/lib/types/search'
import { AskMessageHistory } from './AskMessageHistory'

vi.mock('./StreamingResponse', () => ({
  StreamingResponse: ({ finalAnswer, answerSections }: { finalAnswer: string | null; answerSections?: Array<unknown> }) => (
    <article data-testid="polished-answer-card" data-section-count={answerSections?.length || 0}>{finalAnswer}</article>
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
})
