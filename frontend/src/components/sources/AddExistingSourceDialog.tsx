'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useDebounce } from 'use-debounce'
import { Search, Link2, LoaderIcon, FileText, Link as LinkIcon, Upload } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { sourcesApi } from '@/lib/api/sources'
import { useSources, useAddSourcesToNotebook } from '@/lib/hooks/use-sources'
import { SourceListResponse } from '@/lib/types/api'
import { useTranslation } from '@/lib/hooks/use-translation'
import { legalDocumentsApi, type LegalDocumentListItem } from '@/lib/api/legal-documents'

interface AddExistingSourceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notebookId: string
  onSuccess?: () => void
}

export function AddExistingSourceDialog({
  open,
  onOpenChange,
  notebookId,
  onSuccess,
}: AddExistingSourceDialogProps) {
  const { t } = useTranslation()
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery] = useDebounce(searchQuery, 300)
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const [selectedLegalDocs, setSelectedLegalDocs] = useState<Record<string, LegalDocumentListItem>>({})
  const [allSources, setAllSources] = useState<SourceListResponse[]>([])
  const [filteredSources, setFilteredSources] = useState<SourceListResponse[]>([])
  const [isSearching, setIsSearching] = useState(false)

  // Get sources already in this notebook
  const { data: currentNotebookSources } = useSources(notebookId)
  const currentSourceIds = useMemo(
    () => new Set(currentNotebookSources?.map(s => s.id) || []),
    [currentNotebookSources]
  )

  const addSources = useAddSourcesToNotebook()

  const loadAllSources = useCallback(async () => {
    try {
      setIsSearching(true)
      const sources = await sourcesApi.list({
        limit: 100,
        offset: 0,
        sort_by: 'created',
        sort_order: 'desc',
      })
      setAllSources(sources)
      setFilteredSources(sources)
    } catch (error) {
      console.error('Error loading sources:', error)
    } finally {
      setIsSearching(false)
    }
  }, [])

  const performSearch = useCallback(async () => {
    const norm = debouncedSearchQuery.trim().toLowerCase()
    setIsSearching(true)
    try {
      const localMatches = allSources.filter((source) => {
        const title = (source.title || '').toLowerCase()
        const scope = (source.source_scope || '').toLowerCase()
        const topics = (source.topics || []).join(' ').toLowerCase()
        return !norm || title.includes(norm) || scope.includes(norm) || topics.includes(norm)
      })

      let legalDocsAsSources: SourceListResponse[] = []
      if (norm.length >= 1) {
        const res = await legalDocumentsApi.list({ q: norm, limit: 20 }).catch(() => ({ items: [] }))
        const docsMap: Record<string, LegalDocumentListItem> = {}
        legalDocsAsSources = (res.items || []).map((doc) => {
          const documentKey = `document:${doc.doc_id}`
          docsMap[documentKey] = doc
          return {
            id: documentKey,
            legal_document_id: String(doc.doc_id),
            title: `[Văn bản] ${doc.law_number ? `${doc.law_number} - ` : ''}${doc.document_title || ''}`,
            topics: [doc.domain || doc.domain_name || 'phap_luat'],
            asset: doc.source_url ? { url: doc.source_url } : null,
            embedded: true,
            embedded_chunks: doc.article_count || 1,
            insights_count: 0,
            created: doc.effective_date || new Date().toISOString(),
            updated: doc.effective_date || new Date().toISOString(),
          }
        }) as unknown as SourceListResponse[]

        setSelectedLegalDocs((prev) => ({ ...prev, ...docsMap }))
      }

      setFilteredSources([...localMatches, ...legalDocsAsSources])
    } catch {
      setFilteredSources(allSources)
    } finally {
      setIsSearching(false)
    }
  }, [debouncedSearchQuery, allSources])

  // Load all sources initially
  useEffect(() => {
    if (open) {
      void loadAllSources()
    }
  }, [open, loadAllSources])

  // Filter sources when search query changes
  useEffect(() => {
    void performSearch()
  }, [debouncedSearchQuery, performSearch])

  const handleToggleSource = (sourceId: string) => {
    setSelectedSourceIds(prev =>
      prev.includes(sourceId)
        ? prev.filter(id => id !== sourceId)
        : [...prev, sourceId]
    )
  }

  const handleAddSelected = async () => {
    if (selectedSourceIds.length === 0) return

    try {
      const normalIds = selectedSourceIds.filter((id) => !id.startsWith('document:'))
      const legalDocIds = selectedSourceIds.filter((id) => id.startsWith('document:'))

      if (normalIds.length > 0) {
        await addSources.mutateAsync({
          notebookId,
          sourceIds: normalIds,
        })
      }

      for (const legalId of legalDocIds) {
        const docData = selectedLegalDocs[legalId]
        if (docData) {
          await sourcesApi.addLegalDocumentToNotebook(notebookId, String(docData.doc_id))
        }
      }

      // Reset state
      setSelectedSourceIds([])
      setSearchQuery('')
      onOpenChange(false)
      onSuccess?.()
    } catch (error) {
      console.error('Error adding sources:', error)
    }
  }

  const getSourceIcon = (source: SourceListResponse) => {
    // Derive type from asset
    if (source.asset?.url) {
      return <LinkIcon className="h-4 w-4" />
    }
    if (source.asset?.file_path) {
      return <Upload className="h-4 w-4" />
    }
    return <FileText className="h-4 w-4" />
  }

  const formatDate = (dateString: string) => {
    try {
      return new Date(dateString).toLocaleDateString()
    } catch {
      return ''
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl sm:max-w-4xl w-full max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-lg">
            <Link2 className="h-5 w-5" />
            {t('sources.addExistingTitle')}
          </DialogTitle>
          <DialogDescription>
            {t('sources.addExistingDesc')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 flex-1 overflow-hidden flex flex-col">
          {/* Search Input */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder={t('sources.searchPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
            {isSearching && (
              <LoaderIcon className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-muted-foreground" />
            )}
          </div>

          {/* Source List */}
          <ScrollArea className="h-[460px] border rounded-md">
            {isSearching && filteredSources.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-[200px] text-muted-foreground">
                <LoaderIcon className="h-12 w-12 mb-2 animate-spin" />
                <p>{t('common.loading')}</p>
              </div>
            ) : filteredSources.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-[200px] text-muted-foreground">
                <FileText className="h-12 w-12 mb-2 opacity-50" />
                <p>{t('sources.noNotebooksFound')}</p>
              </div>
            ) : (
              <div className="space-y-2 p-4">
                {filteredSources.map((source) => {
                  const isAlreadyLinked = source.legal_document_id
                    ? currentNotebookSources?.some((item) => item.legal_document_id === source.legal_document_id) || false
                    : currentSourceIds.has(source.id)
                  const isSelected = selectedSourceIds.includes(source.id)

                  return (
                    <div
                      key={source.id}
                      onClick={() => !isAlreadyLinked && handleToggleSource(source.id)}
                      className={`flex items-start gap-3 p-3 rounded-lg border transition-colors min-w-0 cursor-pointer ${
                        isAlreadyLinked
                          ? 'opacity-60 bg-muted/40 cursor-not-allowed'
                          : isSelected
                          ? 'bg-accent/80 border-primary/40 ring-1 ring-primary/20'
                          : 'hover:bg-accent/50'
                      }`}
                    >
                      <Checkbox
                        checked={isSelected}
                        disabled={isAlreadyLinked}
                        className="mt-1 pointer-events-none"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start gap-2 mb-1">
                          <div className="shrink-0 mt-0.5">
                            {getSourceIcon(source)}
                          </div>
                          <h4 className="font-medium text-sm text-foreground flex-1 min-w-0 leading-snug">
                            {source.title}
                          </h4>
                          {isAlreadyLinked && (
                            <Badge variant="secondary" className="text-xs shrink-0">
                              {t('common.linked')}
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {t('sources.added').replace('{date}', formatDate(source.created))}
                        </p>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </ScrollArea>

          {/* Truncation Warning */}
          {allSources.length >= 100 && !debouncedSearchQuery && (
            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded-md">
              {t('sources.showingFirst100')}
            </div>
          )}

          {/* Selection Summary */}
          {selectedSourceIds.length > 0 && (
            <div className="text-sm text-muted-foreground">
              {t('sources.selectedCount').replace('{count}', selectedSourceIds.length.toString())}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={addSources.isPending}
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleAddSelected}
            disabled={selectedSourceIds.length === 0 || addSources.isPending}
          >
            {addSources.isPending ? (
              <>
                <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
                {t('common.adding')}
              </>
            ) : (
              <>{t('common.addSelected')}</>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
