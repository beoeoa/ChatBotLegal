import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatMemoryPanel } from './ChatMemoryPanel'

const { apiGet, apiPatch } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
}))

vi.mock('@/lib/api/client', () => ({
  apiClient: { get: apiGet, patch: apiPatch, post: vi.fn() },
}))

const state = {
  version: 'conversation-state-v1',
  conversation_id: 'conv-1',
  canonical_domain: 'cu_tru_an_ninh',
  procedure: { id: '1.004194', name: 'Đăng ký tạm trú' },
  actors: ['chủ nhà'],
  legal_objects: ['đăng ký tạm trú'],
  locations: ['Hải Phòng'],
  answered_facets: ['documents'],
  unresolved_facets: ['deadline'],
  active_document: {
    document_id: 'doc-1',
    title: 'Nghị định 154/2024/NĐ-CP',
    source_url: 'https://example.test/doc-1',
    article_refs: ['Điều 5'],
    pinned: false,
  },
  recent_source_refs: [
    { document_id: 'doc-1', title: 'Nghị định 154/2024/NĐ-CP' },
    { document_id: 'doc-2', title: 'Luật Cư trú' },
  ],
  revision: 3,
}

describe('ChatMemoryPanel active source', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPatch.mockReset()
    apiGet.mockImplementation((url: string) => Promise.resolve({
      data: url.endsWith('/state')
        ? state
        : { enabled: false, runtime_available: true },
    }))
    apiPatch.mockResolvedValue({
      data: { ...state, active_document: { ...state.active_document, pinned: true } },
    })
  })

  it('shows, views, pins, switches and clears only backend-known sources', async () => {
    render(<ChatMemoryPanel conversationId="conv-1" />)

    expect(await screen.findByTestId('active-document-banner')).toHaveTextContent('Nghị định 154/2024/NĐ-CP')
    expect(screen.getByRole('link', { name: /Xem/ })).toHaveAttribute('href', 'https://example.test/doc-1')

    fireEvent.click(screen.getByRole('button', { name: 'Ghim' }))
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith(
      '/conversations/conv-1/state',
      { pin_active_document: true },
    ))

    apiPatch.mockResolvedValueOnce({ data: { ...state, active_document: state.recent_source_refs[1] } })
    fireEvent.click(screen.getByRole('button', { name: 'Đổi nguồn' }))
    fireEvent.click(screen.getByRole('button', { name: 'Luật Cư trú' }))
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith(
      '/conversations/conv-1/state',
      { set_active_document_id: 'doc-2' },
    ))
  })
})
