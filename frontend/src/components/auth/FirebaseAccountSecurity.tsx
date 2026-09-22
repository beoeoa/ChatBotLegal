'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  Check,
  KeyRound,
  LockKeyhole,
  Mail,
  MailCheck,
  ShieldCheck,
} from 'lucide-react'
import { useRouter } from 'next/navigation'
import {
  onAuthStateChanged,
  sendEmailVerification,
  sendPasswordResetEmail,
  verifyBeforeUpdateEmail,
} from '@firebase/auth'
import type { User } from '@firebase/auth'
import { firebaseEnabled, getFirebaseAuth } from '@/lib/firebase/client'
import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { formatApiError } from '@/lib/utils/error-handler'

function firebaseErrorMessage(error: unknown): string {
  const code = error && typeof error === 'object' && 'code' in error
    ? String((error as { code?: string }).code)
    : ''
  if (code === 'auth/requires-recent-login') return 'Vì lý do bảo mật, hãy đăng nhập lại rồi thực hiện thao tác này.'
  if (code === 'auth/email-already-in-use') return 'Email mới đang được sử dụng bởi tài khoản khác.'
  if (code === 'auth/invalid-email') return 'Email mới không hợp lệ.'
  if (code === 'auth/operation-not-allowed') return 'Chức năng email chưa được bật trong dịch vụ xác thực tài khoản.'
  if (code === 'auth/too-many-requests') return 'Bạn thao tác quá nhiều lần. Hãy thử lại sau ít phút.'
  return 'Không thể hoàn tất thao tác xác thực tài khoản. Vui lòng thử lại.'
}

function accountErrorMessage(error: unknown): string {
  return formatApiError(error, firebaseErrorMessage(error))
}

export function FirebaseAccountSecurity() {
  const accountEmail = useAuthStore((state) => state.email)
  const userId = useAuthStore((state) => state.userId)
  const logout = useAuthStore((state) => state.logout)
  const router = useRouter()
  const [firebaseUser, setFirebaseUser] = useState<User | null>(null)
  const [newEmail, setNewEmail] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [passwordChanged, setPasswordChanged] = useState(false)

  useEffect(() => {
    if (!firebaseEnabled) return undefined
    return onAuthStateChanged(getFirebaseAuth(), setFirebaseUser)
  }, [])

  const email = useMemo(() => firebaseUser?.email || accountEmail || '', [accountEmail, firebaseUser])
  const isFirebaseAccount = Boolean(firebaseUser)
  const isServerAccount = !isFirebaseAccount && Boolean(userId)
  const supportsPassword = Boolean(firebaseUser?.providerData.some((provider) => provider.providerId === 'password'))

  const run = async (action: () => Promise<void>, successMessage: string): Promise<boolean> => {
    setBusy(true)
    setNotice(null)
    setError(null)
    try {
      await action()
      setNotice(successMessage)
      return true
    } catch (actionError) {
      setError(accountErrorMessage(actionError))
      return false
    } finally {
      setBusy(false)
    }
  }

  const handleVerifyEmail = () => {
    if (!firebaseUser) {
      setError('Chức năng xác minh email chỉ áp dụng cho tài khoản đăng nhập bằng dịch vụ bên ngoài. Tài khoản nội bộ không dùng cách này.')
      return
    }
    if (firebaseUser.emailVerified) {
      setNotice('Email của bạn đã được xác minh.')
      setError(null)
      return
    }
    void run(
      () => sendEmailVerification(firebaseUser, {
        url: `${window.location.origin}/account`,
        handleCodeInApp: false,
      }),
      'Đã gửi email xác minh. Hãy mở liên kết trong hộp thư để hoàn tất.',
    )
  }

  const handlePasswordReset = () => {
    if (!email) {
      setError('Không tìm thấy email tài khoản.')
      return
    }
    if (!supportsPassword) {
      setError('Tài khoản Google không có mật khẩu riêng trong ứng dụng. Hãy quản lý mật khẩu tại Google.')
      return
    }
    void run(
      () => sendPasswordResetEmail(getFirebaseAuth(), email, {
        url: `${window.location.origin}/login`,
        handleCodeInApp: false,
      }),
      'Đã gửi liên kết đặt lại mật khẩu tới email của bạn.',
    )
  }

  const handleServerPasswordChange = (event: React.FormEvent) => {
    event.preventDefault()
    if (!currentPassword) {
      setError('Vui lòng nhập mật khẩu hiện tại.')
      return
    }
    if (newPassword.length < 12) {
      setError('Mật khẩu mới phải có ít nhất 12 ký tự.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('Mật khẩu mới và phần xác nhận không khớp.')
      return
    }
    void run(
      async () => {
        await apiClient.post('/users/me/change-password', {
          current_password: currentPassword,
          new_password: newPassword,
        })
        setCurrentPassword('')
        setNewPassword('')
        setConfirmPassword('')
        setPasswordChanged(true)
      },
      'Đã đổi mật khẩu. Vì lý do bảo mật, phiên hiện tại đã được thu hồi; hãy đăng nhập lại.',
    )
  }

  const handleRelogin = async () => {
    await logout()
    router.push('/login')
  }

  const handleChangeEmail = (event: React.FormEvent) => {
    event.preventDefault()
    if (!firebaseUser) {
      setError('Chỉ tài khoản đăng nhập trực tuyến mới có thể đổi email bằng chức năng này.')
      return
    }
    if (!newEmail.trim() || newEmail.trim().toLowerCase() === email.toLowerCase()) {
      setError('Vui lòng nhập một email mới khác email hiện tại.')
      return
    }
    void run(
      () => verifyBeforeUpdateEmail(firebaseUser, newEmail.trim(), {
        url: `${window.location.origin}/account`,
        handleCodeInApp: false,
      }),
      'Đã gửi liên kết xác nhận tới email mới. Email chỉ được thay đổi sau khi bạn bấm liên kết đó.',
    ).then((success) => {
      if (success) setNewEmail('')
    })
  }

  return (
      <Card className="border-border/80 bg-card/90 shadow-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-primary" />
          Bảo mật tài khoản
        </CardTitle>
        <CardDescription>
          Quản lý mật khẩu và email theo đúng phương thức đăng nhập của tài khoản. Các thao tác nhạy cảm luôn yêu cầu xác thực lại.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {isServerAccount && (
          <form onSubmit={handleServerPasswordChange} className="rounded-xl border bg-background/70 p-4" data-testid="server-password-form">
            <div className="flex items-start gap-3">
              <LockKeyhole className="mt-0.5 h-5 w-5 text-primary" />
              <div>
                <p className="text-sm font-medium">Đổi mật khẩu tài khoản nội bộ</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">Áp dụng cho công dân, cán bộ và quản trị viên đăng nhập bằng tài khoản của hệ thống.</p>
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <Input type="password" placeholder="Mật khẩu hiện tại" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} disabled={busy} autoComplete="current-password" aria-label="Mật khẩu hiện tại" />
              <Input type="password" placeholder="Mật khẩu mới (tối thiểu 12 ký tự)" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} disabled={busy} autoComplete="new-password" aria-label="Mật khẩu mới" />
              <Input type="password" placeholder="Xác nhận mật khẩu mới" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} disabled={busy} autoComplete="new-password" aria-label="Xác nhận mật khẩu mới" />
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">Sau khi đổi, mọi phiên đăng nhập cũ sẽ bị thu hồi.</p>
              <Button type="submit" disabled={busy || passwordChanged}>
                <KeyRound className="h-4 w-4" />
                {busy ? 'Đang xử lý...' : passwordChanged ? 'Đã đổi mật khẩu' : 'Đổi mật khẩu'}
              </Button>
            </div>
            {passwordChanged && <Button type="button" variant="outline" className="mt-3" onClick={() => void handleRelogin()}><Check className="h-4 w-4" />Đăng nhập lại</Button>}
          </form>
        )}

        {isFirebaseAccount && <div className="rounded-xl border bg-background/70 p-4">
          <div className="flex items-start gap-3">
            {firebaseUser?.emailVerified ? <MailCheck className="mt-0.5 h-5 w-5 text-emerald-300" /> : <Mail className="mt-0.5 h-5 w-5 text-red-300" />}
            <div className="min-w-0">
              <p className="text-sm font-medium">Email đăng nhập</p>
              <p className="mt-1 truncate text-sm text-muted-foreground">{email || 'Chưa có email'}</p>
              <p className="mt-2 text-xs text-muted-foreground">
                {firebaseUser?.emailVerified ? 'Đã xác minh' : isFirebaseAccount ? 'Chưa xác minh' : 'Tài khoản nội bộ'}
              </p>
            </div>
            <Button type="button" variant="outline" size="sm" className="ml-auto shrink-0" onClick={handleVerifyEmail} disabled={busy || Boolean(firebaseUser?.emailVerified)}>
              {firebaseUser?.emailVerified ? 'Đã xác minh' : 'Gửi email xác minh'}
            </Button>
          </div>
        </div>}

        {isServerAccount && <div className="rounded-xl border bg-background/70 p-4">
          <div className="flex items-start gap-3">
            <Mail className="mt-0.5 h-5 w-5 text-primary" />
            <div className="min-w-0">
              <p className="text-sm font-medium">Email tài khoản nội bộ</p>
              <p className="mt-1 truncate text-sm text-muted-foreground">{email || 'Chưa có email'}</p>
              <p className="mt-2 text-xs text-muted-foreground">Email nội bộ do quản trị viên quản lý để bảo đảm an toàn tài khoản.</p>
            </div>
          </div>
        </div>}

        {isFirebaseAccount && <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border bg-background/70 p-4">
            <div className="flex items-start gap-3">
              <KeyRound className="mt-0.5 h-5 w-5 text-red-300" />
              <div>
                <p className="text-sm font-medium">Đặt lại mật khẩu</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">Nhận liên kết đặt lại mật khẩu qua email hiện tại.</p>
              </div>
            </div>
            <Button type="button" variant="outline" className="mt-4 w-full" onClick={handlePasswordReset} disabled={busy || !supportsPassword}>
              Gửi liên kết đặt lại
            </Button>
          </div>

          <form onSubmit={handleChangeEmail} className="rounded-xl border bg-background/70 p-4">
            <div className="flex items-start gap-3">
              <Mail className="mt-0.5 h-5 w-5 text-red-300" />
              <div>
                <p className="text-sm font-medium">Đổi địa chỉ email</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">Hệ thống sẽ gửi liên kết xác nhận tới email mới trước khi thay đổi.</p>
              </div>
            </div>
            <Input className="mt-4" type="email" value={newEmail} onChange={(event) => setNewEmail(event.target.value)} placeholder="Email mới" disabled={busy} />
            <Button type="submit" className="mt-3 w-full" disabled={busy || !newEmail.trim()}>
              Gửi link xác nhận email mới
            </Button>
          </form>
        </div>}

        {!isFirebaseAccount && !isServerAccount && (
          <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-800 dark:text-amber-200">
            Phiên đăng nhập kiểu cũ không gắn với hồ sơ tài khoản riêng. Hãy đăng nhập bằng username/email để dùng đổi mật khẩu và chỉnh sửa hồ sơ.
          </p>
        )}

        {error && <p className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p>}
        {notice && <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300" role="status">{notice}</p>}
      </CardContent>
    </Card>
  )
}
