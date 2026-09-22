import { beforeEach, describe, expect, it, vi } from 'vitest'
import { faqAdminApi } from './faq-admin'
import { apiClient } from './client'

vi.mock('./client', () => ({ apiClient: { post: vi.fn() } }))

describe('FAQ withdrawal release', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends explicit revision IDs, reason and the observed active release without deleting', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'candidate-2', status: 'candidate' } })
    const result = await faqAdminApi.previewWithdrawal(['revision-1'], 'Nội dung kiểm thử', 'release-1')
    expect(apiClient.post).toHaveBeenCalledExactlyOnceWith('/faq/governance/releases/preview', {
      revision_ids: [], withdraw_revision_ids: ['revision-1'],
      withdrawal_reason: 'Nội dung kiểm thử', expected_active_release_id: 'release-1',
    })
    expect(result.status).toBe('candidate')
  })
})
