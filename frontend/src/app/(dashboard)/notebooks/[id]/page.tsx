'use client'

import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { AppShell } from '@/components/layout/AppShell'
import { NotebookHeader } from '../components/NotebookHeader'
import { SourcesColumn } from '../components/SourcesColumn'
import { NotesColumn } from '../components/NotesColumn'
import { ChatColumn } from '../components/ChatColumn'
import { useNotebook } from '@/lib/hooks/use-notebooks'
import { useNotebookSources } from '@/lib/hooks/use-sources'
import { useNotes } from '@/lib/hooks/use-notes'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useNotebookColumnsStore } from '@/lib/stores/notebook-columns-store'
import { useIsDesktop } from '@/lib/hooks/use-media-query'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { FileText, StickyNote, MessageSquare } from 'lucide-react'
import { useAuthStore } from '@/lib/stores/auth-store'
import { ForbiddenState } from '@/components/common/ForbiddenState'
import { BusinessReasonDialog, getBusinessReason } from '@/components/common/BusinessReasonDialog'
import { useRouter } from 'next/navigation'

export type ContextMode = 'off' | 'insights' | 'full'

export interface ContextSelections {
  sources: Record<string, ContextMode>
  notes: Record<string, ContextMode>
}

export default function NotebookPage() {
  const { t } = useTranslation()
  const params = useParams()
  const role = useAuthStore((state) => state.role)
  const router = useRouter()
  const [reasonReady, setReasonReady] = useState(false)
  const [reasonDialogOpen, setReasonDialogOpen] = useState(false)

  // Ensure the notebook ID is properly decoded from URL
  const notebookId = params?.id ? decodeURIComponent(params.id as string) : ''
  const reasonKey = `notebook:${notebookId}`

  useEffect(() => {
    if (role === 'admin' && notebookId) {
      const existing = getBusinessReason(reasonKey)
      setReasonReady(Boolean(existing))
      setReasonDialogOpen(!existing)
    } else {
      setReasonReady(true)
      setReasonDialogOpen(false)
    }
  }, [notebookId, role, reasonKey])

  const {
    data: notebook,
    isLoading: notebookLoading,
    isError: notebookFailed,
    error: notebookError,
  } = useNotebook(notebookId)
  const {
    sources,
    isLoading: sourcesLoading,
    refetch: refetchSources,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useNotebookSources(notebookId)
  const { data: notes, isLoading: notesLoading } = useNotes(notebookId)

  // Get collapse states for dynamic layout
  const { sourcesCollapsed, notesCollapsed } = useNotebookColumnsStore()

  // Detect desktop to avoid double-mounting ChatColumn
  const isDesktop = useIsDesktop()

  // Mobile tab state (Sources, Notes, or Chat)
  const [mobileActiveTab, setMobileActiveTab] = useState<'sources' | 'notes' | 'chat'>('chat')

  // Context selection state
  const [contextSelections, setContextSelections] = useState<ContextSelections>({
    sources: {},
    notes: {}
  })

  // Initialize and update selections when sources load or change
  useEffect(() => {
    if (sources && sources.length > 0) {
      setContextSelections(prev => {
        const newSourceSelections = { ...prev.sources }
        sources.forEach(source => {
          const currentMode = newSourceSelections[source.id]
          if (currentMode === undefined) {
            // Default to bounded source excerpts. Full text is an explicit
            // user choice because large notebooks can contain millions of tokens.
            newSourceSelections[source.id] = 'insights'
          }
        })
        return { ...prev, sources: newSourceSelections }
      })
    }
  }, [sources])

  useEffect(() => {
    if (notes && notes.length > 0) {
      setContextSelections(prev => {
        const newNoteSelections = { ...prev.notes }
        notes.forEach(note => {
          // Only set default if not already set
          if (!(note.id in newNoteSelections)) {
            // Notes default to 'full'
            newNoteSelections[note.id] = 'full'
          }
        })
        return { ...prev, notes: newNoteSelections }
      })
    }
  }, [notes])

  // Handler to update context selection
  const handleContextModeChange = (itemId: string, mode: ContextMode, type: 'source' | 'note') => {
    setContextSelections(prev => ({
      ...prev,
      [type === 'source' ? 'sources' : 'notes']: {
        ...(type === 'source' ? prev.sources : prev.notes),
        [itemId]: mode
      }
    }))
  }

  if (role === 'citizen') {
    return (
      <AppShell>
        <ForbiddenState
          title="Hồ sơ pháp lý không khả dụng"
          description="Hồ sơ pháp lý chỉ dành cho cán bộ và quản trị viên."
          onBack={() => router.replace('/search')}
        />
      </AppShell>
    )
  }

  if (role === 'admin' && !reasonReady) {
    return (
      <AppShell>
        <div className="p-6">
          <BusinessReasonDialog
            open={reasonDialogOpen}
            resourceKey={reasonKey}
            onOpenChange={setReasonDialogOpen}
            onConfirmed={() => {
              setReasonReady(true)
              setReasonDialogOpen(false)
            }}
          />
        </div>
      </AppShell>
    )
  }

  if (notebookLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  if (notebookFailed) {
    const status = (notebookError as { response?: { status?: number } })?.response?.status
    const forbidden = status === 401 || status === 403
    return (
      <AppShell>
        <ForbiddenState
          title={forbidden ? 'Không thể mở hồ sơ này' : t('notebooks.notFound')}
          description={forbidden
            ? 'Hồ sơ pháp lý thuộc một tài khoản khác. Hãy chọn hồ sơ trong danh sách của tài khoản đang đăng nhập.'
            : t('notebooks.notFoundDesc')}
          onBack={() => router.replace('/notebooks')}
          backLabel="Về danh sách hồ sơ"
        />
      </AppShell>
    )
  }

  if (!notebook) {
    return (
      <AppShell>
        <ForbiddenState
          title={t('notebooks.notFound')}
          description={t('notebooks.notFoundDesc')}
          onBack={() => router.replace('/notebooks')}
          backLabel="Về danh sách hồ sơ"
        />
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-shrink-0 border-b border-border/60 bg-card/70 px-3 py-3 backdrop-blur md:px-5">
          <NotebookHeader notebook={notebook} />
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-2">
          {/* Mobile: Tabbed interface - only render on mobile to avoid double-mounting */}
          {!isDesktop && (
            <>
              <div className="mb-2 xl:hidden">
                <Tabs value={mobileActiveTab} onValueChange={(value) => setMobileActiveTab(value as 'sources' | 'notes' | 'chat')}>
                  <TabsList className="grid h-12 w-full grid-cols-3 rounded-xl">
                    <TabsTrigger value="sources" className="gap-2">
                      <FileText className="h-4 w-4" />
                      {t('navigation.sources')}
                    </TabsTrigger>
                    <TabsTrigger value="chat" className="gap-2">
                      <MessageSquare className="h-4 w-4" />
                      {t('common.chat')}
                    </TabsTrigger>
                    <TabsTrigger value="notes" className="gap-2">
                      <StickyNote className="h-4 w-4" />
                      {t('common.notes')}
                    </TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>

              {/* Mobile: Show only active tab */}
              <div className="min-h-0 flex-1 overflow-hidden xl:hidden">
                {mobileActiveTab === 'sources' && (
                  <SourcesColumn
                    sources={sources}
                    isLoading={sourcesLoading}
                    notebookId={notebookId}
                    notebookName={notebook?.name}
                    onRefresh={refetchSources}
                    contextSelections={contextSelections.sources}
                    onContextModeChange={(sourceId, mode) => handleContextModeChange(sourceId, mode, 'source')}
                    hasNextPage={hasNextPage}
                    isFetchingNextPage={isFetchingNextPage}
                    fetchNextPage={fetchNextPage}
                  />
                )}
                {mobileActiveTab === 'notes' && (
                  <NotesColumn
                    notes={notes}
                    isLoading={notesLoading}
                    notebookId={notebookId}
                    contextSelections={contextSelections.notes}
                    onContextModeChange={(noteId, mode) => handleContextModeChange(noteId, mode, 'note')}
                  />
                )}
                {mobileActiveTab === 'chat' && (
                  <ChatColumn
                    notebookId={notebookId}
                    contextSelections={contextSelections}
                    sources={sources}
                    sourcesLoading={sourcesLoading}
                    notes={notes || []}
                    notesLoading={notesLoading}
                  />
                )}
              </div>
            </>
          )}

          {/* Desktop: Collapsible columns layout */}
          <div className={cn(
            'hidden h-full min-h-0 gap-2 transition-all duration-150 xl:flex',
            'flex-row'
          )}>
            {/* Sources Column */}
            <div className={cn(
              'transition-all duration-150',
              sourcesCollapsed ? 'w-12 flex-shrink-0' : 'w-[20rem] 2xl:w-[23rem] flex-shrink-0'
            )}>
              <SourcesColumn
                sources={sources}
                isLoading={sourcesLoading}
                notebookId={notebookId}
                notebookName={notebook?.name}
                onRefresh={refetchSources}
                contextSelections={contextSelections.sources}
                onContextModeChange={(sourceId, mode) => handleContextModeChange(sourceId, mode, 'source')}
                hasNextPage={hasNextPage}
                isFetchingNextPage={isFetchingNextPage}
                fetchNextPage={fetchNextPage}
              />
            </div>

            {/* Chat is the primary workspace and remains the widest pane. */}
            <div className="min-w-0 flex-1 transition-all duration-150">
              <ChatColumn
                notebookId={notebookId}
                contextSelections={contextSelections}
                sources={sources}
                sourcesLoading={sourcesLoading}
                notes={notes || []}
                notesLoading={notesLoading}
              />
            </div>

            {/* Notes / studio pane */}
            <div className={cn(
              'transition-all duration-150',
              notesCollapsed ? 'w-12 flex-shrink-0' : 'w-[19rem] 2xl:w-[22rem] flex-shrink-0'
            )}>
              <NotesColumn
                notes={notes}
                isLoading={notesLoading}
                notebookId={notebookId}
                contextSelections={contextSelections.notes}
                onContextModeChange={(noteId, mode) => handleContextModeChange(noteId, mode, 'note')}
              />
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
