'use client'

import { useEffect, useState } from 'react'
import { Download, FileText, X } from 'lucide-react'
import { apiClient } from '@/lib/api/client'
import { toast } from 'sonner'

export function AttachmentCard({ file, busy, status, error, onRemove, removing = false, removeDisabled = false }: {
  file: File | { name: string; size: number; type: string; file_id?: string }; busy: boolean; status?: 'processing' | 'complete' | 'partial' | 'error'; error?: string | null; onRemove: () => void; removing?: boolean; removeDisabled?: boolean
}) {
  const [preview, setPreview] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const canDownload = file instanceof Blob || ('file_id' in file && !!file.file_id)
  const download = async () => {
    setDownloading(true)
    try {
      const blob = file instanceof Blob ? file : (await apiClient.get(`/media/files/${file.file_id}`, { responseType: 'blob' })).data
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = file.name
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      toast.error('Không tải được tệp gốc. Vui lòng thử lại.')
    } finally { setDownloading(false) }
  }
  useEffect(() => {
    if (!(file instanceof Blob) || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { setPreview(null); return }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  return <div className="relative flex w-64 max-w-full items-center gap-3 rounded-xl border bg-muted/40 p-3 pr-9">
    {preview ? <img src={preview} alt="Ảnh tài liệu đính kèm" className="h-14 w-12 shrink-0 rounded object-cover" /> : <div className="flex h-14 w-12 shrink-0 items-center justify-center rounded bg-background"><FileText className="h-6 w-6 text-muted-foreground" /></div>}
    <div className="min-w-0"><p className="truncate text-sm font-medium" title={file.name}>{file.name}</p><p className="mt-1 text-xs text-muted-foreground">{file.size >= 1048576 ? `${(file.size / 1048576).toFixed(1)} MB` : `${Math.ceil(file.size / 1024)} KB`}</p><p role="status" className={`mt-1 text-xs ${error || status === 'error' ? 'text-destructive' : status === 'partial' ? 'text-amber-600' : 'text-muted-foreground'}`}>{busy || status === 'processing' ? 'Đang đọc tài liệu…' : error || status === 'error' ? 'Đọc tệp thất bại' : status === 'partial' ? 'Đã đọc một phần' : 'Đã đọc xong'}</p></div>
    {canDownload && <button type="button" aria-label="Tải tệp gốc" disabled={downloading || removing} onClick={() => void download()} className="shrink-0 rounded p-2 hover:bg-background focus-visible:outline focus-visible:outline-2 disabled:opacity-50"><Download className="h-4 w-4" /></button>}
    <button type="button" aria-label={removing ? 'Đang gỡ tệp' : 'Gỡ tệp đính kèm'} disabled={removing || removeDisabled} onClick={onRemove} className="absolute right-1 top-1 rounded p-1.5 text-muted-foreground hover:bg-background hover:text-foreground disabled:opacity-50"><X className="h-4 w-4" /></button>
  </div>
}
