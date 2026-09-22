import apiClient from './client'
import { SearchRequest, SearchResponse, AskRequest, AskResponse } from '@/lib/types/search'
import {
  AskStreamTransportError,
  consumeAskSseStream,
  type AskSseHandlers,
} from './ask-sse'
import { sessionSecurityHeaders } from './session-security'
import { useAuthStore } from '@/lib/stores/auth-store'

// The browser deadline must be longer than the server's complete Direct RAG
// budget. Otherwise the UI can abort a valid SSE response while retrieval or
// generation is still inside its server-side deadline.
const configuredAskTimeout = Number(process.env.NEXT_PUBLIC_ASK_TIMEOUT_MS)
const ASK_TIMEOUT_MS = Number.isFinite(configuredAskTimeout)
  ? Math.min(120_000, Math.max(60_000, configuredAskTimeout))
  : 120_000

function askAuthHeaders(): Record<string, string> {
  const { token, role } = useAuthStore.getState()
  const currentRole = role || 'citizen'
  return {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    ...sessionSecurityHeaders('POST'),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    'X-User-Role': currentRole,
  }
}

export interface LocalModelInfo {
  available: boolean
  recommended: string
  recommended_installed: boolean
  models: Array<{ name: string; size?: number; modified_at?: string }>
  error?: string
}

export const searchApi = {
  // Standard search (non-streaming)
  search: async (params: SearchRequest, signal?: AbortSignal) => {
    const response = await apiClient.post<SearchResponse>('/search', params, { signal })
    return response.data
  },

  // Ask with streaming (uses relative URL for Docker compatibility)
  askKnowledgeBase: async (params: AskRequest) => {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), ASK_TIMEOUT_MS)

    // Get auth token using the same logic as apiClient interceptor
    const { token, role } = useAuthStore.getState()
    const currentRole = role || 'citizen'

    // Use relative URL to leverage Next.js rewrites
    // This works both in dev (Next.js proxy) and production (Docker network)
    const url = '/api/search/ask'

    // Use fetch with ReadableStream for SSE
    let response: Response
    try {
      response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
          ...sessionSecurityHeaders('POST'),
          ...(token && { Authorization: `Bearer ${token}` }),
          'X-User-Role': currentRole,
        },
        body: JSON.stringify(params),
        signal: controller.signal
      })
    } catch (error) {
      window.clearTimeout(timeout)
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new Error('Yêu cầu quá thời gian 60 giây. Vui lòng thử lại.')
      }
      throw error
    }

    if (!response.ok) {
      window.clearTimeout(timeout)
      // Try to extract error message from response
      let errorMessage = `HTTP error! status: ${response.status}`
      try {
        const errorData = await response.json()
        errorMessage = errorData.detail || errorData.message || errorMessage
      } catch {
        // If response isn't JSON, use status text
        errorMessage = response.statusText || errorMessage
      }
      throw new Error(errorMessage)
    }

    if (!response.body) {
      window.clearTimeout(timeout)
      throw new Error('No response body received')
    }

    return {
      body: response.body,
      clearTimeout: () => window.clearTimeout(timeout)
    }
  },

  // Safe progressive Ask path. It only resolves from a structured `final`
  // event; draft-like SSE events are discarded by the parser.
  askKnowledgeBaseStream: async (
    params: AskRequest,
    handlers: AskSseHandlers,
  ): Promise<AskResponse> => {
    const { signal, ...payload } = params
    const controller = new AbortController()
    let streamOpened = false
    let timedOut = false
    const abortFromCaller = () => controller.abort()
    signal?.addEventListener('abort', abortFromCaller, { once: true })
    const timeout = window.setTimeout(() => {
      timedOut = true
      controller.abort()
    }, ASK_TIMEOUT_MS)

    try {
      let response: Response
      try {
        response = await fetch('/api/search/ask/progress', {
          method: 'POST',
          credentials: 'include',
          headers: askAuthHeaders(),
          body: JSON.stringify(payload),
          signal: controller.signal,
        })
      } catch {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        if (timedOut) {
          throw new AskStreamTransportError('Yêu cầu mở luồng đã quá thời gian chờ.', false)
        }
        throw new AskStreamTransportError('Không thể mở luồng trả lời.', false)
      }

      if (!response.ok) {
        let message = `Không thể mở luồng trả lời (HTTP ${response.status}).`
        try {
          const data = await response.json() as { detail?: string; message?: string }
          message = data.detail || data.message || message
        } catch {
          // Keep the safe status-based message.
        }
        throw new AskStreamTransportError(message, false)
      }
      if (!response.body) {
        throw new AskStreamTransportError('Luồng trả lời không có dữ liệu.', false)
      }

      streamOpened = true
      return await consumeAskSseStream(response.body, handlers, controller.signal)
    } catch (error) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      if (error instanceof AskStreamTransportError) throw error
      if (timedOut) {
        throw new AskStreamTransportError('Yêu cầu trả lời đã quá thời gian chờ.', streamOpened)
      }
      throw new AskStreamTransportError('Luồng trả lời bị gián đoạn.', streamOpened)
    } finally {
      window.clearTimeout(timeout)
      signal?.removeEventListener('abort', abortFromCaller)
    }
  },

  // Reliable non-streaming path for long legal answers.
  askKnowledgeBaseSimple: async (params: AskRequest) => {
    const { signal, ...payload } = params
    const response = await apiClient.post<AskResponse>(
      '/search/ask/simple',
      payload,
      { timeout: ASK_TIMEOUT_MS, signal }
    )
    return response.data
  },

  localModels: async () => {
    const response = await apiClient.get<LocalModelInfo>('/search/local-models')
    return response.data
  },


  /**
   * Voice input stage:
   * Upload recorded audio and return transcript text. The transcript is then
   * inserted into the question box and sent through the existing ask pipeline.
   */
  transcribeVoice: async (file: Blob, filename = 'voice-input.webm'): Promise<{
    transcript: string
    filename: string
    mime_type: string
    char_count: number
    language?: string | null
    model?: string | null
    provider?: string | null
    max_file_mb: number
  }> => {
    const formData = new FormData()
    formData.append('file', file, filename)
    const response = await apiClient.post('/media/transcribe-voice', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 90_000,
    })
    return response.data
  },

  /**
   * Stage 1 of the 2-stage pipeline:
   * Upload txt/docx/pdf/png/jpg/jpeg and return extracted plain text/context.
   * Audio/video are intentionally not supported here.
   */
  extractTextFromFile: async (file: File): Promise<{
    file_id?: string | null
    job_id?: string | null
    extracted_text: string
    source_type: 'text' | 'doc' | 'docx' | 'pdf' | 'xls' | 'xlsx' | 'image' | 'unknown'
    filename: string
    char_count: number
    mime_type: string
    max_file_mb: number
    sha256?: string
    extraction_status?: 'processing' | 'complete' | 'partial' | 'error'
    extraction_cached?: boolean
    extractor?: string | null
    extractor_version?: string | null
    warnings?: string[]
  }> => {
    const formData = new FormData()
    formData.append('file', file)
    const response = await apiClient.post('/media/extract-text', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    })
    return response.data
  },

  getExtractionJob: async (jobId: string): Promise<{
    job_id: string
    file_id: string
    sha256: string
    extraction_status: 'processing' | 'complete' | 'partial' | 'error'
    extracted_text?: string
    char_count?: number
    error?: string | null
    warnings?: string[]
  }> => {
    const response = await apiClient.get(`/media/extraction-jobs/${jobId}`)
    return response.data
  },
}

export type MediaExtractResponse = Awaited<ReturnType<typeof searchApi.extractTextFromFile>>
export type VoiceTranscribeResponse = Awaited<ReturnType<typeof searchApi.transcribeVoice>>
