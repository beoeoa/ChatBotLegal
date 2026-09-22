'use client'

import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import Link from 'next/link'
import Image from 'next/image'
import { usePathname } from 'next/navigation'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/hooks/use-auth'
import { useAuthStore } from '@/lib/stores/auth-store'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import {
  createActionsForRole,
  navigationForRole
} from '@/lib/navigation/capabilities'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { legalImportApi } from '@/lib/api/legal-import'
import { useTranslation } from '@/lib/hooks/use-translation'
import { Separator } from '@/components/ui/separator'
import { LogOut, ChevronLeft, Menu, Plus, Command, ShieldCheck } from 'lucide-react'
import { getConfig } from '@/lib/config'
import { UserNavigationSidebar } from './UserNavigationSidebar'

const getNavigation = (role: 'officer' | 'citizen' | 'admin' | null) =>
  navigationForRole(role)
/*
  if (role === 'citizen') {
    return [
      {
        title: t('navigation.process'),
        items: [
          { name: t('navigation.askAndSearch'), href: '/search?mode=ask', icon: Search },
          { name: 'Câu hỏi thường gặp', href: '/procedures', icon: MessageCircleQuestion },
          { name: 'Hỗ trợ trực tuyến', href: '/live-support', icon: Bot },
        ],
      },
    ] as const
  }
  // officer/admin: show both Ask and Search tabs
  if (role === 'officer') {
    return [
      {
        title: t('navigation.process'),
        items: [
          { name: t('navigation.askAndSearch', 'Hỏi đáp AI'), href: '/search?mode=ask', icon: MessageCircleQuestion },
          { name: t('navigation.search', 'Tra cứu văn bản'), href: '/search?mode=search', icon: Search },
          { name: t('navigation.sources'), href: '/sources', icon: FileText },
          { name: 'Câu hỏi thường gặp', href: '/procedures', icon: MessageCircleQuestion },
          { name: t('navigation.notebooks'), href: '/notebooks', icon: Book },
          { name: 'Hỗ trợ trực tuyến', href: '/live-support', icon: Bot },
          { name: 'Đề xuất văn bản', href: '/officer-proposals', icon: FileSearch },
        ],
      },
    ] as const
  }

  // Admin: focused on Legal Data Management & System Administration
  return [
    {
      title: 'Dữ liệu pháp lý',
      items: [
        { name: 'Nạp dữ liệu luật', href: '/legal-import', icon: DatabaseZap },
        { name: t('navigation.sources'), href: '/sources', icon: FileText },
      ],
    },
    {
      title: t('navigation.process'),
      items: [
        { name: t('navigation.askAndSearch'), href: '/search', icon: Search },
        { name: 'Câu hỏi thường gặp', href: '/procedures', icon: MessageCircleQuestion },
        { name: t('navigation.notebooks'), href: '/notebooks', icon: Book },
      ],
    },
    {
      title: t('navigation.manage'),
      items: [
        { name: 'Tài khoản', href: '/users', icon: UserCog },
        { name: 'Quản lý FAQ', href: '/faq-management', icon: MessageCircleQuestion },
        { name: t('navigation.models'), href: '/settings/api-keys', icon: Bot },
        { name: t('navigation.settings'), href: '/settings', icon: Settings },
      ],
    },
  ] as const
*/

type CreateTarget = 'source' | 'notebook'

export function AppSidebar({ onNavigate }: { onNavigate?: () => void } = {}) {
  const role = useAuthStore((state) => state.role)

  if (role === 'citizen' || role === 'officer') {
    return <UserNavigationSidebar role={role} onNavigate={onNavigate} />
  }

  return <AdminSidebar onNavigate={onNavigate} />
}

function AdminSidebar({ onNavigate }: { onNavigate?: () => void } = {}) {
  const { t } = useTranslation()
  const role = useAuthStore((state) => state.role)
  const username = useAuthStore((state) => state.username)
  const navigation = getNavigation(role)
  const createActions = createActionsForRole(role)
  const pathname = usePathname()
  const { logout } = useAuth()
  const { isCollapsed, toggleCollapse } = useSidebarStore()
  const { openSourceDialog, openNotebookDialog } = useCreateDialogs()

  const [createMenuOpen, setCreateMenuOpen] = useState(false)
  const [isMac, setIsMac] = useState(true) // Default to Mac for SSR
  const [pendingLegalReviews, setPendingLegalReviews] = useState(0)
  const [systemName, setSystemName] = useState('Pháp luật Hải Phòng')
  const navRef = useRef<HTMLElement>(null)
  useLayoutEffect(() => {
    const nav = navRef.current
    if (!nav || !role) return
    try {
      const top = Number(sessionStorage.getItem('admin-sidebar-scroll-top'))
      if (Number.isFinite(top) && top >= 0) nav.scrollTop = top
    } catch { /* Storage may be unavailable in private browser contexts. */ }
  }, [role])

  const rememberScroll = () => {
    if (!navRef.current) return
    try {
      sessionStorage.setItem('admin-sidebar-scroll-top', String(navRef.current.scrollTop))
    } catch { /* Navigation remains available without browser storage. */ }
  }

  // Detect platform for keyboard shortcut display
  useEffect(() => {
    setIsMac(navigator.platform.toLowerCase().includes('mac'))
  }, [])

  useEffect(() => {
    if (role !== 'admin') {
      setPendingLegalReviews(0)
      return
    }

    let cancelled = false
    const loadSummary = async () => {
      try {
        const summary = await legalImportApi.crawlSummary()
        if (!cancelled) {
          setPendingLegalReviews(summary.pending_review_count || 0)
        }
      } catch {
        if (!cancelled) {
          setPendingLegalReviews(0)
        }
      }
    }

    void loadSummary()
    const timer = window.setInterval(loadSummary, 60000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [role])

  useEffect(() => {
    let cancelled = false
    const loadBranding = () =>
      void getConfig()
        .then((config) => {
          if (!cancelled && config.systemName) setSystemName(config.systemName)
        })
        .catch(() => {
          // Keep the safe built-in name when the optional branding projection is unavailable.
        })
    loadBranding()
    window.addEventListener('system-settings-updated', loadBranding)
    return () => {
      cancelled = true
      window.removeEventListener('system-settings-updated', loadBranding)
    }
  }, [])

  const handleCreateSelection = (target: CreateTarget) => {
    setCreateMenuOpen(false)

    if (target === 'source') {
      openSourceDialog()
    } else if (target === 'notebook') {
      openNotebookDialog()
    }
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div
        className={cn(
          'app-sidebar flex h-full flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-300',
          isCollapsed ? 'w-[76px]' : 'w-72'
        )}
      >
        <div
          className={cn(
            'group flex min-h-20 shrink-0 items-center border-b border-sidebar-border',
            isCollapsed ? 'justify-center px-2' : 'justify-between px-4'
          )}
        >
          {isCollapsed ? (
            <div className="relative flex items-center justify-center w-full">
              <Image
                src="/logo.svg"
                alt={systemName}
                width={32}
                height={32}
                className="transition-opacity group-hover:opacity-0"
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleCollapse}
                className="absolute text-sidebar-foreground hover:bg-sidebar-accent opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Menu className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <>
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sidebar-primary/15 p-1 ring-1 ring-sidebar-primary/35">
                  <Image
                    src="/logo.svg"
                    alt={systemName}
                    width={32}
                    height={32}
                  />
                </span>
                <div className="min-w-0">
                  <span className="block truncate text-[15px] font-bold text-sidebar-foreground">{systemName}</span>
                  <span className="mt-0.5 block truncate text-[11px] text-sidebar-foreground/60">Cổng tra cứu pháp luật</span>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleCollapse}
                className="text-sidebar-foreground hover:bg-sidebar-accent"
                data-testid="sidebar-toggle"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>

        <nav ref={navRef} onScroll={rememberScroll} onClickCapture={rememberScroll} className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 py-4">
          {createActions.length > 0 && (
            <div className={cn('mb-5', isCollapsed ? 'px-0' : 'px-1')}>
              <DropdownMenu
                open={createMenuOpen}
                onOpenChange={setCreateMenuOpen}
              >
                {isCollapsed ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <DropdownMenuTrigger asChild>
                        <Button
                          onClick={() => setCreateMenuOpen(true)}
                          variant="default"
                          size="sm"
                          className="w-full justify-center border-0 bg-sidebar-primary px-2 text-sidebar-primary-foreground hover:bg-sidebar-primary/90"
                          aria-label={t('common.create')}
                        >
                          <Plus className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                    </TooltipTrigger>
                    <TooltipContent side="right">
                      {t('common.create')}
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <DropdownMenuTrigger asChild>
                    <Button
                      onClick={() => setCreateMenuOpen(true)}
                      variant="default"
                      size="sm"
                      className="w-full justify-start border-0 bg-sidebar-primary text-sidebar-primary-foreground shadow-sm hover:bg-sidebar-primary/90"
                    >
                      <Plus className="h-4 w-4 mr-2" />
                      {t('common.create')}
                    </Button>
                  </DropdownMenuTrigger>
                )}

                <DropdownMenuContent
                  align={isCollapsed ? 'end' : 'start'}
                  side={isCollapsed ? 'right' : 'bottom'}
                  className="w-48"
                >
                  {createActions.map((action) => (
                    <DropdownMenuItem
                      key={action.id}
                      onSelect={(event) => {
                        event.preventDefault()
                        handleCreateSelection(action.id)
                      }}
                      className="gap-2"
                    >
                      <action.icon className="h-4 w-4" />
                      {action.name}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}

          {navigation.map((section, index) => (
            <div key={section.title}>
              {index > 0 && <Separator className="my-3 bg-sidebar-border/80" />}
              <div className="space-y-1">
                {!isCollapsed && (
                  <p className="mb-2 px-3 font-ui text-[10px] font-bold uppercase tracking-[0.2em] text-sidebar-primary/80">
                    {section.title}
                  </p>
                )}

                {section.items.map((item) => {
                  const isActive = Boolean(
                    pathname === item.href ||
                    (item.href !== '/' && pathname?.startsWith(`${item.href}/`)),
                  )
                  const button = (
                    <Button
                      variant="ghost"
                      className={cn(
                        'sidebar-menu-item h-auto min-h-11 w-full gap-2.5 rounded-lg border border-transparent py-2 text-sidebar-foreground',
                        isActive &&
                          'border-sidebar-primary/35 bg-sidebar-accent text-sidebar-accent-foreground shadow-[inset_3px_0_0_var(--sidebar-primary)]',
                        isCollapsed ? 'justify-center px-2' : 'justify-start px-3'
                      )}
                    >
                      <item.icon
                        className={cn(
                          'h-4 w-4 shrink-0',
                          isActive ? 'text-sidebar-primary' : 'text-sidebar-foreground/75'
                        )}
                        aria-hidden="true"
                      />
                      {!isCollapsed && (
                        <span className="min-w-0 flex-1 whitespace-normal break-words text-left leading-5">
                          {item.name}
                        </span>
                      )}
                      {!isCollapsed &&
                        item.href === '/legal-import' &&
                        pendingLegalReviews > 0 && (
                          <span className="ml-auto rounded-full bg-sidebar-primary px-2 py-0.5 text-[10px] font-bold text-sidebar-primary-foreground">
                            {pendingLegalReviews}
                          </span>
                        )}
                    </Button>
                  )

                  if (isCollapsed) {
                    return (
                      <Tooltip key={item.name}>
                        <TooltipTrigger asChild>
                          <Link
                            href={item.href}
                            scroll={false}
                            prefetch={true}
                            onClick={onNavigate}
                          >
                            {button}
                          </Link>
                        </TooltipTrigger>
                        <TooltipContent side="right">
                          {item.name}
                        </TooltipContent>
                      </Tooltip>
                    )
                  }

                  return (
                    <Link
                      key={item.name}
                      href={item.href}
                      scroll={false}
                      prefetch={true}
                      onClick={onNavigate}
                    >
                      {button}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div
          className={cn(
            'shrink-0 space-y-3 border-t border-sidebar-border bg-black/10 p-3',
            isCollapsed && 'px-2'
          )}
        >
          {/* Command Palette hint */}
          {!isCollapsed && (
            <div className="rounded-xl border border-sidebar-primary/35 bg-sidebar-accent/65 px-3 py-2.5 text-xs text-sidebar-foreground">
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
                  <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-sidebar-primary">
                    {t('auth.currentRole')}
                  </div>
                  <div className="mt-0.5 truncate font-semibold">
                    {role === 'admin'
                      ? t('auth.admin')
                      : role === 'officer'
                        ? t('auth.officer')
                        : t('auth.citizen')}
                  </div>
                  {username && (
                    <div className="mt-0.5 truncate text-[11px] text-sidebar-foreground/60">
                      @{username}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {!isCollapsed && (
            <div className="px-1 py-1 text-xs text-sidebar-foreground/70">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <Command className="h-3 w-3" />
                  {t('common.quickActions')}
                </span>
                <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border border-sidebar-border bg-sidebar-foreground/10 px-1.5 font-mono text-[10px] font-medium text-sidebar-foreground/80">
                  {isMac ? (
                    <span className="text-xs">⌘</span>
                  ) : (
                    <span>Ctrl+</span>
                  )}
                  K
                </kbd>
              </div>
              <p className="mt-1 text-[10px] text-sidebar-foreground/50">
                {t('common.quickActionsDesc')}
              </p>
            </div>
          )}

          {isCollapsed ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  className="w-full justify-center border-sidebar-border bg-transparent text-sidebar-foreground hover:border-sidebar-primary/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground sidebar-menu-item"
                  onClick={logout}
                  aria-label={t('common.signOut')}
                >
                  <LogOut className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">
                {t('common.signOut')}
              </TooltipContent>
            </Tooltip>
          ) : (
            <Button
              variant="outline"
              className="w-full justify-start gap-3 border-sidebar-border bg-transparent text-sidebar-foreground hover:border-sidebar-primary/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground sidebar-menu-item"
              onClick={logout}
              aria-label={t('common.signOut')}
            >
              <LogOut className="h-4 w-4" />
              {t('common.signOut')}
            </Button>
          )}
        </div>
      </div>
    </TooltipProvider>
  )
}
