import { describe, expect, it } from 'vitest'

import { crawlerErrorCopy, extractionReasonCopy } from './crawler-copy'

describe('crawler user copy', () => {
  it('translates extraction machine codes', () => {
    expect(extractionReasonCopy('document_type_missing|issuing_agency_missing|issued_date_missing'))
      .toBe('Thiếu loại văn bản. Thiếu cơ quan ban hành. Thiếu ngày ban hành.')
  })

  it('never exposes Crawl4AI anti-bot diagnostics', () => {
    const message = crawlerErrorCopy(
      'không kiểm tra được robots.txt qua Crawl4AI: Blocked by anti-bot protection: Structural: minimal_text',
    )
    expect(message).toMatch(/Chưa kiểm tra được quy tắc truy cập/)
    expect(message).not.toMatch(/Crawl4AI|anti-bot|minimal_text/)
  })

  it('explains when a listing has no exact records', () => {
    expect(crawlerErrorCopy('listing_no_exact_records: empty')).toMatch(/từng văn bản cụ thể/)
  })

  it('removes the persisted English import-job prefix', () => {
    const message = crawlerErrorCopy('Import job failed: Thiếu ngày ban hành.')
    expect(message).toContain('Không nhập được văn bản vào kho')
    expect(message).toContain('Thiếu ngày ban hành')
    expect(message).not.toContain('Import job failed')
  })
})
