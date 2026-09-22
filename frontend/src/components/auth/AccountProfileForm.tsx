'use client'

import { useEffect, useMemo, useState } from 'react'
import { Check, Save, UserRound } from 'lucide-react'

import { apiClient } from '@/lib/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatApiError } from '@/lib/utils/error-handler'

export type AccountProfileValues = {
  full_name?: string | null
  phone?: string | null
  ward?: string | null
  department?: string | null
  job_title?: string | null
}

type AccountProfileFormProps = {
  initialProfile: AccountProfileValues
  roleLabel: string
  onSaved: (profile: AccountProfileValues) => void
}

function normalize(value: string | null | undefined) {
  return String(value || '').trim()
}

function validatePhone(phone: string): string | null {
  if (!phone) return null
  const digits = phone.replace(/\D/g, '')
  if (!/^[+0-9().\-\s]+$/.test(phone) || digits.length < 8 || digits.length > 15) {
    return 'Số điện thoại cần có từ 8 đến 15 chữ số.'
  }
  return null
}

function apiErrorMessage(error: unknown): string {
  return formatApiError(error, 'Không thể lưu thông tin hồ sơ. Vui lòng thử lại.')
}

export function AccountProfileForm({ initialProfile, roleLabel, onSaved }: AccountProfileFormProps) {
  const [fullName, setFullName] = useState(normalize(initialProfile.full_name))
  const [phone, setPhone] = useState(normalize(initialProfile.phone))
  const [ward, setWard] = useState(normalize(initialProfile.ward))
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setFullName(normalize(initialProfile.full_name))
    setPhone(normalize(initialProfile.phone))
    setWard(normalize(initialProfile.ward))
  }, [initialProfile.full_name, initialProfile.phone, initialProfile.ward])

  const dirty = useMemo(() => (
    fullName !== normalize(initialProfile.full_name)
    || phone !== normalize(initialProfile.phone)
    || ward !== normalize(initialProfile.ward)
  ), [fullName, initialProfile.full_name, initialProfile.phone, initialProfile.ward, phone, ward])

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    const phoneError = validatePhone(phone)
    if (phoneError) {
      setError(phoneError)
      setNotice(null)
      return
    }

    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const response = await apiClient.put('/users/me', {
        full_name: fullName || null,
        phone: phone || null,
        ward: ward || null,
      })
      const profile = response.data?.profile || response.data || {}
      const nextProfile = {
        ...initialProfile,
        full_name: profile.full_name ?? fullName,
        phone: profile.phone ?? phone,
        ward: profile.ward ?? ward,
      }
      onSaved(nextProfile)
      setNotice('Đã lưu thông tin hồ sơ của bạn.')
    } catch (saveError) {
      setError(apiErrorMessage(saveError))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="border-border/80 bg-card/90 shadow-sm" data-testid="account-profile-form">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserRound className="h-5 w-5 text-primary" />
          Hồ sơ của tôi
        </CardTitle>
        <CardDescription>
          Cập nhật thông tin liên hệ dùng cho tài khoản {roleLabel.toLowerCase()}. Vai trò và quyền truy cập chỉ do quản trị viên thay đổi.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="account-full-name">Họ và tên</Label>
              <Input
                id="account-full-name"
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                placeholder="Nhập họ và tên"
                disabled={saving}
                autoComplete="name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-phone">Số điện thoại</Label>
              <Input
                id="account-phone"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                placeholder="Ví dụ: 0912 345 678"
                disabled={saving}
                autoComplete="tel"
                inputMode="tel"
              />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="account-ward">Địa bàn làm việc / cư trú</Label>
              <Input
                id="account-ward"
                value={ward}
                onChange={(event) => setWard(event.target.value)}
                placeholder="Phường, xã, thành phố"
                disabled={saving}
                autoComplete="street-address"
              />
            </div>
          </div>

          {error && <p className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p>}
          {notice && <p className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300" role="status"><Check className="h-4 w-4" />{notice}</p>}

          <div className="flex justify-end">
            <Button type="submit" disabled={saving || !dirty}>
              <Save className="h-4 w-4" />
              {saving ? 'Đang lưu...' : 'Lưu thay đổi'}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
