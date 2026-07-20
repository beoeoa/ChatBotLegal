import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { sanitizeDisplayAnswer, StreamingResponse } from './StreamingResponse'

describe('section-grounding display sanitizer', () => {
  it('never leaves internal citation or evidence markers in visible answer text', () => {
    const rendered = sanitizeDisplayAnswer(
      'Nguồn [legal:481400 - 60/2014/QH13 - Điều 35] #ref-source-481400 chunk_id=abc trace_id=t-1',
      [{ chunk_id: '481400', law_number: '60/2014/QH13', article_number: '35' }],
    )

    expect(rendered).not.toMatch(/#ref-source|legal:|chunk_id|trace_id/i)
    expect(rendered).toContain('60/2014/QH13')
  })

  it('labels verified, guidance-only and insufficient sections distinctly', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Legacy aggregate must not be the primary section rendering"
        answerSections={[
          { issue_id: 'a', title: 'Thẩm quyền', status: 'sufficiently_evidenced', answer: 'Đã xác minh', citations: [{ document_title: 'Luật Đất đai', law_number: '31/2024/QH15', article_number: '137', source_url: 'https://official.example/land' }] },
          { issue_id: 'b', title: 'Hồ sơ', status: 'partially_evidenced', guidance: 'Hướng dẫn chung', limitation: 'Thiếu nguồn', citations: [] },
          { issue_id: 'c', title: 'Lệ phí', status: 'insufficiently_evidenced', limitation: 'Chưa xác minh', citations: [], clarifying_question: 'Bạn hỏi thủ tục nào?' },
        ]}
      />,
    )

    expect(screen.getAllByTestId('answer-section')).toHaveLength(3)
    expect(screen.getByTestId('answer-section-status-sufficiently_evidenced')).toHaveTextContent('Đã xác minh')
    expect(screen.getByTestId('answer-section-status-partially_evidenced')).toHaveTextContent('Hướng dẫn tham khảo')
    expect(screen.getByTestId('answer-section-status-insufficiently_evidenced')).toHaveTextContent('Chưa đủ căn cứ')
    expect(screen.queryByText('Legacy aggregate must not be the primary section rendering')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /31\/2024\/QH15/i })).toHaveAttribute('href', 'https://official.example/land')
  })
})
