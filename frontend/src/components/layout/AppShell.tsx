'use client'

import { useState } from 'react'
import { usePathname } from 'next/navigation'
import { Menu, X, ShieldCheck } from 'lucide-react'
import { AppSidebar } from './AppSidebar'
import { SetupBanner } from './SetupBanner'
import { Button } from '@/components/ui/button'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useAuthStore } from '@/lib/stores/auth-store'
import { cn } from '@/lib/utils'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const pathname = usePathname()
  const role = useAuthStore((state) => state.role)
  const setCollapsed = useSidebarStore((state) => state.setCollapsed)
  const userNavigation = role === 'citizen' || role === 'officer'
  const usesConversationNavigation = pathname === '/search'
  // The Q&A route owns the conversation workspace sidebar. Every other
  // protected route gets the same primary navigation shell as Admin, so
  // feature pages open in the main area instead of switching to a different
  // topbar layout.
  // The Q&A page owns the citizen/officer conversation sidebar. Rendering the
  // role navigation here as well creates two permanent left rails on desktop.
  // Keep the primary navigation for every other protected page.
  const usesPrimaryNavigation = Boolean(role) && !usesConversationNavigation

  const openMobileSidebar = () => {
    setCollapsed(false)
    setMobileSidebarOpen(true)
  }

  return (
    <div className={cn('flex h-dvh min-w-0 overflow-hidden bg-background', userNavigation && 'user-workspace')}>
      <a
        href="#main-content"
        className="sr-only z-[60] rounded-md bg-card px-4 py-2 text-sm font-semibold text-foreground shadow-lg focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:outline-none focus:ring-2 focus:ring-ring"
      >
        Chuyển đến nội dung chính
      </a>
      {usesPrimaryNavigation && (
        <div className="hidden h-full shrink-0 lg:block">
          <AppSidebar />
        </div>
      )}
      {mobileSidebarOpen && usesPrimaryNavigation && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-label="Điều hướng">
          <button
            type="button"
            aria-label="Đóng điều hướng"
            className="absolute inset-0 bg-black/45"
            onClick={() => setMobileSidebarOpen(false)}
          />
          <div className="relative h-full w-72 max-w-[86vw] bg-sidebar shadow-2xl">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-2 top-2 z-10"
              onClick={() => setMobileSidebarOpen(false)}
              aria-label="Đóng điều hướng"
            >
              <X className="h-5 w-5" aria-hidden="true" />
            </Button>
            <AppSidebar onNavigate={() => setMobileSidebarOpen(false)} />
          </div>
        </div>
      )}

      <main id="main-content" className="app-main-gradient flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <SetupBanner />
        <header className={cn(
          'min-h-16 shrink-0 items-center justify-between gap-4 border-b border-border/70 bg-card/90 px-4 py-3 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-card/75 md:px-7 lg:hidden',
          usesPrimaryNavigation ? 'flex' : 'hidden',
        )}>
          <div className="flex min-w-0 items-center gap-3">
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={openMobileSidebar}
              className="h-11 w-11 shrink-0 lg:hidden"
              aria-label="Mở điều hướng"
              aria-expanded={mobileSidebarOpen}
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </Button>
            <div className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary sm:flex">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground md:text-base">Không gian pháp luật số</p>
              <p className="hidden truncate text-xs text-muted-foreground sm:block">Tra cứu có căn cứ · Hỗ trợ đúng quy trình</p>
            </div>
          </div>
          <div className="hidden items-center gap-2 text-xs text-muted-foreground sm:flex">
            <span className="h-2 w-2 rounded-full bg-emerald-600" aria-hidden="true" />
            Hệ thống đang hoạt động
          </div>
        </header>
        {children}
      </main>
    </div>
  )
}
