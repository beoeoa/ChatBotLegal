import { formatApiError } from './error-handler'

const EXTRACTION_REASON_COPY: Record<string, string> = {
  loading_placeholder: 'Trang nguồn chưa tải xong nội dung.',
  domain_unresolved: 'Chưa xác định được nhóm nghiệp vụ phù hợp.',
  content_too_short: 'Nội dung đọc được quá ngắn để kiểm tra.',
  law_number_missing: 'Thiếu số hoặc ký hiệu văn bản.',
  document_type_missing: 'Thiếu loại văn bản.',
  issuing_agency_missing: 'Thiếu cơ quan ban hành.',
  issued_date_missing: 'Thiếu ngày ban hành.',
  effective_date_missing: 'Thiếu ngày có hiệu lực.',
}

function rawMessage(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return rawMessage(record.detail ?? record.message ?? record.error ?? record.reason)
  }
  return ''
}

export function crawlerErrorCopy(
  value: unknown,
  fallback = 'Không thể đọc nguồn này. Dữ liệu hiện có không bị thay đổi.',
): string {
  const raw = rawMessage(value)
  const lower = raw.toLocaleLowerCase('vi-VN')
  if (!raw) return fallback
  if (lower.startsWith('import/embed failed:') || lower.startsWith('import job failed:')) {
    return 'Không nhập được văn bản vào kho. ' + formatApiError(raw.slice(raw.indexOf(':') + 1).trim(), 'Hãy kiểm tra lại nguồn chính thức và thử lại.')
  }
  if (lower.includes('listing_no_exact_records') || lower.includes('không trả bản ghi văn bản có thể kiểm chứng')) {
    return 'Chưa tìm thấy liên kết tới từng văn bản cụ thể. Hãy chọn trang danh sách có nút mở chi tiết từng văn bản hoặc đổi loại nội dung cần lấy.'
  }
  if (lower.includes('robots.txt') && lower.includes('không cho phép')) {
    return 'Website không cho phép công cụ tự động truy cập đường dẫn này. Hãy dùng API, sitemap, RSS, tệp PDF chính thức hoặc chọn nguồn khác.'
  }
  if (lower.includes('robots.txt')) {
    return 'Chưa kiểm tra được quy tắc truy cập tự động của website. Nguồn chưa được quét và dữ liệu cũ vẫn được giữ nguyên.'
  }
  if (
    lower.includes('anti-bot') || lower.includes('access denied') ||
    lower.includes('cloudflare') || lower.includes('datadome') ||
    lower.includes('minimal_text') || lower.includes('structural:')
  ) {
    return 'Website đang hạn chế truy cập tự động hoặc chưa trả đủ nội dung. Hãy bật “Tương thích cao”, kiểm tra lại đường dẫn, hoặc dùng API/PDF chính thức.'
  }
  if (lower.includes('crawl4ai') || lower.includes('render failed') || lower.includes('crawl unsuccessful')) {
    return 'Trình đọc website không tải được nội dung. Hãy thử chế độ “Tương thích cao” hoặc kiểm tra nguồn chính thức khác.'
  }
  if (lower.includes('selector')) {
    return 'Quy tắc nhận diện liên kết chưa phù hợp với trang này. Hãy quét thử lại hoặc để hệ thống tự nhận diện.'
  }
  if (lower.includes('timeout') || lower.includes('timed out')) {
    return 'Website phản hồi quá chậm. Hãy thử chế độ “Tương thích cao” hoặc quét lại sau.'
  }
  return formatApiError(value, fallback)
}

export function extractionReasonCopy(value?: string | null): string {
  if (!value) return ''
  const codes = value.split(/[|,;]+/).map((item) => item.trim()).filter(Boolean)
  const translated = codes.map((code) => EXTRACTION_REASON_COPY[code])
  if (translated.every(Boolean)) return translated.join(' ')
  return crawlerErrorCopy(value, 'Nội dung cần được kiểm tra thủ công trước khi duyệt.')
}

export function crawlerCandidateTypeCopy(value?: string | null): string {
  if (value === 'document') return 'Văn bản pháp luật'
  if (value === 'procedure') return 'Thủ tục hành chính'
  if (value === 'form') return 'Biểu mẫu'
  if (value === 'reference') return 'Tài liệu tham khảo'
  return 'Nội dung cần phân loại'
}
