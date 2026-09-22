'use client'

import { useEffect, useState } from 'react'
import Image from 'next/image'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ChevronDown, LogOut, Menu, Plus, ShieldCheck, UserRound } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { ThemeToggle } from '@/components/common/ThemeToggle'
import { LanguageToggle } from '@/components/common/LanguageToggle'
import { useAuth } from '@/lib/hooks/use-auth'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import { useAuthStore } from '@/lib/stores/auth-store'
import { createActionsForRole, navigationForRole } from '@/lib/navigation/capabilities'
import { getConfig } from '@/lib/config'
import { cn } from '@/lib/utils'

export function AppTopbar() {
  const pathname = usePathname()
  const role = useAuthStore((state) => state.role)
  const username = useAuthStore((state) => state.username)
  const { logout } = useAuth()
  const { openSourceDialog, openNotebookDialog } = useCreateDialogs()
  const navigation = navigationForRole(role)
  const createActions = createActionsForRole(role)
  const [systemName, setSystemName] = useState('Pháp luật Hải Phòng')
  const isSearchWorkspace = pathname === '/search'

  useEffect(() => {
    let cancelled = false
    const loadBranding = () => {
      void getConfig().then((config) => {
        if (!cancelled && config.systemName) setSystemName(config.systemName)
      }).catch(() => undefined)
    }
    loadBranding()
    window.addEventListener('system-settings-updated', loadBranding)
    return () => {
      cancelled = true
      window.removeEventListener('system-settings-updated', loadBranding)
    }
  }, [])

  const isActive = (href: string) => pathname === href || (href !== '/' && pathname?.startsWith(`${href}/`))
  const roleLabel = role === 'admin' ? 'Quản trị viên' : role === 'officer' ? 'Cán bộ' : 'Người dân'

  if (isSearchWorkspace) return null

  return (
    <header className="relative hidden min-h-16 shrink-0 items-center justify-between border-b border-border/70 bg-card/95 px-4 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-card/85 lg:flex xl:px-6">
      <div className="flex min-w-[12rem] items-center gap-2">
        {!isSearchWorkspace && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" className="h-10 gap-2 rounded-full px-3">
                <Menu className="h-4 w-4" aria-hidden="true" />
                Chức năng
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="max-h-[75vh] w-72 overflow-y-auto">
              {navigation.map((group, index) => (
                <div key={group.id}>
                  {index > 0 && <DropdownMenuSeparator />}
                  <DropdownMenuLabel>{group.title}</DropdownMenuLabel>
                  {group.items.map((item) => (
                    <DropdownMenuItem key={item.id} asChild className={cn('gap-3 py-2.5', isActive(item.href) && 'bg-primary/10 text-primary')}>
                      <Link href={item.href}>
                        <item.icon className="h-4 w-4" aria-hidden="true" />
                        {item.name}
                      </Link>
                    </DropdownMenuItem>
                  ))}
                </div>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {!isSearchWorkspace && createActions.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button className="h-10 gap-2 rounded-full px-3"><Plus className="h-4 w-4" aria-hidden="true" />Tạo mới</Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-52">
              {createActions.map((action) => (
                <DropdownMenuItem key={action.id} className="gap-3 py-2.5" onSelect={() => action.id === 'source' ? openSourceDialog() : openNotebookDialog()}>
                  <action.icon className="h-4 w-4" aria-hidden="true" />{action.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <Link href={role === 'admin' ? '/admin' : role === 'officer' ? '/officer-dashboard' : '/search'} className="absolute left-1/2 flex max-w-[40%] -translate-x-1/2 items-center gap-2.5 rounded-full px-3 py-1.5 hover:bg-muted">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 p-1 ring-1 ring-primary/20">
          <Image src="/logo.svg" alt={systemName} width={34} height={34} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-base font-bold text-foreground">{systemName}</span>
          <span className="hidden truncate text-[11px] text-muted-foreground xl:block">Cổng tra cứu pháp luật</span>
        </span>
      </Link>

      <div className="flex min-w-[12rem] items-center justify-end gap-1">
        {!isSearchWorkspace && (
          <>
            <ThemeToggle iconOnly className="h-10 w-10 rounded-full text-foreground hover:bg-muted" />
            <LanguageToggle iconOnly className="h-10 w-10 rounded-full text-foreground hover:bg-muted" />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="h-10 gap-2 rounded-full px-2.5" aria-label="Mở menu tài khoản">
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground"><ShieldCheck className="h-4 w-4" aria-hidden="true" /></span>
                  <span className="hidden max-w-28 truncate text-sm font-semibold xl:inline">{username || roleLabel}</span>
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel><span className="block text-sm">{roleLabel}</span>{username && <span className="block truncate text-xs font-normal text-muted-foreground">@{username}</span>}</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild className="gap-2 py-2.5"><Link href="/account"><UserRound className="h-4 w-4" />Tài khoản của tôi</Link></DropdownMenuItem>
                <DropdownMenuItem className="gap-2 py-2.5 text-destructive" onSelect={logout}><LogOut className="h-4 w-4" />Đăng xuất</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      </div>
    </header>
  )
}
