'use client'

import { Mail, ShieldCheck, UserRound } from 'lucide-react'
import { AppShell } from '@/components/layout/AppShell'
import { AccountProfileForm, type AccountProfileValues } from '@/components/auth/AccountProfileForm'
import { FirebaseAccountSecurity } from '@/components/auth/FirebaseAccountSecurity'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { useEffect, useState } from 'react'

const ROLE_LABEL: Record<string, string> = {
  admin: 'Quản trị viên',
  officer: 'Cán bộ',
  citizen: 'Công dân',
}

type AccountData = {
  id?: string | null
  username?: string | null
  email?: string | null
  role?: string | null
  auth_mode?: string | null
  profile?: AccountProfileValues
}

export default function AccountPage() {
  const username = useAuthStore((state) => state.username)
  const email = useAuthStore((state) => state.email)
  const role = useAuthStore((state) => state.role)
  const userId = useAuthStore((state) => state.userId)
  const [account, setAccount] = useState<AccountData>({ username, email, role })
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const loadAccount = async () => {
      try {
        const response = await apiClient.get('/users/me')
        if (active) {
          setAccount(response.data)
          setLoadError(null)
        }
      } catch {
        if (active) setLoadError('Chưa tải được hồ sơ mới nhất. Bạn vẫn có thể xem thông tin phiên hiện tại.')
      }
    }
    void loadAccount()
    return () => { active = false }
  }, [userId])

  const resolvedRole = account.role || role || 'citizen'
  const roleLabel = ROLE_LABEL[resolvedRole] || 'Người dùng'
  const profile = account.profile || {}

  const handleProfileSaved = (nextProfile: AccountProfileValues) => {
    setAccount((current) => ({ ...current, profile: { ...current.profile, ...nextProfile } }))
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto bg-gradient-to-br from-background via-background to-red-50/30 dark:to-red-950/10">
        <div className="mx-auto w-full max-w-5xl space-y-6 p-6">
          <div>
            <p className="text-sm font-medium uppercase tracking-[0.18em] text-red-600">Tài khoản {roleLabel.toLowerCase()}</p>
            <h1 className="mt-2 text-2xl font-bold">Tài khoản của tôi</h1>
            <p className="mt-2 text-sm text-muted-foreground">Cập nhật hồ sơ, đăng nhập và bảo mật tài khoản pháp luật Hải Phòng.</p>
          </div>

          {loadError && <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200" role="status">{loadError}</p>}

          <Card className="border-red-200/70 bg-card/80 shadow-sm dark:border-red-900/50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <UserRound className="h-5 w-5 text-red-600" />
                Thông tin đăng nhập
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-3">
              <div className="rounded-xl border bg-background/70 p-4">
                <p className="text-xs text-muted-foreground">Tên tài khoản</p>
                <p className="mt-1 truncate font-medium">{account.username || username || 'Chưa cập nhật'}</p>
              </div>
              <div className="rounded-xl border bg-background/70 p-4">
                <p className="flex items-center gap-1 text-xs text-muted-foreground"><Mail className="h-3.5 w-3.5" /> Email</p>
                <p className="mt-1 truncate font-medium">{account.email || email || 'Chưa cập nhật'}</p>
              </div>
              <div className="rounded-xl border bg-background/70 p-4">
                <p className="flex items-center gap-1 text-xs text-muted-foreground"><ShieldCheck className="h-3.5 w-3.5" /> Vai trò</p>
                <p className="mt-1 font-medium">{roleLabel}</p>
              </div>
            </CardContent>
          </Card>

          <AccountProfileForm
            initialProfile={profile}
            roleLabel={roleLabel}
            onSaved={handleProfileSaved}
          />

          <FirebaseAccountSecurity />
        </div>
      </div>
    </AppShell>
  )
}
