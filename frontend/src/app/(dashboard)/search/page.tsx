'use client'
import { AttachmentCard } from '@/components/search/AttachmentCard'
import { restoreConversationAttachment } from '@/lib/utils/conversation-attachment'
import { LegalPreviewProvider } from '@/components/legal/LegalPreviewProvider'

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import dynamic from 'next/dynamic'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { AppShell } from '@/components/layout/AppShell'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AlertCircle, ArrowUp, Settings, Paperclip } from 'lucide-react'
import { useSearch } from '@/lib/hooks/use-search'
import { useAsk } from '@/lib/hooks/use-ask'
import { useModelDefaults, useModels } from '@/lib/hooks/use-models'
import { shouldLoadAdminModelMetadata } from '@/lib/utils/model-access'
import { AskMessageHistory } from '@/components/search/AskMessageHistory'
import { ChatModelSelector, type ChatModelOption } from '@/components/search/ChatModelSelector'
import { ChatMemoryContinuations } from '@/components/search/ChatMemoryContinuations'
import { ConversationSidebar, type ConversationDetail } from '@/components/search/ConversationSidebar'
import { useAuthStore } from '@/lib/stores/auth-store'
import { searchApi, LocalModelInfo } from '@/lib/api/search'
import { legalImportApi, LegalDomain } from '@/lib/api/legal-import'
import { apiClient } from '@/lib/api/client'
import { formatApiError, getApiErrorCode } from '@/lib/utils/error-handler'
import type { AskMessage } from '@/lib/types/search'
import {
  appendPendingAskTurn,
  cancelAskTurn,
  completeAskTurn,
  failAskTurn,
} from '@/lib/utils/ask-turn-history'
import { isCurrentSessionEpoch, nextSessionEpoch, shouldAutoLoadLatestConversation } from '@/lib/utils/conversation-session'
import { chatStarterQuestions, inferOfficerDepartment, welcomeMessage, type ChatViewerIdentity } from '@/lib/utils/chat-address'
import { mergeConversationMessagePage, normalizeConversationMessages } from '@/lib/utils/conversation-message-pages'
import { shouldOfferOfficerSupport } from '@/lib/utils/support-routing'

const AdvancedModelsDialog = dynamic(
  () => import('@/components/search/AdvancedModelsDialog').then((module) => module.AdvancedModelsDialog),
  { ssr: false },
)

type SupportRoutingOption = {
  domain: string
  domain_name: string
  unit_id: string
  unit_name: string
  unit_short_name?: string | null
}

type SupportPreview = {
  config_revision: number
  conversation_id?: string | null
  source_message_id?: string | null
  question: string
  ai_summary?: string | null
  suggested_domain?: string | null
  suggested_unit?: SupportRoutingOption | null
  options: SupportRoutingOption[]
}

const WARD_AGENCY_OPTIONS = [
  {
    id: 'tu_phap_ho_tich',
    agency: 'Tư pháp - Hộ tịch',
    domain: 'ho_tich_chung_thuc',
    domainName: 'Hộ tịch - chứng thực',
    keywords: ['khai sinh', 'kết hôn', 'độc thân', 'khai tử', 'hôn nhân'],
  },
  {
    id: 'dia_chinh_xay_dung',
    agency: 'Địa chính - Xây dựng - Đô thị - Môi trường',
    domain: 'dat_dai_xay_dung',
    domainName: 'Đất đai - Xây dựng',
    keywords: ['xây dựng', 'sổ đỏ', 'sang tên', 'đất đai', 'giấy phép xây dựng'],
  },
  {
    id: 'ldtbxh',
    agency: 'Lao động - Thương binh và Xã hội',
    domain: 'an_sinh_y_te_giao_duc',
    domainName: 'An sinh - Y tế - Giáo dục',
    keywords: ['trợ cấp', 'bảo trợ', 'xã hội'],
  },
  {
    id: 'tai_chinh_ke_hoach',
    agency: 'Tài chính - Kế hoạch / Bộ phận Một cửa',
    domain: 'hanh_chinh_cong',
    domainName: 'Hành chính công',
    keywords: ['hộ kinh doanh', 'đăng ký kinh doanh', 'thủ tục hành chính'],
  },
  {
    id: 'trat_tu_do_thi',
    agency: 'Địa chính - Xây dựng - Đô thị - Môi trường',
    domain: 'trat_tu_do_thi',
    domainName: 'Trật tự đô thị',
    keywords: ['đỗ xe', 'vỉa hè', 'trật tự', 'phạt vi phạm'],
  },
] as const

export default function SearchPage() {
  const { t } = useTranslation()
  const router = useRouter()
  const role = useAuthStore((state) => state.role) || 'citizen'
  const username = useAuthStore((state) => state.username)
  const canUseSearchTab = false
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const hasHydrated = useAuthStore((state) => state.hasHydrated)
  const [officerAssignment, setOfficerAssignment] = useState<string | null>(null)
  const [viewerIdentity, setViewerIdentity] = useState<ChatViewerIdentity | null>({ username })
  
// URL params
  const searchParams = useSearchParams()
  const urlQuery = searchParams?.get('q') || ''
  const explicitNewConversation = searchParams?.get('new') === '1'
  const rawMode = searchParams?.get('mode')
  // Citizen cannot use Search tab; force ask even if mode=search is in URL.
  const urlMode: 'ask' | 'search' =
    rawMode === 'search' && (role === 'officer' || role === 'admin') ? 'search' : 'ask'

  // Tab state (controlled)
  const [activeTab, setActiveTab] = useState<'ask' | 'search'>(
    urlMode === 'search' ? 'search' : 'ask'
  )

  // Search state
  const [searchQuery, setSearchQuery] = useState(urlMode === 'search' ? urlQuery : '')
  const [searchType] = useState<'text' | 'vector'>('text')
  const [searchSources] = useState(false)
  const [searchNotes] = useState(false)

  // Ask state
  const [askQuestion, setAskQuestion] = useState(urlMode === 'ask' ? urlQuery : '')
  const [offlineMode, setOfflineMode] = useState(false)
  const [offlineModel, setOfflineModel] = useState('qwen2.5:3b')
  const [showRagTrace, setShowRagTrace] = useState(true)
  const [selectedDomain, setSelectedDomain] = useState<string>('__auto__')
  const [domains, setDomains] = useState<LegalDomain[]>([])
  const [localModels, setLocalModels] = useState<LocalModelInfo | null>(null)
  const [chatModelOptions, setChatModelOptions] = useState<ChatModelOption[]>([])
  const [selectedModelOption, setSelectedModelOption] = useState<string>('')
  const [attachmentText, setAttachmentText] = useState('')
  const extractionEpoch = useRef(0)
  const [answerDepth, setAnswerDepth] = useState<'quick' | 'balanced' | 'deep'>('balanced')
  const askInputRef = useRef<HTMLTextAreaElement>(null)
  const focusComposerAfterTransitionRef = useRef(false)

  // --- Persistent Conversation Management ---
  // The API is the source of truth. Local state only mirrors the currently-open conversation.
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [chatHistory, setChatHistory] = useState<AskMessage[]>([])
  const [olderMessageCursor, setOlderMessageCursor] = useState<string | null>(null)
  const [hasOlderMessages, setHasOlderMessages] = useState(false)
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false)
  const [mobileConversationDrawerOpen, setMobileConversationDrawerOpen] = useState(false)
  const [conversationRefreshKey, setConversationRefreshKey] = useState(0)
  const [selectedMemoryItemIds, setSelectedMemoryItemIds] = useState<string[]>([])
  const [latestConversationLoaded, setLatestConversationLoaded] = useState(false)
  const [sessionHydrating, setSessionHydrating] = useState(false)
  const chatContainerRef = useRef<HTMLDivElement>(null)
  const askSubmitLockRef = useRef(false)
  const lastAskSubmissionRef = useRef<{ key: string; at: number } | null>(null)
  const sessionEpochRef = useRef(0)
  const observedNewParamRef = useRef(false)
  const pendingScrollRestoreRef = useRef<{ height: number; top: number } | null>(null)

  const loadConversation = useCallback((conversation: ConversationDetail, expectedEpoch = sessionEpochRef.current) => {
    if (!isCurrentSessionEpoch(expectedEpoch, sessionEpochRef.current)) return false
    setCurrentSessionId(conversation.id)
    // A conversation must alternate user/assistant turns. Older duplicate
    // requests could leave two assistant messages next to each other; keep
    // the latest completed answer when repairing that persisted history.
    const normalizedMessages = normalizeConversationMessages(conversation.messages)
    setChatHistory(normalizedMessages)
    const savedContext = restoreConversationAttachment(conversation.messages)
    extractionEpoch.current += 1
    setExtracting(false)
    setExtractError(null)
    setAttachmentText(savedContext?.text || '')
    setAttachedFile(savedContext ? {
      name: savedContext.name,
      size: savedContext.size,
      type: savedContext.type,
      file_id: savedContext.file_id,
      sha256: savedContext.sha256,
    } : null)
    setExtractionStatus(savedContext?.status || 'complete')
    const latestModelMessage = [...normalizedMessages]
      .reverse()
      .find((message) => message.model_option_id)
    const persistedOption = conversation.model_option_id || latestModelMessage?.model_option_id
    if (persistedOption) setSelectedModelOption(persistedOption)
    setOlderMessageCursor(conversation.next_cursor || null)
    setHasOlderMessages(Boolean(conversation.has_older_messages && conversation.next_cursor))
    setLoadingOlderMessages(false)
    pendingScrollRestoreRef.current = null
    setSelectedMemoryItemIds([])
    setSessionHydrating(false)
    return true
  }, [])

  const handleConversationDeleted = useCallback((conversationId: string) => {
    if (currentSessionId === conversationId) {
      sessionEpochRef.current = nextSessionEpoch(sessionEpochRef.current)
      setCurrentSessionId(null)
      setChatHistory([])
      setOlderMessageCursor(null)
      setHasOlderMessages(false)
      setSelectedMemoryItemIds([])
      setSessionHydrating(false)
      setLatestConversationLoaded(true)
      setConversationRefreshKey((value) => value + 1)
      router.replace('/search?new=1')
    }
  }, [currentSessionId, router])

  useEffect(() => {
    if (explicitNewConversation && !observedNewParamRef.current) {
      observedNewParamRef.current = true
      sessionEpochRef.current = nextSessionEpoch(sessionEpochRef.current)
      setCurrentSessionId(null)
      setChatHistory([])
      setOlderMessageCursor(null)
      setHasOlderMessages(false)
      setSelectedMemoryItemIds([])
      setSessionHydrating(false)
    } else if (!explicitNewConversation) {
      observedNewParamRef.current = false
    }
  }, [explicitNewConversation])

  useEffect(() => {
    if (explicitNewConversation) {
      if (hasHydrated && isAuthenticated && !latestConversationLoaded) {
        setLatestConversationLoaded(true)
      }
      return
    }
    if (!shouldAutoLoadLatestConversation({
      hasHydrated,
      isAuthenticated,
      latestConversationLoaded,
      currentSessionId,
      explicitNewConversation,
    })) {
      return
    }

    let cancelled = false
    const controller = new AbortController()
    const requestEpoch = sessionEpochRef.current
    setSessionHydrating(true)
    const loadLatestConversation = async () => {
      try {
        const detailRes = await apiClient.get('/conversations/latest', {
          signal: controller.signal,
        })
        if (!detailRes.data || cancelled || !isCurrentSessionEpoch(requestEpoch, sessionEpochRef.current)) return
        if (!cancelled && isCurrentSessionEpoch(requestEpoch, sessionEpochRef.current)) {
          loadConversation(detailRes.data as ConversationDetail, requestEpoch)
        }
      } catch {
        // Empty or temporarily unavailable history should not block asking.
      } finally {
        if (!cancelled && isCurrentSessionEpoch(requestEpoch, sessionEpochRef.current)) {
          setLatestConversationLoaded(true)
          setSessionHydrating(false)
        }
      }
    }

    void loadLatestConversation()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [currentSessionId, explicitNewConversation, hasHydrated, isAuthenticated, latestConversationLoaded, loadConversation, role])

  // Preserve the visible anchor when older pages are prepended. Normal answer
  // updates still follow the latest message at the bottom.
  useLayoutEffect(() => {
    if (chatContainerRef.current) {
      const restore = pendingScrollRestoreRef.current
      if (restore) {
        chatContainerRef.current.scrollTop =
          restore.top + (chatContainerRef.current.scrollHeight - restore.height)
        pendingScrollRestoreRef.current = null
        return
      }
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior: 'smooth',
      })
    }
  }, [chatHistory])

  const loadOlderMessages = useCallback(async () => {
    if (!currentSessionId || !olderMessageCursor || loadingOlderMessages) return
    const requestEpoch = sessionEpochRef.current
    const container = chatContainerRef.current
    if (container) {
      pendingScrollRestoreRef.current = {
        height: container.scrollHeight,
        top: container.scrollTop,
      }
    }
    setLoadingOlderMessages(true)
    try {
      const response = await apiClient.get(`/conversations/${currentSessionId}/messages`, {
        params: { before: olderMessageCursor, limit: 30 },
      })
      if (!isCurrentSessionEpoch(requestEpoch, sessionEpochRef.current)) return
      const messages = Array.isArray(response.data?.messages)
        ? response.data.messages as AskMessage[]
        : []
      setChatHistory((current) => mergeConversationMessagePage(current, messages))
      setOlderMessageCursor(response.data?.next_cursor || null)
      setHasOlderMessages(Boolean(response.data?.has_more && response.data?.next_cursor))
    } catch {
      pendingScrollRestoreRef.current = null
      toast.error('Không tải được các tin nhắn cũ.')
    } finally {
      if (isCurrentSessionEpoch(requestEpoch, sessionEpochRef.current)) {
        setLoadingOlderMessages(false)
      }
    }
  }, [currentSessionId, loadingOlderMessages, olderMessageCursor])

  // Advanced models dialog
  const [showAdvancedModels, setShowAdvancedModels] = useState(false)
  const [customModels, setCustomModels] = useState<{
    strategy: string
    answer: string
    finalAnswer: string
  } | null>(null)

  // Citizen upload context state
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
  const SUPPORTED_UPLOAD_EXTENSIONS = ['.txt', '.doc', '.docx', '.pdf', '.xls', '.xlsx', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff']
  type ChatAttachment = {name: string; size: number; type: string; file_id?: string; sha256?: string}
  const [attachedFile, setAttachedFile] = useState<ChatAttachment | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [removingAttachment, setRemovingAttachment] = useState(false)
  const [extractError, setExtractError] = useState<string | null>(null)
  const [extractionStatus, setExtractionStatus] = useState<'processing' | 'complete' | 'partial' | 'error'>('complete')
  const fileInputRef = useRef<HTMLInputElement>(null)



  // Hooks
  const searchMutation = useSearch()
  const ask = useAsk()
  // Conversation hydration is the critical first-load request. Model catalog
  // requests are useful for the composer but must not compete with restoring
  // the latest answer on a cold page load.
  const backgroundMetadataEnabled = hasHydrated && isAuthenticated && latestConversationLoaded
  const adminModelMetadataEnabled = shouldLoadAdminModelMetadata(role, backgroundMetadataEnabled)
  const { data: modelDefaults, isLoading: modelsLoading } = useModelDefaults(adminModelMetadataEnabled)
  const { data: availableModels } = useModels(adminModelMetadataEnabled)

  const beginSessionTransition = useCallback(() => {
    sessionEpochRef.current = nextSessionEpoch(sessionEpochRef.current)
    setSessionHydrating(true)
  }, [])

  const handleSessionTransitionError = useCallback(() => {
    setSessionHydrating(false)
  }, [])

  const handleConversationCreated = useCallback((conversation: ConversationDetail) => {
    const epoch = sessionEpochRef.current
    setSessionHydrating(false)
    loadConversation(conversation, epoch)
    router.replace('/search')
    // A new conversation must not inherit a failed STT attempt, an attachment
    // extraction error, or a draft from the previous conversation.  Keeping
    // those states made the citizen composer look enabled while the submit
    // path still carried stale voice/new-chat state.
    setAskQuestion('')
    setSelectedMemoryItemIds([])
    extractionEpoch.current += 1
    setAttachmentText('')
    setExtracting(false)
    setAttachedFile(null)
    setExtractionStatus('complete')
    setExtractError(null)
    setExtracting(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
    ask.reset()
    lastAskSubmissionRef.current = null
    focusComposerAfterTransitionRef.current = true
  }, [ask, loadConversation, router])

  const handleConversationSelected = useCallback((conversation: ConversationDetail) => {
    const epoch = sessionEpochRef.current
    setSessionHydrating(false)
    loadConversation(conversation, epoch)
    router.replace('/search')
  }, [loadConversation, router])

  const handleMemoryContinuation = useCallback((question: string, memoryItemId: string) => {
    setAskQuestion(question)
    setSelectedMemoryItemIds([memoryItemId])
  }, [])

  const handleDraftQuestion = useCallback((question: string) => {
    setAskQuestion(question)
    setSelectedMemoryItemIds([])
  }, [])

  useEffect(() => {
    if (!focusComposerAfterTransitionRef.current || ask.isStreaming) return
    let cancelled = false
    let retryTimer: number | undefined
    const focusComposer = () => {
      if (cancelled) return
      const input = askInputRef.current
      if (!input || input.disabled) {
        retryTimer = window.setTimeout(focusComposer, 50)
        return
      }
      input.focus()
      if (document.activeElement === input) {
        focusComposerAfterTransitionRef.current = false
        if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      }
    }
    const frame = requestAnimationFrame(focusComposer)
    retryTimer = window.setTimeout(focusComposer, 100)
    return () => {
      cancelled = true
      cancelAnimationFrame(frame)
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    }
  }, [ask.isStreaming, currentSessionId, sessionHydrating])

  // Keep the composer compact for short questions and let it grow naturally
  // for pasted text, without forcing a second scrollbar inside the page.
  useEffect(() => {
    const input = askInputRef.current
    if (!input) return
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`
  }, [askQuestion])

  useEffect(() => {
    if (!backgroundMetadataEnabled) return

    let cancelled = false
    void apiClient.get<ChatModelOption[]>('/chat/model-options')
      .then((response) => {
        if (cancelled) return
        const options = Array.isArray(response.data) ? response.data : []
        setChatModelOptions(options)
        setSelectedModelOption((current) => (
          options.some((item) => item.option_id === current)
            ? current
            : options.find((item) => item.is_default)?.option_id || options[0]?.option_id || ''
        ))
      })
      .catch(() => {
        if (!cancelled) setChatModelOptions([])
      })

    return () => {
      cancelled = true
    }
  }, [backgroundMetadataEnabled, role])

  const modelNameById = useMemo(() => {
    if (!availableModels) {
      return new Map<string, string>()
    }
    return new Map(availableModels.map((model) => [model.id, model.name]))
  }, [availableModels])

  const resolveModelName = (id?: string | null) => {
    if (!id) return t('searchPage.notSet')
    return modelNameById.get(id) ?? id
  }

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  const getFileExtension = (fileName: string) => {
    const dotIndex = fileName.lastIndexOf('.')
    return dotIndex >= 0 ? fileName.slice(dotIndex).toLowerCase() : ''
  }

  const validateCitizenUpload = (file: File): string | null => {
    const ext = getFileExtension(file.name)
    if (!SUPPORTED_UPLOAD_EXTENSIONS.includes(ext)) {
      return `Định dạng ${ext || 'không rõ'} chưa được hỗ trợ. Chỉ nhận: doc, docx, pdf, txt, xls, xlsx, png, jpg, jpeg, webp, bmp, tiff. Không hỗ trợ video/audio.`
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      return `File quá lớn (${formatFileSize(file.size)}). Giới hạn tối đa là ${formatFileSize(MAX_UPLOAD_BYTES)}.`
    }
    if (file.type.startsWith('video/') || file.type.startsWith('audio/')) {
      return 'Không hỗ trợ audio/video trong bước này. Vui lòng gửi txt, docx, pdf hoặc ảnh PNG/JPG/WEBP/BMP/TIFF.'
    }
    return null
  }

  const clearAttachedFile = async () => {
    if (removingAttachment || ask.isStreaming) return
    const sessionEpoch = sessionEpochRef.current
    setRemovingAttachment(true)
    try {
      if (currentSessionId) await apiClient.delete(`/conversations/${currentSessionId}/attachment`)
      if (sessionEpoch !== sessionEpochRef.current) return
    extractionEpoch.current += 1
    setAttachmentText('')
    setExtracting(false)
    setAttachedFile(null)
    setExtractionStatus('complete')
    setExtractError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
    } catch {
      toast.error('Chưa gỡ được tệp khỏi hội thoại. Vui lòng thử lại.')
    } finally { setRemovingAttachment(false) }
  }

  const extractApiErrorMessage = (err: unknown, fallback: string) => {
    return formatApiError(err, fallback)
  }

  const handleCitizenFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    const epoch = ++extractionEpoch.current
    setExtracting(false)
    setAttachmentText('')
    setAttachedFile(file)
    setExtractError(null)
    setExtractionStatus('processing')

    const validationError = validateCitizenUpload(file)
    if (validationError) {
      setExtractError(validationError)
      setExtractionStatus('error')
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    setExtracting(true)
    try {
      let result = await searchApi.extractTextFromFile(file)
      if (epoch !== extractionEpoch.current) return
      setAttachedFile({
        name: file.name,
        size: file.size,
        type: file.type,
        file_id: result.file_id || undefined,
        sha256: result.sha256,
      })
      if (result.extraction_status === 'processing' && result.job_id) {
        for (let attempt = 0; attempt < 120 && epoch === extractionEpoch.current; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 750))
          const job = await searchApi.getExtractionJob(result.job_id)
          if (job.extraction_status === 'processing') continue
          result = {...result, ...job}
          break
        }
      }
      if (epoch !== extractionEpoch.current) return
      if (result.extraction_status === 'complete' || result.extraction_status === 'partial') {
        // Indexed attachments are referenced by owner-scoped ID. Keep text
        // only for backward compatibility with conversations saved pre-v2.
        setAttachmentText(result.file_id ? '' : result.extracted_text)
        setExtractionStatus(result.extraction_status)
      } else if (result.extraction_status === 'processing') {
        setExtractError('Tài liệu mất quá nhiều thời gian để đọc. Có thể thử lại sau khi tải lại tệp.')
        setExtractionStatus('error')
      } else {
        setExtractError('Không trích xuất được văn bản từ file này. Hãy kiểm tra file có chữ rõ ràng hoặc thử định dạng khác.')
        setExtractionStatus('error')
      }
    } catch (err: unknown) {
      if (epoch !== extractionEpoch.current) return
      setExtractError(formatApiError(err, 'Không đọc được tệp. Vui lòng kiểm tra định dạng rồi thử lại.'))
      setExtractionStatus('error')
    } finally {
      if (epoch === extractionEpoch.current) setExtracting(false)
    }
  }

  useEffect(() => {
    if (!backgroundMetadataEnabled) {
      return
    }

    let cancelled = false
    setViewerIdentity({ username })

    const loadSearchDependencies = async () => {
      try {
        // These requests are independent. Starting the profile request with
        // the public metadata requests removes one full network round trip
        // from the first paint after login.
        const viewerPromise = apiClient.get('/users/me').catch((error) => {
          console.error('Failed to load viewer profile for greeting', error)
          return null
        })
        const [domainList, modelInfo, meRes] = await Promise.all([
          legalImportApi.domains().catch(() => []),
          searchApi.localModels().catch(() => null),
          viewerPromise,
        ])

        if (cancelled) {
          return
        }

        setDomains(domainList)
        setLocalModels(modelInfo)

        if (modelInfo?.recommended) {
          setOfflineModel(modelInfo.recommended)
        }

        // Keep names/titles local to the UI. They are used for respectful
        // greetings and are not inserted into the external LLM prompt.
        if (meRes) {
          const profile = meRes.data.profile || {}
          if (cancelled) return
          setViewerIdentity({
            gender: profile.preferences?.gender || null,
            fullName: profile.full_name || meRes.data.full_name || null,
            department: profile.department || meRes.data.department || null,
            jobTitle: profile.job_title || meRes.data.job_title || null,
            username: meRes.data.username || null,
          })

          if (role === 'officer') {
            const rawDomain = String(
              (Array.isArray(profile.allowed_domains) && profile.allowed_domains[0]) ||
                (Array.isArray(meRes.data.allowed_domains) && meRes.data.allowed_domains[0]) ||
                '',
            ).trim()
            const canonicalDomain = ({
              ho_tich: 'ho_tich_chung_thuc',
              tu_phap_ho_tich: 'ho_tich_chung_thuc',
              dat_dai: 'dat_dai_xay_dung',
              cu_tru: 'cu_tru_an_ninh',
              khieu_nai: 'khieu_nai_to_cao_xu_phat',
              xu_phat: 'khieu_nai_to_cao_xu_phat',
            } as Record<string, string>)[rawDomain] || rawDomain
            const matchedDomain = WARD_AGENCY_OPTIONS.find(
              (item) => item.domain === canonicalDomain,
            )
            const dept =
              profile.department ||
              meRes.data.department ||
              matchedDomain?.domainName ||
              inferOfficerDepartment(meRes.data.username || username) ||
              null
            if (!cancelled) {
              setOfficerAssignment(dept)
              setViewerIdentity((current) => ({
                ...(current || {}),
                department: dept || current?.department || null,
              }))
              // The officer Q&A surface deliberately has no persistent domain
              // selector.  The server auto-detects the canonical domain and
              // enforces this profile's allowed_domains before retrieval.
            }
          }
        }
      } catch {
        if (!cancelled) {
          setDomains([])
          setLocalModels(null)
        }
      }
    }

    void loadSearchDependencies()

    return () => {
      cancelled = true
    }
  }, [backgroundMetadataEnabled, role, username])

  const welcome = useMemo(
    () => welcomeMessage(role, viewerIdentity),
    [role, viewerIdentity],
  )
  const starterQuestions = useMemo(
    () => chatStarterQuestions(role, viewerIdentity),
    [role, viewerIdentity],
  )


  // Track if we've already auto-triggered from URL params
  const hasAutoTriggeredRef = useRef(false)
  // The initial URL is already copied into askQuestion/searchQuery during
  // state initialization. Treat it as observed so the URL-sync effect does
  // not reset the auto-submit guard and fire the same request a second time.
  const lastUrlParamsRef = useRef({ q: urlQuery, mode: urlMode })

  const handleSearch = useCallback(() => {
    if (!searchQuery.trim()) return

    searchMutation.mutate({
      query: searchQuery,
      type: searchType,
      limit: 100,
      search_sources: searchSources,
      search_notes: searchNotes,
      minimum_score: 0.2
    })
  }, [searchQuery, searchType, searchSources, searchNotes, searchMutation])

  const persistConversationMessage = useCallback(async (
    conversationId: string,
    payload: Record<string, unknown>,
  ) => {
    let lastError: unknown
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        return await apiClient.post(`/conversations/${conversationId}/messages`, payload)
      } catch (error) {
        lastError = error
        if (attempt === 0) {
          await new Promise((resolve) => window.setTimeout(resolve, 250))
        }
      }
    }
    throw lastError
  }, [])

  const handleAsk = useCallback(async () => {
    const attachmentReady = Boolean(
      attachedFile && (attachedFile.file_id || attachmentText) &&
      (extractionStatus === 'complete' || extractionStatus === 'partial')
    )
    const questionText = askQuestion.trim() || (attachmentReady ? 'Hãy đọc và phân tích tài liệu đính kèm.' : '')
    if (!questionText) return
    if (sessionHydrating || ask.isStreaming || extracting || removingAttachment || extractError || askSubmitLockRef.current) return
    const requestEpoch = sessionEpochRef.current
    const requestIsCurrent = () => isCurrentSessionEpoch(requestEpoch, sessionEpochRef.current)
    // A click, Enter, and a URL auto-submit can arrive in the same
    // render window. Do not create a second conversation turn for that event.
    const submissionKey = `${currentSessionId || 'new'}:${questionText.replace(/\s+/g, ' ')}`
    const previousSubmission = lastAskSubmissionRef.current
    if (previousSubmission && previousSubmission.key === submissionKey && Date.now() - previousSubmission.at < 3000) {
      return
    }
    lastAskSubmissionRef.current = { key: submissionKey, at: Date.now() }
    // React state is asynchronous; this synchronous lock closes the small
    // window where click + Enter or URL auto-submit can start two requests.
    askSubmitLockRef.current = true
    try {

    // Render the pending turn before the first network await. Session creation
    // and persistence may be slow, but this placeholder remains the stable
    // assistant turn for the whole attempt.
    const turnId = `turn-${crypto.randomUUID()}`
    const turnStartedAt = Date.now()
    const turnCreatedAt = new Date(turnStartedAt).toISOString()
    const userMsg: AskMessage = {
      id: `u_${turnStartedAt}`,
      role: "user",
      content: questionText,
      created_at: turnCreatedAt,
      attachments: attachedFile && attachmentReady ? [{
        kind: 'uploaded_document_context_v1',
        value: {
          name: attachedFile.name,
          size: attachedFile.size,
          type: attachedFile.type,
          file_id: attachedFile.file_id,
          sha256: attachedFile.sha256,
          status: extractionStatus,
        },
      }] : undefined,
    }
    const pendingAssistantId = `a_pending_${turnStartedAt}`
    const pendingAssistantMsg: AskMessage = {
      id: pendingAssistantId,
      role: "assistant",
      content: "",
      status: "pending",
      created_at: turnCreatedAt,
    }
    setChatHistory((prev) => appendPendingAskTurn(prev, userMsg, pendingAssistantMsg))

    // Auto-create session on first ask if none exists
    let activeSessionId = currentSessionId
    if (!activeSessionId) {
      try {
        const res = await apiClient.post('/conversations', {
          title: (askQuestion.trim() || attachedFile?.name || questionText).substring(0, 60).replace(/\n/g, ' '),
          domain: null,
        })
        if (!requestIsCurrent()) return
        activeSessionId = res.data.id
        setCurrentSessionId(activeSessionId)
        setConversationRefreshKey((value) => value + 1)
        router.replace('/search')
      } catch (error) {
        setChatHistory((prev) => failAskTurn(
          prev,
          pendingAssistantId,
          'Không thể tạo cuộc trò chuyện. Vui lòng kiểm tra kết nối rồi thử lại.',
        ))
        toast.error('Không tạo được cuộc trò chuyện để lưu lịch sử.', {
          description: extractApiErrorMessage(error, 'Vui lòng kiểm tra kết nối hệ thống rồi thử lại.'),
        })
        return
      }
    }

    if (activeSessionId) {
      try {
        if (!requestIsCurrent()) return
        await persistConversationMessage(activeSessionId, {
          role: 'user',
          content: questionText,
          turn_id: turnId,
          document_context: attachedFile && attachmentReady ? {
            name: attachedFile.name, size: attachedFile.size, type: attachedFile.type,
            text: attachedFile.file_id ? '' : attachmentText,
            file_id: attachedFile.file_id,
            sha256: attachedFile.sha256,
            status: extractionStatus,
          } : null,
        })
      } catch (error) {
        const persistenceError = extractApiErrorMessage(
          error,
          'Không lưu được câu hỏi vào lịch sử. Vui lòng thử lại.',
        )
        setChatHistory((prev) => failAskTurn(prev, pendingAssistantId, persistenceError))
        toast.error('Không thể lưu câu hỏi', { description: persistenceError })
        return
      }
    }

    const defaultChat = modelDefaults?.default_chat_model || ""
    if (!offlineMode && !defaultChat && !customModels?.strategy && !customModels?.answer && !customModels?.finalAnswer) {
      // Still allow send: backend will resolve defaults.
    }

    const models = {
      strategy: (customModels?.strategy || defaultChat || "").trim(),
      answer: (customModels?.answer || defaultChat || "").trim(),
      finalAnswer: (customModels?.finalAnswer || defaultChat || "").trim(),
    }

    const response = await ask.sendAsk(questionText, models, role, {
      turnId,
      offlineMode,
      offlineModel,
      domain: null,
      modelOptionId: selectedModelOption || null,
      answerDepth,
      attachmentId: attachedFile?.file_id,
      attachmentText: attachedFile?.file_id ? undefined : attachmentText,
      attachmentName: attachedFile?.name,
      attachmentSha256: attachedFile?.sha256,
      attachmentStatus: extractionStatus,
      showRagTrace,
      conversationId: activeSessionId,
      prePersistedUserMessage: Boolean(activeSessionId),
      memoryItemIds: selectedMemoryItemIds,
    })
    if (!requestIsCurrent()) return

    // Update the pending assistant message in-place with full snapshot
    if (response?.answer) {
      const completeAssistantMsg: AskMessage = {
        ...pendingAssistantMsg,
        content: response.answer,
        status: "complete",
        citations: response.citations || undefined,
        answer_sections: response.answer_sections || undefined,
        recommended_forms: response.recommended_forms || undefined,
        faq_refs: (response.faqs || []).map((f) => f.id).filter(Boolean),
        faqs: response.faqs || undefined,
        procedure_detail: response.procedure_detail || undefined,
        rag_trace: response.rag_trace || undefined,
        grounding_status: response.grounding_status || undefined,
        answer_completeness: response.answer_completeness || undefined,
        answer_mode: response.answer_mode || undefined,
        answer_status: response.answer_status || undefined,
        fallback_tier: response.fallback_tier || undefined,
        canonical_domain: response.canonical_domain || undefined,
        evidence_count: response.evidence_count,
        coverage_warning: response.coverage_warning || undefined,
        blocked_reason: response.blocked_reason || undefined,
        outcome: response.outcome || undefined,
        reason_code: response.reason_code || undefined,
        retryable: response.retryable,
        scope: response.scope || undefined,
        persistence_degraded: response.persistence_degraded === true,
        quality: response.quality || undefined,
        forms_unavailable: response.forms_unavailable,
        presentation_version: response.presentation_version || undefined,
        answer_route: response.answer_route || undefined,
        pipeline_version: response.pipeline_version || undefined,
        data_release_id: response.data_release_id || undefined,
        release_id: response.release_id || undefined,
        index_fingerprint: response.index_fingerprint || undefined,
        manifest_hash: response.manifest_hash || undefined,
        validity_snapshot: response.validity_snapshot || undefined,
        verification_label: response.verification_label || undefined,
        historical_label: response.historical_label || undefined,
        sections: response.sections || undefined,
        suggested_questions: response.suggested_questions || undefined,
        conversation_route: response.conversation_route || undefined,
        active_document: response.active_document || undefined,
        related_documents: response.related_documents || undefined,
        memory_usage: response.memory_usage || undefined,
        model_option_id: response.model_option_id || undefined,
        model_display_name: response.model_display_name || undefined,
        model_locked: response.model_locked,
        generation_provenance: response.generation_provenance || undefined,
        timing_summary: response.timing_summary || undefined,
      }
      setChatHistory((prev) => completeAskTurn(prev, pendingAssistantId, completeAssistantMsg))
      if (response.model_option_id !== undefined && response.model_option_id !== null) {
        setSelectedModelOption(response.model_option_id)
      }

      if (activeSessionId) {
        // Direct RAG already commits the assistant snapshot using the same
        // idempotency key. Do not read the entire conversation or write a
        // second copy from the browser; refresh only when the user later
        // navigates/reloads the session.
        setConversationRefreshKey((value) => value + 1)
      }
    } else if (response === null) {
      // Abort is a neutral user action. Keep the turn visible but never mark or
      // persist it as an error.
      setChatHistory((prev) => cancelAskTurn(prev, pendingAssistantId))
    } else {
      // Error case: mark pending as error
      const responseError = response && typeof response === 'object' && 'error' in response
        ? (response as { error?: string }).error
        : null
      const visibleError = responseError || ask.error || 'Không thể trả lời câu hỏi. Vui lòng thử lại.'
      setChatHistory((prev) =>
        failAskTurn(prev, pendingAssistantId, "Không thể trả lời câu hỏi. Vui lòng thử lại.")
      )
      // The legacy fallback above is kept for compatibility; overwrite it
      // immediately with the structured backend error when one is available.
      if (visibleError) {
        setChatHistory((prev) => failAskTurn(prev, pendingAssistantId, visibleError))
      }
      if (activeSessionId) {
        try {
          if (!requestIsCurrent()) return
          await persistConversationMessage(activeSessionId, {
            role: 'assistant',
            content: visibleError,
            status: 'error',
          })
          setConversationRefreshKey((value) => value + 1)
        } catch (error) {
          toast.error('Không lưu được trạng thái lỗi vào lịch sử.', {
            description: extractApiErrorMessage(
              error,
              'Dữ liệu vẫn được giữ tạm trên màn hình hiện tại.',
            ),
          })
        }
      }
    }

    // Clear input after successful turn so citizen can continue the conversation.
    if (response?.answer) {
      setAskQuestion("")
      setSelectedMemoryItemIds([])
    }

      // Reset useAsk state so it does not render a second copy
      ask.reset()
    } finally {
      askSubmitLockRef.current = false
    }
  }, [askQuestion, modelDefaults, customModels, ask, role, offlineMode, offlineModel, selectedModelOption, answerDepth, attachmentText, attachedFile, extractionStatus, extracting, removingAttachment, extractError, showRagTrace, currentSessionId, persistConversationMessage, router, sessionHydrating, selectedMemoryItemIds])

  const [escalating, setEscalating] = useState(false)
  const [supportDialogOpen, setSupportDialogOpen] = useState(false)
  const [supportPreview, setSupportPreview] = useState<SupportPreview | null>(null)
  const [supportDomain, setSupportDomain] = useState('')
  const [supportConfirming, setSupportConfirming] = useState(false)

  const lastCompleteAssistant = useMemo(
    () => [...chatHistory].reverse().find((m) => m.role === 'assistant' && m.status === 'complete' && m.content),
    [chatHistory]
  )

  const handleEscalateSupport = useCallback(async () => {
    setEscalating(true)
    try {
      if (!currentSessionId) {
        toast.error('Chưa có phiên hỏi đáp để chuyển cho cán bộ.')
        return
      }
      const sourceMessage = [...chatHistory].reverse().find((message) => message.role === 'user')
      const response = await apiClient.post<SupportPreview>('/support/tickets/preview', {
        conversation_id: currentSessionId,
        source_message_id: sourceMessage?.id,
      })
      const preview = response.data
      setSupportPreview(preview)
      setSupportDomain(preview.suggested_domain || preview.options[0]?.domain || '')
      setSupportDialogOpen(true)
    } catch (err) {
      toast.error(getApiErrorCode(err) === 'conversation_has_no_user_question'
        ? 'Phiên này chưa có câu hỏi để chuyển hỗ trợ.'
        : formatApiError(err, 'Không thể chuẩn bị yêu cầu hỗ trợ trực tuyến.'))
    } finally {
      setEscalating(false)
    }
  }, [currentSessionId, chatHistory])

  const confirmSupport = useCallback(async () => {
    if (!supportPreview || !supportDomain) return
    setSupportConfirming(true)
    try {
      const response = await apiClient.post('/support/tickets', {
        question: supportPreview.question,
        domain: supportDomain,
        ai_summary: supportPreview.ai_summary || null,
        conversation_id: supportPreview.conversation_id || currentSessionId,
        source_message_id: supportPreview.source_message_id || undefined,
        routing_revision: supportPreview.config_revision,
        priority: 'normal',
      })
      toast.success('Đã gửi yêu cầu hỗ trợ trực tuyến thành công!')
      setSupportDialogOpen(false)
      router.push(`/live-support?ticket=${response.data.id}`)
    } catch (err) {
      if (getApiErrorCode(err) === 'support_routing_changed') {
        toast.error('Cơ cấu tiếp nhận vừa thay đổi. Vui lòng mở lại hộp xác nhận.')
        setSupportDialogOpen(false)
      } else {
        toast.error('Không thể gửi yêu cầu hỗ trợ trực tuyến.')
      }
    } finally {
      setSupportConfirming(false)
    }
  }, [currentSessionId, router, supportDomain, supportPreview])

  // Auto-trigger search/ask when arriving with URL params
  useEffect(() => {
    // Skip if already triggered or no query
    if (hasAutoTriggeredRef.current || !urlQuery) return

    // Wait for models to load before triggering ask
    if (urlMode === 'ask' && modelsLoading) return

    if (urlMode === 'search' && canUseSearchTab) {
      handleSearch()
      hasAutoTriggeredRef.current = true
    } else if (urlMode === 'ask' && (!adminModelMetadataEnabled || modelDefaults?.default_chat_model)) {
      hasAutoTriggeredRef.current = true
      handleAsk()
    }
  }, [urlQuery, urlMode, modelsLoading, modelDefaults, adminModelMetadataEnabled, handleSearch, handleAsk, canUseSearchTab])

  // Handle URL param changes while on page (e.g., from command palette again)
  useEffect(() => {
    const currentQ = searchParams?.get('q') || ''
    const rawCurrentMode = searchParams?.get('mode')
    const currentMode: 'ask' | 'search' =
      rawCurrentMode === 'search' && canUseSearchTab ? 'search' : 'ask'

    // Check if URL params have changed
    if (currentQ !== lastUrlParamsRef.current.q || currentMode !== lastUrlParamsRef.current.mode) {
      lastUrlParamsRef.current = { q: currentQ, mode: currentMode }

      if (currentQ) {
        // Update state based on mode
        if (currentMode === 'search') {
          setSearchQuery(currentQ)
          setActiveTab('search')
          // Reset trigger flag so we auto-trigger with new params
          hasAutoTriggeredRef.current = false
        } else {
          setAskQuestion(currentQ)
          setActiveTab('ask')
          hasAutoTriggeredRef.current = false
        }
      } else if (!canUseSearchTab) {
        setActiveTab('ask')
      }
    }
  }, [searchParams, canUseSearchTab])

  // Citizen must never stay on Search tab (direct link, stale state, role switch).
  useEffect(() => {
    if (!canUseSearchTab && activeTab === 'search') {
      setActiveTab('ask')
    }
  }, [canUseSearchTab, activeTab])

  return (
    <LegalPreviewProvider><AppShell>
      <div className="flex min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b bg-card px-4 py-2 lg:hidden">
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-primary">Cổng hỏi đáp có căn cứ</p>
            <h1 className="truncate font-display text-2xl font-bold text-foreground">{t('navigation.askAndSearch', 'Hỏi đáp pháp luật')}</h1>
          </div>
          <Button
            type="button"
            variant="outline"
            className="h-11 shrink-0 rounded-full px-4 lg:hidden"
            onClick={() => setMobileConversationDrawerOpen(true)}
          >
            Lịch sử
          </Button>
        </div>

        <div className="flex min-h-0 w-full flex-1">
            <div className="flex h-full min-h-0 w-full overflow-hidden bg-card">
              <ConversationSidebar
                role={role}
                currentId={currentSessionId}
                onSelect={handleConversationSelected}
                onCreate={handleConversationCreated}
                onSelectStart={beginSessionTransition}
                onCreateStart={beginSessionTransition}
                onTransitionError={handleSessionTransitionError}
                onDeleted={handleConversationDeleted}
                mobileOpen={mobileConversationDrawerOpen}
                onMobileOpenChange={setMobileConversationDrawerOpen}
                refreshKey={conversationRefreshKey}
                contextLabel={role === 'officer' ? officerAssignment : null}
              />
              <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                <header className="hidden shrink-0 items-center justify-between border-b px-6 py-4 lg:flex">
                  <h1 className="text-base font-semibold">Hỏi đáp pháp luật</h1>
                  <span className="text-xs text-muted-foreground">Tra cứu có căn cứ</span>
                </header>
            <Card className="flex h-full min-h-0 flex-col border-0 bg-card shadow-none">
              <CardContent className="relative flex min-h-0 flex-1 flex-col overflow-hidden p-0">
                {role === 'citizen' && shouldOfferOfficerSupport(lastCompleteAssistant) && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleEscalateSupport}
                    disabled={escalating}
                    className="absolute right-4 top-3 z-10 h-9 rounded-full border-amber-300 bg-amber-50/95 px-3 text-amber-950 shadow-sm backdrop-blur hover:bg-amber-100 dark:bg-amber-950/90 dark:text-amber-100"
                    title="Chuyển câu hỏi chưa đủ căn cứ đến cán bộ chuyên trách"
                  >
                    <AlertCircle className="mr-1.5 h-4 w-4" aria-hidden="true" />
                    {escalating ? 'Đang chuyển…' : 'Nhờ cán bộ hỗ trợ'}
                  </Button>
                )}
                <div
                  ref={chatContainerRef}
                  onScroll={(event) => {
                    if (event.currentTarget.scrollTop <= 24 && hasOlderMessages) {
                      void loadOlderMessages()
                    }
                  }}
                  className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-background/45 px-3 py-5 md:px-6"
                >
                  <div className="mx-auto w-full max-w-[820px]">
                  {chatHistory.length === 0 && sessionHydrating && (
                    <div className="mx-auto max-w-3xl space-y-3 py-8" role="status" aria-live="polite">
                      <p className="text-sm font-medium text-muted-foreground">Đang mở cuộc trò chuyện gần nhất…</p>
                      <div className="h-20 animate-pulse rounded-2xl border bg-muted/35" aria-hidden="true" />
                      <div className="ml-auto h-12 w-2/3 animate-pulse rounded-3xl bg-muted/50" aria-hidden="true" />
                    </div>
                  )}
                  {chatHistory.length === 0 && !sessionHydrating && (
                    <div className="mx-auto flex min-h-full max-w-2xl items-center justify-center py-8" data-testid="chat-welcome">
                      <div className="w-full rounded-2xl border bg-card/80 px-5 py-6 text-center shadow-sm">
                        <p className="text-lg font-semibold text-foreground">{welcome.title}</p>
                        <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
                          {welcome.invitation}
                        </p>
                        {starterQuestions.length > 0 && (
                          <div className="mt-4 flex flex-wrap justify-center gap-2" aria-label="Câu hỏi bắt đầu">
                            {starterQuestions.map((question) => (
                              <button
                                key={question}
                                type="button"
                                className="max-w-full rounded-full border bg-background px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:border-primary hover:bg-primary/5"
                                onClick={() => {
                                  setAskQuestion(question)
                                  setSelectedMemoryItemIds([])
                                }}
                              >
                                {question}
                              </button>
                            ))}
                          </div>
                        )}
                        <ChatMemoryContinuations onSelect={handleMemoryContinuation} />
                      </div>
                    </div>
                  )}
                  {chatHistory.length > 0 && hasOlderMessages && (
                    <div className="mb-3 flex justify-center">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => void loadOlderMessages()}
                        disabled={loadingOlderMessages}
                        aria-label="Tải tin nhắn cũ"
                      >
                        {loadingOlderMessages ? 'Đang tải…' : 'Tải tin nhắn cũ'}
                      </Button>
                    </div>
                  )}
                  <AskMessageHistory
                    messages={chatHistory}
                    role={role}
                    viewerIdentity={viewerIdentity}
                    showRagTrace={showRagTrace}
                    pendingStageLabel={ask.stageLabel}
                    pendingAnswer={ask.isStreaming ? ask.finalAnswer : null}
                    pendingCitations={ask.citations}
                    onSuggestionClick={handleDraftQuestion}
                    onRetryClick={handleDraftQuestion}
                  />
                  </div>
                </div>

                <div className="mx-auto w-full max-w-[860px] shrink-0 space-y-3 border-t border-border/60 bg-card px-3 py-3 md:px-5">
                {/* Question Input */}
                <div className="space-y-2">
                  <Label htmlFor="ask-question" className="sr-only">{t('searchPage.question')}</Label>
                  {attachedFile && <AttachmentCard file={attachedFile} busy={extracting} status={extractionStatus} error={extractError} removing={removingAttachment} removeDisabled={ask.isStreaming} onRemove={() => void clearAttachedFile()} />}
                  <div className="rounded-[28px] border border-border bg-card p-2 shadow-[0_2px_12px_rgba(47,35,22,0.06)] transition-shadow focus-within:border-primary/50 focus-within:shadow-[0_4px_20px_rgba(143,29,44,0.12)]">
                    <div className="flex min-w-0 flex-col">
                      <div className="flex min-w-0 items-end gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-11 w-11 shrink-0 rounded-full"
                          disabled={ask.isStreaming || extracting || removingAttachment}
                          onClick={() => fileInputRef.current?.click()}
                          title="Đính kèm văn bản hoặc ảnh tài liệu để đọc chữ"
                          aria-label="Đính kèm tệp"
                        >
                          <Paperclip className="h-5 w-5" />
                        </Button>
                        <Textarea
                          ref={askInputRef}
                          id="ask-question"
                          name="ask-question"
                          placeholder={t('searchPage.enterQuestionPlaceholder')}
                          value={askQuestion}
                          onChange={(e) => setAskQuestion(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !ask.isStreaming && !sessionHydrating && (askQuestion.trim() || (attachedFile && (extractionStatus === 'complete' || extractionStatus === 'partial')))) {
                              e.preventDefault()
                              void handleAsk()
                            }
                          }}
                          disabled={ask.isStreaming}
                          rows={1}
                          className="min-h-11 max-h-[180px] flex-1 resize-none overflow-y-auto border-0 bg-transparent px-2 py-2.5 text-[17px] leading-7 shadow-none focus-visible:ring-0 md:text-[17px]"
                          aria-label={t('common.accessibility.enterQuestion')}
                        />
                      </div>

                      <div className="mt-1 flex min-h-11 shrink-0 items-center justify-between gap-2 border-t border-border/50 pt-1 pl-1">
                        <ChatModelSelector compact options={chatModelOptions} value={selectedModelOption}
                          onValueChange={setSelectedModelOption} depth={answerDepth} onDepthChange={setAnswerDepth} busy={ask.isStreaming} />

                        <Button
                          type="button"
                          size="icon"
                          onClick={ask.isStreaming ? ask.cancel : handleAsk}
                          disabled={!ask.isStreaming && (extracting || removingAttachment || !!extractError || sessionHydrating || (!askQuestion.trim() && !(attachedFile && (extractionStatus === 'complete' || extractionStatus === 'partial'))))}
                          className="h-11 w-11 rounded-full"
                          aria-label={ask.isStreaming ? 'Dừng tạo câu trả lời' : t('searchPage.ask')}
                        >
                          {ask.isStreaming ? <span className="h-3.5 w-3.5 rounded-sm bg-current" /> : <ArrowUp className="h-5 w-5" />}
                        </Button>
                      </div>
                    </div>
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    className="hidden"
                    accept=".txt,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg"
                    onChange={handleCitizenFileChange}
                  />
                  {extractError && (
                    <p className="text-xs text-destructive flex items-center gap-1">
                      <AlertCircle className="h-3 w-3" /> {extractError}
                    </p>
                  )}

                  {sessionHydrating && (
                    <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
                      Bạn có thể nhập ngay; nút gửi sẽ sẵn sàng khi lịch sử tải xong.
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">{t('searchPage.pressToSubmit')}</p>
                </div>

                {/* Models Display */}
                {role === 'admin' && (
                  <>
                    <div className="grid gap-3 rounded-lg border p-3 md:grid-cols-2">
                      <div className="flex items-start gap-3">
                        <Checkbox
                          id="offline-mode"
                          checked={offlineMode}
                          onCheckedChange={(checked) => setOfflineMode(checked === true)}
                          disabled={ask.isStreaming}
                        />
                        <div className="space-y-1">
                          <Label htmlFor="offline-mode">Chạy local bằng Ollama</Label>
                          <p className="text-xs text-muted-foreground">
                            Dùng khi tắt mạng. Cần bật Ollama trên máy.
                          </p>
                        </div>
                      </div>
                      <div className="flex items-start gap-3">
                        <Checkbox
                          id="show-rag-trace"
                          checked={showRagTrace}
                          onCheckedChange={(checked) => setShowRagTrace(checked === true)}
                          disabled={ask.isStreaming}
                        />
                        <div className="space-y-1">
                          <Label htmlFor="show-rag-trace">Hiển thị quy trình RAG</Label>
                          <p className="text-xs text-muted-foreground">
                            Xem câu hỏi, lĩnh vực, đoạn tra cứu, nguồn và mục bị lọc.
                          </p>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <Label>Lĩnh vực phường/xã</Label>
                        <Select
                          value={selectedDomain}
                          onValueChange={setSelectedDomain}
                          disabled={ask.isStreaming}
                        >
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__auto__">Tự nhận diện</SelectItem>
                            {domains.map((domain) => (
                              <SelectItem key={domain.slug} value={domain.slug}>
                                {domain.name} ({domain.field_count})
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-2">
                        <Label>{t('common.modelLocal')}</Label>
                        <Select
                          value={offlineModel}
                          onValueChange={setOfflineModel}
                          disabled={!offlineMode || ask.isStreaming}
                        >
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value={localModels?.recommended || 'qwen2.5:3b'}>
                              {localModels?.recommended || 'qwen2.5:3b'} (khuyến nghị)
                            </SelectItem>
                            {(localModels?.models || [])
                              .filter((model) => model.name)
                              .map((model) => (
                                <SelectItem key={model.name} value={model.name}>
                                  {model.name}
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                          {localModels?.available
                            ? localModels.recommended_installed
                              ? 'Ollama đã có model khuyến nghị.'
                              : 'Ollama chạy được nhưng chưa thấy model khuyến nghị.'
                            : 'Chưa kết nối được Ollama.'}
                        </p>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label className="text-xs text-muted-foreground">
                          {customModels ? t('searchPage.usingCustomModels') : t('searchPage.usingDefaultModels')}
                        </Label>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setShowAdvancedModels(true)}
                          disabled={ask.isStreaming}
                          className="h-auto py-1 px-2"
                        >
                          <Settings className="h-3 w-3 mr-1" />
                          {t('searchPage.advanced')}
                        </Button>
                      </div>
                      <div className="flex gap-2 text-xs flex-wrap">
                        <Badge variant="secondary">
                          {t('searchPage.strategy')}: {resolveModelName(customModels?.strategy || modelDefaults?.default_chat_model)}
                        </Badge>
                        <Badge variant="secondary">
                          {t('searchPage.answer')}: {resolveModelName(customModels?.answer || modelDefaults?.default_chat_model)}
                        </Badge>
                        <Badge variant="secondary">
                          {t('searchPage.final')}: {resolveModelName(customModels?.finalAnswer || modelDefaults?.default_chat_model)}
                        </Badge>
                      </div>
                    </div>
                  </>
                )}



                </div>

                {ask.isStreaming && (
                  <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                    {ask.stageLabel || 'Đang xử lý câu hỏi…'}
                  </div>
                )}

                {ask.error && (
                  <div
                    role="alert"
                    className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-950"
                  >
                    <div className="mb-2 flex items-center gap-2 font-semibold">
                      <AlertCircle className="h-4 w-4" />
                      Không thể trả lời câu hỏi
                    </div>
                    <pre className="whitespace-pre-wrap break-words font-sans text-sm">
                      {ask.error}
                    </pre>
                  </div>
                )}


                {ask.domainMismatch && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/30 p-4 text-sm text-amber-950 dark:text-amber-100 space-y-3">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                      <div className="space-y-1">
                        <p className="font-semibold">
                          {role === 'officer' ? 'Bạn đang hỏi sai lĩnh vực' : 'Gợi ý chuyển lĩnh vực phù hợp'}
                        </p>
                        <p className="text-xs md:text-sm opacity-90">
                          {role === 'officer'
                          ? 'Luồng trả lời chuyên sâu đã được tạm dừng vì câu hỏi nằm ngoài lĩnh vực được phân công. Hãy gửi cho cán bộ đúng lĩnh vực.'
                            : 'Hệ thống nhận thấy câu hỏi có thể thuộc lĩnh vực khác. Bạn có thể gửi yêu cầu hỗ trợ để được cán bộ phù hợp hướng dẫn.'}
                        </p>
                        {ask.suggestedDomain && (
                          <p className="text-xs md:text-sm">
                            Gợi ý: <strong>{WARD_AGENCY_OPTIONS.find(i => i.domain === ask.suggestedDomain)?.domainName || ask.suggestedDomain}</strong>
                            {ask.suggestedAgency ? <> · Cơ quan: <strong>{ask.suggestedAgency}</strong></> : null}
                          </p>
                        )}
                      </div>
                    </div>
                    <div className="flex gap-2">
                      {role === 'admin' && ask.suggestedDomain && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="bg-background"
                          onClick={() => {
                            setSelectedDomain(ask.suggestedDomain || '__auto__')
                          }}
                        >
                          Chuyển sang {WARD_AGENCY_OPTIONS.find(i => i.domain === ask.suggestedDomain)?.domainName || ask.suggestedDomain}
                        </Button>
                      )}
                      {role === 'citizen' && (
                        <Button
                          type="button"
                          size="sm"
                          variant="secondary"
                          onClick={handleEscalateSupport}
                          disabled={escalating}
                        >
                          {escalating ? 'Đang gửi...' : 'Gửi hỗ trợ trực tuyến'}
                        </Button>
                      )}
                    </div>
                  </div>
                )}

                <Dialog open={supportDialogOpen} onOpenChange={setSupportDialogOpen}>
                  <DialogContent className="max-w-2xl">
                    <DialogHeader>
                      <DialogTitle>Xác nhận yêu cầu hỗ trợ trực tuyến</DialogTitle>
                      <DialogDescription>
                        Kiểm tra nội dung và chọn lĩnh vực để chuyển đúng đơn vị cấp 2. Chưa có ticket nào được tạo ở bước này.
                      </DialogDescription>
                    </DialogHeader>
                    {supportPreview && (
                      <div className="space-y-4">
                        <div className="rounded-lg border bg-muted/30 p-3 text-sm">
                          <p className="mb-1 font-medium">Câu hỏi chuyển cho cán bộ</p>
                          <p className="whitespace-pre-wrap break-words">{supportPreview.question}</p>
                        </div>
                        {supportPreview.ai_summary && (
                          <div className="rounded-lg border p-3 text-sm">
                            <p className="mb-1 font-medium">Thông tin AI đã cung cấp</p>
                            <p className="whitespace-pre-wrap break-words text-muted-foreground">{supportPreview.ai_summary}</p>
                          </div>
                        )}
                        <div className="space-y-2">
                          <Label htmlFor="support-domain">Lĩnh vực đề xuất / lựa chọn lại</Label>
                          <Select value={supportDomain} onValueChange={setSupportDomain}>
                            <SelectTrigger id="support-domain"><SelectValue placeholder="Chọn lĩnh vực" /></SelectTrigger>
                            <SelectContent>
                              {supportPreview.options.map((option) => (
                                <SelectItem key={`${option.domain}:${option.unit_id}`} value={option.domain}>
                                  {option.domain_name} · {option.unit_short_name || option.unit_name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        {supportPreview.options.find((option) => option.domain === supportDomain) && (
                          <p className="text-sm text-muted-foreground">
                            Đơn vị tiếp nhận: <strong className="text-foreground">{supportPreview.options.find((option) => option.domain === supportDomain)?.unit_name}</strong>
                          </p>
                        )}
                      </div>
                    )}
                    <DialogFooter>
                      <Button type="button" variant="outline" onClick={() => setSupportDialogOpen(false)} disabled={supportConfirming}>Hủy</Button>
                      <Button type="button" onClick={() => void confirmSupport()} disabled={!supportPreview || !supportDomain || supportConfirming}>
                        {supportConfirming ? 'Đang gửi…' : 'Xác nhận và gửi'}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>

                {/* Advanced Models Dialog */}
                {showAdvancedModels && <AdvancedModelsDialog
                  open={showAdvancedModels}
                  onOpenChange={setShowAdvancedModels}
                  defaultModels={{
                    strategy: customModels?.strategy || modelDefaults?.default_chat_model || '',
                    answer: customModels?.answer || modelDefaults?.default_chat_model || '',
                    finalAnswer: customModels?.finalAnswer || modelDefaults?.default_chat_model || ''
                  }}
                  onSave={setCustomModels}
                />}

              </CardContent>
            </Card>
              </div>
            </div>
        </div>
      </div>
    </AppShell></LegalPreviewProvider>
  )
}
