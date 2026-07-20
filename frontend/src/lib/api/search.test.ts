import { afterEach, describe, it, expect, vi, beforeEach } from 'vitest'
import { searchApi } from './search'
import apiClient from './client'

vi.mock('./client', () => ({
  default: {
    post: vi.fn(),
  },
}))

describe('searchApi.transcribeVoice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('posts audio to voice transcription endpoint and propagates STT unavailable', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce({
      response: {
        status: 503,
        data: { detail: 'Chưa cấu hình speech-to-text model.' },
      },
    })

    const blob = new Blob(['audio'], { type: 'audio/webm' })
    await expect(searchApi.transcribeVoice(blob)).rejects.toMatchObject({
      response: { status: 503 },
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/media/transcribe-voice',
      expect.any(FormData),
      expect.objectContaining({ timeout: 90_000 })
    )
  })
})

describe('searchApi.askKnowledgeBaseStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses the progress endpoint and keeps the transport signal out of JSON', async () => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: { token: 'test-token', role: 'officer' },
    }))
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(
          'data: {"type":"final","response":{"answer":"verified","question":"fixture"}}\n\n'
          + 'data: {"type":"complete"}\n\n',
        ))
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, body })
    vi.stubGlobal('fetch', fetchMock)
    const callerController = new AbortController()

    const response = await searchApi.askKnowledgeBaseStream({
      question: 'fixture',
      role: 'officer',
      strategy_model: '',
      answer_model: '',
      final_answer_model: '',
      idempotency_key: 'ask-fixture',
      signal: callerController.signal,
    }, { onEvent: vi.fn() })

    expect(response.answer).toBe('verified')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/search/ask/progress')
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer test-token',
      'X-User-Role': 'officer',
      Accept: 'text/event-stream',
    })
    const payload = JSON.parse(String(init.body))
    expect(payload).toMatchObject({ role: 'officer', idempotency_key: 'ask-fixture' })
    expect(payload).not.toHaveProperty('signal')
  })
})
