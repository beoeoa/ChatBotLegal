'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookmarkPlus, Brain, ExternalLink, FileText, Pin, PinOff, RefreshCw, X } from 'lucide-react'
import { toast } from 'sonner'
import { apiClient } from '@/lib/api/client'
import { Button } from '@/components/ui/button'

type ConversationState = {
  version: string
  conversation_id: string
  canonical_domain?: string | null
  procedure?: { id?: string | null; name?: string | null } | null
  actors: string[]
  legal_objects: string[]
  locations: string[]
  answered_facets: string[]
  unresolved_facets: string[]
  active_document?: {
    document_id: string
    title: string
    law_number?: string | null
    source_url?: string | null
    article_refs?: string[]
    pinned?: boolean
  } | null
  recent_source_refs?: Array<{
    document_id: string
    title: string
    law_number?: string | null
    source_url?: string | null
    article_refs?: string[]
    pinned?: boolean
  }>
  revision: number
}

type MemoryField = {
  key: string
  label: string
  value: string
}

type MemorySettings = {
  enabled: boolean
  runtime_available: boolean
}

export function ChatMemoryPanel({
  conversationId,
  refreshKey = 0,
}: {
  conversationId: string | null
  refreshKey?: number
}) {
  const [state, setState] = useState<ConversationState | null>(null)
  const [loading, setLoading] = useState(false)
  const [longTermAvailable, setLongTermAvailable] = useState(false)
  const [tracking, setTracking] = useState(false)
  const [showSources, setShowSources] = useState(false)

  const load = useCallback(async () => {
    if (!conversationId) {
      setState(null)
      setLongTermAvailable(false)
      return
    }
    try {
      const [stateResponse, settingsResponse] = await Promise.all([
        apiClient.get<ConversationState>(`/conversations/${conversationId}/state`),
        apiClient.get<MemorySettings>('/chat-memory/settings').catch(() => null),
      ])
      setState(stateResponse.data)
      setLongTermAvailable(Boolean(
        settingsResponse?.data.runtime_available && settingsResponse.data.enabled,
      ))
    } catch {
      setState(null)
      setLongTermAvailable(false)
    }
  }, [conversationId])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  const fields = useMemo<MemoryField[]>(() => {
    if (!state) return []
    const values: MemoryField[] = []
    if (state.canonical_domain) values.push({ key: 'canonical_domain', label: 'Lĩnh vực', value: state.canonical_domain })
    if (state.procedure?.name || state.procedure?.id) values.push({ key: 'procedure', label: 'Thủ tục', value: state.procedure.name || state.procedure.id || '' })
    if (state.locations.length) values.push({ key: 'locations', label: 'Địa bàn', value: state.locations.join(', ') })
    if (state.actors.length) values.push({ key: 'actors', label: 'Chủ thể', value: state.actors.join(', ') })
    if (state.legal_objects.length) values.push({ key: 'legal_objects', label: 'Nội dung', value: state.legal_objects.join(', ') })
    return values
  }, [state])

  const forget = async (field: string) => {
    if (!conversationId || loading) return
    setLoading(true)
    try {
      const response = await apiClient.patch<ConversationState>(
        `/conversations/${conversationId}/state`,
        { forget_fields: [field] },
      )
      setState(response.data)
    } finally {
      setLoading(false)
    }
  }

  const updateSource = async (payload: Record<string, unknown>) => {
    if (!conversationId || loading) return
    setLoading(true)
    try {
      const response = await apiClient.patch<ConversationState>(
        `/conversations/${conversationId}/state`,
        payload,
      )
      setState(response.data)
      setShowSources(false)
    } catch {
      toast.error('Chưa thể cập nhật văn bản đang trao đổi.')
    } finally {
      setLoading(false)
    }
  }

  const trackProcedure = async () => {
    if (!conversationId || !state?.procedure || tracking) return
    setTracking(true)
    try {
      await apiClient.post('/chat-memory/items/track-procedure', { conversation_id: conversationId })
      toast.success('Đã lưu thủ tục để tiếp tục ở cuộc trò chuyện mới.')
    } catch {
      toast.error('Chưa thể lưu thủ tục đang theo dõi.')
    } finally {
      setTracking(false)
    }
  }

  if (!conversationId || !state || (fields.length === 0 && !state.active_document)) return null

  return (
    <div className="space-y-2" data-testid="conversation-memory-panel">
      {state.active_document && (
        <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2" data-testid="active-document-banner">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <FileText className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
              <div className="min-w-0 text-xs">
                <span className="text-muted-foreground">Đang trao đổi về </span>
                <span className="font-semibold text-foreground">{state.active_document.title}</span>
                {state.active_document.article_refs && state.active_document.article_refs.length > 0 && (
                  <span className="ml-1 text-muted-foreground">· {state.active_document.article_refs.join(', ')}</span>
                )}
                {state.active_document.pinned && (
                  <span className="ml-2 rounded-full bg-primary/10 px-1.5 py-0.5 font-medium text-primary">Đã ghim</span>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1">
              {state.active_document.source_url && (
                <Button asChild type="button" variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs">
                  <a href={state.active_document.source_url} target="_blank" rel="noreferrer">
                    <ExternalLink className="h-3.5 w-3.5" /> Xem
                  </a>
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 gap-1 px-2 text-xs"
                disabled={loading}
                onClick={() => void updateSource(state.active_document?.pinned ? { unpin_active_document: true } : { pin_active_document: true })}
              >
                {state.active_document.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                {state.active_document.pinned ? 'Bỏ ghim' : 'Ghim'}
              </Button>
              {(state.recent_source_refs?.length || 0) > 1 && (
                <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setShowSources((value) => !value)}>
                  Đổi nguồn
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label="Bỏ văn bản đang trao đổi"
                disabled={loading}
                onClick={() => void updateSource({ clear_active_document: true })}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
          {showSources && (
            <div className="mt-2 grid gap-1 border-t pt-2" aria-label="Các văn bản gần đây">
              {(state.recent_source_refs || []).map((source) => (
                <button
                  key={source.document_id}
                  type="button"
                  className="rounded-md px-2 py-1.5 text-left text-xs hover:bg-background"
                  disabled={loading || source.document_id === state.active_document?.document_id}
                  onClick={() => void updateSource({ set_active_document_id: source.document_id })}
                >
                  <span className="font-medium">{source.title}</span>
                  {source.law_number && <span className="ml-1 text-muted-foreground">· {source.law_number}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {fields.length > 0 && (
        <div className="rounded-lg border bg-muted/20 px-3 py-2">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-foreground">
            <Brain className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            Chatbot đang ghi nhớ trong cuộc trò chuyện này
          </div>
          <div className="flex flex-wrap gap-2">
            {fields.map((field) => (
              <span key={field.key} className="inline-flex max-w-full items-center gap-1 rounded-full border bg-background px-2.5 py-1 text-xs">
                <span className="font-medium">{field.label}:</span>
                <span className="max-w-48 truncate text-muted-foreground">{field.value}</span>
                <button
                  type="button"
                  className="ml-0.5 rounded-full p-0.5 hover:bg-muted"
                  aria-label={`Quên ${field.label}`}
                  disabled={loading}
                  onClick={() => void forget(field.key)}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
      {state.unresolved_facets.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          Còn có thể hỏi tiếp: {state.unresolved_facets.join(', ')}.
        </p>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <Button type="button" variant="link" size="sm" className="h-auto gap-1 px-0 py-0 text-xs" onClick={() => void load()}>
          <RefreshCw className="h-3 w-3" /> Làm mới ngữ cảnh
        </Button>
        {longTermAvailable && state.procedure && (
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto gap-1 px-0 py-0 text-xs"
            disabled={tracking}
            onClick={() => void trackProcedure()}
          >
            <BookmarkPlus className="h-3.5 w-3.5" />
            Theo dõi thủ tục này
          </Button>
        )}
      </div>
        </div>
      )}
    </div>
  )
}
