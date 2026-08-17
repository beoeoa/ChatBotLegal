'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { ADMIN_LANDING_PATH } from '@/lib/navigation/capabilities'
import { useAuthStore } from '@/lib/stores/auth-store'

export default function DashboardPage() {
  const router = useRouter()
  const role = useAuthStore((state) => state.role)

  useEffect(() => {
    if (!role) return
    router.replace(role === 'admin' ? ADMIN_LANDING_PATH : '/search')
  }, [role, router])

  return <div className="flex min-h-screen items-center justify-center"><LoadingSpinner /></div>
}
