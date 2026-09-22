export interface RestoredDocument { name: string; text: string; size: number; type: string; file_id?: string; sha256?: string; status?: 'complete' | 'partial' }

export function restoreConversationAttachment(messages: Array<{role?: string; attachments?: unknown}>): RestoredDocument | null {
  const lastUser = [...messages].reverse().find(message => message.role === 'user')
  if (!Array.isArray(lastUser?.attachments)) return null
  const value = lastUser.attachments.find(item => item?.kind === 'uploaded_document_context_v1')?.value
  if (!value || typeof value.name !== 'string' || (value.text != null && typeof value.text !== 'string')
    || typeof value.type !== 'string' || !Number.isFinite(value.size)
    || value.size < 0 || value.size > 104857600 || (value.text || '').length > 81000 || value.name.length > 255) return null
  const fileId = typeof value.file_id === 'string' && /^[a-f0-9]{32}$/.test(value.file_id) ? value.file_id : undefined
  if (!fileId && !(value.text || '').trim()) return null
  return {name: value.name, text: value.text || '', size: value.size, type: value.type,
    ...(fileId ? {file_id: fileId} : {}),
    ...(typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256) ? {sha256: value.sha256} : {}),
    ...(value.status === 'partial' ? {status: 'partial' as const} : {status: 'complete' as const})}
}
