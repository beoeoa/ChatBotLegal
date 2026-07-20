'use client'

import { useAuthStore } from '@/lib/stores/auth-store'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

const ROLE_ALLOWED_PATHS = {
  citizen: ['/search', '/procedures', '/legal-docs', '/legal-documents', '/live-support'],
  officer: ['/search', '/sources', '/notebooks', '/procedures', '/legal-docs', '/legal-documents', '/live-support', '/officer-proposals'],
} as const

export function useAuth() {
  const router = useRouter()
  const {
    isAuthenticated,
    isLoading,
    login,
    logout,
    checkAuth,
    checkAuthRequired,
    error,
    hasHydrated,
    authRequired,
  } = useAuthStore()
  const role = useAuthStore((state) => state.role)
  const mustChangePassword = useAuthStore((state) => state.mustChangePassword)

  useEffect(() => {
    if (!hasHydrated) return

    if (authRequired === null) {
      checkAuthRequired().then((required) => {
        if (required) {
          checkAuth()
        }
      })
    } else if (authRequired) {
      checkAuth()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasHydrated, authRequired])

  const handleLogin = async (identifier: string, password: string) => {
    const success = await login(identifier, password)
    if (!success) return false

    const state = useAuthStore.getState()
    const actualRole = state.role || 'citizen'

    if (state.mustChangePassword) {
      router.push('/change-password')
      return true
    }

    const redirectPath = sessionStorage.getItem('redirectAfterLogin')
    if (redirectPath) {
      sessionStorage.removeItem('redirectAfterLogin')
      const allowedPaths = actualRole === 'citizen' || actualRole === 'officer'
        ? ROLE_ALLOWED_PATHS[actualRole]
        : null
      if (
        allowedPaths &&
        !allowedPaths.some((path) => redirectPath.startsWith(path))
      ) {
        router.push('/search')
      } else {
        router.push(redirectPath)
      }
    } else if (actualRole === 'admin') {
      router.push('/notebooks')
    } else {
      router.push('/search')
    }

    return true
  }

  const handleLogout = () => {
    logout()
    router.push('/login')
  }

  return {
    isAuthenticated,
    isLoading: isLoading || !hasHydrated,
    error,
    role,
    mustChangePassword,
    login: handleLogin,
    logout: handleLogout,
  }
}
