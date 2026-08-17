'use client'

import { useAuthStore } from '@/lib/stores/auth-store'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { ADMIN_LANDING_PATH } from '@/lib/navigation/capabilities'

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
    setupTotp,
    confirmTotp,
    mfaChallenge,
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

  const finishLogin = () => {
    const state = useAuthStore.getState()
    const actualRole = state.role || 'citizen'

    if (state.mustChangePassword) {
      router.push('/change-password')
      return
    }

    if (actualRole === 'admin') {
      sessionStorage.removeItem('redirectAfterLogin')
      router.push(ADMIN_LANDING_PATH)
      return
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
    } else {
      router.push('/search')
    }
  }

  const handleLogin = async (identifier: string, password: string, totpCode?: string) => {
    const success = await login(identifier, password, totpCode)
    if (!success) return false

    finishLogin()
    return true
  }

  const handleConfirmTotp = async (confirmToken: string, code: string) => {
    const success = await confirmTotp(confirmToken, code)
    if (!success) return false
    finishLogin()
    return true
  }

  const handleLogout = async () => {
    await logout()
    router.push('/login')
  }

  return {
    isAuthenticated,
    isLoading: isLoading || !hasHydrated,
    error,
    role,
    mustChangePassword,
    mfaChallenge,
    login: handleLogin,
    setupTotp,
    confirmTotp: handleConfirmTotp,
    logout: handleLogout,
  }
}
