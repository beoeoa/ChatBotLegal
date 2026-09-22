'use client'

import { useEffect, useState } from 'react'
import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'

export interface OperatingScope {
  mode: string
  primary_organization_unit_id?: string | null
  primary_organization_unit_name?: string | null
  domains: string[]
  domain_labels?: Record<string, string>
  can_manage_content: boolean
  can_receive_support?: boolean
  proposal_units: Array<{ id: string; name: string; domains: string[] }>
}

// Refetch on focus and during an open officer session: grant expiry and unit
// suspension must be visible without logging out. Server authorization remains
// authoritative on every write; this is only a read-only UI projection.
export function useOperatingScope() {
  const role = useAuthStore((state) => state.role)
  const userId = useAuthStore((state) => state.userId)
  const [scope, setScope] = useState<OperatingScope | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    setScope(null)
    if (role !== 'officer') return
    let active = true
    let pending = false
    const refresh = async () => {
      if (pending) return
      pending = true
      try {
        const response = await apiClient.get<OperatingScope>('/users/me/operating-scope')
        if (active) { setScope(response.data); setError('') }
      } catch {
        if (active) { setScope(null); setError('Chưa xác nhận được quyền phòng ban. Vui lòng thử lại.') }
      } finally { pending = false }
    }
    void refresh()
    const onFocus = () => { void refresh() }
    const timer = window.setInterval(onFocus, 30_000)
    window.addEventListener('focus', onFocus)
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', onFocus) }
  }, [role, userId])
  return { scope, error }
}
