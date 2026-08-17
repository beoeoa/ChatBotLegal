import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { sanitizeDisplayAnswer, StreamingResponse } from './StreamingResponse'

describe('section-grounding display sanitizer', () => {
  it('uses LegalAnswerCard for both legacy and structured payloads when V1 projection is present', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Legacy prose must not render twice"
        answerSections={[{
          issue_id: 'legacy-structured',
          title: 'Legacy structured section',
          status: 'insufficiently_evidenced',
          limitation: 'Legacy structured section must not render twice',
          citations: [],
        }]}
        presentationVersion="legal-answer-v1"
        presentationSections={{
          short_answer: 'Câu trả lời duy nhất từ projection V1.',
          actions: [],
          dossier: [],
          procedure: null,
          recommended_forms: [],
          legal_bases: [],
          caveats: ['Cần làm rõ thêm một phần.'],
          clarifying_questions: [],
        }}
        answerStatus="partial_grounded"
        answerRoute="general_legal"
        evidenceCount={0}
      />,
    )

    expect(screen.getByTestId('legal-answer-card')).toHaveTextContent('Câu trả lời duy nhất từ projection V1.')
    expect(screen.queryByTestId('structured-legal-answer')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy prose must not render twice')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy structured section must not render twice')).not.toBeInTheDocument()
  })

  it('uses the explicit source-gap state and never claims verification with zero evidence', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Cần bổ sung thông tin để xác định đúng thủ tục."
        groundingStatus="fully_grounded"
        answerStatus="source_gap"
        fallbackTier="support"
        evidenceCount={0}
        blockedReason="approved_current_source_not_found"
      />,
    )

    expect(screen.getByTestId('answer-status-banner')).toHaveTextContent('Cần bổ sung nguồn')
    expect(screen.queryByText('Căn cứ hợp lệ')).not.toBeInTheDocument()
    expect(screen.getByText(/Chưa đủ căn cứ hiện hành/)).toBeInTheDocument()
  })

  it('never leaves internal citation or evidence markers in visible answer text', () => {
    const rendered = sanitizeDisplayAnswer(
      'Nguồn [legal:481400 - 60/2014/QH13 - Điều 35] #ref-source-481400 chunk_id=abc trace_id=t-1',
      [{ chunk_id: '481400', law_number: '60/2014/QH13', article_number: '35' }],
    )

    expect(rendered).not.toMatch(/#ref-source|legal:|chunk_id|trace_id/i)
    expect(rendered).toContain('60/2014/QH13')
  })

  it('renders one role-aware legal answer shell with distinct grounding states', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Legacy aggregate must not be the primary section rendering"
        answerSections={[
          { issue_id: 'a', title: 'Thẩm quyền', status: 'sufficiently_evidenced', answer: 'Đã xác minh', citations: [{ document_title: 'Luật Đất đai', law_number: '31/2024/QH15', article_number: '137', source_url: 'https://official.example/land' }], facet: 'rule', priority: 'critical', claim_types: ['rule'] },
          { issue_id: 'b', title: 'Hồ sơ', status: 'partially_evidenced', guidance: 'Hướng dẫn chung', limitation: 'Thiếu nguồn', citations: [], facet: 'documents', priority: 'high', claim_types: ['documents'] },
          { issue_id: 'c', title: 'Lệ phí', status: 'insufficiently_evidenced', limitation: 'Chưa xác minh', citations: [], clarifying_question: 'Bạn hỏi thủ tục nào?' },
        ]}
      />,
    )

    expect(screen.getByTestId('structured-legal-answer')).toBeInTheDocument()
    expect(screen.getByText('Hướng dẫn dành cho người dân')).toBeInTheDocument()
    expect(screen.getByText('Kết luận ngắn')).toBeInTheDocument()
    expect(screen.getAllByTestId('answer-section')).toHaveLength(3)
    expect(screen.getByTestId('answer-section-status-sufficiently_evidenced')).toHaveTextContent('Đã xác minh')
    expect(screen.getByTestId('answer-section-status-partially_evidenced')).toHaveTextContent('Kết luận có điều kiện')
    expect(screen.getByTestId('answer-section-status-insufficiently_evidenced')).toHaveTextContent('Cần bổ sung thông tin')
    expect(screen.queryByText('Legacy aggregate must not be the primary section rendering')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /31\/2024\/QH15/i })).toHaveAttribute('href', 'https://official.example/land')
  })

  it('uses the officer presentation and highlights a verified next action', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        role="officer"
        groundingStatus="fully_grounded"
        finalAnswer="Nội dung tổng hợp"
        answerSections={[
          {
            issue_id: 'officer-action',
            title: 'Đề xuất xử lý',
            status: 'sufficiently_evidenced',
            answer: 'Kiểm tra tính đầy đủ của hồ sơ.',
            citations: [],
            facet: 'procedure',
            priority: 'critical',
            claim_types: ['next_action'],
          },
        ]}
      />,
    )

    expect(screen.getByText('Hướng dẫn nghiệp vụ cán bộ')).toBeInTheDocument()
    expect(screen.getByText('Kết luận nghiệp vụ')).toBeInTheDocument()
    expect(screen.getByText('Căn cứ hợp lệ')).toBeInTheDocument()
  })

  it('separates valid grounding from an incomplete answer', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        role="citizen"
        groundingStatus="fully_grounded"
        answerCompleteness={{
          status: 'incomplete',
          coverage_ratio: 0.25,
          source_unit_count: 4,
          covered_unit_count: 1,
          required_checks: ['coverage', 'order', 'plain_language', 'practical_meaning'],
          checks: { coverage: false, order: false, plain_language: false, practical_meaning: false },
          reason_codes: ['missing_coverage'],
        }}
        finalAnswer="Nội dung tổng hợp"
        answerSections={[{
          issue_id: 'rule',
          title: 'Kết luận',
          status: 'sufficiently_evidenced',
          answer: 'Một đoạn trích có nguồn.',
          citations: [{ document_title: 'Văn bản kiểm thử', effective_status: 'active' }],
          facet: 'rule',
          priority: 'critical',
          claim_types: ['rule'],
        }]}
      />,
    )

    expect(screen.getByTestId('grounding-status-badge')).toHaveTextContent('Căn cứ hợp lệ')
    expect(screen.getByTestId('answer-completeness-badge')).toHaveTextContent('Trả lời chưa đầy đủ')
    expect(screen.queryByText('Trả lời đầy đủ')).not.toBeInTheDocument()
  })

  it('shows verified condensed mode instead of the normal grounding badge', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Bản rút gọn xác định từ nguồn."
        groundingStatus="fully_grounded"
        answerMode="verified_source_condensed"
        citations={[{ law_number: '60/2014/QH13', source_url: 'https://vbpl.vn/source' }]}
      />,
    )

    expect(screen.getByTestId('answer-mode-badge')).toHaveTextContent(
      'Nguồn đã xác minh nhưng câu trả lời đang ở chế độ rút gọn',
    )
    expect(screen.queryByText('Căn cứ đầy đủ')).not.toBeInTheDocument()
    expect(screen.queryByText('Căn cứ hợp lệ')).not.toBeInTheDocument()
  })

  it('does not repeat the same verified passage across citizen sections', () => {
    const repeated = 'Người sử dụng đất được đăng ký lần đầu khi đáp ứng các điều kiện luật định.'
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        role="citizen"
        groundingStatus="fully_grounded"
        finalAnswer="Nội dung tổng hợp"
        answerSections={[
          {
            issue_id: 'rule',
            title: 'Giá trị giao dịch',
            status: 'sufficiently_evidenced',
            answer: `- Kết luận: ${repeated}`,
            citations: [],
            facet: 'rule',
            priority: 'critical',
            claim_types: ['rule'],
          },
          {
            issue_id: 'condition',
            title: 'Điều kiện cấp',
            status: 'sufficiently_evidenced',
            answer: `- Điều kiện áp dụng: ${repeated}`,
            citations: [],
            facet: 'condition',
            priority: 'critical',
            claim_types: ['condition'],
          },
        ]}
      />,
    )

    expect(screen.getByText(`Kết luận: ${repeated}`)).toBeInTheDocument()
    expect(screen.queryByText(`Điều kiện áp dụng: ${repeated}`)).not.toBeInTheDocument()
    expect(screen.getByText('Nội dung này áp dụng cùng căn cứ đã nêu trong kết luận ngắn ở trên.')).toBeInTheDocument()
  })

  it('states explicitly when an official form was requested but is unavailable', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Hệ thống chưa có biểu mẫu chính thức đã duyệt."
        formsUnavailable
      />,
    )

    expect(screen.getByTestId('forms-unavailable')).toHaveTextContent(
      'Chưa có biểu mẫu chính thức đã duyệt',
    )
  })

  it('links an official citation even when no internal document id exists', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Kết quả đã kiểm chứng."
        citations={[{
          law_number: '02/2011/QH13',
          article_number: '7',
          source_url: 'https://vanban.chinhphu.vn/source',
        }]}
      />,
    )

    expect(screen.getByRole('link', { name: 'Xem nguồn chính thức' })).toHaveAttribute(
      'href',
      'https://vanban.chinhphu.vn/source',
    )
  })

  it('renders reviewed form code, procedure, source, format and effectivity', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Mẫu đã được xác minh."
        recommendedForms={[{
          name: 'Đơn đăng ký biến động đất đai',
          form_code: '09/ĐK',
          procedure_id: 'sang_ten_so_do',
          procedure_name: 'Đăng ký biến động đất đai',
          file_type: 'pdf',
          download_url: 'https://dichvucong.gov.vn/files/09-dk.pdf',
          source_url: 'https://dichvucong.gov.vn/p/home/procedure',
          effective_from: '2025-01-01',
          official_level: 'official',
          review_status: 'approved',
          has_official_file: true,
          audience: 'citizen',
          usage: 'applicant_form',
        }]}
      />,
    )

    expect(screen.getByTestId('official-forms')).toHaveTextContent('Mã mẫu: 09/ĐK')
    expect(screen.getByTestId('official-forms')).toHaveTextContent('Đăng ký biến động đất đai')
    expect(screen.getByTestId('official-forms')).toHaveTextContent('PDF')
    expect(screen.getByTestId('official-forms')).toHaveTextContent('2025-01-01')
    expect(screen.getByRole('link', { name: /Nguồn biểu mẫu/i })).toHaveAttribute(
      'href',
      'https://dichvucong.gov.vn/p/home/procedure',
    )
  })

  it('shows form provenance and coverage only in an expanded admin trace', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Kết quả kiểm tra"
        role="admin"
        showRagTrace
        ragTrace={{
          evidence_coverage: { documents: { status: 'verified' } },
          form_provenance: {
            requested: true,
            forms_unavailable: true,
            accepted: [],
            rejected: [{ form_id: 'candidate-1', reasons: ['not_approved'] }],
          },
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Quy trình RAG/i }))
    expect(screen.getByTestId('form-provenance')).toHaveTextContent('not_approved')
    expect(screen.getByTestId('admin-evidence-coverage')).toHaveTextContent('verified')
  })
})
