import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImportSourceViewer } from './ImportSourceViewer'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('import source review', () => {
  it('keeps the complete long text in the viewer and supports editing', () => {
    const content = 'Điều 1. Nội dung và phụ lục. '.repeat(10000) + 'KẾT THÚC VĂN BẢN'
    const onChange = vi.fn()
    render(<ImportSourceViewer name="Nguồn thử" content={content} open onOpenChange={() => {}} onChange={onChange} />)
    const field = screen.getByLabelText('Toàn bộ nội dung đã trích xuất')
    expect(field).toHaveValue(content)
    fireEvent.change(field, {target: {value: 'Nội dung đã đối chiếu'}})
    expect(onChange).toHaveBeenCalledWith('Nội dung đã đối chiếu')
  })

  it('shows original PDF, page gaps and detected tables without claiming complete extraction', () => {
    const create = vi.fn(() => 'blob:review-pdf')
    const revoke = vi.fn()
    vi.stubGlobal('URL', {createObjectURL: create, revokeObjectURL: revoke})
    const file = new File(['%PDF'], 'original.pdf', {type: 'application/pdf'})
    const result = render(<ImportSourceViewer name="original.pdf" file={file} content="Phần đã đọc" details={{total_pages: 12, processed_pages: 12, complete: false, pages_without_text: [3], native_text_pages: [1, 2], ocr_pages: [4], coverage_percent: 92, table_count: 2, table_extracted_count: 1}} open onOpenChange={() => {}} onChange={() => {}} />)
    expect(screen.getByTitle('Bản PDF gốc để đối chiếu')).toHaveAttribute('src', 'blob:review-pdf')
    expect(screen.getByText('2 bảng nhận diện')).toBeInTheDocument()
    expect(screen.getByText('1 bảng đã trích cấu trúc')).toBeInTheDocument()
    expect(screen.getByText('PyMuPDF: trang 1, 2')).toBeInTheDocument()
    expect(screen.getByText('OCR tiếng Việt: trang 4')).toBeInTheDocument()
    expect(screen.getByText('Trang ít/không có chữ: 3')).toBeInTheDocument()
    expect(screen.getByText(/Có trang chưa đọc đủ/)).toBeInTheDocument()
    result.unmount()
    expect(revoke).toHaveBeenCalledWith('blob:review-pdf')
  })

  it('uses the authenticated server preview when the upload has a file id', () => {
    const file = new File(['%PDF'], 'original.pdf', {type: 'application/pdf'})
    render(<ImportSourceViewer
      name="original.pdf"
      file={file}
      content="Nội dung"
      details={{file_id: '0123456789abcdef0123456789abcdef', total_pages: 3, processed_pages: 3, complete: true}}
      open
      onOpenChange={() => {}}
      onChange={() => {}}
    />)
    expect(screen.getByAltText('Trang 1 PDF gốc để đối chiếu')).toHaveAttribute(
      'src',
      '/api/media/files/0123456789abcdef0123456789abcdef/preview?page=1',
    )
    fireEvent.click(screen.getByRole('button', {name: 'Trang PDF sau'}))
    expect(screen.getByAltText('Trang 2 PDF gốc để đối chiếu')).toHaveAttribute(
      'src',
      '/api/media/files/0123456789abcdef0123456789abcdef/preview?page=2',
    )
    expect(screen.getByText('Trang 2 / 3')).toBeInTheDocument()
    expect(screen.getByText('Mở PDF ở tab riêng (toàn bộ trang)')).toHaveAttribute(
      'href',
      '/api/media/files/0123456789abcdef0123456789abcdef',
    )
  })

  it('does not offer editing in saved-candidate review mode', () => {
    render(<ImportSourceViewer name="Đề xuất" content="Bản đã lưu" readOnly open onOpenChange={() => {}} onChange={() => {}} />)
    expect(screen.getByLabelText('Toàn bộ nội dung đã trích xuất')).toHaveAttribute('readonly')
  })

  it('shows deferred OCR as a background queue instead of a completed read', () => {
    render(<ImportSourceViewer
      name="scan.pdf"
      content="Phần lớp chữ đã đọc"
      details={{total_pages: 24, processed_pages: 6, complete: false, coverage_percent: 25, ocr_status: 'queued', deferred_ocr: true, ocr_requested_pages: [7, 8]}}
      open
      onOpenChange={() => {}}
      onChange={() => {}}
    />)
    expect(screen.getByText('OCR: Chờ xử lý nền')).toBeInTheDocument()
    expect(screen.getAllByText(/6 \/ 24 trang đã xử lý · phủ 25%/).length).toBeGreaterThan(0)
  })
})
