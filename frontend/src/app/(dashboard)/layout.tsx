'use client'

import { useAuth } from '@/lib/hooks/use-auth'
import { usePathname, useRouter } from 'next/navigation'
import dynamic from 'next/dynamic'
import { useEffect, useState } from 'react'
import { ErrorBoundary } from '@/components/common/ErrorBoundary'
import { ModalProvider } from '@/components/providers/ModalProvider'
import { CreateDialogsProvider } from '@/lib/hooks/use-create-dialogs'
import { getAuthRedirect } from '@/lib/auth/route-guard'

function DashboardLoadingShell() {
  return (
    <div className="flex h-screen min-w-0 flex-col overflow-hidden" aria-busy="true" aria-label="Đang tải hệ thống">
      <header className="hidden h-16 shrink-0 items-center gap-3 border-b bg-card px-6 lg:flex">
        <div className="h-10 w-10 animate-pulse rounded-xl bg-muted" />
        <div className="h-9 w-28 animate-pulse rounded-full bg-muted" />
        <div className="h-9 w-28 animate-pulse rounded-full bg-muted" />
        <div className="ml-auto h-10 w-32 animate-pulse rounded-full bg-muted" />
      </header>
      <main className="flex min-w-0 flex-1 flex-col bg-background">
        <div className="flex-1 space-y-5 overflow-hidden p-4 pt-16 sm:p-6 md:p-8 md:pt-8">
          <div className="h-16 max-w-3xl animate-pulse rounded-xl bg-muted" />
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }, (_, index) => (
              <div key={index} className="h-28 animate-pulse rounded-xl bg-muted" />
            ))}
          </div>
          <div className="h-72 animate-pulse rounded-xl bg-muted" />
        </div>
      </main>
    </div>
  )
}

const CommandPalette = dynamic(
  () => import('@/components/common/CommandPalette').then((module) => module.CommandPalette),
  { ssr: false },
)

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { isAuthenticated, isLoading, role, mustChangePassword } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const [hasCheckedAuth, setHasCheckedAuth] = useState(false)
  const authRedirect = !isLoading && isAuthenticated && role
    ? getAuthRedirect({ isAuthenticated, role, mustChangePassword, pathname })
    : null

  // Check for version updates once per session
  useEffect(() => {
    // Mark that we've completed the initial auth check
    if (!isLoading) {
      setHasCheckedAuth(true)

      if (!isAuthenticated) {
        // Store the current path to redirect back after login
        const currentPath = window.location.pathname + window.location.search
        sessionStorage.setItem('redirectAfterLogin', currentPath)
        router.push('/login')
      } else {
        const redirect = getAuthRedirect({
          isAuthenticated,
          role,
          mustChangePassword,
          pathname,
        })
        if (redirect) router.replace(redirect)
      }
    }
  }, [isAuthenticated, isLoading, role, mustChangePassword, pathname, router])

  // Show the neutral shell during the initial auth check so the page never
  // appears blank. Protected children still wait for authentication below.
  if (isLoading || !hasCheckedAuth) {
    return <DashboardLoadingShell />
  }

  // Don't render anything if not authenticated (during redirect)
  if (!isAuthenticated || !role || authRedirect) {
    return null
  }

  return (
    <ErrorBoundary>
      <CreateDialogsProvider>
        {children}
        <ModalProvider />
        <CommandPalette />
      </CreateDialogsProvider>
    </ErrorBoundary>
  )
}
