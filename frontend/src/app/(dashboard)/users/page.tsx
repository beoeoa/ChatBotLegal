'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { getApiUrl } from '@/lib/config'
import { useAuthStore } from '@/lib/stores/auth-store'
import { useTranslation } from '@/lib/hooks/use-translation'

type UserRole = 'citizen' | 'officer' | 'admin'

type ManagedUser = {
  id: string
  username: string
  email: string
  role: UserRole
  is_active: boolean
  created?: string
  last_login_at?: string | null
  profile?: {
    full_name?: string
    phone?: string
    ward?: string
    department?: string
    allowed_domains?: string[]
    job_title?: string
    notes?: string
  }
}

type AuditLog = {
  id: string
  action: string
  actor_role?: string
  entity_type?: string
  entity_id?: string
  created?: string
}

type AskHistory = {
  id: string
  owner_role?: string
  question: string
  domain?: string
  duration_ms?: number | null
  created?: string
}

const emptyForm = {
  username: '',
  email: '',
  password: '',
  role: 'citizen' as UserRole,
  is_active: true,
  full_name: '',
  phone: '',
  ward: 'Phường Lê Chân, Hải Phòng',
  department: '',
  allowed_domains: [] as string[],
  job_title: '',
  notes: '',
}

const today = new Date().toISOString().slice(0, 10)

function formatDateTime(value?: string | null) {
  if (!value) return 'Chưa có'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('vi-VN')
}

export default function UsersPage() {
  const { t } = useTranslation()
  const token = useAuthStore((state) => state.token)
  const role = useAuthStore((state) => state.role)
  const authRequired = useAuthStore((state) => state.authRequired)

  const [users, setUsers] = useState<ManagedUser[]>([])
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [askHistory, setAskHistory] = useState<AskHistory[]>([])
  const [form, setForm] = useState(emptyForm)
  const [editingUserId, setEditingUserId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [businessReason, setBusinessReason] = useState('')
  const [temporaryPassword, setTemporaryPassword] = useState('')
  const [filters, setFilters] = useState({
    auditRole: '',
    auditAction: '',
    auditDateFrom: '',
    auditDateTo: '',
    askRole: '',
    askDomain: '',
    askDateFrom: '',
    askDateTo: '',
  })

  const headers = useMemo(() => {
    const nextHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) nextHeaders.Authorization = `Bearer ${token}`
    if (role) nextHeaders['X-User-Role'] = role
    return nextHeaders
  }, [role, token])

  const sensitiveHeaders = useCallback(() => ({
    ...headers,
    'X-Business-Reason': businessReason.trim(),
  }), [businessReason, headers])

  const requireBusinessReason = () => {
    if (businessReason.trim().length >= 3) return true
    setMessage('Vui lòng nhập lý do nghiệp vụ, tối thiểu 3 ký tự.')
    return false
  }

  const loadAll = useCallback(async () => {
    if (authRequired !== false && role !== 'admin') {
      setLoading(false)
      return
    }

    setLoading(true)
    setMessage(null)
    try {
      const apiUrl = await getApiUrl()

      const auditQuery = new URLSearchParams({ limit: '50' })
      const askQuery = new URLSearchParams({ limit: '50' })

      if (filters.auditRole) auditQuery.set('actor_role', filters.auditRole)
      if (filters.auditAction) auditQuery.set('action', filters.auditAction)
      if (filters.auditDateFrom) auditQuery.set('date_from', filters.auditDateFrom)
      if (filters.auditDateTo) auditQuery.set('date_to', `${filters.auditDateTo}T23:59:59+07:00`)

      if (filters.askRole) askQuery.set('role', filters.askRole)
      if (filters.askDomain) askQuery.set('domain', filters.askDomain)
      if (filters.askDateFrom) askQuery.set('date_from', filters.askDateFrom)
      if (filters.askDateTo) askQuery.set('date_to', `${filters.askDateTo}T23:59:59+07:00`)

      const [usersResult, auditResult, askResult] = await Promise.allSettled([
        fetch(`${apiUrl}/api/users`, { headers, cache: 'no-store' }),
        fetch(`${apiUrl}/api/users/audit-logs?${auditQuery.toString()}`, { headers, cache: 'no-store' }),
        fetch(`${apiUrl}/api/users/ask-history?${askQuery.toString()}`, { headers, cache: 'no-store' }),
      ])

      if (usersResult.status === 'rejected') {
        throw usersResult.reason
      }
      const usersRes = usersResult.value
      if (!usersRes.ok) {
        throw new Error(`Không tải được danh sách tài khoản (${usersRes.status})`)
      }

      setUsers(await usersRes.json())
      const warnings: string[] = []

      if (auditResult.status === 'fulfilled' && auditResult.value.ok) {
        setAuditLogs(await auditResult.value.json())
      } else {
        setAuditLogs([])
        warnings.push('nhật ký quản trị')
      }

      if (askResult.status === 'fulfilled' && askResult.value.ok) {
        setAskHistory(await askResult.value.json())
      } else {
        setAskHistory([])
        warnings.push('lịch sử hỏi đáp')
      }

      if (warnings.length > 0) {
        setMessage(`Danh sách tài khoản đã tải; chưa tải được ${warnings.join(' và ')}.`)
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Không tải được dữ liệu người dùng')
    } finally {
      setLoading(false)
    }
  }, [authRequired, filters, headers, role])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const handleCreateOrUpdate = async () => {
    if (editingUserId && !requireBusinessReason()) return
    setSubmitting(true)
    setMessage(null)
    try {
      const apiUrl = await getApiUrl()
      const targetUrl = editingUserId ? `${apiUrl}/api/users/${editingUserId}` : `${apiUrl}/api/users`
      const method = editingUserId ? 'PUT' : 'POST'
      const payload = editingUserId && !form.password
        ? {
            ...form,
            password: undefined,
          }
        : form

      const response = await fetch(targetUrl, {
        method,
        headers: editingUserId ? sensitiveHeaders() : headers,
        body: JSON.stringify(payload),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Lưu tài khoản thất bại')
      }

      setForm(emptyForm)
      setEditingUserId(null)
      setMessage(editingUserId ? `Đã cập nhật tài khoản ${data.username}` : `Đã tạo tài khoản ${data.username}`)
      await loadAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Lưu tài khoản thất bại')
    } finally {
      setSubmitting(false)
    }
  }

  const handleEdit = (user: ManagedUser) => {
    setEditingUserId(user.id)
    setForm({
      username: user.username,
      email: user.email,
      password: '',
      role: user.role,
      is_active: user.is_active,
      full_name: user.profile?.full_name || '',
      phone: user.profile?.phone || '',
      ward: user.profile?.ward || 'Phường Lê Chân, Hải Phòng',
      department: user.profile?.department || '',
      allowed_domains: user.profile?.allowed_domains || [],
      job_title: user.profile?.job_title || '',
      notes: user.profile?.notes || '',
    })
  }

  const handleDeactivate = async (userId: string) => {
    if (!requireBusinessReason()) return
    setMessage(null)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/users/${userId}`, {
        method: 'DELETE',
        headers: sensitiveHeaders(),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Khóa tài khoản thất bại')
      }
      setMessage('Đã khóa tài khoản')
      await loadAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Khóa tài khoản thất bại')
    }
  }

  const handleResetPassword = async (user: ManagedUser) => {
    if (!requireBusinessReason()) return
    if (temporaryPassword.length < 12) {
      setMessage('Mật khẩu tạm phải có ít nhất 12 ký tự.')
      return
    }
    setSubmitting(true)
    setMessage(null)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/users/${user.id}/reset-password`, {
        method: 'POST',
        headers: sensitiveHeaders(),
        body: JSON.stringify({ new_password: temporaryPassword }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Đặt lại mật khẩu thất bại')
      setTemporaryPassword('')
      setMessage(`Đã đặt mật khẩu tạm cho tài khoản ${user.username}.`)
      await loadAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Đặt lại mật khẩu thất bại')
    } finally {
      setSubmitting(false)
    }
  }

  if (authRequired !== false && role !== 'admin') {
    return (
      <div className="space-y-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <h1 className="text-2xl font-semibold">Quản lý tài khoản</h1>
          <Button variant="outline" className="gap-2 self-start sm:self-auto" asChild>
            <Link href="/notebooks">
              <ArrowLeft className="h-4 w-4" />
              Quay lại Trang chính
            </Link>
          </Button>
        </div>
        <Card>
          <CardContent className="pt-6">Chỉ admin mới được truy cập trang này.</CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Quản lý tài khoản</h1>
          <p className="text-sm text-muted-foreground">
            Quản lý tài khoản thật theo role, đổi mật khẩu, khóa tài khoản, theo dõi lịch sử hỏi đáp và audit log.
          </p>
        </div>
        <Button variant="outline" className="gap-2 self-start sm:self-auto" asChild>
          <Link href="/notebooks">
            <ArrowLeft className="h-4 w-4" />
            Quay lại Trang chính
          </Link>
        </Button>
      </div>

      {message && (
        <Card>
          <CardContent className="pt-6 text-sm">{message}</CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Xác nhận thao tác quản trị</CardTitle>
          <CardDescription>
            Lý do được lưu vào nhật ký khi sửa, khóa, mở khóa hoặc đặt lại mật khẩu.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="business-reason">Lý do nghiệp vụ</Label>
            <Input
              id="business-reason"
              value={businessReason}
              onChange={(event) => setBusinessReason(event.target.value)}
              placeholder="Ví dụ: Cập nhật phân công cán bộ"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="temporary-password">Mật khẩu tạm khi cần reset</Label>
            <Input
              id="temporary-password"
              type="password"
              value={temporaryPassword}
              onChange={(event) => setTemporaryPassword(event.target.value)}
              placeholder="Tối thiểu 12 ký tự"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{editingUserId ? 'Sửa tài khoản' : 'Tạo tài khoản mới'}</CardTitle>
          <CardDescription>
            {editingUserId
              ? 'Có thể để trống mật khẩu nếu không cần đổi.'
              : 'Tài khoản được tạo với username, email và mật khẩu hash riêng cho từng người dùng.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Tên đăng nhập</Label>
            <Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>{t('common.email')}</Label>
            <Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Mật khẩu</Label>
            <Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Vai trò</Label>
            <select
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as UserRole })}
            >
              <option value="citizen">Người dân</option>
              <option value="officer">Cán bộ</option>
              <option value="admin">{t('common.admin')}</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label>Họ và tên</Label>
            <Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Số điện thoại</Label>
            <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Phường pilot</Label>
            <Input value={form.ward} onChange={(e) => setForm({ ...form, ward: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Đơn vị (Chọn hoặc Nhập)</Label>
            <div className="flex gap-2">
              <select
                className="h-10 rounded-md border border-input bg-background px-3 text-sm flex-1"
                value={form.department}
                onChange={(e) => {
                  const dept = e.target.value;
                  const deptToDomains: Record<string, string[]> = {
                    "Tư pháp - Hộ tịch": ["ho_tich", "chung_thuc"],
                    "Địa chính - Xây dựng": ["dat_dai", "xay_dung"],
                    "Văn hóa - Xã hội": ["an_sinh_y_te_giao_duc", "khieu_nai", "xu_phat"]
                  };
                  setForm({
                    ...form,
                    department: dept,
                    allowed_domains: deptToDomains[dept] || form.allowed_domains
                  });
                }}
              >
                <option value="">-- Chọn phòng ban pilot --</option>
                <option value="Tư pháp - Hộ tịch">Tư pháp - Hộ tịch (Hộ tịch, Chứng thực)</option>
                <option value="Địa chính - Xây dựng">Địa chính - Xây dựng (Đất đai, Xây dựng)</option>
                <option value="Văn hóa - Xã hội">Văn hóa - Xã hội (An sinh - Y tế - Giáo dục, Khiếu nại, Xử phạt)</option>
                <option value="Khác">Khác...</option>
              </select>
              {form.department === 'Khác' && (
                <Input
                  className="flex-1"
                  placeholder="Nhập tên đơn vị khác"
                  onChange={(e) => setForm({ ...form, department: e.target.value })}
                />
              )}
            </div>
          </div>
          <div className="space-y-2">
            <Label>Chức vụ</Label>
            <Input value={form.job_title} onChange={(e) => setForm({ ...form, job_title: e.target.value })} />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Lĩnh vực được phép truy cập (allowed_domains)</Label>
            <div className="flex flex-wrap gap-4 rounded-md border p-3 bg-slate-50 dark:bg-slate-900">
              {[
                { id: 'ho_tich', label: 'Hộ tịch' },
                { id: 'chung_thuc', label: 'Chứng thực' },
                { id: 'dat_dai', label: 'Đất đai' },
                { id: 'xay_dung', label: 'Xây dựng' },
                { id: 'an_sinh_y_te_giao_duc', label: 'An sinh - Y tế - Giáo dục' },
                { id: 'khieu_nai', label: 'Khiếu nại' },
                { id: 'xu_phat', label: 'Xử phạt' },
              ].map((domain) => (
                <div key={domain.id} className="flex items-center gap-2">
                  <input
                    id={`domain_${domain.id}`}
                    type="checkbox"
                    checked={form.allowed_domains.includes(domain.id)}
                    onChange={(e) => {
                      const nextDomains = e.target.checked
                        ? [...form.allowed_domains, domain.id]
                        : form.allowed_domains.filter((d) => d !== domain.id);
                      setForm({ ...form, allowed_domains: nextDomains });
                    }}
                  />
                  <Label htmlFor={`domain_${domain.id}`} className="cursor-pointer font-normal text-xs">
                    {domain.label}
                  </Label>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Ghi chú</Label>
            <Textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
          {editingUserId && (
            <div className="flex items-center gap-2 md:col-span-2">
              <input
                id="is_active"
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              <Label htmlFor="is_active">Tài khoản đang hoạt động</Label>
            </div>
          )}
          <div className="flex gap-2 md:col-span-2">
            <Button onClick={handleCreateOrUpdate} disabled={submitting}>
              {submitting ? 'Đang lưu...' : editingUserId ? 'Lưu thay đổi' : 'Tạo tài khoản'}
            </Button>
            {editingUserId && (
              <Button
                variant="outline"
                onClick={() => {
                  setEditingUserId(null)
                  setForm(emptyForm)
                }}
              >
                Hủy sửa
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Danh sách tài khoản</CardTitle>
          <CardDescription>{loading ? 'Đang tải...' : `${users.length} tài khoản`}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {users.map((user) => (
            <div key={user.id} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="font-medium">{user.profile?.full_name || user.username}</div>
                  <div className="text-sm text-muted-foreground">@{user.username} · {user.email}</div>
                  <div className="text-sm text-muted-foreground">
                    Vai trò: {user.role} · Trạng thái: {user.is_active ? 'Hoạt động' : 'Đã khóa'}
                  </div>
                  {(user.profile?.department || user.profile?.job_title || user.profile?.ward) && (
                    <div className="text-sm text-muted-foreground">
                      {[
                        user.profile?.ward,
                        user.profile?.department,
                        user.profile?.job_title,
                      ].filter(Boolean).join(' · ')}
                    </div>
                  )}
                  {user.profile?.allowed_domains && user.profile.allowed_domains.length > 0 && (
                    <div className="text-xs text-blue-600 dark:text-blue-400">
                      Lĩnh vực được phép: {user.profile.allowed_domains.join(', ')}
                    </div>
                  )}
                  <div className="text-xs text-muted-foreground">
                    Tạo lúc: {formatDateTime(user.created)} · Đăng nhập gần nhất: {formatDateTime(user.last_login_at)}
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => handleEdit(user)}>Sửa</Button>
                  <Button
                    variant="outline"
                    disabled={submitting}
                    onClick={() => void handleResetPassword(user)}
                  >
                    Đặt mật khẩu tạm
                  </Button>
                  {user.is_active && user.username !== 'admin' && (
                    <Button variant="outline" onClick={() => void handleDeactivate(user.id)}>Khóa</Button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t('common.auditLog')}</CardTitle>
            <CardDescription>Xem thao tác quản trị theo role, action và khoảng ngày.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <Input
                placeholder="Role: admin / officer / citizen"
                value={filters.auditRole}
                onChange={(e) => setFilters((prev) => ({ ...prev, auditRole: e.target.value }))}
              />
              <Input
                placeholder="Action: user.create, legal.candidate.review..."
                value={filters.auditAction}
                onChange={(e) => setFilters((prev) => ({ ...prev, auditAction: e.target.value }))}
              />
              <div className="space-y-2">
                <Label>Từ ngày</Label>
                <Input
                  type="date"
                  value={filters.auditDateFrom}
                  max={today}
                  onChange={(e) => setFilters((prev) => ({ ...prev, auditDateFrom: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label>Đến ngày</Label>
                <Input
                  type="date"
                  value={filters.auditDateTo}
                  max={today}
                  onChange={(e) => setFilters((prev) => ({ ...prev, auditDateTo: e.target.value }))}
                />
              </div>
            </div>
            <Button variant="outline" onClick={() => void loadAll()}>Áp dụng lọc</Button>
            <div className="space-y-3">
              {auditLogs.map((log) => (
                <div key={log.id} className="rounded-lg border p-3 text-sm">
                  <div className="font-medium">{log.action}</div>
                  <div className="text-muted-foreground">
                    {log.actor_role || 'unknown'} · {log.entity_type || 'entity'} · {formatDateTime(log.created)}
                  </div>
                  {log.entity_id && <div className="text-xs text-muted-foreground">{log.entity_id}</div>}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Lịch sử hỏi đáp</CardTitle>
            <CardDescription>Lọc theo role, lĩnh vực và khoảng ngày để soi chất lượng pilot.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <Input
                placeholder={t("common.role")}
                value={filters.askRole}
                onChange={(e) => setFilters((prev) => ({ ...prev, askRole: e.target.value }))}
              />
              <Input
                placeholder="Lĩnh vực"
                value={filters.askDomain}
                onChange={(e) => setFilters((prev) => ({ ...prev, askDomain: e.target.value }))}
              />
              <div className="space-y-2">
                <Label>Từ ngày</Label>
                <Input
                  type="date"
                  value={filters.askDateFrom}
                  max={today}
                  onChange={(e) => setFilters((prev) => ({ ...prev, askDateFrom: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label>Đến ngày</Label>
                <Input
                  type="date"
                  value={filters.askDateTo}
                  max={today}
                  onChange={(e) => setFilters((prev) => ({ ...prev, askDateTo: e.target.value }))}
                />
              </div>
            </div>
            <Button variant="outline" onClick={() => void loadAll()}>Áp dụng lọc</Button>
            <div className="space-y-3">
              {askHistory.map((item) => (
                <div key={item.id} className="rounded-lg border p-3 text-sm">
                  <div className="font-medium">{item.question}</div>
                  <div className="text-muted-foreground">
                    {item.owner_role || 'unknown'}
                    {item.domain ? ` · ${item.domain}` : ''}
                    {item.duration_ms ? ` · ${item.duration_ms} ms` : ''}
                    {' · '}
                    {formatDateTime(item.created)}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
