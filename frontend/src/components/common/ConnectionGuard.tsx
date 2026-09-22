'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { ConnectionError } from '@/lib/types/config'
import { ConnectionErrorOverlay } from '@/components/errors/ConnectionErrorOverlay'
import { getConfig, resetConfig } from '@/lib/config'

interface ConnectionGuardProps {
  children: React.ReactNode
}

export function ConnectionGuard({ children }: ConnectionGuardProps) {
  const [error, setError] = useState<ConnectionError | null>(null)
  const [isChecking, setIsChecking] = useState(true)
  // Use a ref to track checking status to avoid dependency cycles
  const isCheckingRef = useRef(false)

  const checkConnection = useCallback(async (force = false) => {
    // Prevent re-entry if already checking
    if (isCheckingRef.current) {
       return
    }
    
    isCheckingRef.current = true
    setIsChecking(true)
    
    setError(null)

    // Mount shares the in-flight configuration request with authentication.
    // Only an explicit retry should invalidate it.
    if (force) resetConfig()

    try {
      const config = await getConfig()

      // Check if database is offline
      if (config.dbStatus === 'offline') {
        const dbError: ConnectionError = {
          type: 'database-offline',
          details: {
            message: 'Kho dữ liệu đang tạm thời không sẵn sàng.',
          },
        }
        setError(dbError)
        isCheckingRef.current = false
        setIsChecking(false)
        return
      }

      // If we got here, connection is good
      setError(null)
      isCheckingRef.current = false
      setIsChecking(false)
    } catch {
      // API is unreachable
      const apiError: ConnectionError = {
        type: 'api-unreachable',
        details: {
          message: 'Không thể kết nối tới hệ thống lúc này.',
        },
      }
      
      setError(apiError)
      isCheckingRef.current = false
      setIsChecking(false)
    }
  }, []) // Empty dependency array - stable callback

  // Check connection on mount
  useEffect(() => {
    checkConnection()
  }, [checkConnection])

  // Add keyboard shortcut for retry (R key)
  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      if (error && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault()
        void checkConnection(true)
      }
    }

    window.addEventListener('keydown', handleKeyPress)
    return () => window.removeEventListener('keydown', handleKeyPress)
  }, [error, checkConnection])

  // Show overlay if there's an error
  if (error) {
    return <ConnectionErrorOverlay error={error} onRetry={() => void checkConnection(true)} />
  }

  // Keep the app shell interactive while the connection check runs. The
  // protected dashboard still gates its data behind authentication, while
  // the login page can render immediately instead of showing a blank screen.
  if (isChecking) {
    return <>{children}</>
  }

  // Render children if connection is good
  return <>{children}</>
}
