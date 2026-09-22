'use client'

import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Check, Languages, LogOut, Menu, MessageSquarePlus, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Pencil, Settings, ShieldCheck, Sun, Trash2, UserRound, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'
import { apiClient } from '@/lib/api/client'
import type { UserRole } from '@/lib/stores/auth-store'
import { useAuthStore } from '@/lib/stores/auth-store'
import { useAuth } from '@/lib/hooks/use-auth'
import { useOperatingScope } from '@/lib/hooks/use-operating-scope'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useTheme, type Theme } from '@/lib/stores/theme-store'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { navigationForRole } from '@/lib/navigation/capabilities'
import { getConfig } from '@/lib/config'
import type { AskMessage } from '@/lib/types/search'

export interface ConversationSummary {
  id: string
  title: string
  domain?: string | null
  role_context: string
  status: string
  owner_user_id?: string | null
  created_at: string
  last_message_at: string
  expires_at?: string
  message_count?: number | null
}

export type ConversationMessage = AskMessage

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[]
  next_cursor?: string | null
  has_older_messages?: boolean
  model_option_id?: string | null
  model_display_name?: string | null
  model_locked?: boolean
}

interface ConversationSidebarProps {
  role: UserRole
  currentId: string | null
  onSelect: (conversation: ConversationDetail) => void
  onCreate: (conversation: ConversationDetail) => void
  onSelectStart?: () => void
  onCreateStart?: () => void
  onTransitionError?: () => void
  onDeleted: (id: string) => void
  className?: string
  mobileOpen?: boolean
  onMobileOpenChange?: (open: boolean) => void
  refreshKey?: number
  contextLabel?: string | null
}

const LANGUAGE_OPTIONS = [
  ['vi-VN', 'Tiếng Việt'], ['en-US', 'English'], ['zh-CN', '简体中文'], ['zh-TW', '繁體中文'],
  ['ja-JP', '日本語'], ['fr-FR', 'Français'], ['de-DE', 'Deutsch'], ['es-ES', 'Español'],
  ['pt-BR', 'Português'], ['ru-RU', 'Русский'], ['ca-ES', 'Català'], ['pl-PL', 'Polski'], ['bn-IN', 'বাংলা'],
] as const

const CONVERSATION_PAGE_SIZE = 20
const CONVERSATION_CACHE_TTL_MS = 10 * 60 * 1000

export const ConversationSidebar = memo(function ConversationSidebar({
  role,
  currentId,
  onSelect,
  onCreate,
  onSelectStart,
  onCreateStart,
  onTransitionError,
  onDeleted,
  className,
  mobileOpen = false,
  onMobileOpenChange,
  refreshKey = 0,
  contextLabel,
}: ConversationSidebarProps) {
  const pathname = usePathname()
  const username = useAuthStore((state) => state.username)
  const userId = useAuthStore((state) => state.userId)
  const { logout } = useAuth()
  const { scope: operatingScope } = useOperatingScope()
  const { language, setLanguage } = useTranslation()
  const { theme, setTheme } = useTheme()
  const { isCollapsed, toggleCollapse } = useSidebarStore()
  const [systemName, setSystemName] = useState('Pháp luật Hải Phòng')
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [historyQuery, setHistoryQuery] = useState('')
  const deferredHistoryQuery = useDeferredValue(historyQuery)
  const openControllerRef = useRef<AbortController | null>(null)
  const historyCacheKey = userId ? `conversation-summaries-v1:${userId}:${role}` : null
  const [hydratedCacheKey, setHydratedCacheKey] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get('/conversations', {
        params: { limit: CONVERSATION_PAGE_SIZE, include_message_count: false },
      })
      const rows = Array.isArray(res.data) ? res.data as ConversationSummary[] : []
      setItems(rows)
      setHasMore(rows.length === CONVERSATION_PAGE_SIZE)
    } catch {
      // Keep empty list if backend is unavailable
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMore = useCallback(async () => {
    if (loading || loadingMore || !hasMore) return
    setLoadingMore(true)
    try {
      const res = await apiClient.get('/conversations', {
        params: { limit: CONVERSATION_PAGE_SIZE, offset: items.length, include_message_count: false },
      })
      const rows = Array.isArray(res.data) ? res.data as ConversationSummary[] : []
      setItems((current) => [
        ...current,
        ...rows.filter((row) => !current.some((item) => item.id === row.id)),
      ])
      setHasMore(rows.length === CONVERSATION_PAGE_SIZE)
    } catch {
      toast.error('Không tải thêm được lịch sử trò chuyện.')
    } finally {
      setLoadingMore(false)
    }
  }, [hasMore, items.length, loading, loadingMore])

  useEffect(() => {
    setItems([])
    setHasMore(false)
    setHistoryQuery('')
    setHydratedCacheKey(null)
    if (!historyCacheKey) return
    try {
      const raw = sessionStorage.getItem(historyCacheKey)
      if (raw) {
        const cached = JSON.parse(raw) as {
          savedAt?: number
          items?: ConversationSummary[]
          hasMore?: boolean
        }
        if (
          typeof cached.savedAt === 'number'
          && Date.now() - cached.savedAt <= CONVERSATION_CACHE_TTL_MS
          && Array.isArray(cached.items)
        ) {
          setItems(cached.items)
          setHasMore(Boolean(cached.hasMore))
        } else {
          sessionStorage.removeItem(historyCacheKey)
        }
      }
    } catch {
      // A cache failure must never prevent the authoritative API refresh.
    }
    setHydratedCacheKey(historyCacheKey)
  }, [historyCacheKey])

  useEffect(() => {
    if (!historyCacheKey || hydratedCacheKey !== historyCacheKey) return
    try {
      sessionStorage.setItem(historyCacheKey, JSON.stringify({
        savedAt: Date.now(),
        items,
        hasMore,
      }))
    } catch {
      // Session storage can be unavailable in hardened/private browsers.
    }
  }, [hasMore, historyCacheKey, hydratedCacheKey, items])

  useEffect(() => () => openControllerRef.current?.abort(), [])

  useEffect(() => {
    if (!userId) return
    void load()
  }, [load, refreshKey, userId])

  useEffect(() => {
    let cancelled = false
    const loadBranding = () => void getConfig().then((config) => {
      if (!cancelled && config.systemName) setSystemName(config.systemName)
    }).catch(() => undefined)
    loadBranding()
    window.addEventListener('system-settings-updated', loadBranding)
    return () => {
      cancelled = true
      window.removeEventListener('system-settings-updated', loadBranding)
    }
  }, [])

  const create = useCallback(async () => {
    if (creating) return
    onCreateStart?.()
    setCreating(true)
    try {
      const res = await apiClient.post('/conversations', {
        title: 'Cuộc trò chuyện mới',
      })
      const created = res.data as ConversationDetail
      setItems((prev) => [created, ...prev.filter((item) => item.id !== created.id)])
      onCreate(created)
      onMobileOpenChange?.(false)
    } catch {
      toast.error('Không tạo được cuộc trò chuyện mới.')
      onTransitionError?.()
    } finally {
      setCreating(false)
    }
  }, [creating, onCreate, onCreateStart, onMobileOpenChange, onTransitionError])

  const open = useCallback(async (id: string) => {
    if (openingId === id) return
    openControllerRef.current?.abort()
    const controller = new AbortController()
    openControllerRef.current = controller
    setOpeningId(id)
    onSelectStart?.()
    try {
      const res = await apiClient.get(`/conversations/${id}`, { signal: controller.signal })
      if (controller.signal.aborted || openControllerRef.current !== controller) return
      onSelect(res.data as ConversationDetail)
      onMobileOpenChange?.(false)
    } catch {
      if (controller.signal.aborted) return
      toast.error('Không tải được cuộc trò chuyện.')
      onTransitionError?.()
    } finally {
      if (openControllerRef.current === controller) {
        openControllerRef.current = null
        setOpeningId(null)
      }
    }
  }, [onMobileOpenChange, onSelect, onSelectStart, onTransitionError, openingId])

  const rename = useCallback(async (id: string) => {
    const title = renameValue.trim()
    if (!title) return
    try {
      const res = await apiClient.patch(`/conversations/${id}`, { title })
      setItems((prev) => prev.map((item) => (item.id === id ? { ...item, title: res.data.title } : item)))
      setRenamingId(null)
    } catch {
      toast.error('Không đổi được tên cuộc trò chuyện.')
    }
  }, [renameValue])

  const remove = useCallback(async (id: string) => {
    try {
      await apiClient.delete(`/conversations/${id}`)
      setItems((prev) => prev.filter((item) => item.id !== id))
      onDeleted(id)
    } catch {
      toast.error('Không xóa được cuộc trò chuyện.')
    }
  }, [onDeleted])

  const visibleItems = useMemo(() => {
    const query = deferredHistoryQuery.trim().toLocaleLowerCase('vi-VN')
    if (!query) return items
    return items.filter((item) => `${item.title} ${item.domain || ''}`.toLocaleLowerCase('vi-VN').includes(query))
  }, [deferredHistoryQuery, items])

  const { pinnedNavigation, overflowNavigation } = useMemo(() => {
    const allItems = navigationForRole(role).flatMap((group) => group.items)
      .filter(item => role !== 'officer' || item.id !== 'support' || operatingScope?.can_receive_support === true)
    const preferredIds = role === 'admin'
      ? ['legal-management', 'legal-import', 'procedures', 'notebooks']
      : role === 'officer'
        ? ['ask', 'sources', 'procedures', 'notebooks']
        : ['ask', 'procedures']
    const preferred = preferredIds
      .map((id) => allItems.find((item) => item.id === id))
      .filter((item): item is NonNullable<typeof item> => Boolean(item))
    return {
      pinnedNavigation: preferred,
      overflowNavigation: allItems.filter((item) => !preferredIds.includes(item.id)),
    }
  }, [role, operatingScope?.can_receive_support])

  const roleLabel = role === 'admin' ? 'Quản trị viên' : role === 'officer' ? 'Cán bộ' : 'Người dân'
  const isActivePath = (href: string) => pathname === href || (href !== '/' && pathname?.startsWith(`${href}/`))

  const featureMenu = (compact = false) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className={cn('h-11 gap-2 rounded-xl', compact ? 'w-11 p-0' : 'w-full justify-start px-3')} aria-label="Mở chức năng">
          <Menu className="h-5 w-5" aria-hidden="true" />
          {!compact && 'Chức năng'}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-[70dvh] w-72 overflow-y-auto">
        <DropdownMenuLabel>Chức năng dành cho {roleLabel.toLocaleLowerCase('vi-VN')}</DropdownMenuLabel>
        {[...pinnedNavigation, ...overflowNavigation].filter(item => item.id !== 'account').map(item => (
          <DropdownMenuItem key={item.id} asChild className="min-h-11 gap-2">
            <Link href={item.href} onClick={() => onMobileOpenChange?.(false)} aria-current={isActivePath(item.href) ? 'page' : undefined}>
              <item.icon className="h-4 w-4 shrink-0" aria-hidden="true" />{item.name}
            </Link>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )

  const settingsMenu = (compact = false) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={cn('h-11 w-11 shrink-0 rounded-full', compact && 'border border-border/70 bg-card shadow-sm')}
          aria-label="Cài đặt và tài khoản"
          title="Cài đặt và tài khoản"
        >
          <Settings className="h-5 w-5" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align={compact ? 'start' : 'end'} side="top" className="w-72">
        <DropdownMenuLabel>Cài đặt và tiện ích</DropdownMenuLabel>
        {overflowNavigation.filter((item) => item.id !== 'account').map((item) => (
          <DropdownMenuItem key={item.id} asChild className="gap-3 py-2.5">
            <Link href={item.href}><item.icon className="h-4 w-4" aria-hidden="true" />{item.name}</Link>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="gap-2 py-2.5"><Sun className="h-4 w-4" />Giao diện</DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-48">
            <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
              <DropdownMenuRadioItem value="light"><Sun className="h-4 w-4" />Sáng</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark"><Moon className="h-4 w-4" />Tối</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="system"><Monitor className="h-4 w-4" />Theo hệ thống</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="gap-2 py-2.5"><Languages className="h-4 w-4" />Ngôn ngữ</DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="max-h-80 w-52 overflow-y-auto">
            {LANGUAGE_OPTIONS.map(([code, label]) => (
              <DropdownMenuItem key={code} className="gap-2" onSelect={() => void setLanguage(code)}>
                <Check className={cn('h-4 w-4', language === code || language.startsWith(code.split('-')[0]) ? 'opacity-100' : 'opacity-0')} />
                {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild className="gap-2 py-2.5"><Link href="/account"><UserRound className="h-4 w-4" />Tài khoản của tôi</Link></DropdownMenuItem>
        <DropdownMenuItem className="gap-2 py-2.5 text-destructive" onSelect={logout}><LogOut className="h-4 w-4" />Đăng xuất</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  const body = (
    <div className="flex h-full min-h-0 flex-col border-r border-border/70 bg-muted/20">
      <div className="shrink-0 p-2 pb-0">
        <div className="flex h-14 items-center gap-2 rounded-2xl border border-border/70 bg-card px-2.5 shadow-sm">
          <Link href="/search" className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl py-1">
            <Image src="/logo.svg" alt={systemName} width={34} height={34} className="shrink-0" />
            <span className="min-w-0">
              <span className="block truncate text-sm font-bold">{systemName}</span>
              <span className="block truncate text-[10px] text-muted-foreground">Trợ lý pháp luật</span>
            </span>
          </Link>
          <Button type="button" variant="ghost" size="icon" className="hidden h-11 w-11 shrink-0 rounded-full lg:inline-flex" onClick={toggleCollapse} aria-label="Thu gọn thanh bên">
            <PanelLeftClose className="h-5 w-5" aria-hidden="true" />
          </Button>
        </div>
      </div>
      <div className="shrink-0 px-2 pt-2">{featureMenu()}</div>
      <div className="shrink-0 border-b border-border/70 px-2 pb-2 pt-3">
        <div className="flex items-center justify-between gap-2 px-1">
          <div>
            <p className="text-base font-semibold">Lịch sử trò chuyện</p>
            <p className="text-xs text-muted-foreground">Lịch sử riêng của bạn</p>
          </div>
          <Button size="sm" variant="ghost" onClick={() => void create()} disabled={creating || loading} className="h-10 gap-1 rounded-full px-3">
            <MessageSquarePlus className="h-3.5 w-3.5" />
            Mới
          </Button>
        </div>
        <Input value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} placeholder="Tìm trong lịch sử…" className="mt-2 h-10 rounded-full bg-background px-4 text-sm" aria-label="Tìm cuộc trò chuyện" />

        {contextLabel && (
          <p className="mt-2 truncate rounded-full bg-primary/8 px-3 py-1.5 text-xs text-muted-foreground" title={contextLabel}>
            Lĩnh vực: <span className="font-medium text-foreground">{contextLabel}</span>
          </p>
        )}


      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 space-y-0.5" data-testid="conversation-history-scroll-region">
        {loading && <p className="px-2 py-3 text-xs text-muted-foreground">Đang tải…</p>}
        {!loading && visibleItems.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted-foreground">Chưa có cuộc trò chuyện. Bấm “Mới” để bắt đầu.</p>
        )}
        {visibleItems.map((item) => (
          <div
            key={item.id}
            className={cn(
              'group relative rounded-lg border border-transparent px-3 py-3 text-left transition-colors',
              currentId === item.id ? 'bg-primary/10 text-foreground' : 'hover:bg-muted/70'
            )}
          >
            {renamingId === item.id ? (
              <div className="flex items-center gap-1">
                <Input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  className="h-8 text-xs"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void rename(item.id)
                    if (e.key === 'Escape') setRenamingId(null)
                  }}
                />
                <Button size="sm" className="h-8 px-2" onClick={() => void rename(item.id)}>OK</Button>
              </div>
            ) : (
              <button
                type="button"
                className="min-h-11 w-full pr-9 text-left focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => void open(item.id)}
                disabled={openingId === item.id}
                aria-busy={openingId === item.id}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{item.title}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {typeof item.message_count === 'number' ? `${item.message_count} tin · ` : ''}
                      {item.last_message_at ? new Date(item.last_message_at).toLocaleString('vi-VN') : '—'}
                    </p>
                  </div>
                  {item.domain ? <Badge variant="secondary" className="max-w-24 shrink-0 truncate rounded-full text-[10px]">{item.domain}</Badge> : null}
                </div>
              </button>
            )}
            <div className="absolute right-1 top-2 flex flex-col opacity-100 transition-opacity lg:opacity-0 lg:group-hover:opacity-100 focus-within:opacity-100">
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                onClick={() => {
                  setRenamingId(item.id)
                  setRenameValue(item.title)
                }}
                aria-label="Đổi tên"
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-destructive"
                onClick={() => void remove(item.id)}
                aria-label="Xóa"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ))}
        {hasMore && !deferredHistoryQuery.trim() && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2 w-full rounded-xl text-xs"
            onClick={() => void loadMore()}
            disabled={loadingMore}
          >
            {loadingMore ? 'Đang tải thêm…' : 'Tải thêm lịch sử'}
          </Button>
        )}
      </div>

      <div className="shrink-0 border-t border-border/70 bg-card/70 p-2">
        <div className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-full px-2 py-1">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground"><ShieldCheck className="h-4 w-4" aria-hidden="true" /></span>
            <span className="min-w-0">
              <span className="block truncate text-xs font-semibold">{username || roleLabel}</span>
              <span className="block truncate text-[10px] text-muted-foreground">{roleLabel}</span>
            </span>
          </div>
          {settingsMenu()}
        </div>
      </div>
    </div>
  )

  const collapsedBody = (
    <div className="flex h-full min-h-0 flex-col items-center border-r border-border/70 bg-muted/20 py-2">
      <Link href="/search" className="flex h-12 w-12 items-center justify-center rounded-2xl border border-border/70 bg-card shadow-sm" title={systemName}>
        <Image src="/logo.svg" alt={systemName} width={32} height={32} />
      </Link>
      <Button type="button" variant="ghost" size="icon" className="mt-2 h-11 w-11 rounded-full" onClick={toggleCollapse} aria-label="Mở thanh bên" title="Mở thanh bên">
        <PanelLeftOpen className="h-5 w-5" aria-hidden="true" />
      </Button>
      <Button type="button" variant="ghost" size="icon" className="mt-2 h-11 w-11 rounded-full" onClick={() => void create()} disabled={creating || loading} aria-label="Cuộc trò chuyện mới" title="Cuộc trò chuyện mới">
        <MessageSquarePlus className="h-5 w-5" aria-hidden="true" />
      </Button>
      {featureMenu(true)}
      <div className="mt-auto pt-2">{settingsMenu(true)}</div>
    </div>
  )

  return (
    <>
      {/* Desktop sidebar */}
      <aside className={cn(
        'hidden h-full min-h-0 shrink-0 overflow-hidden transition-[width] duration-200 lg:block',
        isCollapsed ? 'w-[76px]' : 'w-[260px]',
        className,
      )}>
        {isCollapsed ? collapsedBody : body}
      </aside>

      {/* Mobile trigger + drawer */}
      <div className="lg:hidden">
        <Button
          variant="outline"
          size="sm"
          className="sr-only"
          onClick={() => onMobileOpenChange?.(true)}
        >
          <Menu className="h-4 w-4" />
          Lịch sử chat
        </Button>
        {mobileOpen ? (
          <div className="fixed inset-0 z-50 bg-black/40" onClick={() => onMobileOpenChange?.(false)}>
            <div
              className="absolute left-0 top-0 h-full w-[85%] max-w-sm bg-background shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-end p-2">
                <Button size="icon" variant="ghost" aria-label="Đóng lịch sử" onClick={() => onMobileOpenChange?.(false)}>
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <div className="h-[calc(100%-48px)]">{body}</div>
            </div>
          </div>
        ) : null}
      </div>
    </>
  )
})
