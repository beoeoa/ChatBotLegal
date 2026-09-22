import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { sanitizeDisplayAnswer, StreamingResponse } from './StreamingResponse'

describe('section-grounding display sanitizer', () => {
  it('keeps the instrument identity in compact citizen citations', () => {
    const result = sanitizeDisplayAnswer('Hồ sơ [E1].', [{ evidence_ids: ['E1'], article_number: '16', document_title: 'Hộ tịch', law_number: '60/2014/QH13', viewer_url: '/legal-documents/37879?article=16' }], true)
    expect(result).toContain('Điều 16, 60/2014/QH13')
    expect(result).not.toContain('Nguồn 1')
  })
  it('keeps code and explicit URLs intact without legal evidence markers', () => {
    const code = '```cpp\nint main() {\n    return sum(1,  2);\n}\n```'
    const link = '[Nguồn](https://official.example/a(b)#ref-source-original)'
    const answer = `${code}\n\nDùng \`sum()\` và ${link}`
    expect(sanitizeDisplayAnswer(answer)).toBe(answer)
  })
  it('preserves direct paragraph structure, code, exceptions and backend URLs', () => {
    const text = 'Điều kiện A.  \nNgoại lệ B [E1].\n\n`[E1]`\n\n[Điều 2](https://official.example/explicit)';
    const rendered = sanitizeDisplayAnswer(text, [{evidence_ids:['E1'], article_number:'2', source_url:'https://official.example/source', viewer_url:'https://official.example/source'}], true);
    expect(rendered).toContain('Điều kiện A.  \nNgoại lệ B');
    expect(rendered).toContain('`[E1]`');
    expect(rendered).toContain('[Điều 2](https://official.example/explicit)');
    expect(rendered).toContain('(<https://official.example/source>)');
    expect(rendered).not.toContain('#:~:text=');
  });
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
        answerStatus="partial"
        answerRoute="general_legal"
        evidenceCount={0}
      />,
    )

    expect(screen.getByTestId('legal-answer-card')).toHaveTextContent('Câu trả lời duy nhất từ projection V1.')
    expect(screen.queryByTestId('structured-legal-answer')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy prose must not render twice')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy structured section must not render twice')).not.toBeInTheDocument()
  })

  it('does not render delivery-state banners around the raw answer', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Cần bổ sung thông tin để xác định đúng thủ tục."
        groundingStatus="fully_grounded"
        answerStatus="cannot_verify"
        fallbackTier="support"
        evidenceCount={0}
        blockedReason="approved_current_source_not_found"
      />,
    )

    expect(screen.queryByTestId('answer-status-banner')).not.toBeInTheDocument()
    expect(screen.queryByText('Căn cứ hợp lệ')).not.toBeInTheDocument()
    expect(screen.getByText('Cần bổ sung thông tin để xác định đúng thủ tục.')).toBeInTheDocument()
  })

  it('removes legacy internal citation metadata from visible answer text', () => {
    const rendered = sanitizeDisplayAnswer(
      'Nguồn [legal:481400 - 60/2014/QH13 - Điều 35] #ref-source-481400 chunk_id=abc trace_id=t-1',
      [{ chunk_id: '481400', law_number: '60/2014/QH13', article_number: '35' }],
    )

    expect(rendered).not.toMatch(/#ref-source|legal:|chunk_id|trace_id/i)
    expect(rendered).toContain('60/2014/QH13')
  })

  it('replaces direct evidence markers with their source links without losing punctuation', () => {
    const rendered = sanitizeDisplayAnswer(
      'Theo quy định tại [E1], hồ sơ được tiếp nhận. Quy định sửa đổi bởi [E2].',
      [{
        evidence_ids: ['E1', 'E2'],
        document_title: 'Luật Cư trú',
        law_number: '68/2020/QH14',
        article_number: '28',
        clause_number: '2',
        source_url: 'https://example.test/luat-cu-tru',
      }],
    )

    const linkedCitation = '[khoản 2 Điều 28 Luật Cư trú số 68/2020/QH14](<https://example.test/luat-cu-tru>)'
    expect(rendered).toBe(
      `Theo quy định tại ${linkedCitation}, hồ sơ được tiếp nhận. Quy định sửa đổi bởi ${linkedCitation}.`,
    )
    expect(rendered).not.toMatch(/\[E\d+\]/)
  })

  it('replaces an unresolved internal marker with a local readable warning', () => {
    const rendered = sanitizeDisplayAnswer('Theo quy định tại [E99], hồ sơ được tiếp nhận.', [])

    expect(rendered).toBe('Theo quy định tại nguồn chưa liên kết, hồ sơ được tiếp nhận.')
    expect(rendered).not.toContain('[E99]')
  })

  it('uses the legal instrument kind when a citation has no document title', () => {
    const rendered = sanitizeDisplayAnswer('Căn cứ [E1], người dân nộp hồ sơ.', [{
      evidence_ids: ['E1'],
      law_number: '154/2024/NĐ-CP',
      article_number: '5',
      source_url: 'https://vbpl.vn/154',
    }])

    expect(rendered).toBe(
      'Căn cứ [Điều 5 Nghị định số 154/2024/NĐ-CP](<https://vbpl.vn/154>), người dân nộp hồ sơ.',
    )
  })

  it('does not expose an invalid Điều 0 label', () => {
    const rendered = sanitizeDisplayAnswer('Nguồn [E1].', [{
      evidence_ids: ['E1'],
      law_number: '23/2026/NQ-HĐND',
      article_number: '0',
      source_url: 'https://vbpl.vn/23-2026',
    }])

    expect(rendered).toBe(
      'Nguồn [Nghị quyết số 23/2026/NQ-HĐND](<https://vbpl.vn/23-2026>).',
    )
    expect(rendered).not.toContain('Điều 0')
  })

  it('demotes model-emitted H1 while preserving the answer content', () => {
    const rendered = sanitizeDisplayAnswer('# Thủ tục đăng ký tạm trú\n\nNội dung hướng dẫn.')

    expect(rendered).toBe('## Thủ tục đăng ký tạm trú\n\nNội dung hướng dẫn.')
  })

  it('renders one role-aware legal answer shell with distinct grounding states', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Legacy aggregate must not be the primary section rendering"
        answerSections={[
          { issue_id: 'a', title: 'Thẩm quyền', status: 'sufficiently_evidenced', answer: 'Áp dụng Điều 137 Luật 31/2024/QH15.', citations: [{ document_title: 'Luật Đất đai', law_number: '31/2024/QH15', article_number: '137', source_url: 'https://official.example/land' }], facet: 'rule', priority: 'critical', claim_types: ['rule'] },
          { issue_id: 'b', title: 'Hồ sơ', status: 'partially_evidenced', guidance: 'Hướng dẫn chung', limitation: 'Thiếu nguồn', citations: [], facet: 'documents', priority: 'high', claim_types: ['documents'] },
          { issue_id: 'c', title: 'Lệ phí', status: 'insufficiently_evidenced', limitation: 'Chưa xác minh', citations: [], clarifying_question: 'Bạn hỏi thủ tục nào?' },
        ]}
      />,
    )

    expect(screen.getByTestId('structured-legal-answer')).toBeInTheDocument()
    expect(screen.getByTestId('structured-legal-answer')).toHaveAccessibleName('Hướng dẫn dành cho người dân')
    expect(screen.getByText('Kết luận ngắn')).toBeInTheDocument()
    expect(screen.getAllByTestId('answer-section')).toHaveLength(3)
    expect(screen.queryByTestId('answer-section-status-sufficiently_evidenced')).not.toBeInTheDocument()
    expect(screen.queryByTestId('answer-section-status-partially_evidenced')).not.toBeInTheDocument()
    expect(screen.queryByTestId('answer-section-status-insufficiently_evidenced')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy aggregate must not be the primary section rendering')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Điều 137 Luật 31\/2024\/QH15/i })).toHaveAttribute('href', 'https://official.example/land#:~:text=%C4%90i%E1%BB%81u%20137')
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

    expect(screen.getByTestId('structured-legal-answer')).toHaveAccessibleName('Hướng dẫn nghiệp vụ cán bộ')
    expect(screen.getByText('Kết luận nghiệp vụ')).toBeInTheDocument()
    expect(screen.queryByText('Căn cứ hợp lệ')).not.toBeInTheDocument()
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

    expect(screen.queryByTestId('grounding-status-badge')).not.toBeInTheDocument()
    expect(screen.queryByTestId('answer-completeness-badge')).not.toBeInTheDocument()
    expect(screen.queryByText('Trả lời đầy đủ')).not.toBeInTheDocument()
  })

  it('does not render model-mode or grounding badges around the raw answer', () => {
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

    expect(screen.queryByTestId('answer-mode-badge')).not.toBeInTheDocument()
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

    expect(screen.getByText((_, element) => element?.tagName === 'LI' && element.textContent === `Kết luận: ${repeated}`)).toBeInTheDocument()
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

  it('links an article mentioned inside the answer and omits a separate source block', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Áp dụng Điều 7 để giải quyết."
        citations={[{
          law_number: '02/2011/QH13',
          article_number: '7',
          source_url: 'https://vanban.chinhphu.vn/source',
        }]}
      />,
    )

    expect(screen.getByRole('link', { name: 'Điều 7' })).toHaveAttribute(
      'href',
      'https://vanban.chinhphu.vn/source#:~:text=%C4%90i%E1%BB%81u%207',
    )
    expect(screen.queryByText('Căn cứ pháp lý')).not.toBeInTheDocument()
  })

  it('renders a direct marker as a readable citation that opens the exact VBPL article', () => {
    render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Theo quy định tại [E1], cơ quan đăng ký cư trú giải quyết hồ sơ."
        citations={[{
          evidence_ids: ['E1'],
          law_number: '68/2020/QH14',
          article_number: '28',
          source_url: 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146648&dvid=13#:~:text=Điều%205',
        }]}
      />,
    )

    expect(screen.getByRole('link', { name: 'Điều 28, 68/2020/QH14' })).toHaveAttribute(
      'href',
      'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146648&dvid=13#:~:text=%C4%90i%E1%BB%81u%2028',
    )
    expect(screen.getByText(/cơ quan đăng ký cư trú giải quyết hồ sơ/)).toBeInTheDocument()
  })

  it('adds a respectful UI salutation once without changing the model answer body', () => {
    const { rerender } = render(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Nội dung do mô hình trả về."
        salutation="Thưa anh/chị Nguyễn Văn An,"
      />,
    )
    expect(screen.getByTestId('answer-salutation')).toHaveTextContent('Thưa anh/chị Nguyễn Văn An,')
    expect(screen.getByText('Nội dung do mô hình trả về.')).toBeInTheDocument()

    rerender(
      <StreamingResponse
        isStreaming={false}
        strategy={null}
        answers={[]}
        finalAnswer="Thưa anh/chị Nguyễn Văn An, nội dung đã có lời chào."
        salutation="Thưa anh/chị Nguyễn Văn An,"
      />,
    )
    expect(screen.queryByTestId('answer-salutation')).not.toBeInTheDocument()
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

    fireEvent.click(screen.getByRole('button', { name: /Quy trình tra cứu/i }))
    expect(screen.getByTestId('form-provenance')).toHaveTextContent('1 biểu mẫu bị loại')
    expect(screen.getByTestId('admin-evidence-coverage')).toHaveTextContent('1 nhóm nội dung')
  })
})
