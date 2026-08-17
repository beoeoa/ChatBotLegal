"use client"

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Lock } from 'lucide-react'

import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function ChangePasswordPage() {
  const router = useRouter()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (newPassword !== confirmPassword) {
      toast.error('Mật khẩu mới không khớp.')
      return
    }
    if (newPassword.length < 12) {
      toast.error('Mật khẩu mới phải có ít nhất 12 ký tự.')
      return
    }

    setLoading(true)
    try {
      await apiClient.post('/users/me/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      toast.success('Đã đổi mật khẩu thành công. Đang đăng xuất...')
      setTimeout(async () => {
        await useAuthStore.getState().logout()
        router.push('/login')
      }, 1500)
    } catch (error: unknown) {
      const detail = error as { response?: { data?: { detail?: string } }; message?: string }
      toast.error(detail.response?.data?.detail || detail.message || 'Không thể đổi mật khẩu.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Lock className="h-5 w-5" />
            Đổi mật khẩu
          </CardTitle>
          <CardDescription>
            Bạn đang sử dụng mật khẩu khởi tạo. Vui lòng đổi mật khẩu trước khi tiếp tục.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              type="password"
              placeholder="Mật khẩu hiện tại"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              disabled={loading}
              autoComplete="current-password"
            />
            <Input
              type="password"
              placeholder="Mật khẩu mới (tối thiểu 12 ký tự)"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              disabled={loading}
              autoComplete="new-password"
            />
            <Input
              type="password"
              placeholder="Xác nhận mật khẩu mới"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              disabled={loading}
              autoComplete="new-password"
            />
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Đang xử lý...' : 'Đổi mật khẩu'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
