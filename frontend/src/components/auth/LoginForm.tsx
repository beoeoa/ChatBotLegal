'use client'

import { useEffect, useRef, useState } from 'react'
import Image from 'next/image'
import { useRouter } from 'next/navigation'
import { AlertCircle, Chrome, Eye, EyeOff, KeyRound, LogIn, UserPlus } from 'lucide-react'
import {
  confirmPasswordReset,
  getRedirectResult,
  GoogleAuthProvider,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signInWithRedirect,
  signOut,
  verifyPasswordResetCode,
} from '@firebase/auth'
import { firebaseEnabled, getFirebaseAuth } from '@/lib/firebase/client'
import { useAuth } from '@/lib/hooks/use-auth'
import { useAuthStore } from '@/lib/stores/auth-store'
import type { FirebaseProfileInput, TotpSetup } from '@/lib/stores/auth-store'
import { getConfig } from '@/lib/config'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { formatApiError } from '@/lib/utils/error-handler'

type AuthMode = 'login' | 'register' | 'forgot' | 'reset'

const authInputClass = 'border-white/15 bg-[#2f3440] text-white placeholder:text-slate-300/75 focus-visible:border-[#e8c875] focus-visible:ring-[#e8c875]/30'
const pendingFirebaseProfileKey = 'firebase-pending-profile'
const firebaseLocalRedirectKey = 'firebase-localhost-redirected'

function readPendingFirebaseProfile(email: string): FirebaseProfileInput | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(pendingFirebaseProfileKey)
    if (!raw) return null
    const value = JSON.parse(raw) as FirebaseProfileInput & { email?: string }
    if (value.email?.trim().toLowerCase() !== email.trim().toLowerCase()) return null
    return {
      ...(value.fullName?.trim() ? { fullName: value.fullName.trim() } : {}),
      ...(value.phone?.trim() ? { phone: value.phone.trim() } : {}),
      ...(['male', 'female', 'unspecified'].includes(value.gender || '') ? { gender: value.gender } : {}),
    }
  } catch {
    return null
  }
}

function clearPendingFirebaseProfile() {
  if (typeof window !== 'undefined') window.localStorage.removeItem(pendingFirebaseProfileKey)
}

function redirect127001ToLocalhost(): boolean {
  if (typeof window === 'undefined' || window.location.hostname !== '127.0.0.1') return false
  if (window.sessionStorage.getItem(firebaseLocalRedirectKey) === '1') return false
  window.sessionStorage.setItem(firebaseLocalRedirectKey, '1')
  const next = new URL(window.location.href)
  next.hostname = 'localhost'
  window.location.assign(next.toString())
  return true
}

function firebaseErrorCode(error: unknown): string {
  if (!error || typeof error !== 'object' || !('code' in error)) return ''
  return String((error as { code?: string }).code || '')
}

function firebasePasswordResetMessage(error: unknown): string {
  const code = firebaseErrorCode(error)
  if (code === 'auth/invalid-email') return 'Email không hợp lệ.'
  if (code === 'auth/operation-not-allowed') return 'Chức năng đăng nhập bằng email và mật khẩu chưa được bật.'
  if (code === 'auth/too-many-requests') return 'Bạn thao tác quá nhiều lần. Hãy thử lại sau ít phút.'
  if (code === 'auth/network-request-failed') return 'Không kết nối được dịch vụ đăng nhập. Hãy kiểm tra mạng hoặc thử lại.'
  if (code === 'auth/expired-action-code') return 'Liên kết đặt lại mật khẩu đã hết hạn. Hãy yêu cầu một liên kết mới.'
  if (code === 'auth/invalid-action-code') return 'Liên kết đặt lại mật khẩu không hợp lệ hoặc đã được sử dụng.'
  if (code === 'auth/user-disabled') return 'Tài khoản này đã bị vô hiệu hóa.'
  if (code === 'auth/weak-password') return 'Mật khẩu mới cần có ít nhất 12 ký tự.'
  if (code === 'auth/api-key-not-valid') return 'Cấu hình dịch vụ đăng nhập không hợp lệ. Vui lòng liên hệ quản trị viên.'
  return 'Không thể hoàn tất đặt lại mật khẩu. Vui lòng thử lại.'
}

export function LoginForm() {
  const [mode, setMode] = useState<AuthMode>('login')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [showLoginPassword, setShowLoginPassword] = useState(false)
  const [totpCode, setTotpCode] = useState('')
  const [totpSetup, setTotpSetup] = useState<TotpSetup | null>(null)
  const [registerUsername, setRegisterUsername] = useState('')
  const [registerEmail, setRegisterEmail] = useState('')
  const [registerFullName, setRegisterFullName] = useState('')
  const [registerGender, setRegisterGender] = useState<'male' | 'female' | 'unspecified'>('unspecified')
  const [registerPhone, setRegisterPhone] = useState('')
  const [registerPassword, setRegisterPassword] = useState('')
  const [registerPasswordConfirmation, setRegisterPasswordConfirmation] = useState('')
  const [forgotIdentifier, setForgotIdentifier] = useState('')
  const [firebaseResetCode, setFirebaseResetCode] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isCheckingAuth, setIsCheckingAuth] = useState(true)
  const [systemName, setSystemName] = useState('Pháp luật Hải Phòng')
  const [configInfo, setConfigInfo] = useState<{ apiUrl: string; version: string; buildTime: string } | null>(null)
  const [pendingGoogleLink, setPendingGoogleLink] = useState<{ idToken: string; email: string } | null>(null)
  const [linkIdentifier, setLinkIdentifier] = useState('')
  const [linkPassword, setLinkPassword] = useState('')
  const redirectResultHandled = useRef(false)
  const resetLinkHandled = useRef(false)

  const router = useRouter()
  const {
    login,
    register,
    loginWithFirebase,
    linkGoogleAccount,
    isLoading,
    error,
    mfaChallenge,
    setupTotp,
    confirmTotp,
  } = useAuth()
  const { authRequired, checkAuthRequired, hasHydrated, isAuthenticated } = useAuthStore()

  useEffect(() => {
    if (!firebaseEnabled || !hasHydrated || redirectResultHandled.current) return
    redirectResultHandled.current = true

    let cancelled = false
    const handleRedirectResult = async () => {
      try {
        const result = await getRedirectResult(getFirebaseAuth())
        if (!result || cancelled) return

        setLocalError(null)
        setNotice(null)
        setIsSubmitting(true)
        const pendingProfile = result.user.email
          ? readPendingFirebaseProfile(result.user.email)
          : null
        const idToken = await result.user.getIdToken()
        const success = await loginWithFirebase(idToken, pendingProfile || undefined)
        if (success) clearPendingFirebaseProfile()
        else {
          const authError = useAuthStore.getState().error || ''
          if (result.user.email && authError.includes('liên kết Google')) {
            setPendingGoogleLink({ idToken, email: result.user.email })
            setLinkIdentifier(result.user.email)
            setLinkPassword('')
            setNotice('Email này đã có tài khoản nội bộ. Nhập mật khẩu tài khoản đó để liên kết Google.')
          } else await signOut(getFirebaseAuth())
        }
      } catch (error) {
        const code = error && typeof error === 'object' && 'code' in error
          ? String((error as { code?: string }).code)
          : ''
        const message = error instanceof Error ? error.message : String(error)
        console.error(`Google redirect sign-in failed: ${code || 'unknown'} ${message}`)
        if (!cancelled) {
          const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
          setLocalError(
            code === 'auth/unauthorized-domain'
              ? (redirect127001ToLocalhost() ? 'Đang chuyển sang địa chỉ localhost để đăng nhập Google trên máy này…' : `Trang ${host} chưa được cho phép sử dụng đăng nhập Google. Hãy mở bằng localhost.`)
              : code === 'auth/configuration-not-found'
                ? 'Đăng nhập Google chưa được cấu hình. Vui lòng liên hệ quản trị viên.'
                : code === 'auth/operation-not-allowed'
                  ? 'Đăng nhập Google chưa được bật.'
                  : code === 'auth/network-request-failed'
                    ? 'Không kết nối được dịch vụ đăng nhập. Hãy kiểm tra mạng rồi thử lại.'
                    : code
                      ? `Không thể đăng nhập bằng Google (${code}).`
                      : 'Không thể đăng nhập bằng Google. Vui lòng thử lại.',
          )
        }
      } finally {
        if (!cancelled) setIsSubmitting(false)
      }
    }

    void handleRedirectResult()
    return () => {
      cancelled = true
    }
  }, [hasHydrated, loginWithFirebase])

  useEffect(() => {
    if (!firebaseEnabled || !hasHydrated || resetLinkHandled.current || typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    if (params.get('mode') !== 'resetPassword') return
    const actionCode = params.get('oobCode')?.trim()
    if (!actionCode) return

    resetLinkHandled.current = true
    setLocalError(null)
    setNotice(null)
    setIsSubmitting(true)
    void verifyPasswordResetCode(getFirebaseAuth(), actionCode)
      .then((email) => {
        setFirebaseResetCode(actionCode)
        setForgotIdentifier(email)
        setResetPassword('')
        setMode('reset')
        setNotice(`Liên kết hợp lệ cho ${email}. Hãy đặt mật khẩu mới để hoàn tất.`)
      })
      .catch((actionError) => {
        setMode('forgot')
        setLocalError(firebasePasswordResetMessage(actionError))
      })
      .finally(() => setIsSubmitting(false))
  }, [hasHydrated])

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        setSystemName(cfg.systemName || 'Pháp luật Hải Phòng')
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
        const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null
        if (params?.get('mode') === 'resetPassword' && params.get('oobCode')) {
          setIsCheckingAuth(false)
          return
        }
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

    const resetActionLink = typeof window !== 'undefined'
      && new URLSearchParams(window.location.search).get('mode') === 'resetPassword'
      && Boolean(new URLSearchParams(window.location.search).get('oobCode'))
    if (resetActionLink) {
      setIsCheckingAuth(false)
    } else if (authRequired !== null) {
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
            <CardDescription>Máy chủ chưa phản hồi hoặc cấu hình kết nối chưa đúng.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-start gap-2 text-sm text-red-600">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>{formatApiError(error, 'Hãy kiểm tra máy chủ ứng dụng rồi thử kết nối lại.')}</div>
            </div>
            {configInfo && <p className="border-t pt-3 text-xs text-muted-foreground">Phiên bản ứng dụng: {configInfo.version}</p>}
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
    if (!password.trim()) {
      setLocalError('Vui lòng nhập mật khẩu.')
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
    const localSuccess = await login(identifier, password, totpCode)
    if (localSuccess || useAuthStore.getState().mfaChallenge || !identifier.includes('@')) {
      return
    }

    // Keep legacy username/email accounts working first. If an email is not a
    // local account (or its password does not match), try Firebase email auth
    // so newly registered Firebase citizens can use the same form.
    const localError = useAuthStore.getState().error || ''
    if (
      localError.includes('Không kết nối được backend')
      || localError.includes('Lỗi máy chủ')
      || localError.includes('(429)')
    ) {
      return
    }
    if (!firebaseEnabled) return

    setIsSubmitting(true)
    let firebaseError: string | null = null
    try {
      const credential = await signInWithEmailAndPassword(
        getFirebaseAuth(),
        identifier.trim(),
        password,
      )
      if (!credential.user.emailVerified) {
        await sendEmailVerification(credential.user, {
          url: `${window.location.origin}/login`,
          handleCodeInApp: false,
        })
        firebaseError = 'Email chưa được xác minh. Một email xác minh mới đã được gửi; hãy mở liên kết rồi đăng nhập lại.'
      } else {
        const pendingProfile = credential.user.email
          ? readPendingFirebaseProfile(credential.user.email)
          : null
        const firebaseSuccess = await loginWithFirebase(await credential.user.getIdToken(), pendingProfile || undefined)
        if (firebaseSuccess) {
          clearPendingFirebaseProfile()
          return
        }
        firebaseError = useAuthStore.getState().error
        if (credential.user.email && firebaseError?.includes('liên kết Google')) {
          setPendingGoogleLink({ idToken: await credential.user.getIdToken(), email: credential.user.email })
          setLinkIdentifier(credential.user.email)
          setLinkPassword('')
          setNotice('Email này đã có tài khoản nội bộ. Nhập mật khẩu tài khoản đó để liên kết Google.')
          return
        }
      }
      await signOut(getFirebaseAuth())
    } catch (error) {
      const code = error && typeof error === 'object' && 'code' in error
        ? String((error as { code?: string }).code)
        : ''
      if (!['auth/invalid-credential', 'auth/user-not-found', 'auth/wrong-password'].includes(code)) {
        firebaseError = code === 'auth/too-many-requests'
          ? 'Tài khoản tạm thời bị giới hạn đăng nhập. Vui lòng thử lại sau.'
          : 'Không thể xác thực email. Vui lòng kiểm tra email và mật khẩu.'
      }
    } finally {
      setIsSubmitting(false)
    }

    // An unverified Firebase account has a more useful message than the
    // generic legacy-password failure. For all other failures, keep the
    // legacy login error so old accounts remain understandable.
    if (firebaseError?.includes('xác minh')) {
      setLocalError(firebaseError)
    }
  }

  const handleGoogleLogin = async () => {
    setLocalError(null)
    setNotice(null)
    if (!firebaseEnabled) {
      setLocalError('Đăng nhập Google chưa được bật trên môi trường này.')
      return
    }
    setIsSubmitting(true)
    try {
      const provider = new GoogleAuthProvider()
      const result = await signInWithPopup(getFirebaseAuth(), provider)
      const idToken = await result.user.getIdToken()
      const success = await loginWithFirebase(idToken)
      if (!success) {
        const authError = useAuthStore.getState().error || ''
        if (result.user.email && authError.includes('liên kết Google')) {
          setPendingGoogleLink({ idToken, email: result.user.email })
          setLinkIdentifier(result.user.email)
          setLinkPassword('')
          setMode('login')
          setNotice('Email này đã có tài khoản nội bộ. Nhập mật khẩu tài khoản đó để liên kết Google.')
        } else await signOut(getFirebaseAuth())
      }
    } catch (error) {
      const code = error && typeof error === 'object' && 'code' in error
        ? String((error as { code?: string }).code)
        : ''
      const message = error instanceof Error ? error.message : String(error)
      console.error(`Google sign-in failed: ${code || 'unknown'} ${message}`)
      if (code === 'auth/popup-blocked') {
        setNotice('Cửa sổ đăng nhập bị chặn. Đang chuyển sang trang đăng nhập Google...')
        try {
          await signInWithRedirect(getFirebaseAuth(), new GoogleAuthProvider())
          return
        } catch (redirectError) {
          const redirectCode = redirectError && typeof redirectError === 'object' && 'code' in redirectError
            ? String((redirectError as { code?: string }).code)
            : ''
          console.error(`Google redirect fallback failed: ${redirectCode || 'unknown'}`, redirectError)
        }
      }
      const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
      setLocalError(
        code === 'auth/popup-closed-by-user'
          ? 'Bạn đã đóng cửa sổ đăng nhập Google.'
          : code === 'auth/unauthorized-domain'
          ? (redirect127001ToLocalhost() ? 'Đang chuyển sang địa chỉ localhost để đăng nhập Google trên máy này…' : `Trang ${host} chưa được cho phép sử dụng đăng nhập Google. Hãy mở bằng localhost.`)
          : code === 'auth/configuration-not-found'
            ? 'Đăng nhập Google chưa được cấu hình. Vui lòng liên hệ quản trị viên.'
          : code === 'auth/popup-blocked'
              ? 'Trình duyệt đã chặn cửa sổ Google. Hãy cho phép popup cho trang này rồi thử lại.'
              : code === 'auth/operation-not-allowed'
                ? 'Đăng nhập Google chưa được bật.'
                : code === 'auth/network-request-failed'
                  ? 'Không kết nối được dịch vụ đăng nhập. Hãy kiểm tra mạng rồi thử lại.'
                  : code === 'auth/api-key-not-valid'
                    ? 'Cấu hình dịch vụ đăng nhập không hợp lệ. Vui lòng liên hệ quản trị viên.'
                    : code
                      ? `Không thể đăng nhập bằng Google (${code}).`
                      : 'Không thể đăng nhập bằng Google. Vui lòng thử lại.',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleGoogleLinkSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!firebaseEnabled || !pendingGoogleLink) return
    setLocalError(null)
    setNotice(null)
    if (!linkIdentifier.trim() || !linkPassword) {
      setLocalError('Nhập email/tên đăng nhập và mật khẩu tài khoản nội bộ để liên kết.')
      return
    }
    setIsSubmitting(true)
    try {
      const success = await linkGoogleAccount(pendingGoogleLink.idToken, linkIdentifier, linkPassword)
      if (success) {
        setPendingGoogleLink(null)
        setLinkPassword('')
        clearPendingFirebaseProfile()
        await signOut(getFirebaseAuth())
      } else {
        setLocalError(useAuthStore.getState().error || 'Không thể liên kết Google với tài khoản nội bộ.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const cancelGoogleLink = async () => {
    setPendingGoogleLink(null)
    setLinkPassword('')
    setLocalError(null)
    setNotice(null)
    try {
      if (firebaseEnabled) await signOut(getFirebaseAuth())
    } catch { /* best effort */ }
  }

  const handleRegisterSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!registerUsername.trim() || !registerFullName.trim() || !registerEmail.trim() || !registerPhone.trim() || !registerPassword.trim() || !registerPasswordConfirmation.trim()) {
      setLocalError('Vui lòng nhập đầy đủ tên đăng nhập, họ tên, email, số điện thoại và mật khẩu.')
      return
    }
    if (registerUsername.trim().length < 3) {
      setLocalError('Tên đăng nhập cần có ít nhất 3 ký tự.')
      return
    }
    if (!/^\+?[0-9().\-\s]{8,20}$/.test(registerPhone.trim()) || registerPhone.replace(/\D/g, '').length < 8) {
      setLocalError('Số điện thoại không hợp lệ; hãy nhập từ 8 đến 15 chữ số.')
      return
    }
    if (registerPassword.length < 12) {
      setLocalError('Mật khẩu cần có ít nhất 12 ký tự.')
      return
    }
    if (registerPassword !== registerPasswordConfirmation) {
      setLocalError('Mật khẩu xác nhận không khớp.')
      return
    }
    setIsSubmitting(true)
    const success = await register({
      username: registerUsername,
      email: registerEmail,
      password: registerPassword,
      fullName: registerFullName,
      phone: registerPhone,
      gender: registerGender,
    })
    if (!success) {
      setLocalError(useAuthStore.getState().error || 'Không đăng ký được tài khoản. Vui lòng thử lại.')
    }
    setIsSubmitting(false)
  }

  const handleForgotSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!firebaseEnabled) {
      setLocalError('Khôi phục mật khẩu qua email chưa được bật. Hãy liên hệ quản trị viên để đặt lại mật khẩu.')
      return
    }
    const value = forgotIdentifier.trim()
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      setLocalError('Vui lòng nhập địa chỉ email hợp lệ.')
      return
    }
    setIsSubmitting(true)
    try {
      await sendPasswordResetEmail(getFirebaseAuth(), value, {
        url: `${window.location.origin}/login`,
        handleCodeInApp: false,
      })
      setNotice('Nếu email này thuộc tài khoản email trực tuyến, liên kết đặt lại sẽ được gửi. Hãy kiểm tra cả thư mục thư rác.')
    } catch (error) {
      if (firebaseErrorCode(error) === 'auth/user-not-found') {
        setNotice('Nếu email này thuộc tài khoản email trực tuyến, liên kết đặt lại sẽ được gửi. Hãy kiểm tra cả thư mục thư rác.')
      } else {
        setLocalError(firebasePasswordResetMessage(error))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleResetSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    setNotice(null)
    if (!firebaseEnabled) {
      setLocalError('Khôi phục mật khẩu qua email chưa được bật.')
      return
    }
    if (!firebaseResetCode.trim() || !resetPassword.trim()) {
      setLocalError('Vui lòng mở liên kết đặt lại trong email và nhập mật khẩu mới.')
      return
    }
    if (resetPassword.length < 12) {
      setLocalError('Mật khẩu mới phải có ít nhất 12 ký tự.')
      return
    }
    setIsSubmitting(true)
    try {
      await confirmPasswordReset(getFirebaseAuth(), firebaseResetCode.trim(), resetPassword)
      setIdentifier(forgotIdentifier)
      setPassword('')
      setResetPassword('')
      setFirebaseResetCode('')
      setMode('login')
      setNotice('Đã đặt lại mật khẩu. Bạn có thể đăng nhập bằng mật khẩu mới.')
    } catch (error) {
      setLocalError(firebasePasswordResetMessage(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  const busy = isLoading || isSubmitting
  const visibleError = localError || error

  return (
    <div className="relative min-h-screen overflow-y-auto bg-[#f6f1e8] px-4 pb-8 text-slate-900 sm:px-6 lg:px-8">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(183,134,44,0.12),_transparent_34%),radial-gradient(circle_at_bottom_right,_rgba(143,29,44,0.10),_transparent_36%)]" />
      <header className="relative -mx-4 border-b-4 border-[#d3a74a] bg-gradient-to-r from-[#5b1420] via-[#8f1d2c] to-[#5b1420] text-white shadow-lg shadow-red-950/20 sm:-mx-6 lg:-mx-8">
        <div className="mx-auto flex w-full max-w-6xl items-center gap-3 px-6 py-4 lg:px-10">
          <Image src="/logo.svg" alt="Biểu trưng Pháp luật Hải Phòng" width={48} height={48} className="h-12 w-12 rounded-full shadow-md" priority />
          <div>
            <p className="text-sm font-bold uppercase tracking-[0.18em] sm:text-base">{systemName}</p>
            <p className="mt-1 text-[11px] text-[#f9e6bd] sm:text-xs">Trợ lý pháp luật phường/xã · Tra cứu có căn cứ</p>
          </div>
        </div>
      </header>
      <main className="relative mx-auto grid min-h-[calc(100vh-8rem)] w-full max-w-6xl items-start gap-10 py-10 lg:items-center lg:grid-cols-[1fr_430px] lg:gap-16 lg:py-14">
        <section className="hidden space-y-7 lg:block">
          <div>
            <p className="mb-3 text-sm font-semibold uppercase tracking-[0.2em] text-red-700">AI pháp luật</p>
            <h1 className="font-display max-w-xl text-5xl font-bold leading-[1.04] text-[#5b1420]">Hiểu đúng quy định.<br />Thực hiện đúng thủ tục.</h1>
            <p className="mt-5 max-w-xl text-base leading-7 text-slate-600">Trợ lý pháp luật hỗ trợ người dân Hải Phòng tra cứu văn bản hiện hành, chuẩn bị hồ sơ và tìm đúng cơ quan giải quyết.</p>
          </div>
          <div className="grid gap-3 text-sm leading-6 text-slate-700">
            <div className="flex items-start gap-3 rounded-xl border border-[#d8c9b4] bg-white/70 p-3"><span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[#8f1d2c]" /><span>Tra cứu điều khoản, thời hạn, thành phần hồ sơ và lệ phí.</span></div>
            <div className="flex items-start gap-3 rounded-xl border border-[#d8c9b4] bg-white/70 p-3"><span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[#b7862c]" /><span>Hướng dẫn trình tự thực hiện thủ tục hành chính theo địa bàn.</span></div>
            <div className="flex items-start gap-3 rounded-xl border border-[#d8c9b4] bg-white/70 p-3"><span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[#2f6b56]" /><span>Câu trả lời đi kèm căn cứ và nguồn pháp lý để bạn kiểm tra.</span></div>
          </div>
          <p className="max-w-xl border-l-2 border-red-300 pl-4 text-xs leading-5 text-slate-500">Thông tin mang tính tham khảo, không thay thế quyết định của cơ quan có thẩm quyền hoặc tư vấn pháp lý chuyên sâu.</p>
        </section>
        <Card className="rounded-2xl border border-[#d3a74a]/40 bg-[#202632] text-[#fff8ec] shadow-2xl shadow-slate-950/25">
        <CardHeader className="px-6 pb-3 pt-8 text-center sm:px-10">
          <CardTitle className="font-display text-3xl">Chào mừng bạn</CardTitle>
          <CardDescription className="text-[#f9e6bd]/80">
            {mode === 'login' && 'Đăng nhập để tra cứu quy định, thủ tục và nguồn pháp lý chính thống.'}
            {mode === 'register' && 'Tạo tài khoản công dân để nhận hướng dẫn phù hợp với địa bàn Hải Phòng.'}
            {mode === 'forgot' && 'Chọn cách khôi phục phù hợp với tài khoản của bạn.'}
            {mode === 'reset' && 'Đặt mật khẩu mới từ liên kết trong email.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 px-6 pb-8 sm:px-10">
          {mode === 'login' && (
            <div className="space-y-4">
              {firebaseEnabled && (
                <>
                  <Button type="button" variant="outline" className="h-11 w-full gap-3 border-red-800/70 bg-[#32121a] text-red-50 hover:bg-[#47131e] hover:text-white" onClick={handleGoogleLogin} disabled={busy}>
                    <Chrome className="h-4 w-4" />
                    Tiếp tục với Google
                  </Button>
                  <div className="flex items-center gap-3 text-xs text-red-200/55"><span className="h-px flex-1 bg-red-900/70" /><span>hoặc tài khoản cơ quan</span><span className="h-px flex-1 bg-red-900/70" /></div>
                </>
              )}
            <form onSubmit={handleLoginSubmit} className="space-y-4">
              <label htmlFor="login-identifier" className="sr-only">Email hoặc tên đăng nhập</label>
              <Input
                id="login-identifier"
                className={authInputClass}
                type="text"
                placeholder="Username/email (có thể bỏ trống với mật khẩu hệ thống cũ)"
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                disabled={busy}
                autoComplete="username"
              />
              <label htmlFor="login-password" className="sr-only">Mật khẩu</label>
              <div className="relative">
                <Input
                  id="login-password"
                  className={`${authInputClass} pr-12`}
                  type={showLoginPassword ? 'text' : 'password'}
                  placeholder="Mật khẩu"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={busy}
                  autoComplete="current-password"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-1/2 size-11 -translate-y-1/2 rounded-md text-slate-300 hover:bg-white/10 hover:text-white focus-visible:ring-[#e8c875]/60"
                  aria-label={showLoginPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                  aria-pressed={showLoginPassword}
                  title={showLoginPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                  onClick={() => setShowLoginPassword((visible) => !visible)}
                  disabled={busy}
                >
                  {showLoginPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                </Button>
              </div>
              {mfaChallenge?.type === 'setup' && totpSetup && (
                <div className="space-y-2 rounded-md border border-red-900/70 bg-[#32121a] p-3 text-sm">
                  <p className="font-medium">Thiết lập xác thực hai lớp</p>
                  <p className="text-red-200/65">
                    Thêm tài khoản vào ứng dụng xác thực bằng khóa dưới đây, sau đó nhập mã 6 số.
                  </p>
                  <code className="block break-all rounded bg-red-950/60 p-2 text-xs text-red-100">
                    {totpSetup.secret}
                  </code>
                </div>
              )}
              {mfaChallenge && (
                <Input
                  id="login-totp"
                  className={authInputClass}
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
              <Button type="submit" className="w-full gap-2 bg-red-700 text-white shadow-lg shadow-red-950/30 hover:bg-red-600" disabled={busy || !password.trim()}>
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
            {pendingGoogleLink && (
              <form onSubmit={handleGoogleLinkSubmit} className="space-y-3 rounded-lg border border-amber-400/40 bg-amber-950/30 p-3" aria-label="Liên kết Google với tài khoản nội bộ">
                <p className="text-sm font-medium text-amber-100">Liên kết Google với tài khoản nội bộ</p>
                <p className="text-xs leading-5 text-amber-100/75">Email Google đã tồn tại trong hệ thống. Xác nhận bằng mật khẩu nội bộ; hệ thống không đổi quyền tài khoản.</p>
                <Input
                  id="google-link-identifier"
                  className={authInputClass}
                  value={linkIdentifier}
                  onChange={(event) => setLinkIdentifier(event.target.value)}
                  placeholder="Email hoặc tên đăng nhập nội bộ"
                  disabled={busy}
                  autoComplete="username"
                />
                <Input
                  id="google-link-password"
                  className={authInputClass}
                  type="password"
                  value={linkPassword}
                  onChange={(event) => setLinkPassword(event.target.value)}
                  placeholder="Mật khẩu nội bộ"
                  disabled={busy}
                  autoComplete="current-password"
                />
                <div className="flex gap-2">
                  <Button type="submit" className="flex-1 bg-amber-700 text-white hover:bg-amber-600" disabled={busy || !linkPassword.trim()}>Liên kết và đăng nhập</Button>
                  <Button type="button" variant="ghost" className="text-amber-100 hover:bg-amber-950/60" onClick={() => void cancelGoogleLink()} disabled={busy}>Hủy</Button>
                </div>
              </form>
            )}
            </div>
          )}

          {mode === 'register' && (
            <div className="space-y-4">
              {firebaseEnabled && (
                <>
                  <Button type="button" variant="outline" className="h-11 w-full gap-3 border-red-800/70 bg-[#32121a] text-red-50 hover:bg-[#47131e] hover:text-white" onClick={handleGoogleLogin} disabled={busy}>
                    <Chrome className="h-4 w-4" />
                    Đăng ký bằng Google
                  </Button>
                  <div className="flex items-center gap-3 text-xs text-red-200/70"><span className="h-px flex-1 bg-red-900/70" /><span>hoặc tạo tài khoản bằng biểu mẫu</span><span className="h-px flex-1 bg-red-900/70" /></div>
                </>
              )}
              <form onSubmit={handleRegisterSubmit} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="register-username" className="sr-only">Tên đăng nhập</label>
                <Input id="register-username" className={authInputClass} placeholder="Tên đăng nhập" value={registerUsername} onChange={(event) => setRegisterUsername(event.target.value)} disabled={busy} autoComplete="username" />
              </div>
              <div>
                <label htmlFor="register-name" className="sr-only">Họ và tên</label>
                <Input id="register-name" className={authInputClass} placeholder="Họ và tên" value={registerFullName} onChange={(event) => setRegisterFullName(event.target.value)} disabled={busy} autoComplete="name" />
              </div>
              <div className="sm:col-span-2">
                <label htmlFor="register-gender" className="sr-only">Giới tính</label>
                <select id="register-gender" aria-label="Giới tính" className={`${authInputClass} h-11 w-full rounded-md border px-3`} value={registerGender} onChange={event => setRegisterGender(event.target.value as typeof registerGender)} disabled={busy}>
                  <option value="unspecified">Giới tính — chưa chọn</option><option value="male">Nam</option><option value="female">Nữ</option>
                </select>
              </div>
              <div>
                <label htmlFor="register-email" className="sr-only">Email</label>
                <Input id="register-email" className={authInputClass} type="email" placeholder="Email" value={registerEmail} onChange={(event) => setRegisterEmail(event.target.value)} disabled={busy} autoComplete="email" />
              </div>
              <div>
                <label htmlFor="register-phone" className="sr-only">Số điện thoại</label>
                <Input id="register-phone" className={authInputClass} type="tel" placeholder="Số điện thoại" value={registerPhone} onChange={(event) => setRegisterPhone(event.target.value)} disabled={busy} autoComplete="tel" />
              </div>
              <div>
                <label htmlFor="register-password" className="sr-only">Mật khẩu</label>
                <Input id="register-password" className={authInputClass} type="password" placeholder="Mật khẩu (12+ ký tự)" value={registerPassword} onChange={(event) => setRegisterPassword(event.target.value)} disabled={busy} autoComplete="new-password" />
              </div>
              <div>
                <label htmlFor="register-password-confirmation" className="sr-only">Nhập lại mật khẩu</label>
                <Input id="register-password-confirmation" className={authInputClass} type="password" placeholder="Nhập lại mật khẩu" value={registerPasswordConfirmation} onChange={(event) => setRegisterPasswordConfirmation(event.target.value)} disabled={busy} autoComplete="new-password" />
              </div>
              <Button type="submit" className="h-11 w-full gap-2 bg-red-700 text-white shadow-lg shadow-red-950/30 hover:bg-red-600 sm:col-span-2" disabled={busy}>
                <UserPlus className="h-4 w-4" />
                {busy ? 'Đang tạo tài khoản...' : 'Tạo tài khoản'}
              </Button>
              </form>
            </div>
          )}

          {mode === 'forgot' && (
            <form onSubmit={handleForgotSubmit} className="space-y-4">
              <p className="rounded-lg border border-red-900/60 bg-[#32121a] p-3 text-xs leading-5 text-red-100/90">
                Tài khoản tạo bằng biểu mẫu hoặc do cơ quan cấp cần quản trị viên đặt lại mật khẩu. Tài khoản email trực tuyến trước đây có thể nhận liên kết bên dưới. Nếu dùng Google, hãy đăng nhập bằng Google hoặc dùng <a className="underline decoration-red-300/60 underline-offset-2 hover:text-white" href="https://accounts.google.com/signin/recovery" target="_blank" rel="noreferrer">trang khôi phục Google</a>.
              </p>
              <label htmlFor="forgot-identifier" className="sr-only">Email tài khoản trực tuyến</label>
              <Input
                id="forgot-identifier"
                className={authInputClass}
                type="email"
                placeholder="Email tài khoản trực tuyến"
                value={forgotIdentifier}
                onChange={(event) => setForgotIdentifier(event.target.value)}
                disabled={busy}
                autoComplete="email"
              />
              <Button type="submit" className="w-full gap-2 bg-red-700 text-white shadow-lg shadow-red-950/30 hover:bg-red-600" disabled={busy || !forgotIdentifier.trim()}>
                <KeyRound className="h-4 w-4" />
                {busy ? 'Đang gửi yêu cầu...' : 'Gửi liên kết đặt lại'}
              </Button>
            </form>
          )}

          {mode === 'reset' && (
            <form onSubmit={handleResetSubmit} className="space-y-4">
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/30 p-3 text-sm text-emerald-100">
                Đặt lại mật khẩu cho <strong>{forgotIdentifier}</strong> bằng liên kết đã xác minh.
              </div>
              <label htmlFor="reset-password" className="sr-only">Mật khẩu mới</label>
              <Input id="reset-password" className={authInputClass} type="password" placeholder="Mật khẩu mới (ít nhất 12 ký tự)" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} disabled={busy} autoComplete="new-password" />
              <Button type="submit" className="w-full gap-2 bg-red-700 text-white shadow-lg shadow-red-950/30 hover:bg-red-600" disabled={busy || !firebaseResetCode.trim() || !resetPassword.trim()}>
                <KeyRound className="h-4 w-4" />
                {busy ? 'Đang đặt lại...' : 'Đặt lại mật khẩu'}
              </Button>
            </form>
          )}

          {visibleError && (
            <div role="alert" aria-live="assertive" className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-950/60 p-3 text-sm text-red-100">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>{visibleError}</div>
            </div>
          )}

          {notice && (
            <div role="status" aria-live="polite" className="rounded-md border border-rose-500/30 bg-rose-950/50 p-3 text-sm text-rose-100">
              {notice}
            </div>
          )}

          <div className={`grid ${firebaseEnabled ? 'grid-cols-3' : 'grid-cols-2'} gap-2 border-t border-red-900/60 pt-4 text-xs`}>
            <Button type="button" variant="ghost" className={mode === 'login' ? 'bg-red-700 text-white hover:bg-red-600 hover:text-white' : 'text-red-200/70 hover:bg-red-950/60 hover:text-red-50'} onClick={() => setMode('login')} disabled={busy}>
              Đăng nhập
            </Button>
            <Button type="button" variant="ghost" className={mode === 'register' ? 'bg-red-700 text-white hover:bg-red-600 hover:text-white' : 'text-red-200/70 hover:bg-red-950/60 hover:text-red-50'} onClick={() => setMode('register')} disabled={busy}>
              Đăng ký
            </Button>
            {firebaseEnabled && (
              <Button type="button" variant="ghost" className={mode === 'forgot' || mode === 'reset' ? 'bg-red-700 text-white hover:bg-red-600 hover:text-white' : 'text-red-200/70 hover:bg-red-950/60 hover:text-red-50'} onClick={() => { setMode('forgot'); setFirebaseResetCode(''); setResetPassword(''); setLocalError(null); setNotice(null) }} disabled={busy}>
                Quên mật khẩu
              </Button>
            )}
          </div>

          <div className="border-t border-red-900/60 pt-4 text-center text-sm text-red-100/80">
            Chỉ muốn hỏi nhanh một thủ tục?{' '}
            <a href="/hoi-nhanh" className="font-semibold text-[#f9e6bd] underline decoration-[#d3a74a]/70 underline-offset-4 hover:text-white">
              Không cần đăng nhập
            </a>
          </div>
          {configInfo && <div className="pt-2 text-center text-xs text-red-200/45">Phiên bản {configInfo.version}</div>}
        </CardContent>
        </Card>
      </main>
    </div>
  )
}
