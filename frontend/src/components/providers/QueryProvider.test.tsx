import { act, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { queryClient } from '@/lib/api/query-client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { QueryProvider } from './QueryProvider'

describe('QueryProvider account isolation', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ userId: 'user-a' })
  })

  it('clears account-scoped responses when the authenticated user changes', async () => {
    render(<QueryProvider><div>content</div></QueryProvider>)
    queryClient.setQueryData(['notebooks', 'notebook:private'], { name: 'Private' })

    act(() => useAuthStore.setState({ userId: 'user-b' }))

    await waitFor(() => {
      expect(queryClient.getQueryData(['notebooks', 'notebook:private'])).toBeUndefined()
    })
  })
})
