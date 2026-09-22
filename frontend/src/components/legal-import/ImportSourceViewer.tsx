'use client'

import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, FileText, Expand } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'

export interface ImportExtractionDetails {
  total_pages?: number
  processed_pages?: number
  page_count?: number
  complete?: boolean
  truncated?: boolean
  failed_pages?: number[]
  ocr_status?: string
  reason?: string
  extractor_used?: string
  table_count?: number
  table_extracted_count?: number
  pages_without_text?: number[]
  native_text_pages?: number[]
  ocr_requested_pages?: number[]
  ocr_pages?: number[]
  page_extractors?: Record<string, string>
  coverage_percent?: number
  file_fingerprint?: string
  deferred_ocr?: boolean
  cache_hit?: boolean
  extraction_job_id?: string
  file_id?: string
  extraction_status?: 'processing' | 'complete' | 'partial' | 'error'
}

export function ImportSourceViewer({name, file, content, details, onChange, open, onOpenChange, readOnly = false}: {
  name: string; file?: File | null; content: string; details?: ImportExtractionDetails | null
  onChange: (text: string) => void; open: boolean; onOpenChange: (open: boolean) => void
  readOnly?: boolean
}) {
  const [url, setUrl] = useState<string>()
  const [previewPage, setPreviewPage] = useState(1)
  useEffect(() => {
    setPreviewPage(1)
    if (details?.file_id) {
      const base = `/api/media/files/${encodeURIComponent(details.file_id)}`
      setUrl(base)
      return
    }
    if (!file) { setUrl(undefined); return }
    const value = URL.createObjectURL(file)
    setUrl(value)
    return () => URL.revokeObjectURL(value)
  }, [details?.file_id, file])
  const total = details?.total_pages ?? details?.page_count
  const processed = details?.processed_pages
  const partial = details?.complete === false || details?.truncated || !!details?.failed_pages?.length || !!details?.pages_without_text?.length
  const coverage = details?.coverage_percent ?? (total && processed != null ? Math.round(processed / total * 100) : undefined)
  const summary = total ? `${processed ?? '?'} / ${total} trang đã xử lý${coverage != null ? ` · phủ ${coverage}%` : ''}` : 'Chưa có số trang để đối chiếu'
  const pdf = file?.type === 'application/pdf' || file?.name.toLowerCase().endsWith('.pdf')
  const processing = details?.extraction_status === 'processing' || details?.ocr_status === 'pending' || details?.ocr_status === 'queued'
  const serverPreviewUrl = details?.file_id && url ? `${url}/preview?page=${previewPage}` : undefined
  const previewTotal = Math.max(1, total || 1)
  return <>
    <button type="button" onClick={() => onOpenChange(true)} aria-busy={processing} className="flex w-full min-w-0 items-center gap-3 rounded-lg border p-4 text-left hover:bg-muted/50 focus-visible:outline focus-visible:outline-2">
      <FileText className="h-6 w-6 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1"><span className="block truncate font-medium">{name || 'Nội dung nhập trực tiếp'}</span><span className="block text-sm text-muted-foreground">{summary} · {content.length.toLocaleString('vi-VN')} ký tự</span><span className="block text-sm text-primary">Mở toàn văn và đối chiếu bản gốc</span></span><Expand className="h-4 w-4 shrink-0" aria-hidden="true" />
    </button>
    {processing && <div role="status" aria-live="polite" className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <div className="flex items-center justify-between gap-3"><span>Đang OCR {details?.ocr_requested_pages?.length || Math.max((total || 0) - (processed || 0), 0)} trang ảnh trong nền…</span><span className="tabular-nums">{coverage ?? 0}%</span></div>
      <div className="h-2 overflow-hidden rounded-full bg-amber-100"><div className="h-full rounded-full bg-amber-600" style={{width: `${Math.max(4, coverage ?? 0)}%`}} /></div>
      <p>Bạn có thể tiếp tục xem trang đã đọc; nút kiểm tra sẽ mở lại khi xử lý xong.</p>
    </div>}
    <p className="text-sm text-muted-foreground">{processing ? 'Hệ thống đang tiếp tục đọc các trang còn lại; không cần gửi duyệt trước.' : partial ? 'Có trang chưa đọc đủ hoặc chưa có chữ. Mở nguồn để kiểm tra trước khi duyệt.' : 'Số trang và ký tự thể hiện phần đã xử lý; cần đối chiếu bảng, ảnh và phụ lục với bản gốc.'}</p>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[90dvh] w-[96vw] !max-w-[1500px] flex-col overflow-hidden">
        <DialogHeader><DialogTitle className="break-words pr-6">{name || 'Nội dung nhập trực tiếp'}</DialogTitle><DialogDescription>{summary} · {content.length.toLocaleString('vi-VN')} ký tự · {readOnly ? 'Bản đã lưu trong đề xuất.' : 'Có thể sửa nội dung trước khi gửi duyệt.'}</DialogDescription></DialogHeader>
        <div className="flex flex-wrap gap-3 text-sm">
          {details?.table_count != null && <span>{details.table_count} bảng nhận diện</span>}
          {details?.table_extracted_count != null && <span>{details.table_extracted_count} bảng đã trích cấu trúc</span>}
          {!!details?.native_text_pages?.length && <span>PyMuPDF: trang {details.native_text_pages.join(', ')}</span>}
          {!!details?.ocr_pages?.length && <span>OCR tiếng Việt: trang {details.ocr_pages.join(', ')}</span>}
          {!!details?.pages_without_text?.length && <span className="text-amber-700">Trang ít/không có chữ: {details.pages_without_text.join(', ')}</span>}
          {!!details?.failed_pages?.length && <span className="text-destructive">Trang lỗi: {details.failed_pages.join(', ')}</span>}
          {details?.ocr_status && <span>OCR: {({ok:'Đã nhận dạng',cached:'Dùng kết quả đã đọc',not_required:'Có lớp chữ',partial:'Chưa đầy đủ',failed:'Chưa đọc được',unavailable:'Chưa có công cụ',pending:'Đang xử lý',queued:'Chờ xử lý nền'} as Record<string,string>)[details.ocr_status] || details.ocr_status}</span>}
        </div>
        <div className={`grid min-h-0 flex-1 gap-4 ${url && pdf ? 'md:grid-cols-2' : ''}`}>
          {url && pdf && <div className="flex min-h-0 flex-col gap-2"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">PDF gốc — giữ nguyên bảng và hình ảnh</span>
            {serverPreviewUrl && <div className="flex items-center gap-2" aria-label="Chuyển trang PDF gốc"><Button type="button" variant="outline" size="sm" aria-label="Trang PDF trước" disabled={previewPage <= 1} onClick={() => setPreviewPage(page => Math.max(1, page - 1))}><ChevronLeft className="h-4 w-4" aria-hidden="true" /></Button><span className="min-w-20 text-center text-sm tabular-nums">Trang {previewPage} / {previewTotal}</span><Button type="button" variant="outline" size="sm" aria-label="Trang PDF sau" disabled={previewPage >= previewTotal} onClick={() => setPreviewPage(page => Math.min(previewTotal, page + 1))}><ChevronRight className="h-4 w-4" aria-hidden="true" /></Button></div>}
          </div>
            {serverPreviewUrl ? <div className="min-h-0 flex-1 overflow-auto rounded border bg-muted/20 p-2"><img src={serverPreviewUrl} alt={`Trang ${previewPage} PDF gốc để đối chiếu`} className="mx-auto h-auto max-w-full shadow-sm" /></div> : <iframe title="Bản PDF gốc để đối chiếu" src={url} className="min-h-0 flex-1 rounded border" />}
            <a href={url} target="_blank" rel="noreferrer" className="text-sm underline">Mở PDF ở tab riêng (toàn bộ trang)</a>
          </div>}
          <div className="flex min-h-0 flex-col gap-2"><label htmlFor="legal-content-full" className="font-medium">Toàn bộ nội dung đã trích xuất</label><Textarea id="legal-content-full" readOnly={readOnly} value={content} onChange={event => onChange(event.target.value)} className="min-h-0 flex-1 resize-none overflow-auto whitespace-pre-wrap font-mono text-sm" placeholder="Dán nội dung văn bản vào đây…" /></div>
        </div>
        <div className="flex justify-end"><Button type="button" onClick={() => onOpenChange(false)}>Xong — quay lại thông tin</Button></div>
      </DialogContent>
    </Dialog>
  </>
}
