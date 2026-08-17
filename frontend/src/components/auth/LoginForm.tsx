'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { AlertCircle, KeyRound, LogIn, UserPlus } from 'lucide-react'
import { useAuth } from '@/lib/hooks/use-auth'
import { useAuthStore } from '@/lib/stores/auth-store'
import type { TotpSetup } from '@/lib/stores/auth-store'
import { getApiUrl, getConfig } from '@/lib/config'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

type AuthMode = 'login' | 'register' | 'forgot' | 'reset'

type ApiMessage = {
  detail?: string
  message?: string
  reset_token?: string
  expires_in_minutes?: number
}

async function readApiMessage(response: Response): Promise<string> {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string') return data.detail
    if (Array.isArray(data?.detail)) return data.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join('; ')
    if (typeof data?.message === 'string') return data.message
  } catch {
    // keep fallback below
  }
  return `Yêu cầu thất bại (${response.status})`
}

export function LoginForm() {
  const [mode, setMode] = useState<AuthMode>('login')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [totpSetup, setTotpSetup] = useState<TotpSetup | null>(null)
  const [registerUsername, setRegisterUsername] = useState('')
  const [registerEmail, setRegisterEmail] = useState('')
  const [registerFullName, setRegisterFullName] = useState('')
  const [registerPhone, setRegisterPhone] = useState('')
  const [registerPassword, setRegisterPassword] = useState('')
  const [forgotIdentifier, setForgotIdentifier] = useState('')
  const [resetToken, setResetToken] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isCheckingAuth, setIsCheckingAuth] = useState(true)
  const [configInfo, setConfigInfo] = useState<{ apiUrl: string; version: string; buildTime: string } | null>(null)

  const router = useRouter()
  const {
    login,
    isLoading,
    error,
    mfaChallenge,
    setupTotp,
    confirmTotp,
  } = useAuth()
  const { authRequired, checkAuthRequired, hasHydrated, isAuthenticated } = useAuthStore()

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        setConfigInfo({
          apiUrl: cfg.apiUrl || 'proxy nội bộ',
          version: cfg.version,
          buildTime: cfg.buildTime,
        })
      })
      .catch((err) => {
        console.error('Failed to load config:', err)
      })
  }, [])

  useEffect(() => {
    if (!hasHydrated) return

    const checkAuth = async () => {
      try {
        const required = await checkAuthRequired()
        if (!required && useAuthStore.getState().role) {
          router.push('/search')
        }
      } catch (error) {
        console.error('Error checking auth requirement:', error)
      } finally {
        setIsCheckingAuth(false)
      }
    }

    if (authRequired !== null) {
      if (!authRequired && isAuthenticated && useAuthStore.getState().role) {
        router.push('/search')
      } else {
        setIsCheckingAuth(false)
      }
    } else {
      void checkAuth()
    }
  }, [hasHydrated, authRequired, checkAuthRequired, router, isAuthenticated])

  useEffect(() => {
    if (mfaChallenge?.type !== 'setup' || totpSetup) return
    void setupTotp(mfaChallenge.setupToken).then((result) => {
      if (result) setTotpSetup(result)
    })
  }, [mfaChallenge, setupTotp, totpSetup])

  if (!hasHydrated || isCheckingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <LoadingSpinner />
      </div>
    )
  }

  if (authRequired === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle>Không kết nối được hệ thống</CardTitle>
            <CardDescription>Backend chưa phản hồi hoặc cấu hình API chưa đúng.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-start gap-2 text-sm text-red-600">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>{error || 'Hãy kiểm tra backend local đang chạy ở cổng 5055.'}</div>
            </div>
            {configInfo && (
              <div className="space-y-1 border-t pt-3 font-mono text-xs text-muted-foreground">
                <div>Version: {configInfo.version}</div>
                <div>API: {configInfo.apiUrl}</div>
              </div>
            )}
            <Button onClick={() => window.location.reload()} className="w-full">
              Thử kết nối lại
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const handleLoginSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!identifier.trim() || !password.trim()) {
      setLocalError('Vui lòng nhập username/email và mật khẩu.')
      return
    }
    if (mfaChallenge?.type === 'setup' && totpSetup) {
      if (!totpCode.trim()) {
        setLocalError('Vui lòng nhập mã 6 số từ ứng dụng xác thực.')
        return
      }
      await confirmTotp(totpSetup.confirmToken, totpCode)
      return
    }
    if (mfaChallenge?.type === 'code' && !totpCode.trim()) {
      setLocalError('Vui lòng nhập mã 6 số từ ứng dụng xác thực.')
      return
    }
    await login(identifier, password, totpCode)
  }

  const handleRegisterSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!registerUsername.trim() || !registerEmail.trim() || !registerPassword.trim()) {
      setLocalError('Vui lòng nhập đủ username, email và mật khẩu.')
      return
    }
    setIsSubmitting(true)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: registerUsername.trim(),
          email: registerEmail.trim(),
          password: registerPassword,
          full_name: registerFullName.trim() || null,
          phone: registerPhone.trim() || null,
        }),
      })
      if (!response.ok) {
        setLocalError(await readApiMessage(response))
        return
      }
      const success = await login(registerUsername, registerPassword)
      if (!success) {
        setIdentifier(registerUsername)
        setPassword('')
        setMode('login')
        setNotice('Đăng ký thành công. Hãy đăng nhập bằng tài khoản vừa tạo.')
      }
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Không đăng ký được tài khoản.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleForgotSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!forgotIdentifier.trim()) {
      setLocalError('Vui lòng nhập username hoặc email.')
      return
    }
    setIsSubmitting(true)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: forgotIdentifier.trim() }),
      })
      if (!response.ok) {
        setLocalError(await readApiMessage(response))
        return
      }
      const data = (await response.json()) as ApiMessage
      if (data.reset_token) {
        setResetToken(data.reset_token)
        setMode('reset')
        setNotice(`Hệ thống local đã tạo mã đặt lại mật khẩu, có hiệu lực ${data.expires_in_minutes || 15} phút.`)
      } else {
        setNotice(data.message || 'Nếu tài khoản tồn tại, hệ thống đã tạo hướng dẫn đặt lại mật khẩu.')
      }
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Không tạo được yêu cầu đặt lại mật khẩu.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleResetSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!resetToken.trim() || !resetPassword.trim()) {
      setLocalError('Vui lòng nhập mã đặt lại và mật khẩu mới.')
      return
    }
    setIsSubmitting(true)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken.trim(), new_password: resetPassword }),
      })
      if (!response.ok) {
        setLocalError(await readApiMessage(response))
        return
      }
      setIdentifier(forgotIdentifier)
      setPassword('')
      setResetPassword('')
      setMode('login')
      setNotice('Đã đặt lại mật khẩu. Bạn có thể đăng nhập bằng mật khẩu mới.')
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Không đặt lại được mật khẩu.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const busy = isLoading || isSubmitting
  const visibleError = localError || error

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle>Pháp luật Phường/Xã Hải Phòng</CardTitle>
          <CardDescription>
            {mode === 'login' && 'Đăng nhập bằng tài khoản đã được cấp. Role cán bộ/admin được xác định tự động từ tài khoản.'}
            {mode === 'register' && 'Người dân có thể tự đăng ký tài khoản công dân.'}
            {mode === 'forgot' && 'Nhập username hoặc email để tạo mã đặt lại mật khẩu.'}
            {mode === 'reset' && 'Nhập mã đặt lại và mật khẩu mới.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {mode === 'login' && (
            <form onSubmit={handleLoginSubmit} className="space-y-4">
              <Input
                type="text"
                placeholder="Username hoặc email"
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                disabled={busy}
                autoComplete="username"
              />
              <Input
                type="password"
                placeholder="Mật khẩu"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
                autoComplete="current-password"
              />
              {mfaChallenge?.type === 'setup' && totpSetup && (
                <div className="space-y-2 rounded-md border p-3 text-sm">
                  <p className="font-medium">Thiết lập xác thực hai lớp</p>
                  <p className="text-muted-foreground">
                    Thêm tài khoản vào ứng dụng xác thực bằng khóa dưới đây, sau đó nhập mã 6 số.
                  </p>
                  <code className="block break-all rounded bg-muted p-2 text-xs">
                    {totpSetup.secret}
                  </code>
                </div>
              )}
              {mfaChallenge && (
                <Input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={8}
                  placeholder="Mã xác thực 6 số"
                  value={totpCode}
                  onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, ''))}
                  disabled={busy}
                  autoComplete="one-time-code"
                />
              )}
              <Button type="submit" className="w-full gap-2" disabled={busy || !identifier.trim() || !password.trim()}>
                <LogIn className="h-4 w-4" />
                {busy
                  ? 'Đang xác thực...'
                  : mfaChallenge?.type === 'setup'
                    ? 'Xác nhận và đăng nhập'
                    : mfaChallenge?.type === 'code'
                      ? 'Xác thực và đăng nhập'
                      : 'Đăng nhập'}
              </Button>
            </form>
          )}

          {mode === 'register' && (
            <form onSubmit={handleRegisterSubmit} className="space-y-3">
              <Input placeholder="Username" value={registerUsername} onChange={(event) => setRegisterUsername(event.target.value)} disabled={busy} />
              <Input type="email" placeholder="Email" value={registerEmail} onChange={(event) => setRegisterEmail(event.target.value)} disabled={busy} />
              <Input placeholder="Họ tên" value={registerFullName} onChange={(event) => setRegisterFullName(event.target.value)} disabled={busy} />
              <Input placeholder="Số điện thoại" value={registerPhone} onChange={(event) => setRegisterPhone(event.target.value)} disabled={busy} />
              <Input type="password" placeholder="Mật khẩu" value={registerPassword} onChange={(event) => setRegisterPassword(event.target.value)} disabled={busy} autoComplete="new-password" />
              <Button type="submit" className="w-full gap-2" disabled={busy}>
                <UserPlus className="h-4 w-4" />
                {busy ? 'Đang tạo tài khoản...' : 'Đăng ký công dân'}
              </Button>
            </form>
          )}

          {mode === 'forgot' && (
            <form onSubmit={handleForgotSubmit} className="space-y-4">
              <Input
                placeholder="Username hoặc email"
                value={forgotIdentifier}
                onChange={(event) => setForgotIdentifier(event.target.value)}
                disabled={busy}
              />
              <Button type="submit" className="w-full gap-2" disabled={busy || !forgotIdentifier.trim()}>
                <KeyRound className="h-4 w-4" />
                {busy ? 'Đang tạo mã...' : 'Tạo mã đặt lại mật khẩu'}
              </Button>
            </form>
          )}

          {mode === 'reset' && (
            <form onSubmit={handleResetSubmit} className="space-y-4">
              <Input placeholder="Mã đặt lại mật khẩu" value={resetToken} onChange={(event) => setResetToken(event.target.value)} disabled={busy} />
              <Input type="password" placeholder="Mật khẩu mới (ít nhất 12 ký tự)" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} disabled={busy} autoComplete="new-password" />
              <Button type="submit" className="w-full gap-2" disabled={busy || !resetToken.trim() || !resetPassword.trim()}>
                <KeyRound className="h-4 w-4" />
                {busy ? 'Đang đặt lại...' : 'Đặt lại mật khẩu'}
              </Button>
            </form>
          )}

          {visibleError && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>{visibleError}</div>
            </div>
          )}

          {notice && (
            <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">
              {notice}
            </div>
          )}

          <div className="grid grid-cols-3 gap-2 border-t pt-4 text-xs">
            <Button type="button" variant={mode === 'login' ? 'secondary' : 'ghost'} onClick={() => setMode('login')} disabled={busy}>
              Đăng nhập
            </Button>
            <Button type="button" variant={mode === 'register' ? 'secondary' : 'ghost'} onClick={() => setMode('register')} disabled={busy}>
              Đăng ký
            </Button>
            <Button type="button" variant={mode === 'forgot' || mode === 'reset' ? 'secondary' : 'ghost'} onClick={() => setMode('forgot')} disabled={busy}>
              Quên mật khẩu
            </Button>
          </div>

          {configInfo && (
            <div className="border-t pt-3 text-center text-xs text-muted-foreground">
              <div>Version {configInfo.version}</div>
              <div className="font-mono text-[10px]">{configInfo.apiUrl}</div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
