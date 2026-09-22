"use client"

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Download,
  RefreshCw,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { formatApiError } from '@/lib/utils/error-handler'
import { getApiUrl } from '@/lib/config'
import { DocumentLifecyclePanel } from '@/components/legal-management/DocumentLifecyclePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/lib/stores/auth-store'
import {
  buildArticleAnchor,
  buildSectionAnchor,
  buildVBPLLegalTocTree,
  getChunkHighlightState,
  isTargetArticle,
  viewerParagraphs,
  TocNode,
} from '@/components/legal/legal-document-viewer-utils'

interface LegalChunk {
  chunk_id?: string | number
  chunk_index?: number
  content?: string
}

interface LegalArticle {
  article_id?: string | number
  article_number?: string
  article_title?: string
  chunks?: LegalChunk[]
}

interface LegalArticleIndex {
  article_number?: string
  article_title?: string
  has_content?: boolean
}

interface LegalDocumentDetail {
  doc_id?: string
  document_title?: string
  law_number?: string
  document_type?: string
  issuing_agency?: string
  scope?: string
  effective_status?: string
  effective_date?: string | null
  issued_date?: string | null
  expired_date?: string | null
  field_name?: string
  article_index?: LegalArticleIndex[]
  articles?: LegalArticle[]
  content?: string
  source_file_available?: boolean
  source_url?: string
  validity_status?: string
  serving_status?: string
  current_answer_eligible?: boolean
  historical_lookup_allowed?: boolean
  validity_sync?: {
    status: string
    serving_action: string
    display_label: string
    current_answer_eligible: boolean
    historical_lookup_allowed: boolean
    effective_from?: string | null
    effective_to?: string | null
    source_url?: string | null
  }
}

function getAuthToken(): string {
  if (typeof window === 'undefined') return ''
  return useAuthStore.getState().token || ''
}

export function LegalDocumentViewer({ docId, articleParam = '', clauseParam = '', pointParam = '', lawNumber = '', embedded = false }: {
  docId: string; articleParam?: string; clauseParam?: string; pointParam?: string; lawNumber?: string; embedded?: boolean
}) {
  const role = useAuthStore((state) => state.role)
  const [document, setDocument] = useState<LegalDocumentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [locationNotice, setLocationNotice] = useState<string | null>(null)

  // System design controls
  const [rightTocOpen, setRightTocOpen] = useState(!embedded)
  const [fontSizePercent, setFontSizePercent] = useState(100)
  const [activeNodeId, setActiveNodeId] = useState<string>('')
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({})

  const [viewLatencyMs, setViewLatencyMs] = useState<number | null>(null)
  const [downloadingPdf, setDownloadingPdf] = useState(false)
  const [showFullText, setShowFullText] = useState(false)
  const [originalPdf, setOriginalPdf] = useState<string | null>(null)
  const requestedArticle = showFullText ? '' : articleParam

  useEffect(() => { setShowFullText(false) }, [docId, articleParam, lawNumber])
  useEffect(() => {
    setOriginalPdf(null)
    if (requestedArticle || !document?.source_file_available) return
    const controller = new AbortController()
    let objectUrl: string | null = null
    void (async () => {
      try {
        const apiUrl = await getApiUrl()
        const token = getAuthToken()
        const response = await fetch(`${apiUrl}/api/legal/docs/${encodeURIComponent(document.doc_id || docId)}/download.pdf`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal,
        })
        // A generated extract must never be presented as the original layout.
        if (!response.ok || response.headers.get('X-Legal-Pdf-Origin') !== 'original-file') return
        const blob = await response.blob()
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setOriginalPdf(objectUrl)
      } catch { /* Keep the accessible text view if the original is unavailable. */ }
    })()
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [document?.doc_id, document?.source_file_available, docId, requestedArticle])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!docId) {
        setError('Thiếu mã văn bản.')
        setLoading(false)
        return
      }

      // Check session cache first to avoid re-fetching on back navigation
      const cacheKey = `legal_doc_cache_${role}_${lawNumber}_${docId}_${articleParam || 'full'}`
      // Revalidate every opening: access and lifecycle may change after a prior view.
      try {
        setLoading(true)
        setError(null)
        const viewStartedAt = performance.now()
        const response = await apiClient.get<LegalDocumentDetail>(
          `/legal/docs/${encodeURIComponent(lawNumber ? "lookup" : docId)}?${requestedArticle ? `article=${encodeURIComponent(requestedArticle)}` : 'include_content=true'}${lawNumber ? `&law_number=${encodeURIComponent(lawNumber)}` : ''}`,
        )
        if (!cancelled && response.data) {
          setDocument(response.data)
          setViewLatencyMs(Math.round(performance.now() - viewStartedAt))
          try {
            sessionStorage.setItem(
              cacheKey,
              JSON.stringify({ cached_at: Date.now(), document: response.data }),
            )
          } catch {
            // Ignore quota errors
          }
        }
      } catch (caught: unknown) {
        if (!cancelled) {
          setError(formatApiError(caught, 'Không tải được nội dung văn bản.'))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [docId, requestedArticle, lawNumber, role])

  // Build hierarchical tree for TOC (Chương -> Điều -> Khoản -> Điểm)
  const tocTree = useMemo(() => {
    return buildVBPLLegalTocTree(document?.articles || [])
  }, [document?.articles])

  // Auto expand all TOC nodes
  useEffect(() => {
    if (tocTree.length > 0) {
      const initial: Record<string, boolean> = {}
      const traverse = (nodes: TocNode[]) => {
        nodes.forEach((n) => {
          initial[n.id] = true
          if (n.children) traverse(n.children)
        })
      }
      traverse(tocTree)
      setExpandedNodes(initial)
    }
  }, [tocTree])

  // Auto scroll to target article/clause/point if query parameter exists
  useEffect(() => {
    setLocationNotice(null)
    if (!articleParam || !document) return
    const anchorId = clauseParam || pointParam
      ? buildSectionAnchor(articleParam, clauseParam || undefined, pointParam || undefined)
      : buildArticleAnchor(articleParam)

    setActiveNodeId(anchorId)
    const target = window.document.getElementById(anchorId) || window.document.getElementById(buildArticleAnchor(articleParam))
    if (!window.document.getElementById(anchorId)) {
      setLocationNotice(target
        ? 'Đã mở đúng điều. Chưa định vị riêng được khoản hoặc điểm được dẫn.'
        : 'Chưa có nội dung điều được dẫn trong bản số hóa này. Anh/chị có thể mở toàn văn hoặc nguồn gốc để đối chiếu.')
    }
    if (target) {
      window.setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }), 120)
    }
  }, [articleParam, clauseParam, pointParam, document])

  const toggleNodeExpand = (nodeId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setExpandedNodes((prev) => ({ ...prev, [nodeId]: !prev[nodeId] }))
  }

  // Smooth scroll to target Khoản / Điểm / Điều when clicked from TOC
  const handleTocItemClick = (node: TocNode) => {
    setActiveNodeId(node.id)
    const target = window.document.getElementById(node.id)
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else if (node.articleNumber) {
      const artTarget = window.document.getElementById(buildArticleAnchor(node.articleNumber))
      if (artTarget) artTarget.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  const downloadPdf = async () => {
    try {
      setDownloadingPdf(true)
      const apiUrl = await getApiUrl()
      const query = ''
      const response = await fetch(
        `${apiUrl}/api/legal/docs/${encodeURIComponent(document?.doc_id || docId)}/download.pdf${query}`,
        {
          headers: getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {},
        },
      )
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(payload?.detail || 'Không tải được PDF.')
      }
      const href = URL.createObjectURL(await response.blob())
      const anchor = window.document.createElement('a')
      anchor.href = href
      anchor.download = `${document?.law_number || docId}.pdf`
      anchor.click()
      URL.revokeObjectURL(href)
      toast.success(
        document?.source_file_available
          ? 'Đã tải file PDF gốc thành công.'
          : 'Đã tải bản trích xuất PDF từ hệ thống.',
      )
    } catch (caught: unknown) {
      toast.error(formatApiError(caught, 'Không tải được tệp PDF.'))
    } finally {
      setDownloadingPdf(false)
    }
  }

  // Render tree view for TOC sidebar matching app design system
  const renderTocNodes = (nodes: TocNode[], depth = 0) => {
    return (
      <ul className={`space-y-0.5 ${depth > 0 ? 'ml-3 border-l pl-2 border-border' : ''}`}>
        {nodes.map((node) => {
          const isExpanded = expandedNodes[node.id] ?? true
          const hasChildren = node.children && node.children.length > 0
          const isActive = activeNodeId === node.id || (node.articleNumber && isTargetArticle(node.articleNumber, articleParam))

          return (
            <li key={node.id} className="text-xs">
              <div
                onClick={() => handleTocItemClick(node)}
                className={`group flex items-center justify-between rounded-md px-2.5 py-1.5 cursor-pointer transition-colors ${
                  isActive
                    ? 'bg-primary/15 font-semibold text-primary'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                }`}
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  {hasChildren && (
                    <button
                      type="button"
                      onClick={(e) => toggleNodeExpand(node.id, e)}
                      className="p-0.5 text-muted-foreground hover:text-foreground"
                    >
                      {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    </button>
                  )}
                  {!hasChildren && <span className="w-3 text-center text-muted-foreground font-mono">•</span>}
                  <span className="truncate">{node.title || node.label}</span>
                </div>
              </div>
              {hasChildren && isExpanded && renderTocNodes(node.children!, depth + 1)}
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
        {/* App Standard Header */}
        <header className="border-b bg-card px-6 py-4">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              {!embedded && <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  if (window.history.length > 1) window.history.back()
                  else window.location.assign('/search')
                }}
              >
                <ArrowLeft className="mr-2 h-4 w-4" /> Quay lại phiên chat
              </Button>}
              {!embedded && <span className="text-muted-foreground">/</span>}
              <span className="truncate text-sm font-medium max-w-md">
                {document?.law_number || document?.document_title || docId}
              </span>
            </div>

            {/* Controls Toolbar */}
            <div className="flex items-center gap-3">
              {/* Zoom Buttons */}
              <div className="flex items-center gap-1 rounded-md border bg-muted/30 px-2 py-1 text-xs">
                <button
                  type="button"
                  title="Thu nhỏ chữ"
                  onClick={() => setFontSizePercent((prev) => Math.max(80, prev - 10))}
                  className="hover:text-primary p-0.5"
                >
                  <ZoomOut className="h-3.5 w-3.5" />
                </button>
                <span className="font-mono text-xs px-1.5">{fontSizePercent}%</span>
                <button
                  type="button"
                  title="Phóng to chữ"
                  onClick={() => setFontSizePercent((prev) => Math.min(150, prev + 10))}
                  className="hover:text-primary p-0.5"
                >
                  <ZoomIn className="h-3.5 w-3.5" />
                </button>
              </div>

              {/* TOC Toggle */}
              <Button
                variant={rightTocOpen ? 'secondary' : 'outline'}
                size="sm"
                onClick={() => setRightTocOpen((prev) => !prev)}
              >
                <BookOpen className="mr-2 h-4 w-4" /> Mục lục
              </Button>

              {/* Download PDF */}
              <Button size="sm" onClick={() => void downloadPdf()} disabled={downloadingPdf || !document || docId.startsWith('dvc:')}>
                <Download className={`mr-2 h-4 w-4 ${downloadingPdf ? 'animate-spin' : ''}`} />
                {downloadingPdf ? 'Đang tải...' : 'Tải PDF'}
              </Button>
            </div>
          </div>
        </header>

        {/* Main Workspace Body */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-7xl items-start justify-between gap-6 p-6">
            {/* Document Content Canvas */}
            <main className="flex-1 min-w-0 rounded-xl border bg-card p-4 md:p-6 shadow-sm">
              <DocumentLifecyclePanel documentId={docId} isAdmin={role === 'admin'} />
              {loading && (
                <div className="space-y-4 py-16 text-center" aria-busy="true">
                  <RefreshCw className="mx-auto h-8 w-8 animate-spin text-primary" />
                  <p className="text-sm text-muted-foreground">Đang tải nội dung văn bản pháp lý...</p>
                </div>
              )}

              {error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-8 text-center space-y-4">
                  <AlertTriangle className="mx-auto h-10 w-10 text-destructive/80" />
                  <div>
                    <p className="font-semibold text-lg text-foreground">Không tải được văn bản pháp lý</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {error}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => window.open(`https://vbpl.vn`, '_blank')}
                    >
                      Tra cứu trên Cổng VBPL Quốc gia
                    </Button>
                    <Link href="/search">
                      <Button size="sm">
                        Quay lại Hỏi đáp
                      </Button>
                    </Link>
                  </div>
                </div>
              )}

              {document && !loading && !error && (
                <article style={{ fontSize: `${fontSizePercent}%` }} className="space-y-6 leading-relaxed">
                  {/* Document Title Header */}
                  <div className="border-b pb-6 space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      {document.law_number && <Badge variant="default">{document.law_number}</Badge>}
                      {(document.validity_sync?.display_label || document.effective_status) && (
                        <Badge
                          variant={document.current_answer_eligible === false ? 'destructive' : 'outline'}
                          className={document.current_answer_eligible === false ? undefined : 'border-emerald-500/30 text-emerald-600 dark:text-emerald-400 bg-emerald-500/5'}
                        >
                          {document.validity_sync?.display_label || document.effective_status}
                        </Badge>
                      )}
                      {document.issuing_agency && <Badge variant="secondary">{document.issuing_agency}</Badge>}
                      {document.effective_date && (
                        <span className="text-xs text-muted-foreground ml-auto">
                          Hiệu lực từ: {document.effective_date}
                        </span>
                      )}
                    </div>

                    <h1 className="text-xl md:text-2xl font-bold leading-snug pt-2">
                      {document.document_title || document.law_number || `Văn bản ${docId}`}
                    </h1>

                    <div className="grid gap-1 text-xs text-muted-foreground pt-1 sm:grid-cols-2">
                      {document.document_type && <span>Loại văn bản: {document.document_type}</span>}
                      {document.issued_date && <span>Ngày ban hành: {document.issued_date}</span>}
                      {document.scope && <span>Phạm vi: {document.scope}</span>}
                      {viewLatencyMs !== null && <span>Thời gian nạp: {viewLatencyMs} ms</span>}
                    </div>
                  </div>

                  {document.current_answer_eligible === false && (
                    <div className="flex gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-destructive" role="status">
                      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
                      <div>
                        <p className="font-semibold">{document.validity_sync?.display_label}</p>
                        <p className="mt-1 text-sm">
                          Chỉ dùng để tra cứu lịch sử. Văn bản này không được dùng để trả lời pháp luật hiện hành.
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Render Articles & Chunks continuously */}
                  {locationNotice && <p role="status" className="rounded-lg border p-3 text-sm">{locationNotice}</p>}
                  <div className="flex flex-wrap gap-4 text-sm">
                    {articleParam && !showFullText && <button type="button" className="text-primary underline underline-offset-4" onClick={() => setShowFullText(true)}>Xem toàn văn</button>}
                    {document.source_url && /^https?:\/\//i.test(document.source_url) && <a className="text-primary underline" href={document.source_url} target="_blank" rel="noopener noreferrer">Văn bản gốc</a>}
                  </div>
                  <div className="space-y-6 pt-4">
                    {originalPdf ? <iframe title="Toàn văn PDF gốc" src={originalPdf} className="h-[75dvh] min-h-96 w-full rounded border" /> : document.articles && document.articles.length > 0 ? (
                      document.articles.map((art) => {
                        const artNum = String(art.article_number || '').trim()
                        const isHighlighted = isTargetArticle(artNum, articleParam)
                        let currentClauseForChunks = ''

                        return (
                          <div
                            key={String(art.article_id || artNum)}
                            id={buildArticleAnchor(artNum)}
                            className={`transition-colors scroll-mt-24 ${
                              isHighlighted
                                ? 'border-l-2 border-primary pl-4'
                                : ''
                            }`}
                          >
                            <h2 className={`text-base md:text-lg font-bold mb-4 ${isHighlighted ? 'text-primary' : ''}`}>
                              {art.article_title && /^Điều\s/i.test(art.article_title) ? art.article_title : `Điều ${artNum}${art.article_title ? `. ${art.article_title}` : ''}`}
                            </h2>

                            <div className="space-y-3 text-sm md:text-base leading-relaxed">
                              {viewerParagraphs(art.chunks || []).map((chunk, idx) => {
                                const content = chunk.content || ''
                                // Detect Clause (Khoản) or Point (Điểm) for exact scroll anchor target
                                const clauseMatch = content.match(/^\s*(\d+)\.\s+/)
                                const cNum = clauseMatch ? clauseMatch[1] : ''
                                if (cNum) currentClauseForChunks = cNum
                                const pointMatch = content.match(/^\s*([a-đa-z])\)\s+/i)
                                const pLetter = pointMatch ? pointMatch[1].toLowerCase() : ''

                                const anchorId = cNum
                                  ? buildSectionAnchor(artNum, cNum)
                                  : (pLetter
                                    ? buildSectionAnchor(artNum, currentClauseForChunks || undefined, pLetter)
                                    : `chunk-${artNum}-${idx}`)

                                const highlightState = getChunkHighlightState(
                                  content,
                                  isHighlighted,
                                  clauseParam,
                                  pointParam,
                                  currentClauseForChunks,
                                )

                                return (
                                  <div
                                    key={String(chunk.chunk_id || idx)}
                                    id={anchorId}
                                    className={`scroll-mt-24 whitespace-pre-wrap break-words transition-colors ${
                                      highlightState.highlight
                                        ? 'bg-primary/5 rounded px-2 py-1 text-foreground'
                                        : ''
                                    }`}
                                  >
                                    {chunk.content}
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )
                      })
                    ) : document.content?.trim() ? (
                      <div className="whitespace-pre-wrap break-words text-sm leading-relaxed md:text-base">{document.content}</div>
                    ) : (
                      <div className="py-12 text-center text-muted-foreground">
                        Chưa có nội dung toàn văn để hiển thị.
                      </div>
                    )}
                  </div>
                </article>
              )}
            </main>

            {/* Right Collapsible TOC Sidebar */}
            {rightTocOpen && (
              <aside className="hidden xl:block w-64 shrink-0 sticky top-6 rounded-xl border bg-card shadow-sm overflow-hidden transition-all">
                <div className="flex items-center justify-between border-b bg-muted/40 px-4 py-3 font-semibold text-sm">
                  <div className="flex items-center gap-2">
                    <BookOpen className="h-4 w-4 text-primary" />
                    <span>Mục lục văn bản</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setRightTocOpen(false)}
                    className="text-muted-foreground hover:text-foreground text-xs"
                    title="Đóng mục lục"
                  >
                    ✕
                  </button>
                </div>

                <div className="max-h-[calc(100vh-12rem)] overflow-y-auto p-3">
                  {tocTree.length > 0 ? (
                    renderTocNodes(tocTree)
                  ) : (
                    <p className="p-4 text-center text-xs text-muted-foreground">Đang tạo mục lục...</p>
                  )}
                </div>
              </aside>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
