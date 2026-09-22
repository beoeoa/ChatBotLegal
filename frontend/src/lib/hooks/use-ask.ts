'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { searchApi } from '@/lib/api/search'
import { AskStreamTransportError, type AskSseEvent } from '@/lib/api/ask-sse'
import { askStageLabel, isAskSseEnabled } from '@/lib/features/ask-stream'
import type { AskRequest, AskResponse, RagTrace } from '@/lib/types/search'
import type { UserRole } from '@/lib/stores/auth-store'

interface AskModels {
  strategy: string
  answer: string
  finalAnswer: string
}

interface StrategyData {
  reasoning: string
  searches: Array<{ term: string; instructions: string }>
}

interface AskState {
  finalResponse: AskResponse | null
  isStreaming: boolean
  stageLabel: string | null
  strategy: StrategyData | null
  answers: string[]
  finalAnswer: string | null
  ragTrace: RagTrace | null
  procedureDetail: AskResponse['procedure_detail'] | null
  recommendedForms: AskResponse['recommended_forms'] | null
  faqs: AskResponse['faqs'] | null
  domainMismatch: boolean
  selectedDomain: string | null
  suggestedDomain: string | null
  suggestedAgency: string | null
  groundingStatus: string | null
  answerCompleteness: AskResponse['answer_completeness'] | null
  answerMode: AskResponse['answer_mode'] | null
  citations: AskResponse['citations'] | null
  answerSections: AskResponse['answer_sections'] | null
  error: string | null
  cancelled: boolean
}

interface AskOptions {
  answerDepth?: 'quick' | 'balanced' | 'deep'
  attachmentText?: string
  attachmentId?: string
  attachmentName?: string
  attachmentSha256?: string
  attachmentStatus?: 'processing' | 'complete' | 'partial' | 'error'
  offlineMode?: boolean
  offlineModel?: string
  domain?: string | null
  showRagTrace?: boolean
  sessionId?: string | null
  conversationId?: string | null
  eventDate?: string | null
  legalAsOf?: string | null
  modelOptionId?: string | null
  memoryItemIds?: string[]
  activeDocumentId?: string | null
  /** The page already wrote the user message before opening Ask. */
  prePersistedUserMessage?: boolean
  /** Stable only for retries of one submission, never derived from question text. */
  turnId?: string
}

interface AskFailure {
  error: string
  answer?: undefined
}


export function useAsk() {
  const { t } = useTranslation()
  const abortRef = useRef<AbortController | null>(null)
  const [state, setState] = useState<AskState>({
    isStreaming: false,
    stageLabel: null,
    strategy: null,
    answers: [],
    finalAnswer: null,
    finalResponse: null,
    ragTrace: null,
    procedureDetail: null,
    recommendedForms: null,
    faqs: null,
    domainMismatch: false,
    selectedDomain: null,
    suggestedDomain: null,
    suggestedAgency: null,
    groundingStatus: null,
    answerCompleteness: null,
    answerMode: null,
    citations: null,
    answerSections: null,
    error: null,
    cancelled: false
  })

  const sendAsk = useCallback(async (
    question: string,
    models: AskModels,
    role: UserRole,
    options: AskOptions = {}
  ): Promise<AskResponse | AskFailure | null | undefined> => {
    // Validate inputs
    if (!question.trim()) {
      const errorMessage = t('apiErrors.pleaseEnterQuestion')
      toast.error(errorMessage)
      return { error: errorMessage }
    }

    const resolvedModels = {
      strategy: (models.strategy || '').trim(),
      answer: (models.answer || '').trim(),
      finalAnswer: (models.finalAnswer || '').trim(),
    }

    // Backend can fall back to default_chat_model, but avoid sending blank IDs when possible.
    if (!options.offlineMode && (!resolvedModels.strategy || !resolvedModels.answer || !resolvedModels.finalAnswer)) {
      // Keep going only if all are empty (backend defaults). Partial empty is invalid.
      const anySet = Boolean(resolvedModels.strategy || resolvedModels.answer || resolvedModels.finalAnswer)
      const allEmpty = !resolvedModels.strategy && !resolvedModels.answer && !resolvedModels.finalAnswer
      if (anySet && !allEmpty) {
        const errorMessage = t('apiErrors.pleaseConfigureModels')
        toast.error(errorMessage)
        return { error: errorMessage }
      }
    }

    // A new question supersedes the previous request. Axios receives the
    // AbortSignal so cancelled calls do not turn into an error toast.
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    // Reset state
    setState({
      isStreaming: true,
      stageLabel: askStageLabel('preparing'),
      strategy: null,
      answers: [],
      finalAnswer: null,
      finalResponse: null,
      ragTrace: null,
      procedureDetail: null,
      recommendedForms: null,
      faqs: null,
      domainMismatch: false,
      selectedDomain: null,
      suggestedDomain: null,
      suggestedAgency: null,
      groundingStatus: null,
      answerCompleteness: null,
      answerMode: null,
      citations: null,
      answerSections: null,
      error: null,
      cancelled: false
    })

    try {
      const idempotencyKey = options.turnId || `turn-${crypto.randomUUID()}`
      const request: AskRequest = {
        question,
        role,
        strategy_model: resolvedModels.strategy,
        answer_model: resolvedModels.answer,
        final_answer_model: resolvedModels.finalAnswer,
        model_option_id: options.modelOptionId || undefined,
        answer_depth: options.answerDepth || 'balanced',
        attachment_id: options.attachmentId || undefined,
        attachment_text: options.attachmentText || undefined,
        attachment_name: options.attachmentName || undefined,
        attachment_sha256: options.attachmentSha256 || undefined,
        attachment_status: options.attachmentStatus || undefined,
        offline_mode: options.offlineMode,
        offline_model: options.offlineModel,
        domain: options.domain || null,
        // RAG trace is privileged diagnostic data. Keep the client fail-closed
        // even though the server also enforces the admin role.
        show_rag_trace: role === 'admin' && (options.showRagTrace ?? false),
        session_id: options.sessionId || undefined,
        conversation_id: options.conversationId || undefined,
        event_date: options.eventDate || undefined,
        legal_as_of: options.legalAsOf || undefined,
        idempotency_key: idempotencyKey,
        pre_persisted_user_message: options.prePersistedUserMessage === true,
        memory_item_ids: options.memoryItemIds || [],
        active_document_id: options.activeDocumentId || undefined,
        signal: controller.signal
      }

      const onStreamEvent = (event: AskSseEvent) => {
        if (abortRef.current !== controller || controller.signal.aborted) return
        if (event.type === 'accepted') {
          setState(prev => ({ ...prev, stageLabel: askStageLabel('accepted') }))
        } else if (event.type === 'status') {
          setState(prev => ({ ...prev, stageLabel: askStageLabel(event.stage) }))
        } else if (event.type === 'sources') {
          // Structured source cards are safe to show early. Legal prose remains
          // empty until the validated `final` event resolves the request.
          setState(prev => ({
            ...prev,
            citations: event.citations,
            stageLabel: askStageLabel('retrieval'),
          }))
        } else if (event.type === 'text_delta') {
          setState(prev => ({ ...prev, finalAnswer: (prev.finalAnswer || '') + event.text, stageLabel: 'Đang soạn · nội dung chưa hoàn tất' }))
        } else if (event.type === 'completed') {
          setState(prev => ({ ...prev, stageLabel: askStageLabel('complete') }))
        }
      }

      let response: AskResponse
      if (isAskSseEnabled() && !options.offlineMode) {
        try {
          response = await searchApi.askKnowledgeBaseStream(request, { onEvent: onStreamEvent })
        } catch (error) {
          if (controller.signal.aborted) throw error
          // The compatibility request is safe only before an SSE body has
          // opened. After opening, retrying /simple could duplicate the turn.
          if (error instanceof AskStreamTransportError && !error.streamOpened) {
            setState(prev => ({ ...prev, stageLabel: 'Đang chuyển sang chế độ trả lời ổn định' }))
            response = await searchApi.askKnowledgeBaseSimple(request)
          } else {
            throw error
          }
        }
      } else {
        response = await searchApi.askKnowledgeBaseSimple(request)
      }

      if (abortRef.current !== controller || controller.signal.aborted) return null

      if (!response?.answer) {
        throw new Error('No answer received from server')
      }

      setState(prev => ({
        ...prev,
        finalAnswer: response.answer,
        finalResponse: response,
        stageLabel: askStageLabel('complete'),
        ragTrace: response.rag_trace || null,
        procedureDetail: response.procedure_detail || null,
        recommendedForms: response.recommended_forms || null,
        faqs: response.faqs || null,
        domainMismatch: Boolean(response.domain_mismatch),
        selectedDomain: response.selected_domain || options.domain || null,
        suggestedDomain: response.suggested_domain || null,
        suggestedAgency: response.suggested_agency || null,
        groundingStatus: response.grounding_status || null,
        answerCompleteness: response.answer_completeness || null,
        answerMode: response.answer_mode || 'normal',
        citations: response.citations || null,
        answerSections: response.answer_sections || null,
        answers: [],
        isStreaming: false
      }))
      return response

    } catch (error) {
      if (abortRef.current !== controller) return null
      const cancelled = controller.signal.aborted
      if (cancelled) {
        setState(prev => ({
          ...prev,
          isStreaming: false,
          stageLabel: prev.finalAnswer
            ? 'Đã dừng · phần nội dung đã nhận vẫn được giữ lại'
            : 'Đã dừng tạo câu trả lời',
          cancelled: true,
          error: null,
        }))
        return null
      }
      const errorMessage = getApiErrorMessage(error, (key) => t(key))
      setState(prev => ({
        ...prev,
        isStreaming: false,
        stageLabel: prev.finalAnswer
          ? 'Kết nối bị gián đoạn · phần nội dung đã nhận vẫn được giữ lại'
          : prev.stageLabel,
        error: errorMessage
      }))

      toast.error(t('apiErrors.askFailed'), {
        description: errorMessage
      })
      // Return the formatted error as well as storing it in hook state. The
      // caller may render the result before React has committed the state update.
      return { error: errorMessage }
    }
  }, [t])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setState({
      isStreaming: false,
      stageLabel: null,
      strategy: null,
      answers: [],
      finalAnswer: null,
      finalResponse: null,
      ragTrace: null,
      procedureDetail: null,
      recommendedForms: null,
      faqs: null,
      domainMismatch: false,
      selectedDomain: null,
      suggestedDomain: null,
      suggestedAgency: null,
      groundingStatus: null,
      answerCompleteness: null,
      answerMode: null,
      citations: null,
      answerSections: null,
      error: null,
      cancelled: false
    })
  }, [])

  const cancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
      setState(prev => ({
        ...prev,
        isStreaming: false,
        stageLabel: prev.finalAnswer
          ? 'Đã dừng · phần nội dung đã nhận vẫn được giữ lại'
          : 'Đã dừng tạo câu trả lời',
        cancelled: true,
        error: null,
      }))
      toast.message('Đã dừng tạo câu trả lời.')
    }
  }, [])

  useEffect(() => () => abortRef.current?.abort(), [])

  return {
    ...state,
    sendAsk,
    cancel,
    reset
  }
}
