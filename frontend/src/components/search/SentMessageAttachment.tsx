'use client'

import Image from 'next/image'
import { FileText } from 'lucide-react'
import type { AskMessageAttachment, UploadedDocumentContext } from '@/lib/types/search'

const UPLOADED_DOCUMENT_KIND = 'uploaded_document_context_v1'
const FILE_ID_PATTERN = /^[a-f0-9]{32}$/

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.ceil(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function uploadedDocument(
  attachments: AskMessageAttachment[] | null | undefined,
): UploadedDocumentContext | null {
  if (!Array.isArray(attachments)) return null
  const value = attachments.find((item) => item?.kind === UPLOADED_DOCUMENT_KIND)?.value
  if (!value || typeof value !== 'object') return null

  const candidate = value as Partial<UploadedDocumentContext>
  if (
    typeof candidate.name !== 'string'
    || !candidate.name.trim()
    || typeof candidate.type !== 'string'
    || typeof candidate.size !== 'number'
    || !Number.isFinite(candidate.size)
    || candidate.size < 0
  ) return null

  return {
    name: candidate.name,
    type: candidate.type,
    size: candidate.size,
    file_id: typeof candidate.file_id === 'string' && FILE_ID_PATTERN.test(candidate.file_id)
      ? candidate.file_id
      : undefined,
    sha256: typeof candidate.sha256 === 'string' ? candidate.sha256 : undefined,
    status: candidate.status,
  }
}

export function SentMessageAttachment({
  attachments,
}: {
  attachments?: AskMessageAttachment[] | null
}) {
  const document = uploadedDocument(attachments)
  if (!document) return null

  const fileUrl = document.file_id
    ? `/api/media/files/${encodeURIComponent(document.file_id)}`
    : null
  const isImage = document.type.toLowerCase().startsWith('image/')
  const sizeLabel = formatFileSize(document.size)

  if (isImage && fileUrl) {
    return (
      <a
        href={fileUrl}
        target="_blank"
        rel="noreferrer"
        className="group block max-w-[min(15rem,72vw)] overflow-hidden rounded-2xl border bg-card shadow-sm outline-none transition hover:border-primary/40 focus-visible:ring-2 focus-visible:ring-primary/50"
        aria-label={`Mở ảnh đính kèm ${document.name}`}
        data-testid="sent-image-attachment"
      >
        <Image
          src={fileUrl}
          alt={`Ảnh đính kèm: ${document.name}`}
          width={240}
          height={168}
          unoptimized
          className="h-auto max-h-44 w-full object-cover transition-transform duration-200 group-hover:scale-[1.015] motion-reduce:transition-none"
        />
        <span className="block truncate border-t px-3 py-1.5 text-xs text-muted-foreground">
          {document.name}{sizeLabel ? ` · ${sizeLabel}` : ''}
        </span>
      </a>
    )
  }

  const content = (
    <>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted">
        <FileText className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
      </span>
      <span className="min-w-0 text-left">
        <span className="block truncate text-sm font-medium text-foreground">{document.name}</span>
        {sizeLabel && <span className="mt-0.5 block text-xs text-muted-foreground">{sizeLabel}</span>}
      </span>
    </>
  )

  return fileUrl ? (
    <a
      href={fileUrl}
      target="_blank"
      rel="noreferrer"
      className="flex max-w-[min(18rem,78vw)] items-center gap-2.5 rounded-xl border bg-card p-2.5 shadow-sm outline-none transition hover:border-primary/40 hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-primary/50"
      aria-label={`Mở tệp đính kèm ${document.name}`}
      data-testid="sent-file-attachment"
    >
      {content}
    </a>
  ) : (
    <div
      className="flex max-w-[min(18rem,78vw)] items-center gap-2.5 rounded-xl border bg-card p-2.5 shadow-sm"
      data-testid="sent-file-attachment"
    >
      {content}
    </div>
  )
}
