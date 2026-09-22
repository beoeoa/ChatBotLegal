import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LegalAnswerCard } from './LegalAnswerCard'

const sections = {
  short_answer: 'Có thể thực hiện thủ tục theo Điều 16 Luật 60/2014/QH13 khi hồ sơ đáp ứng yêu cầu.',
  actions: ['Chuẩn bị tờ khai', 'Nộp tại Ủy ban nhân dân cấp xã'],
  dossier: ['Tờ khai đăng ký khai sinh'],
  procedure: { id: 'dang_ky_khai_sinh', name: 'Đăng ký khai sinh', department: 'Ủy ban nhân dân cấp xã', duration: 'Trong ngày' },
  recommended_forms: [{ form_id: 'to-khai', name: 'Tờ khai đăng ký khai sinh', download_url: 'https://official.example/form' }],
  legal_bases: [{ law_number: '60/2014/QH13', document_title: 'Luật hộ tịch', article_number: '16', source_url: 'https://official.example/law' }],
  caveats: ['Chưa đủ căn cứ để xác định lệ phí.'],
  clarifying_questions: ['Bạn thực hiện đăng ký đúng hạn hay quá hạn?'],
  unverified_explanations: [],
}

describe('LegalAnswerCard', () => {
  it('renders the one stable section order with backend-owned links', () => {
    render(<LegalAnswerCard sections={sections} answerStatus="partial" answerRoute="procedure_form" evidenceCount={1} verificationLabel="Đã xác minh từ nguồn pháp lý" />)

    const card = screen.getByTestId('legal-answer-card')
    const labels = ['Trả lời ngắn', 'Việc cần làm', 'Hồ sơ, giấy tờ', 'Thủ tục thực hiện', 'Biểu mẫu chính thức', 'Lưu ý và phần cần làm rõ']
    let previous = -1
    for (const label of labels) {
      const position = card.textContent?.indexOf(label) ?? -1
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
    expect(screen.getByRole('link', { name: 'Tờ khai đăng ký khai sinh' })).toHaveAttribute('href', 'https://official.example/form')
    expect(screen.getByRole('link', { name: /Điều 16 Luật 60\/2014\/QH13/ })).toHaveAttribute('href', 'https://official.example/law#:~:text=%C4%90i%E1%BB%81u%2016')
    expect(screen.queryByTestId('legal-answer-bases')).not.toBeInTheDocument()
  })

  it('does not show a verified label when evidence count is zero', () => {
    render(<LegalAnswerCard sections={{ ...sections, legal_bases: [] }} evidenceCount={0} verificationLabel="Đã xác minh từ nguồn pháp lý" />)

    expect(screen.queryByTestId('verified-label')).not.toBeInTheDocument()
  })

  it('does not show a separate historical-status badge', () => {
    render(<LegalAnswerCard sections={sections} answerRoute="historical" evidenceCount={1} historicalLabel="Thông tin lịch sử — áp dụng tại ngày 01/06/2020" />)

    expect(screen.queryByTestId('historical-label')).not.toBeInTheDocument()
  })

  it('shows an explicit text and icon warning for retained unverified interpretations', () => {
    render(
      <LegalAnswerCard
        sections={{
          ...sections,
          unverified_explanations: [{ claim: 'Lệ phí là 50.000 đồng.', reason: 'MATERIAL_VALUE_NOT_IN_SOURCE' }],
        }}
        answerStatus="partial"
        evidenceCount={1}
      />,
    )

    expect(screen.getByTestId('legal-answer-unverified')).toHaveTextContent('Diễn giải tham khảo — chưa xác minh')
    expect(screen.getByTestId('legal-answer-unverified')).toHaveTextContent('Lệ phí là 50.000 đồng.')
    expect(screen.getByTestId('legal-answer-unverified').querySelector('svg')).toBeTruthy()
  })
})
