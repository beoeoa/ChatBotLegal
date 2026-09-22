'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { apiClient } from '@/lib/api/client'
import { formatApiError } from '@/lib/utils/error-handler'
import { Button } from '@/components/ui/button'
import { ImportSourceViewer, type ImportExtractionDetails } from './ImportSourceViewer'

export function CandidateSourceViewer({id, title}: {id: string; title: string}) {
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [source, setSource] = useState<{content: string; details?: ImportExtractionDetails; file?: File}>()
  const show = async () => {
    setLoading(true)
    try {
      const {data} = await apiClient.get(`/legal/crawl/candidates/${encodeURIComponent(id)}`, {timeout: 30000})
      const candidate = data.candidate
      let file: File | undefined
      if (candidate.uploaded_file?.filename) {
        try {
          const original = await apiClient.get(`/legal/crawl/candidates/${encodeURIComponent(id)}/original`, {responseType: 'blob', timeout: 30000})
          file = new File([original.data], candidate.uploaded_file.filename, {type: candidate.uploaded_file.filename.toLowerCase().endsWith('.pdf') ? 'application/pdf' : original.data.type})
        } catch { toast.warning('Chưa tải được tệp gốc. Bạn vẫn có thể xem toàn văn đã lưu.') }
      }
      setSource({content: candidate.content || '', details: candidate.extraction_result, file})
      setOpen(true)
    } catch (error) { toast.error(formatApiError(error)) }
    finally { setLoading(false) }
  }
  return <div className="mt-3 space-y-2">
    <Button type="button" variant="outline" disabled={loading} onClick={show}>{loading ? 'Đang mở nguồn…' : 'Mở toàn văn đã lưu và bản gốc'}</Button>
    {source && <ImportSourceViewer name={title} file={source.file} content={source.content} details={source.details} onChange={() => {}} readOnly open={open} onOpenChange={setOpen} />}
  </div>
}
