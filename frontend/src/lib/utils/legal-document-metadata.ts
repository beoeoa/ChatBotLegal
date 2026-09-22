export interface DetectedLegalDocumentMetadata {
  title?: string
  law_number?: string
  document_type?: string
  issuing_agency?: string
  issued_date?: string
}

/**
 * Deterministic, best-effort hints for an Admin upload form.
 *
 * These values are never treated as verified legal metadata: the Admin still
 * sees and confirms every field before import or replacement.
 */
export function detectLegalDocumentMetadata(
  content: string,
  filename: string,
): DetectedLegalDocumentMetadata {
  const head = `${filename.replace(/[-_]+/g, ' ')}\n${content.slice(0, 12_000)}`
  const lawNumber = head.match(/\b\d{1,4}\s*\/\s*\d{4}\s*\/\s*[A-ZĐ0-9-]{2,20}\b/iu)?.[0]
    ?.replace(/\s+/g, '')
  const normalizedHead = head.toLocaleUpperCase('vi')
  const documentType = normalizedHead.includes('NGHỊ ĐỊNH') || lawNumber?.includes('/NĐ-CP')
    ? 'Nghị định'
    : normalizedHead.includes('NGHỊ QUYẾT') || lawNumber?.includes('/NQ-')
      ? 'Nghị quyết'
      : normalizedHead.includes('THÔNG TƯ') || lawNumber?.includes('/TT-')
        ? 'Thông tư'
        : normalizedHead.includes('QUYẾT ĐỊNH') || lawNumber?.includes('/QĐ-')
          ? 'Quyết định'
          : normalizedHead.includes('LUẬT') || lawNumber?.match(/\/QH\d*$/i)
            ? 'Luật'
            : undefined
  const issuingAgency = normalizedHead.includes('CHÍNH PHỦ')
    ? 'Chính phủ'
    : normalizedHead.includes('QUỐC HỘI')
      ? 'Quốc hội'
      : normalizedHead.includes('THỦ TƯỚNG CHÍNH PHỦ')
        ? 'Thủ tướng Chính phủ'
        : undefined
  const signedDate = head.match(/(?:Hà\s+Nội[^\n,]*,?\s*)?ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})/iu)
  const issuedDate = signedDate
    ? `${signedDate[3]}-${signedDate[2].padStart(2, '0')}-${signedDate[1].padStart(2, '0')}`
    : undefined
  const fallbackTitle = filename.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim()
  return {
    title: documentType && lawNumber ? `${documentType} số ${lawNumber}` : fallbackTitle,
    law_number: lawNumber,
    document_type: documentType,
    issuing_agency: issuingAgency,
    issued_date: issuedDate,
  }
}
