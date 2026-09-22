import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enrichMarkdownWithArticleLinks, resolveLegalArticleTarget, formatLegalCitationLabel } from './legal-article-parser'

describe('legal-article-parser', () => {
  it('uses concise verified instrument labels while retaining named laws', () => {
    expect(formatLegalCitationLabel({ document_title: 'Nghị quyết về tổ chức chính quyền', law_number: '123/2025/QH15' }))
      .toBe('Nghị quyết số 123/2025/QH15')
    expect(formatLegalCitationLabel({ document_title: 'Bộ luật Dân sự', law_number: '91/2015/QH13' }))
      .toBe('Bộ luật Dân sự số 91/2015/QH13')
    expect(formatLegalCitationLabel({ article_number: '16', document_title: 'Hộ tịch', law_number: '60/2014/QH13' }))
      .toBe('Điều 16 Luật Hộ tịch số 60/2014/QH13')
    expect(formatLegalCitationLabel({ article_number: '9', document_title: 'Quy định chi tiết một số điều và biện pháp thi hành Luật Hộ tịch', law_number: '123/2015/NĐ-CP' }))
      .toBe('Điều 9 Nghị định số 123/2015/NĐ-CP')
  })
  it('never rebinds a backend evidence link to a different article of the same law', () => {
    const text = '[Điều 2](<https://official.example/exact#clause-3>) và Điều 5';
    expect(enrichMarkdownWithArticleLinks(text, [{evidence_ids:['E1'], law_number:'1/2026/TEST', article_number:'5', viewer_url:'/legal-documents/other?article=5'}])).toBe(text);
  });
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_CHAT_PRECISE_LEGAL_LINKS_V1_ENABLED', 'true')
  })
  it('resolves internal document links only from verified backend metadata', () => {
    const target = resolveLegalArticleTarget('Điều 9 Nghị định 123/2015/NĐ-CP', [{
      doc_id: 'legal:123',
      law_number: '123/2015/NĐ-CP',
      article_number: '9',
    }])
    expect(target).not.toBeNull()
    expect(target?.url).toBe('/legal-documents/123?article=9')
    expect(target?.isInternal).toBe(true)
    expect(target?.articleNumber).toBe('9')
  })

  it('keeps an unverified provision bold without inventing a viewer id', () => {
    const text = 'Căn cứ vào Điều 9 Nghị định 123/2015/NĐ-CP để thực hiện.'
    const enriched = enrichMarkdownWithArticleLinks(text)
    expect(enriched).not.toContain('⚖️')
    expect(enriched).not.toContain('🔗')
    expect(enriched).toBe('Căn cứ vào **Điều 9 Nghị định 123/2015/NĐ-CP** để thực hiện.')
  })

  it('makes every repeated legal reference clickable', () => {
    const text = 'Căn cứ Điều 9 Nghị định 123/2015/NĐ-CP và theo Nghị định 123/2015/NĐ-CP, đồng thời áp dụng Nghị định 123/2015/NĐ-CP.'
    const enriched = enrichMarkdownWithArticleLinks(text, [{
      doc_id: '123',
      law_number: '123/2015/NĐ-CP',
      article_number: '9',
    }])
    const linkMatches = enriched.match(/\[\*\*[^\]]+\*\*\]\([^)]+\)/g) || []
    expect(linkMatches.length).toBe(3)
  })

  it('links a bare point-clause-article reference only when citation metadata identifies one document', () => {
    const text = 'Áp dụng Điểm a Khoản 2 Điều 5 để tiếp nhận hồ sơ.'
    const enriched = enrichMarkdownWithArticleLinks(text, [{
      doc_id: 'legal:154',
      law_number: '154/2024/NĐ-CP',
      article_number: '5',
      clause_number: '2',
      point_number: 'a',
    }])

    expect(enriched).toContain('[**Điểm a Khoản 2 Điều 5**](/legal-documents/154?article=5&clause=2&point=a)')
  })

  it('does not mistake ordinary wording such as luật định for a legal citation', () => {
    expect(enrichMarkdownWithArticleLinks('Thực hiện khi đáp ứng điều kiện luật định.'))
      .toBe('Thực hiện khi đáp ứng điều kiện luật định.')
  })

  it('upgrades a model-authored bold provision into a verified link', () => {
    const enriched = enrichMarkdownWithArticleLinks(
      'Theo **Điều 5 Nghị định 154/2024/NĐ-CP**, hồ sơ được tiếp nhận.',
      [{
        doc_id: 'legal:154',
        law_number: '154/2024/NĐ-CP',
        article_number: '5',
      }],
    )

    expect(enriched).toContain('[**Điều 5 Nghị định 154/2024/NĐ-CP**](/legal-documents/154?article=5)')
    expect(enriched).not.toContain('****Điều')
  })

  it('treats duplicate mirrors of the same law as one resolvable instrument', () => {
    const enriched = enrichMarkdownWithArticleLinks(
      '**Điều 5 Nghị định 154/2024/NĐ-CP**',
      [
        { law_number: '154/2024/NĐ-CP', article_number: '5', source_url: 'https://vanban.chinhphu.vn/154' },
        { law_number: 'Số: 154/2024/NĐ-CP', article_number: '5', source_url: 'https://vbpl.vn/154' },
      ],
    )

    expect(enriched).toBe('[**Điều 5 Nghị định 154/2024/NĐ-CP**](https://vbpl.vn/154#:~:text=%C4%90i%E1%BB%81u%205)')
  })

  it('replaces a stale VBPL fragment with the verified article target', () => {
    const target = resolveLegalArticleTarget('Điều 28 Luật số 68/2020/QH14', [{
      law_number: '68/2020/QH14',
      article_number: '28',
      source_url: 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146648&dvid=13#:~:text=Điều%205',
    }])

    expect(target?.url).toBe(
      'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146648&dvid=13#:~:text=%C4%90i%E1%BB%81u%2028',
    )
  })

  it('does not attach an unrelated form-source URL to a different law number', () => {
    const enriched = enrichMarkdownWithArticleLinks(
      'Áp dụng Nghị định 154/2024/NĐ-CP.',
      [{
        law_number: '',
        document_title: 'Tờ khai thay đổi thông tin cư trú CT01',
        source_url: 'https://vbpl.vn/thong-tu-53-2025',
      }],
    )

    expect(enriched).toBe('Áp dụng **Nghị định 154/2024/NĐ-CP**.')
    expect(enriched).not.toContain('thong-tu-53-2025')
  })

  it('uses an exact instrument number found in a verified citation title', () => {
    const enriched = enrichMarkdownWithArticleLinks(
      'Sử dụng mẫu ban hành kèm theo Thông tư số 53/2025/TT-BCA.',
      [{
        law_number: '',
        document_title: 'Tờ khai CT01 ban hành kèm theo Thông tư số 53/2025/TT-BCA',
        source_url: 'https://vbpl.vn/thong-tu-53-2025',
      }],
    )

    expect(enriched).toContain('[**Thông tư số 53/2025/TT-BCA**](https://vbpl.vn/thong-tu-53-2025)')
  })

  it('preserves existing markdown links without double bracket nesting', () => {
    const text = 'Căn cứ vào [Nghị định 123/2015/NĐ-CP](https://luatvietnam.vn/nghi-dinh-123.html) để thực hiện.'
    const enriched = enrichMarkdownWithArticleLinks(text)
    expect(enriched).not.toContain('[**[')
    expect(enriched).not.toContain('**]**')
    expect(enriched).toBe('Căn cứ vào [**Nghị định 123/2015/NĐ-CP**](https://luatvietnam.vn/nghi-dinh-123.html) để thực hiện.')
  })

  it('uses a verified viewer_url and includes article, clause and point', () => {
    const target = resolveLegalArticleTarget('Điểm b Khoản 3 Điều 7', [{
      viewer_url: '/legal-documents/481400',
      article_number: '7',
      clause_number: '3',
      point_number: 'b',
    }])

    expect(target?.url).toBe('/legal-documents/481400?article=7&clause=3&point=b')
  })
})
