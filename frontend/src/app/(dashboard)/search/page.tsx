'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import dynamic from 'next/dynamic'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { AppShell } from '@/components/layout/AppShell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Search, ChevronDown, AlertCircle, Settings, Save, MessageCircleQuestion, Paperclip, X, FileText, ImageIcon, Mic, Square, Shield, User } from 'lucide-react'
import { useSearch } from '@/lib/hooks/use-search'
import { useAsk } from '@/lib/hooks/use-ask'
import { useModelDefaults, useModels } from '@/lib/hooks/use-models'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { AskMessageHistory } from '@/components/search/AskMessageHistory'
import { ConversationSidebar, type ConversationDetail } from '@/components/search/ConversationSidebar'
import { useAuthStore } from '@/lib/stores/auth-store'
import { searchApi, LocalModelInfo } from '@/lib/api/search'
import { legalImportApi, LegalDomain } from '@/lib/api/legal-import'
import { apiClient } from '@/lib/api/client'
import type { AskMessage } from '@/lib/types/search'
import { appendPendingAskTurn, cancelAskTurn, completeAskTurn, failAskTurn } from '@/lib/utils/ask-turn-history'

const AdvancedModelsDialog = dynamic(
  () => import('@/components/search/AdvancedModelsDialog').then((module) => module.AdvancedModelsDialog),
  { ssr: false },
)
const SaveToNotebooksDialog = dynamic(
  () => import('@/components/search/SaveToNotebooksDialog').then((module) => module.SaveToNotebooksDialog),
  { ssr: false },
)

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


function buildVbplSearchUrl(lawNumber?: string | null, documentTitle?: string | null): string {
  const query = [lawNumber, documentTitle].filter(Boolean).join(' ').trim() || 'văn bản pháp luật'
  return `https://www.google.com/search?q=${encodeURIComponent(`${query} site:vbpl.vn`)}`
}

function isLikelyBrokenVbplUrl(url?: string | null): boolean {
  if (!url) return true
  // Old VBPL deep links frequently 404 / blank blank; treat as unreliable.
  return /vbpl\.vn\/Pages\/vbpq-toanvan\.aspx\?ItemID=/i.test(url)
}

function resolveLegalSourceLinks(opts: {
  sourceUrl?: string | null
  lawNumber?: string | null
  documentTitle?: string | null
}) {
  const fallbackSearchUrl = buildVbplSearchUrl(opts.lawNumber, opts.documentTitle)
  const directUrl = opts.sourceUrl && !isLikelyBrokenVbplUrl(opts.sourceUrl) ? opts.sourceUrl : null
  return {
    directUrl,
    searchUrl: fallbackSearchUrl,
  }
}


export default function SearchPage() {
  const { t } = useTranslation()
  const router = useRouter()
  const role = useAuthStore((state) => state.role) || 'citizen'
  const canUseSearchTab = false
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const hasHydrated = useAuthStore((state) => state.hasHydrated)
  const [userDept, setUserDept] = useState<string | null>(null)
  
// URL params
  const searchParams = useSearchParams()
  const urlQuery = searchParams?.get('q') || ''
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
  const [searchType, setSearchType] = useState<'text' | 'vector'>('text')
  const [searchSources, setSearchSources] = useState(true)
  const [searchNotes, setSearchNotes] = useState(true)

  // Ask state
  const [askQuestion, setAskQuestion] = useState(urlMode === 'ask' ? urlQuery : '')
  const [offlineMode, setOfflineMode] = useState(false)
  const [offlineModel, setOfflineModel] = useState('qwen2.5:3b')
  const [showRagTrace, setShowRagTrace] = useState(true)
  const [selectedDomain, setSelectedDomain] = useState<string>('__auto__')
  const [selectedAgency, setSelectedAgency] = useState<string>('__auto__')
  const [domains, setDomains] = useState<LegalDomain[]>([])
  const [localModels, setLocalModels] = useState<LocalModelInfo | null>(null)

  // --- Persistent Conversation Management ---
  // The API is the source of truth. Local state only mirrors the currently-open conversation.
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [chatHistory, setChatHistory] = useState<AskMessage[]>([])
  const [mobileConversationDrawerOpen, setMobileConversationDrawerOpen] = useState(false)
  const [conversationRefreshKey, setConversationRefreshKey] = useState(0)
  const [latestConversationLoaded, setLatestConversationLoaded] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)
  const askSubmitLockRef = useRef(false)
  const lastAskSubmissionRef = useRef<{ key: string; at: number } | null>(null)

  const loadConversation = useCallback((conversation: ConversationDetail) => {
    setCurrentSessionId(conversation.id)
    // A conversation must alternate user/assistant turns. Older duplicate
    // requests could leave two assistant messages next to each other; keep
    // the latest completed answer when repairing that persisted history.
    const normalizedMessages = conversation.messages.reduce<AskMessage[]>((acc, message) => {
      const previous = acc[acc.length - 1]
      const previousUser = acc[acc.length - 2]
      const normalizeMessage = (value?: string | null) => (value || '').replace(/\s+/g, ' ').trim()
      const messageTime = Date.parse(message.created_at || '')
      const previousUserTime = Date.parse(previousUser?.created_at || '')
      const isImmediateDuplicateFailedUserTurn =
        message.role === 'user' &&
        previous?.role === 'assistant' &&
        previous.status === 'error' &&
        previousUser?.role === 'user' &&
        normalizeMessage(previousUser.content) === normalizeMessage(message.content) &&
        Number.isFinite(messageTime) &&
        Number.isFinite(previousUserTime) &&
        Math.abs(messageTime - previousUserTime) <= 10000

      // Older builds could persist the same failed turn twice. Keep the first
      // question and let the adjacent-assistant rule below keep one error.
      if (isImmediateDuplicateFailedUserTurn) {
        return acc
      }
      if (message.role === 'assistant' && previous?.role === 'assistant') {
        acc[acc.length - 1] = message
      } else {
        acc.push(message)
      }
      return acc
    }, [])
    setChatHistory(normalizedMessages)
  }, [])

  const createNewSession = useCallback(async () => {
    try {
      const res = await apiClient.post('/conversations/', {
        title: 'Cuộc trò chuyện mới',
        domain: selectedDomain === '__auto__' ? null : selectedDomain,
      })
      loadConversation(res.data as ConversationDetail)
      setConversationRefreshKey((value) => value + 1)
    } catch {
      toast.error('Không tạo được cuộc trò chuyện mới.')
    }
  }, [loadConversation, selectedDomain])

  const handleConversationDeleted = useCallback((conversationId: string) => {
    if (currentSessionId === conversationId) {
      setCurrentSessionId(null)
      setChatHistory([])
      setConversationRefreshKey((value) => value + 1)
    }
  }, [currentSessionId])

  useEffect(() => {
    if (!hasHydrated || !isAuthenticated || latestConversationLoaded || currentSessionId) {
      return
    }

    let cancelled = false
    const loadLatestConversation = async () => {
      try {
        const listRes = await apiClient.get('/conversations/', { params: { limit: 1 } })
        const latest = Array.isArray(listRes.data) ? listRes.data[0] : null
        if (!latest?.id || cancelled) return
        const detailRes = await apiClient.get(`/conversations/${latest.id}`)
        if (!cancelled) {
          loadConversation(detailRes.data as ConversationDetail)
        }
      } catch {
        // Empty or temporarily unavailable history should not block asking.
      } finally {
        if (!cancelled) setLatestConversationLoaded(true)
      }
    }

    void loadLatestConversation()
    return () => {
      cancelled = true
    }
  }, [currentSessionId, hasHydrated, isAuthenticated, latestConversationLoaded, loadConversation, role])

  // Scroll to bottom on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory])

  // Advanced models dialog
  const [showAdvancedModels, setShowAdvancedModels] = useState(false)
  const [customModels, setCustomModels] = useState<{
    strategy: string
    answer: string
    finalAnswer: string
  } | null>(null)

  // Save to notebooks dialog
  const [showSaveDialog, setShowSaveDialog] = useState(false)

  // Citizen upload context state
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
  const SUPPORTED_UPLOAD_EXTENSIONS = ['.txt', '.docx', '.pdf', '.png', '.jpg', '.jpeg']
  const [attachedFile, setAttachedFile] = useState<File | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const [isRecordingVoice, setIsRecordingVoice] = useState(false)
  const [isTranscribingVoice, setIsTranscribingVoice] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const voiceChunksRef = useRef<Blob[]>([])
  const voiceStreamRef = useRef<MediaStream | null>(null)


  // Hooks
  const searchMutation = useSearch()
  const ask = useAsk()
  const { data: modelDefaults, isLoading: modelsLoading } = useModelDefaults()
  const { data: availableModels } = useModels()
  const { openModal } = useModalManager()

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

  const hasEmbeddingModel = !!modelDefaults?.default_embedding_model


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
      return `Định dạng ${ext || 'không rõ'} chưa được hỗ trợ. Chỉ nhận: txt, docx, pdf, png, jpg, jpeg. Không hỗ trợ video/audio.`
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      return `File quá lớn (${formatFileSize(file.size)}). Giới hạn tối đa là ${formatFileSize(MAX_UPLOAD_BYTES)}.`
    }
    if (file.type.startsWith('video/') || file.type.startsWith('audio/')) {
      return 'Không hỗ trợ audio/video trong bước này. Vui lòng gửi txt, docx, pdf hoặc ảnh png/jpg/jpeg.'
    }
    return null
  }

  const clearAttachedFile = () => {
    setAttachedFile(null)
    setExtractError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const stopVoiceTracks = () => {
    voiceStreamRef.current?.getTracks().forEach((track) => track.stop())
    voiceStreamRef.current = null
  }

  const appendVoiceTranscript = (transcript: string) => {
    setAskQuestion((prev) => {
      const cleanTranscript = transcript.trim()
      if (!prev.trim()) return cleanTranscript
      return `${prev.trim()}
${cleanTranscript}`
    })
  }

  const extractApiErrorMessage = (err: unknown, fallback: string) => {
    if (err && typeof err === 'object' && 'response' in err) {
      const response = (err as { response?: { data?: { detail?: unknown; message?: unknown } } }).response
      const detail = response?.data?.detail ?? response?.data?.message
      if (typeof detail === 'string' && detail.trim()) return detail
    }
    if (err instanceof Error && err.message) return err.message
    return fallback
  }

  const transcribeVoiceBlob = async (blob: Blob) => {
    setIsTranscribingVoice(true)
    setVoiceError(null)
    try {
      const result = await searchApi.transcribeVoice(blob, 'voice-input.webm')
      appendVoiceTranscript(result.transcript)
    } catch (err: unknown) {
      const msg = extractApiErrorMessage(
        err,
        'Không thể chuyển giọng nói thành văn bản. Hãy kiểm tra cấu hình speech-to-text model.'
      )
      setVoiceError(msg)
    } finally {
      setIsTranscribingVoice(false)
    }
  }

  const startVoiceRecording = async () => {
    if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('Trình duyệt chưa hỗ trợ ghi âm. Vui lòng nhập câu hỏi bằng văn bản.')
      return
    }

    setVoiceError(null)
    voiceChunksRef.current = []
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
      voiceStreamRef.current = stream
      const recorder = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined })
      mediaRecorderRef.current = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) voiceChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        stopVoiceTracks()
        setIsRecordingVoice(false)
        const audioBlob = new Blob(voiceChunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        voiceChunksRef.current = []
        if (audioBlob.size > 0) void transcribeVoiceBlob(audioBlob)
      }
      recorder.start()
      setIsRecordingVoice(true)
    } catch (err: unknown) {
      stopVoiceTracks()
      setIsRecordingVoice(false)
      const msg = err instanceof DOMException && err.name === 'NotAllowedError'
        ? 'Bạn chưa cấp quyền microphone. Hãy cho phép microphone hoặc nhập bằng văn bản.'
        : 'Không thể bắt đầu ghi âm. Vui lòng kiểm tra microphone.'
      setVoiceError(msg)
    }
  }

  const stopVoiceRecording = () => {
    const recorder = mediaRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
    } else {
      stopVoiceTracks()
      setIsRecordingVoice(false)
    }
  }

  const handleCitizenFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setAttachedFile(file)
    setExtractError(null)

    const validationError = validateCitizenUpload(file)
    if (validationError) {
      setExtractError(validationError)
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    setExtracting(true)
    try {
      const result = await searchApi.extractTextFromFile(file)
      if (result.extracted_text) {
        setAskQuestion((prev) =>
          prev
            ? `${prev}

---
Nội dung trích xuất từ file "${file.name}":
${result.extracted_text}`
            : `Nội dung trích xuất từ file "${file.name}":
${result.extracted_text}`
        )
      } else {
        setExtractError('Không trích xuất được văn bản từ file này. Hãy kiểm tra file có chữ rõ ràng hoặc thử định dạng khác.')
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Lỗi không xác định'
      setExtractError(`Không đọc được file: ${msg}`)
    } finally {
      setExtracting(false)
    }
  }

  useEffect(() => {
    if (!hasHydrated || !isAuthenticated) {
      return
    }

    let cancelled = false

    const loadSearchDependencies = async () => {
      try {
        const [domainList, modelInfo] = await Promise.all([
          legalImportApi.domains().catch(() => []),
          searchApi.localModels().catch(() => null),
        ])

        if (cancelled) {
          return
        }

        setDomains(domainList)
        setLocalModels(modelInfo)

        if (modelInfo?.recommended) {
          setOfflineModel(modelInfo.recommended)
        }

        // Load department if officer
        if (role === 'officer') {
          try {
            const meRes = await apiClient.get('/users/me')
            const dept = meRes.data.profile?.department || meRes.data.department || null
            const allowed = meRes.data.profile?.allowed_domains || meRes.data.allowed_domains || []
            if (!cancelled) {
              setUserDept(dept)
              // Preselect agency/domain for officer by department or first allowed domain
              const byDept = WARD_AGENCY_OPTIONS.find((item) =>
                dept ? item.agency.toLowerCase().includes(String(dept).toLowerCase()) || String(dept).toLowerCase().includes(item.agency.toLowerCase()) : false
              )
              const byDomain = WARD_AGENCY_OPTIONS.find((item) => allowed.includes(item.domain))
              const picked = byDept || byDomain
              if (picked) {
                setSelectedAgency(picked.id)
                setSelectedDomain(picked.domain)
              }
            }
          } catch (e) {
            console.error('Failed to load profile for officer indicator', e)
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
  }, [hasHydrated, isAuthenticated, role])

  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop()
      }
      stopVoiceTracks()
    }
  }, [])

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

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
  }

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
    const questionText = askQuestion.trim()
    if (!questionText) return
    if (ask.isStreaming || askSubmitLockRef.current) return
    // A click, Ctrl+Enter, and a URL auto-submit can arrive in the same
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
    const turnStartedAt = Date.now()
    const turnCreatedAt = new Date(turnStartedAt).toISOString()
    const userMsg: AskMessage = {
      id: `u_${turnStartedAt}`,
      role: "user",
      content: questionText,
      created_at: turnCreatedAt,
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
        const res = await apiClient.post('/conversations/', {
          title: askQuestion.substring(0, 60).replace(/\n/g, ' '),
          domain: selectedDomain === '__auto__' ? null : selectedDomain,
        })
        activeSessionId = res.data.id
        setCurrentSessionId(activeSessionId)
        setConversationRefreshKey((value) => value + 1)
      } catch (error) {
        setChatHistory((prev) => failAskTurn(
          prev,
          pendingAssistantId,
          'Không thể tạo cuộc trò chuyện. Vui lòng kiểm tra kết nối rồi thử lại.',
        ))
        toast.error('Không tạo được cuộc trò chuyện để lưu lịch sử.', {
          description: extractApiErrorMessage(error, 'Vui lòng kiểm tra kết nối backend rồi thử lại.'),
        })
        return
      }
    }

    if (activeSessionId) {
      try {
        await persistConversationMessage(activeSessionId, {
          role: 'user',
          content: questionText,
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
      offlineMode,
      offlineModel,
      domain: selectedDomain === "__auto__" ? null : selectedDomain,
      showRagTrace,
      conversationId: activeSessionId,
    })

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
      }
      setChatHistory((prev) => completeAskTurn(prev, pendingAssistantId, completeAssistantMsg))

      // The ask endpoint normally persists the assistant snapshot. Older or
      // partially failed requests can persist only the user message, though.
      // Never replace a completed local answer with that incomplete snapshot.
      if (activeSessionId) {
        setConversationRefreshKey((value) => value + 1)
        try {
          let synced = (await apiClient.get(`/conversations/${activeSessionId}`)).data as ConversationDetail
          const normalizeAnswer = (value?: string | null) =>
            (value || '').replace(/\s+/g, ' ').trim()
          const currentAnswer = normalizeAnswer(completeAssistantMsg.content)
          const hasMatchingAssistant = (conversation: ConversationDetail) =>
            conversation.messages.some((message) => {
              if (message.role !== "assistant" || message.status !== "complete") {
                return false
              }
              const persistedAnswer = normalizeAnswer(message.content)
              if (!persistedAnswer || !currentAnswer) return false
              const prefix = currentAnswer.substring(0, 120)
              return persistedAnswer === currentAnswer ||
                persistedAnswer.includes(prefix) ||
                currentAnswer.includes(persistedAnswer.substring(0, 120))
            })

          // An older assistant message is not enough. Only replace local UI
          // with the server snapshot after this exact answer is persisted.
          if (!hasMatchingAssistant(synced)) {
            await persistConversationMessage(activeSessionId, {
              role: "assistant",
              content: completeAssistantMsg.content,
              status: "complete",
              citations: completeAssistantMsg.citations,
              recommended_forms: completeAssistantMsg.recommended_forms,
              faq_refs: completeAssistantMsg.faq_refs,
              faqs: completeAssistantMsg.faqs,
              procedure_detail: completeAssistantMsg.procedure_detail,
              rag_trace: role === "admin" ? completeAssistantMsg.rag_trace : undefined,
              grounding_status: completeAssistantMsg.grounding_status,
            })
            synced = (await apiClient.get(`/conversations/${activeSessionId}`)).data as ConversationDetail
          }

          if (hasMatchingAssistant(synced)) {
            setChatHistory((prev) => prev.map((message) =>
              message.id === pendingAssistantId ? { ...message, persisted: true } : message
            ))
            loadConversation(synced)
          }
        } catch {
          // Keep the local completed answer if persistence or refresh fails.
        }
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
    }

      // Reset useAsk state so it does not render a second copy
      ask.reset()
    } finally {
      askSubmitLockRef.current = false
    }
  }, [askQuestion, modelDefaults, customModels, ask, role, offlineMode, offlineModel, selectedDomain, showRagTrace, currentSessionId, loadConversation, persistConversationMessage])

  const [escalating, setEscalating] = useState(false)

  const lastCompleteAssistant = useMemo(
    () => [...chatHistory].reverse().find((m) => m.role === 'assistant' && m.status === 'complete' && m.content),
    [chatHistory]
  )

  const handleEscalateSupport = useCallback(async () => {
    setEscalating(true)
    try {
      const contextMessages = chatHistory.map(m => `${m.role}: ${m.content}`).join('\n')
      const fullContext = contextMessages ? `${contextMessages}\n---\n${askQuestion}` : askQuestion
      if (selectedDomain === '__auto__') {
        toast.error('Vui lòng chọn lĩnh vực phụ trách trước khi chuyển câu hỏi cho cán bộ.')
        return
      }
      const res = await apiClient.post('/support/tickets', {
        question: fullContext,
        domain: selectedDomain,
        ai_summary: lastCompleteAssistant?.content ? lastCompleteAssistant.content.substring(0, 200) : null,
        priority: 'normal'
      })
      toast.success('Đã gửi yêu cầu hỗ trợ trực tuyến thành công!')
      router.push(`/live-support?ticket=${res.data.id}`)
    } catch (err) {
      toast.error('Không thể gửi yêu cầu hỗ trợ trực tuyến.')
    } finally {
      setEscalating(false)
    }
  }, [askQuestion, selectedDomain, lastCompleteAssistant, router, chatHistory])

  // Auto-trigger search/ask when arriving with URL params
  useEffect(() => {
    // Skip if already triggered or no query
    if (hasAutoTriggeredRef.current || !urlQuery) return

    // Wait for models to load before triggering ask
    if (urlMode === 'ask' && modelsLoading) return

    if (urlMode === 'search' && canUseSearchTab) {
      handleSearch()
      hasAutoTriggeredRef.current = true
    } else if (urlMode === 'ask' && modelDefaults?.default_chat_model) {
      hasAutoTriggeredRef.current = true
      handleAsk()
    }
  }, [urlQuery, urlMode, modelsLoading, modelDefaults, handleSearch, handleAsk, canUseSearchTab])

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
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <h1 className="text-xl md:text-2xl font-bold mb-4 md:mb-6">{t('navigation.askAndSearch', 'Hỏi đáp pháp luật')}</h1>

        <div className="w-full space-y-6">
            <div className="flex min-h-[calc(100vh-13rem)] overflow-hidden rounded-xl border bg-card">
              <ConversationSidebar
                role={role}
                currentId={currentSessionId}
                onSelect={loadConversation}
                onCreate={loadConversation}
                onDeleted={handleConversationDeleted}
                mobileOpen={mobileConversationDrawerOpen}
                onMobileOpenChange={setMobileConversationDrawerOpen}
                refreshKey={conversationRefreshKey}
              />
              <div className="flex min-w-0 flex-1 flex-col">
            <Card className="flex h-full flex-col border-0 shadow-none">
              <CardHeader>
                <CardTitle className="text-lg">{t('searchPage.askYourKb')}</CardTitle>
                <p className="text-sm text-muted-foreground">
                  {t('searchPage.askYourKbDesc')}
                </p>
                {role === 'officer' && userDept && (
                  <div className="mt-2 rounded bg-amber-500/5 p-3 text-xs border border-amber-500/20 text-amber-600 dark:text-amber-400 flex items-center gap-2">
                    <span className="flex h-2 w-2 rounded-full bg-amber-500 animate-pulse shrink-0" />
                    <span>
                      Đang kết nối vai trò <strong>Cán bộ ({userDept})</strong>. Kết quả tìm kiếm và câu trả lời sẽ tự động được giới hạn trong lĩnh vực chuyên môn của bạn để đảm bảo nghiệp vụ.
                    </span>
                  </div>
                )}
              </CardHeader>
              <CardContent className="flex h-full flex-col space-y-5 p-4 md:p-6">

                {/* Cơ quan / lĩnh vực phụ trách */}
                <div className="rounded-xl border bg-gradient-to-br from-primary/5 via-background to-background p-4 md:p-5 space-y-3">
                  {role !== 'citizen' && <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold">Cơ quan phụ trách & lĩnh vực</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Chọn đúng đơn vị/lĩnh vực để hệ thống hướng dẫn đúng nghiệp vụ phường/xã.
                      </p>
                    </div>
                    {selectedDomain !== '__auto__' && (
                      <Badge variant="secondary" className="shrink-0">Đã chọn</Badge>
                    )}
                  </div>}
                  <div className={`grid gap-3 ${role !== 'citizen' ? 'md:grid-cols-2' : ''}`}>
                    {role !== 'citizen' && <div className="space-y-2">
                      <Label>Cơ quan phụ trách</Label>
                      <Select
                        value={selectedAgency}
                        onValueChange={(value) => {
                          setSelectedAgency(value)
                          if (value === '__auto__') {
                            setSelectedDomain('__auto__')
                            return
                          }
                          const found = WARD_AGENCY_OPTIONS.find((item) => item.id === value)
                          if (found) setSelectedDomain(found.domain)
                        }}
                        disabled={ask.isStreaming}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Chọn cơ quan" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__auto__">Tự nhận diện</SelectItem>
                          {WARD_AGENCY_OPTIONS.map((item) => (
                            <SelectItem key={item.id} value={item.id}>
                              {item.agency}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>}
                    <div className="space-y-2">
                      <Label>Lĩnh vực</Label>
                      <Select
                        value={selectedDomain}
                        onValueChange={(value) => {
                          setSelectedDomain(value)
                          if (value === '__auto__') {
                            setSelectedAgency('__auto__')
                            return
                          }
                          const found = WARD_AGENCY_OPTIONS.find((item) => item.domain === value)
                          if (found) setSelectedAgency(found.id)
                        }}
                        disabled={ask.isStreaming}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Chọn lĩnh vực" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__auto__">Tự nhận diện</SelectItem>
                          {WARD_AGENCY_OPTIONS.map((item) => (
                            <SelectItem key={`domain-${item.id}`} value={item.domain}>
                              {item.domainName}
                            </SelectItem>
                          ))}
                          {domains
                            .filter((d) => !WARD_AGENCY_OPTIONS.some((w) => w.domain === d.slug))
                            .map((domain) => (
                              <SelectItem key={domain.slug} value={domain.slug}>
                                {domain.name}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  {selectedDomain !== '__auto__' && (
                    <p className="text-xs text-muted-foreground">
                      Đang hỏi theo lĩnh vực: <span className="font-medium text-foreground">{WARD_AGENCY_OPTIONS.find(i => i.domain === selectedDomain)?.domainName || selectedDomain}</span>
                      {role !== 'citizen' && WARD_AGENCY_OPTIONS.find(i => i.domain === selectedDomain)?.agency ? (
                        <> · Cơ quan: <span className="font-medium text-foreground">{WARD_AGENCY_OPTIONS.find(i => i.domain === selectedDomain)?.agency}</span></>
                      ) : null}
                    </p>
                  )}
                </div>

                <div className="min-h-[280px] flex-1 overflow-y-auto rounded-xl border bg-muted/10 p-3 md:p-4">
                  <AskMessageHistory
                    messages={chatHistory}
                    role={role}
                    showRagTrace={showRagTrace}
                    pendingStageLabel={ask.stageLabel}
                  />
                  <div ref={chatEndRef} />
                </div>

                <div className="sticky bottom-0 z-10 space-y-4 border-t bg-card/95 pt-4 backdrop-blur supports-[backdrop-filter]:bg-card/80">
                {/* Question Input */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ask-question">{t('searchPage.question')}</Label>
                    {/* Attach file button */}
                    <div className="flex items-center gap-2">
                      {attachedFile && (
                        <span className="flex items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                          {attachedFile.type.startsWith('image/') ? <ImageIcon className="h-3 w-3" /> : <FileText className="h-3 w-3" />}
                          <span className="max-w-[120px] truncate">{attachedFile.name}</span>
                          <span>({formatFileSize(attachedFile.size)})</span>
                          {extracting && <LoadingSpinner size="sm" />}
                          <button
                            type="button"
                            aria-label="Xóa file đính kèm"
                            className="ml-1 hover:text-destructive"
                            onClick={clearAttachedFile}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        disabled={ask.isStreaming || extracting}
                        onClick={() => fileInputRef.current?.click()}
                        title="Đính kèm txt/docx/pdf/png/jpg/jpeg để hệ thống trích xuất nội dung và đưa vào câu hỏi. Không hỗ trợ video."
                      >
                        <Paperclip className="h-3.5 w-3.5 mr-1" />
                        Đính kèm
                      </Button>
                      <Button
                        type="button"
                        variant={isRecordingVoice ? 'destructive' : 'ghost'}
                        size="sm"
                        className="h-7 px-2 text-xs"
                        disabled={ask.isStreaming || extracting || isTranscribingVoice}
                        onClick={isRecordingVoice ? stopVoiceRecording : startVoiceRecording}
                        title="Ghi âm câu hỏi: hệ thống chỉ chuyển giọng nói thành văn bản rồi dùng ask pipeline hiện có. Không hỗ trợ video."
                        aria-label={isRecordingVoice ? 'Dừng ghi âm' : 'Nhập bằng giọng nói'}
                      >
                        {isRecordingVoice ? <Square className="h-3.5 w-3.5 mr-1" /> : <Mic className="h-3.5 w-3.5 mr-1" />}
                        {isRecordingVoice ? 'Dừng' : isTranscribingVoice ? 'Đang chuyển...' : 'Giọng nói'}
                      </Button>
                      <input
                        ref={fileInputRef}
                        type="file"
                        className="hidden"
                        accept=".txt,.docx,.pdf,.png,.jpg,.jpeg,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg"
                        onChange={handleCitizenFileChange}
                      />
                    </div>
                  </div>
                  <Textarea
                    id="ask-question"
                    name="ask-question"
                    placeholder={t('searchPage.enterQuestionPlaceholder')}
                    value={askQuestion}
                    onChange={(e) => setAskQuestion(e.target.value)}
                    onKeyDown={(e) => {
                      // Submit on Cmd/Ctrl+Enter
                      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !ask.isStreaming && askQuestion.trim()) {
                        e.preventDefault()
                        handleAsk()
                      }
                    }}
                    disabled={ask.isStreaming}
                    rows={7}
                    className="min-h-[160px] md:min-h-[200px] resize-y"
                    aria-label={t('common.accessibility.enterQuestion')}
                  />
                  {extractError && (
                    <p className="text-xs text-destructive flex items-center gap-1">
                      <AlertCircle className="h-3 w-3" /> {extractError}
                    </p>
                  )}
                  {voiceError && (
                    <p className="text-xs text-destructive flex items-center gap-1" role="alert">
                      <AlertCircle className="h-3 w-3" /> {voiceError}
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
                            Xem câu hỏi, lĩnh vực, chunk, nguồn và mục bị lọc.
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

                <div className="flex flex-col sm:flex-row gap-2">
                  <Button
                    type="button"
                    onClick={handleAsk}
                    disabled={ask.isStreaming || !askQuestion.trim()}
                    className="w-full"
                  >
                    {ask.isStreaming ? (
                      <>
                        <LoadingSpinner size="sm" className="mr-2" />
                        {t('searchPage.processing')}
                      </>
                    ) : (
                      t('searchPage.ask')
                    )}
                  </Button>
                  {ask.isStreaming && (
                    <Button type="button" variant="outline" onClick={ask.cancel} className="w-full sm:w-auto">
                      Dừng
                    </Button>
                  )}

                  {chatHistory.some((m) => m.role === 'assistant' && m.status === 'complete' && m.content) && (
                    <Button
                      variant="outline"
                      onClick={() => setShowSaveDialog(true)}
                      className="w-full"
                    >
                      <Save className="h-4 w-4 mr-2" />
                      {t('searchPage.saveToNotebooks')}
                    </Button>
                  )}
                </div>

                </div>

                {ask.isStreaming && (
                  <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                    Hệ thống đang chạy pipeline truy xuất và tổng hợp câu trả lời. Nếu câu hỏi dài hoặc nguồn luật nhiều,
                    thời gian xử lý có thể lên tới vài phút.
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
                            ? 'Luồng trả lời chuyên sâu đã được tạm dừng. Hãy chọn đúng cơ quan/lĩnh vực phụ trách rồi hỏi lại.'
                            : 'Hệ thống nhận thấy câu hỏi có thể thuộc lĩnh vực khác. Bạn nên chuyển để được hướng dẫn đúng hơn.'}
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
                      {ask.suggestedDomain && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="bg-background"
                          onClick={() => {
                            const found = WARD_AGENCY_OPTIONS.find((item) => item.domain === ask.suggestedDomain)
                            setSelectedDomain(ask.suggestedDomain || '__auto__')
                            setSelectedAgency(found?.id || '__auto__')
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

                {lastCompleteAssistant?.grounding_status === 'insufficient_evidence' && (
                  <div className="rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/30 p-4 text-sm text-amber-950 dark:text-amber-100 space-y-3">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                      <div className="space-y-1">
                        <p className="font-semibold">Thiếu cơ sở dữ liệu pháp luật</p>
                        <p className="text-xs md:text-sm opacity-90">
                          Hệ thống chưa tìm thấy văn bản quy định của địa phương Hải Phòng phù hợp với câu hỏi của bạn. 
                          Bạn có muốn gửi yêu cầu hỗ trợ trực tiếp đến cán bộ chuyên trách phường/xã không?
                        </p>
                      </div>
                    </div>
                    {role === 'citizen' && (
                      <Button
                        type="button"
                        size="sm"
                        onClick={handleEscalateSupport}
                        disabled={escalating}
                      >
                        {escalating ? 'Đang gửi...' : 'Gửi yêu cầu hỗ trợ trực tuyến'}
                      </Button>
                    )}
                  </div>
                )}

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

                {/* Save to Notebooks Dialog */}
                {showSaveDialog && lastCompleteAssistant && (
                  <SaveToNotebooksDialog
                    open={showSaveDialog}
                    onOpenChange={setShowSaveDialog}
                    question={
                      [...chatHistory]
                        .reverse()
                        .find((m) => m.role === 'user')
                        ?.content || askQuestion
                    }
                    answer={lastCompleteAssistant.content}
                  />
                )}
              </CardContent>
            </Card>
              </div>
            </div>
        </div>
      </div>
    </AppShell>
  )
}
