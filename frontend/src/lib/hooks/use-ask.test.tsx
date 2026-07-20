import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { searchApi } from '@/lib/api/search'
import { AskStreamTransportError } from '@/lib/api/ask-sse'
import { isAskSseEnabled } from '@/lib/features/ask-stream'
import { useAsk } from './use-ask'


vi.mock('@/lib/api/search', () => ({
  searchApi: {
    askKnowledgeBaseSimple: vi.fn(),
    askKnowledgeBaseStream: vi.fn(),
  },
}))

vi.mock('@/lib/features/ask-stream', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/features/ask-stream')>()
  return { ...actual, isAskSseEnabled: vi.fn() }
})

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    message: vi.fn(),
  },
}))

const models = { strategy: '', answer: '', finalAnswer: '' }
const finalResponse = {
  answer: 'Câu trả lời cuối đã kiểm tra',
  question: 'fixture',
  citations: [{ chunk_id: '12', law_number: '60/2014/QH13' }],
  answer_sections: [{
    issue_id: 'issue-1',
    title: 'Thẩm quyền',
    status: 'sufficiently_evidenced' as const,
    answer: 'Nội dung đã xác minh',
    citations: [],
  }],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useAsk transport contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(isAskSseEnabled).mockReturnValue(false)
  })

  it('publishes pending state before awaiting the network', async () => {
    const pending = deferred<typeof finalResponse>()
    vi.mocked(searchApi.askKnowledgeBaseSimple).mockReturnValueOnce(pending.promise)
    const { result } = renderHook(() => useAsk())

    let request!: ReturnType<typeof result.current.sendAsk>
    act(() => {
      request = result.current.sendAsk('Câu hỏi fixture', models, 'citizen')
    })

    expect(result.current.isStreaming).toBe(true)
    expect(result.current.stageLabel).toBe('Đang chuẩn bị gửi câu hỏi')
    expect(result.current.finalAnswer).toBeNull()

    pending.resolve(finalResponse)
    await act(async () => { await request })
  })

  it('updates Vietnamese stages and safe sources but no answer before final', async () => {
    vi.mocked(isAskSseEnabled).mockReturnValue(true)
    const finish = deferred<typeof finalResponse>()
    vi.mocked(searchApi.askKnowledgeBaseStream).mockImplementationOnce(async (_params, handlers) => {
      handlers.onEvent({ type: 'accepted', turnId: 'turn-1' })
      handlers.onEvent({ type: 'status', stage: 'retrieval' })
      handlers.onEvent({ type: 'sources', citations: finalResponse.citations })
      return finish.promise
    })
    const { result } = renderHook(() => useAsk())

    let request!: ReturnType<typeof result.current.sendAsk>
    act(() => {
      request = result.current.sendAsk('Câu hỏi fixture', models, 'citizen')
    })

    await waitFor(() => {
      expect(result.current.stageLabel).toBe('Đang tìm nguồn pháp luật phù hợp')
      expect(result.current.citations).toEqual(finalResponse.citations)
    })
    expect(result.current.finalAnswer).toBeNull()
    expect(result.current.answers).toEqual([])

    finish.resolve(finalResponse)
    await act(async () => { await request })
    expect(result.current.finalAnswer).toBe(finalResponse.answer)
    expect(result.current.answerSections).toEqual(finalResponse.answer_sections)
  })

  it('falls back to simple only when stream has not opened', async () => {
    vi.mocked(isAskSseEnabled).mockReturnValue(true)
    vi.mocked(searchApi.askKnowledgeBaseStream).mockRejectedValueOnce(
      new AskStreamTransportError('not opened', false),
    )
    vi.mocked(searchApi.askKnowledgeBaseSimple).mockResolvedValueOnce(finalResponse)
    const { result } = renderHook(() => useAsk())

    await act(async () => {
      await result.current.sendAsk('Câu hỏi fixture', models, 'citizen')
    })

    expect(searchApi.askKnowledgeBaseSimple).toHaveBeenCalledTimes(1)
    expect(result.current.finalAnswer).toBe(finalResponse.answer)
  })

  it('never starts simple fallback after stream has opened', async () => {
    vi.mocked(isAskSseEnabled).mockReturnValue(true)
    vi.mocked(searchApi.askKnowledgeBaseStream).mockRejectedValueOnce(
      new AskStreamTransportError('opened failure', true),
    )
    const { result } = renderHook(() => useAsk())

    await act(async () => {
      await result.current.sendAsk('Câu hỏi fixture', models, 'citizen')
    })

    expect(searchApi.askKnowledgeBaseSimple).not.toHaveBeenCalled()
    expect(result.current.error).not.toBeNull()
  })

  it('treats AbortController cancellation as cancelled, never as error', async () => {
    vi.mocked(isAskSseEnabled).mockReturnValue(true)
    vi.mocked(searchApi.askKnowledgeBaseStream).mockImplementationOnce((params) => (
      new Promise((_resolve, reject) => {
        params.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    ))
    const { result } = renderHook(() => useAsk())

    let request!: ReturnType<typeof result.current.sendAsk>
    act(() => {
      request = result.current.sendAsk('Câu hỏi fixture', models, 'citizen')
    })
    act(() => result.current.cancel())
    await act(async () => { await request })

    expect(result.current.cancelled).toBe(true)
    expect(result.current.error).toBeNull()
    expect(searchApi.askKnowledgeBaseSimple).not.toHaveBeenCalled()
  })

  it('reuses the turn idempotency key when retrying the same question', async () => {
    vi.mocked(searchApi.askKnowledgeBaseSimple)
      .mockRejectedValueOnce(new Error('fixture failure'))
      .mockResolvedValueOnce(finalResponse)
    const { result } = renderHook(() => useAsk())

    await act(async () => {
      await result.current.sendAsk('  Câu hỏi   fixture  ', models, 'citizen', { conversationId: 'conv-1' })
      await result.current.sendAsk('Câu hỏi fixture', models, 'citizen', { conversationId: 'conv-1' })
    })

    const first = vi.mocked(searchApi.askKnowledgeBaseSimple).mock.calls[0][0]
    const second = vi.mocked(searchApi.askKnowledgeBaseSimple).mock.calls[1][0]
    expect(first.idempotency_key).toBe(second.idempotency_key)
  })

  it.each(['officer', 'admin'] as const)(
    'keeps the same %s role when progress is unavailable and simple fallback is used',
    async (role) => {
      vi.mocked(isAskSseEnabled).mockReturnValue(true)
      vi.mocked(searchApi.askKnowledgeBaseStream).mockRejectedValueOnce(
        new AskStreamTransportError('not opened', false),
      )
      vi.mocked(searchApi.askKnowledgeBaseSimple).mockResolvedValueOnce(finalResponse)
      const { result } = renderHook(() => useAsk())

      await act(async () => {
        await result.current.sendAsk('fixture', models, role)
      })

      expect(vi.mocked(searchApi.askKnowledgeBaseSimple).mock.calls[0][0].role).toBe(role)
      expect(result.current.finalAnswer).toBe(finalResponse.answer)
    },
  )

  it.each([
    ['citizen', true, false],
    ['officer', true, false],
    ['admin', false, false],
    ['admin', true, true],
  ] as const)(
    'keeps rag_trace fail-closed for role=%s requested=%s',
    async (role, requested, expected) => {
      vi.mocked(searchApi.askKnowledgeBaseSimple).mockResolvedValueOnce(finalResponse)
      const { result } = renderHook(() => useAsk())

      await act(async () => {
        await result.current.sendAsk('fixture', models, role, { showRagTrace: requested })
      })

      expect(vi.mocked(searchApi.askKnowledgeBaseSimple).mock.calls[0][0].show_rag_trace)
        .toBe(expected)
    },
  )
})
