'use client'

import { useState } from 'react'
import { Menu, X } from 'lucide-react'
import { AppSidebar } from './AppSidebar'
import { SetupBanner } from './SetupBanner'
import { Button } from '@/components/ui/button'
import { useSidebarStore } from '@/lib/stores/sidebar-store'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const setCollapsed = useSidebarStore((state) => state.setCollapsed)

  const openMobileSidebar = () => {
    setCollapsed(false)
    setMobileSidebarOpen(true)
  }

  return (
    <div className="flex h-screen min-w-0 overflow-hidden">
      <div className="hidden h-full shrink-0 md:block">
        <AppSidebar />
      </div>

      <Button
        type="button"
        variant="outline"
        size="icon"
        onClick={openMobileSidebar}
        className="fixed left-3 top-3 z-40 bg-background/95 shadow-sm backdrop-blur md:hidden"
        aria-label="Mở điều hướng"
        aria-expanded={mobileSidebarOpen}
      >
        <Menu className="h-5 w-5" aria-hidden="true" />
      </Button>

      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-label="Điều hướng">
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

      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <SetupBanner />
        {children}
      </main>
    </div>
  )
}
