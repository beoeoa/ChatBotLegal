'use client'

import Image from 'next/image'
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { usePathname } from 'next/navigation'
import {
  Check,
  Languages,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldCheck,
  Sun,
  UserRound,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { getConfig } from '@/lib/config'
import { useAuth } from '@/lib/hooks/use-auth'
import { useOperatingScope } from '@/lib/hooks/use-operating-scope'
import { useTranslation } from '@/lib/hooks/use-translation'
import { navigationForRole } from '@/lib/navigation/capabilities'
import { useAuthStore, type UserRole } from '@/lib/stores/auth-store'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useTheme, type Theme } from '@/lib/stores/theme-store'

type UserNavigationRole = Exclude<UserRole, 'admin'>

interface UserNavigationSidebarProps {
  role: UserNavigationRole
  onNavigate?: () => void
}

const LANGUAGE_OPTIONS = [
  ['vi-VN', 'Tiếng Việt'],
  ['en-US', 'English'],
  ['zh-CN', '简体中文'],
  ['zh-TW', '繁體中文'],
  ['ja-JP', '日本語'],
  ['fr-FR', 'Français'],
  ['de-DE', 'Deutsch'],
  ['es-ES', 'Español'],
] as const

const preferredIdsForRole = (role: UserNavigationRole) => role === 'officer'
  ? ['officer-dashboard', 'ask', 'sources', 'procedures', 'support', 'notebooks', 'proposals', 'faq']
  : ['ask', 'procedures', 'support']

const ROUTE_ALIASES: Record<string, string[]> = {
  '/legal-library': ['/sources'],
}

export function UserNavigationSidebar({ role, onNavigate }: UserNavigationSidebarProps) {
  const pathname = usePathname()
  const username = useAuthStore((state) => state.username)
  const { logout } = useAuth()
  const { scope: operatingScope } = useOperatingScope()
  const { language, setLanguage } = useTranslation()
  const { theme, setTheme } = useTheme()
  const { isCollapsed, toggleCollapse } = useSidebarStore()
  const [systemName, setSystemName] = useState('Pháp luật Hải Phòng')

  const { primaryNavigation, settingsNavigation } = useMemo(() => {
    const allItems = navigationForRole(role).flatMap((group) => group.items)
      .filter(item => role !== 'officer' || item.id !== 'support' || operatingScope?.can_receive_support === true)
    const preferredIds = preferredIdsForRole(role)
    return {
      primaryNavigation: preferredIds
        .map((id) => allItems.find((item) => item.id === id))
        .filter((item): item is NonNullable<typeof item> => Boolean(item)),
      settingsNavigation: allItems.filter((item) => !preferredIds.includes(item.id) && item.id !== 'account'),
    }
  }, [role, operatingScope])

  useEffect(() => {
    let cancelled = false
    const loadBranding = () => void getConfig()
      .then((config) => {
        if (!cancelled && config.systemName) setSystemName(config.systemName)
      })
      .catch(() => undefined)

    loadBranding()
    window.addEventListener('system-settings-updated', loadBranding)
    return () => {
      cancelled = true
      window.removeEventListener('system-settings-updated', loadBranding)
    }
  }, [])

  const roleLabel = role === 'officer' ? 'Cán bộ' : 'Người dân'
  const isActivePath = (href: string) => [href, ...(ROUTE_ALIASES[href] || [])]
    .some((candidate) => pathname === candidate || (candidate !== '/' && pathname?.startsWith(`${candidate}/`)))

  const settingsMenu = (compact = false) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={cn(
            'h-11 w-11 shrink-0 rounded-full text-foreground hover:bg-muted',
            compact && 'border border-border/70 bg-card shadow-sm',
          )}
          aria-label="Cài đặt và tài khoản"
          title="Cài đặt và tài khoản"
        >
          <Settings className="h-5 w-5" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align={compact ? 'start' : 'end'} side="top" className="w-72">
        <DropdownMenuLabel>Cài đặt và tiện ích</DropdownMenuLabel>
        {settingsNavigation.map((item) => (
          <DropdownMenuItem key={item.id} asChild className="gap-3 py-2.5">
            <Link href={item.href} onClick={onNavigate}>
              <item.icon className="h-4 w-4" aria-hidden="true" />
              {item.name}
            </Link>
          </DropdownMenuItem>
        ))}
        {settingsNavigation.length > 0 && <DropdownMenuSeparator />}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="gap-2 py-2.5">
            <Sun className="h-4 w-4" aria-hidden="true" />
            Giao diện
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-48">
            <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
              <DropdownMenuRadioItem value="light"><Sun className="h-4 w-4" />Sáng</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark"><Moon className="h-4 w-4" />Tối</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="system"><Monitor className="h-4 w-4" />Theo hệ thống</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="gap-2 py-2.5">
            <Languages className="h-4 w-4" aria-hidden="true" />
            Ngôn ngữ
          </DropdownMenuSubTrigger>
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
        <DropdownMenuItem asChild className="gap-2 py-2.5">
          <Link href="/account" onClick={onNavigate}>
            <UserRound className="h-4 w-4" aria-hidden="true" />
            Tài khoản của tôi
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem className="gap-2 py-2.5 text-destructive" onSelect={logout}>
          <LogOut className="h-4 w-4" aria-hidden="true" />
          Đăng xuất
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  if (isCollapsed) {
    return (
      <aside className="flex h-full w-[76px] shrink-0 flex-col items-center border-l border-border/70 bg-card py-2">
        <Link href="/search" onClick={onNavigate} className="flex h-12 w-12 items-center justify-center rounded-2xl border border-border/70 bg-card shadow-sm" title={systemName}>
          <Image src="/logo.svg" alt={systemName} width={32} height={32} />
        </Link>
        <Button type="button" variant="ghost" size="icon" className="mt-2 h-11 w-11 rounded-full" onClick={toggleCollapse} aria-label="Mở thanh bên" title="Mở thanh bên">
          <PanelLeftOpen className="h-5 w-5" aria-hidden="true" />
        </Button>
        <nav className="mt-2 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto" aria-label="Chức năng chính">
          {primaryNavigation.map((item) => (
            <Button
              key={item.id}
              asChild
              variant="ghost"
              size="icon"
              title={item.name}
              className={cn('h-11 w-11 shrink-0 rounded-full', isActivePath(item.href) && 'bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary')}
            >
              <Link href={item.href} onClick={onNavigate} aria-label={item.name}>
                <item.icon className="h-5 w-5" aria-hidden="true" />
              </Link>
            </Button>
          ))}
        </nav>
        <div className="mt-2">{settingsMenu(true)}</div>
      </aside>
    )
  }

  return (
    <aside className="flex h-full w-[248px] max-w-full shrink-0 flex-col border-l border-border/70 bg-card">
      <div className="shrink-0 p-2 pb-0">
        <div className="flex h-14 items-center gap-2 rounded-2xl border border-border/70 bg-card px-2.5 shadow-sm">
          <Link href="/search" onClick={onNavigate} className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl py-1">
            <Image src="/logo.svg" alt={systemName} width={34} height={34} className="shrink-0" />
            <span className="min-w-0">
              <span className="block truncate text-sm font-bold">{systemName}</span>
              <span className="block truncate text-[10px] text-muted-foreground">Trợ lý pháp luật</span>
            </span>
          </Link>
          <Button type="button" variant="ghost" size="icon" className="hidden h-11 w-11 shrink-0 rounded-full lg:inline-flex" onClick={toggleCollapse} aria-label="Thu gọn thanh bên" title="Thu gọn thanh bên">
            <PanelLeftClose className="h-5 w-5" aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-b border-border/70 px-2 pb-3 pt-3">
        <div className="px-3 pb-2">
          <p className="text-base font-semibold">Chức năng</p>
          <p className="text-xs text-muted-foreground">Dành cho {roleLabel.toLocaleLowerCase('vi-VN')}</p>
        </div>
        <nav className="grid grid-cols-1 gap-0.5" aria-label="Chức năng chính">
          {primaryNavigation.map((item) => (
            <Button
              key={item.id}
              asChild
              variant="ghost"
              title={item.name}
              className={cn(
                'h-auto min-h-11 min-w-0 justify-start gap-2.5 rounded-lg px-3 py-2.5 text-sm',
                isActivePath(item.href) && 'bg-primary/10 font-semibold text-primary hover:bg-primary/15 hover:text-primary',
              )}
            >
                <Link href={item.href} scroll={false} onClick={onNavigate} aria-current={isActivePath(item.href) ? 'page' : undefined}>
                <item.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="whitespace-normal text-left">{item.name}</span>
              </Link>
            </Button>
          ))}
        </nav>
      </div>

      <div className="shrink-0 bg-card/70 p-2">
        <div className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-full px-2 py-1">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-xs font-semibold">{username || roleLabel}</span>
              <span className="block truncate text-[10px] text-muted-foreground">{roleLabel}</span>
            </span>
          </div>
          {settingsMenu()}
        </div>
      </div>
    </aside>
  )
}
