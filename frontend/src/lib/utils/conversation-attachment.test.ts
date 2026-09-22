import { describe, it, expect } from 'vitest'
import { restoreConversationAttachment } from './conversation-attachment'

const value = {name: 'hồ sơ.pdf', text: 'Nội dung đã OCR', size: 1234, type: 'application/pdf'}
const user = {role: 'user', attachments: [{kind: 'uploaded_document_context_v1', value}]}
describe('persisted file context', () => {
  it('restores the latest user file even after an assistant response', () => {
    expect(restoreConversationAttachment([user, {role: 'assistant'}])).toEqual({...value, status: 'complete'})
  })
  it('never resurrects a removed file or leaks one into an empty session', () => {
    expect(restoreConversationAttachment([user, {role: 'user', attachments: [{kind: 'uploaded_document_context_v1', value: null}]}])).toBeNull()
    expect(restoreConversationAttachment([])).toBeNull()
  })
  it('rejects malformed stored metadata', () => {
    expect(restoreConversationAttachment([{role: 'user', attachments: [{kind: 'uploaded_document_context_v1', value: {...value, size: -1}}]}])).toBeNull()
  })
})
