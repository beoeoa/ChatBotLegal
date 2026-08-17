'use client'

import { QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { queryClient } from '@/lib/api/query-client'
import { useAuthStore } from '@/lib/stores/auth-store'

interface QueryProviderProps {
  children: React.ReactNode
}

export function QueryProvider({ children }: QueryProviderProps) {
  const userId = useAuthStore((state) => state.userId)
  const previousUserId = useRef(userId)

  useEffect(() => {
    if (previousUserId.current !== userId) {
      // Legal profiles, notes, sources and chat sessions are account-scoped.
      // Never reuse a successful response after login changes the account.
      queryClient.clear()
      previousUserId.current = userId
    }
  }, [userId])

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  )
}
