import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './client'
import { legalImportApi } from './legal-import'

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(),
}))

vi.mock('./client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('legalImportApi form-resolution contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it('uses the canonical current campaign endpoint', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { run_id: 'run-1' } })

    await legalImportApi.formResolutionCampaignStatus()

    expect(apiClient.get).toHaveBeenCalledWith(
      '/procedures/forms-catalog/form-resolution/current',
    )
  })

  it('reads import readiness from the backend root when an API origin is explicit', async () => {
    const { getApiUrl } = await import('@/lib/config')
    vi.mocked(getApiUrl).mockResolvedValueOnce('http://localhost:5055')
    const fetchMock = vi.fn().mockResolvedValueOnce({
      json: vi.fn().mockResolvedValue({
        status: 'ready',
        components: { import_worker: { healthy: true, code: 'ready' } },
        embedding_device: { requested: 'auto', active: 'cuda' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await legalImportApi.importReadiness()

    expect(fetchMock).toHaveBeenCalledWith('http://localhost:5055/ready/import', {
      cache: 'no-store',
      credentials: 'include',
      signal: expect.any(AbortSignal),
    })
    expect(result.status).toBe('ready')
  })

  it('uses the Next.js API rewrite for readiness when the configured API URL is relative', async () => {
    const { getApiUrl } = await import('@/lib/config')
    vi.mocked(getApiUrl).mockResolvedValueOnce('')
    const fetchMock = vi.fn().mockResolvedValueOnce({
      json: vi.fn().mockResolvedValue({
        status: 'ready',
        components: { import_worker: { healthy: true, code: 'ready' } },
        embedding_device: { requested: 'auto', active: 'cuda' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await legalImportApi.importReadiness()

    expect(fetchMock).toHaveBeenCalledWith('/api/ready/import', {
      cache: 'no-store',
      credentials: 'include',
      signal: expect.any(AbortSignal),
    })
  })

  it('uses the canonical review-shortlist endpoint and encodes the run ID', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { records: [] } })

    await legalImportApi.formResolutionCampaignShortlist('run/one')

    expect(apiClient.get).toHaveBeenCalledWith(
      '/procedures/forms-catalog/form-resolution/run%2Fone/review-shortlist',
    )
  })

  it('keeps the API filtered total and pagination metadata for candidate triage', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        summary: { filtered_total: 128, returned: 50, offset: 50, limit: 50 },
        records: [{ id: 'candidate-51', detected_form_name: 'Mẫu thử' }],
      },
    })

    const result = await legalImportApi.formsCatalogCandidates(
      undefined,
      'candidate_pending_review',
      50,
      50,
    )

    expect(apiClient.get).toHaveBeenCalledWith(
      '/procedures/forms-catalog/candidates-full',
      { params: { domain: undefined, review_status: 'candidate_pending_review', offset: 50, limit: 50 } },
    )
    expect(result.summary.filtered_total).toBe(128)
    expect(result.records[0].title).toBe('Mẫu thử')
  })

  it('binds campaign attestation to its exact batch and source checksums', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { status: 'applied' } })
    const shortlist = {
      run_id: 'run-1',
      legal_as_of: '2026-07-29',
      preview_fingerprint: 'a'.repeat(64),
      attestation_id: 'attestation-1',
      batch_id: 'form-review-001-test',
      batch_count: 2,
      identity_count: 1,
      canonical_form_count: 1,
      procedure_binding_count: 1,
      invalid_record_count: 0,
      feature_flag_enabled: false as const,
      automated_approval: false as const,
      human_attestation_required: true as const,
      manifest_sha256: 'b'.repeat(64),
      source_snapshot_sha256: 'c'.repeat(64),
      records: [{
        candidate_id: 'candidate-1',
        canonical_form_id: 'form-1',
        procedure_id: 'procedure-1',
        canonical_name: 'Giấy đề nghị',
        source_page_url: 'https://dichvucong.gov.vn/thu-tuc-hanh-chinh/test',
        source_download_url: null,
        sha256: null,
        legal_basis: [],
        effective_from: null,
        effective_to: null,
        jurisdiction: 'Hai Phong',
        administrative_level: 'commune',
        review_status: 'candidate_pending_review',
        legal_review_status: 'candidate_pending_review',
        reason_codes: [],
      }],
    }

    await legalImportApi.attestFormResolutionCampaign(
      shortlist,
      'Đã kiểm tra nguồn chính thức',
      '2026-07-29T12:00:00Z',
    )

    expect(apiClient.post).toHaveBeenCalledWith(
      '/procedures/forms-catalog/form-resolution/run-1/attest',
      expect.objectContaining({
        batch_id: 'form-review-001-test',
        preview_fingerprint: 'a'.repeat(64),
        manifest_sha256: 'b'.repeat(64),
        source_snapshot_sha256: 'c'.repeat(64),
      }),
    )
  })
})
