'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BookOpenText, Building2, Clock3, ExternalLink, Eye, History, KeyRound, LockKeyhole, Mail, MoreHorizontal, Pencil, Phone, Plus, RefreshCw, RotateCcw, Search, ShieldCheck, SlidersHorizontal, Trash2, Unlock, UserCheck, UserMinus, UsersRound, UserX } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { getApiUrl } from '@/lib/config'
import { sessionSecurityHeaders } from '@/lib/api/session-security'
import { useAuthStore } from '@/lib/stores/auth-store'
import { AppShell } from '@/components/layout/AppShell'
import {
  buildAccountActionRequest,
  encodeBusinessReason,
  type AccountAction,
  validateAccountContactInput,
} from '@/lib/utils/user-account-actions'
import { formatApiError } from '@/lib/utils/error-handler'
import { activityActionLabel, roleLabel } from '@/lib/utils/system-labels'
import type { SettingsResponse } from '@/lib/types/api'

type UserRole = 'citizen' | 'officer' | 'admin'

type ManagedUser = {
  id: string
  username: string
  email: string
  role: UserRole
  is_active: boolean
  is_deleted?: boolean
  deleted_at?: string | null
  created?: string
  last_login_at?: string | null
  profile?: {
    full_name?: string
    phone?: string
    ward?: string
    department?: string
    organization_unit_id?: string | null
    allowed_domains?: string[]
    job_title?: string
    notes?: string
    must_change_password?: boolean
  }
}

type AuditLog = {
  id: string
  actor_user?: string | null
  actor_role?: string | null
  target_user?: string | null
  action: string
  details?: Record<string, unknown>
  created?: string
}

type AskHistorySource = {
  chunk_id?: string
  law_number?: string
  document_title?: string
  article_number?: string
  source_url?: string
}

type AskHistoryItem = {
  id: string
  owner_user?: string | null
  owner_role?: UserRole | null
  question: string
  answer?: string | null
  domain?: string | null
  department?: string | null
  grounding_status?: string | null
  duration_ms?: number | null
  created?: string
  sources?: AskHistorySource[]
}

type AccountStatusFilter = 'all' | 'active' | 'locked' | 'deleted' | 'password-change'

type AccountForm = {
  username: string
  email: string
  password: string
  role: UserRole
  full_name: string
  phone: string
  ward: string
  department: string
  organization_unit_id: string
  allowed_domains: string[]
  job_title: string
  notes: string
}

const DEFAULT_WARD = 'Phường Lê Chân, Hải Phòng'
const DAY_IN_MS = 24 * 60 * 60 * 1000

export function futureDateTimeLocal(days: number, now = new Date()): string {
  const expiresAt = new Date(now.getTime() + days * DAY_IN_MS)
  const localTimestamp = new Date(expiresAt.getTime() - expiresAt.getTimezoneOffset() * 60_000)
  return localTimestamp.toISOString().slice(0, 16)
}

const ROLE_LABEL: Record<UserRole, string> = {
  citizen: 'Người dân',
  officer: 'Cán bộ',
  admin: 'Quản trị viên',
}

const AUDIT_ACTION_LABEL: Record<string, string> = {
  'user.create': 'Tạo tài khoản',
  'user.update': 'Cập nhật thông tin',
  'user.activate': 'Mở khóa tài khoản',
  'user.deactivate': 'Khóa tài khoản',
  'user.soft_delete': 'Xóa tài khoản',
  'user.password.reset_by_admin': 'Đổi mật khẩu bởi quản trị viên',
  'user.password.change': 'Người dùng đổi mật khẩu',
  'user.password.reset_by_token': 'Đổi mật khẩu bằng mã khôi phục',
  'user.password.reset_requested': 'Yêu cầu khôi phục mật khẩu',
  'user.ask_history.view': 'Xem lịch sử hỏi đáp',
}

const GROUNDING_STATUS_LABEL: Record<string, string> = {
  fully_grounded: 'Đủ căn cứ',
  partially_grounded: 'Căn cứ một phần',
  ungrounded: 'Chưa đủ căn cứ',
  unknown: 'Chưa xác định',
}

const DOMAIN_LABEL: Record<string, string> = {
  ho_tich_chung_thuc: 'Hộ tịch và chứng thực',
  ho_tich: 'Hộ tịch',
  chung_thuc: 'Chứng thực',
  dat_dai_xay_dung: 'Đất đai và xây dựng',
  dat_dai: 'Đất đai',
  xay_dung: 'Xây dựng',
  an_sinh_y_te_giao_duc: 'An sinh, y tế và giáo dục',
  hanh_chinh_cong: 'Hành chính công',
  trat_tu_do_thi: 'Trật tự đô thị',
  cu_tru_an_ninh: 'Cư trú và an ninh trật tự',
  khieu_nai_to_cao_xu_phat: 'Khiếu nại, tố cáo và xử phạt',
  khieu_nai: 'Khiếu nại',
  xu_phat: 'Xử phạt',
}

function createEmptyForm(): AccountForm {
  return {
    username: '',
    email: '',
    password: '',
    role: 'citizen',
    full_name: '',
    phone: '',
    ward: DEFAULT_WARD,
    department: '',
    organization_unit_id: '',
    allowed_domains: [],
    job_title: '',
    notes: '',
  }
}

function formatDateTime(value?: string | null) {
  if (!value) return 'Chưa đăng nhập'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('vi-VN')
}

function errorMessage(detail: unknown, fallback: string) {
  return formatApiError({ detail }, fallback)
}

type OfficerUnitGrant = {
  id: string
  organization_unit_id: string
  domain_codes: string[]
  reason: string
  expires_at: string
}

function domainLabel(domain: string) {
  return DOMAIN_LABEL[domain] || `Mã lĩnh vực nội bộ: ${domain}`
}

function safeHttpUrl(value?: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

function accountInitials(user: ManagedUser) {
  const source = (user.profile?.full_name || user.username).trim()
  const parts = source.split(/\s+/).filter(Boolean)
  return (parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}` : source.slice(0, 2)).toLocaleUpperCase('vi-VN')
}

export default function UsersPage() {
  const token = useAuthStore((state) => state.token)
  const role = useAuthStore((state) => state.role)
  const authRequired = useAuthStore((state) => state.authRequired)
  const currentUserId = useAuthStore((state) => state.userId)

  const [users, setUsers] = useState<ManagedUser[]>([])
  const [organizationUnits, setOrganizationUnits] = useState<Array<{ id: string; name: string; domain_codes: string[]; is_active: boolean }>>([])
  const [organizationRoutingMode, setOrganizationRoutingMode] = useState<
    'legacy' | 'shadow' | 'hybrid' | 'unit_primary'
  >('legacy')
  const [unitGrants, setUnitGrants] = useState<OfficerUnitGrant[]>([])
  const [grantUnitId, setGrantUnitId] = useState('')
  const [grantDomains, setGrantDomains] = useState<string[]>([])
  const [grantReason, setGrantReason] = useState('')
  const [grantExpiresAt, setGrantExpiresAt] = useState('')
  const [savingGrant, setSavingGrant] = useState(false)
  const [form, setForm] = useState<AccountForm>(createEmptyForm)
  const [editingUserId, setEditingUserId] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [searchText, setSearchText] = useState('')
  // Tombstoned test/legacy accounts remain available through the explicit
  // “Đã xóa” filter, but should not dominate the initial working list.
  const [statusFilter, setStatusFilter] = useState<AccountStatusFilter>('active')
  const [roleFilter, setRoleFilter] = useState<'all' | UserRole>('all')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  // Keep a synchronous lock in addition to the disabled button. React state
  // updates happen after the event handler returns, so a rapid double-click
  // could otherwise start two POST requests before the button re-renders.
  const submitLockRef = useRef(false)
  const [message, setMessage] = useState<string | null>(null)
  const [detailUser, setDetailUser] = useState<ManagedUser | null>(null)
  const [editReason, setEditReason] = useState('')
  const [action, setAction] = useState<{ type: AccountAction; user: ManagedUser } | null>(null)
  const [actionReason, setActionReason] = useState('')
  const [temporaryPassword, setTemporaryPassword] = useState('')
  const [auditOpen, setAuditOpen] = useState(false)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditError, setAuditError] = useState<string | null>(null)
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [auditTarget, setAuditTarget] = useState<ManagedUser | null>(null)
  const [auditFilters, setAuditFilters] = useState({
    action: '',
    actorRole: '',
    reason: '',
    dateFrom: '',
    dateTo: '',
  })
  const [askHistoryOpen, setAskHistoryOpen] = useState(false)
  const [askHistoryLoading, setAskHistoryLoading] = useState(false)
  const [askHistoryError, setAskHistoryError] = useState<string | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const requestedStatus = params.get('status')
    if (requestedStatus && ['all', 'active', 'locked', 'password-change', 'deleted'].includes(requestedStatus)) {
      setStatusFilter(requestedStatus as AccountStatusFilter)
    }
    const requestedRole = params.get('role')
    if (requestedRole && ['all', 'admin', 'officer', 'citizen'].includes(requestedRole)) {
      setRoleFilter(requestedRole as 'all' | UserRole)
    }
  }, [])
  const [askHistoryRows, setAskHistoryRows] = useState<AskHistoryItem[]>([])
  const [askHistoryTarget, setAskHistoryTarget] = useState<ManagedUser | null>(null)
  const [askHistoryFilters, setAskHistoryFilters] = useState({
    domain: '',
    groundingStatus: '',
    dateFrom: '',
    dateTo: '',
  })

  const headers = useMemo(() => {
    const nextHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) nextHeaders.Authorization = `Bearer ${token}`
    if (role) nextHeaders['X-User-Role'] = role
    return nextHeaders
  }, [role, token])

  useEffect(() => {
    if (role !== 'admin') return
    void getApiUrl()
      .then((apiUrl) => fetch(`${apiUrl}/api/settings`, { headers, cache: 'no-store' }))
      .then(async (response): Promise<Partial<SettingsResponse>> => (
        response?.ok ? await response.json() as SettingsResponse : {}
      ))
      .then((settings: Partial<SettingsResponse>) => {
        setOrganizationUnits(
          Array.isArray(settings.organization_units)
            ? settings.organization_units
            : []
        )
        setOrganizationRoutingMode(settings.organization_routing_mode || 'legacy')
      })
      .catch(() => {
        setOrganizationUnits([])
        setOrganizationRoutingMode('legacy')
      })
  }, [headers, role])

  const loadUsers = useCallback(async () => {
    if (authRequired !== false && role !== 'admin') {
      setLoading(false)
      return
    }

    setLoading(true)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/users`, { headers, cache: 'no-store' })
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không tải được danh sách tài khoản.'))
      setUsers(Array.isArray(data) ? data : [])
    } catch (error) {
      setMessage(formatApiError(error, 'Không tải được danh sách tài khoản.'))
    } finally {
      setLoading(false)
    }
  }, [authRequired, headers, role])

  useEffect(() => {
    void loadUsers()
  }, [loadUsers])

  const loadAuditLogs = useCallback(async (targetUserId?: string | null) => {
    setAuditLoading(true)
    setAuditError(null)
    try {
      const apiUrl = await getApiUrl()
      const query = new URLSearchParams({ limit: '200', resource_type: 'user_account' })
      if (targetUserId) query.set('resource_id', targetUserId)
      if (auditFilters.action) query.set('action', auditFilters.action)
      if (auditFilters.actorRole) query.set('actor_role', auditFilters.actorRole)
      if (auditFilters.reason.trim()) query.set('reason', auditFilters.reason.trim())
      if (auditFilters.dateFrom) query.set('date_from', `${auditFilters.dateFrom}T00:00:00+07:00`)
      if (auditFilters.dateTo) query.set('date_to', `${auditFilters.dateTo}T23:59:59+07:00`)
      const response = await fetch(`${apiUrl}/api/users/audit-logs?${query}`, {
        headers,
        cache: 'no-store',
      })
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không tải được lịch sử thao tác.'))
      setAuditLogs(Array.isArray(data) ? data : [])
    } catch (error) {
      setAuditLogs([])
      setAuditError(formatApiError(error, 'Không tải được lịch sử thao tác.'))
    } finally {
      setAuditLoading(false)
    }
  }, [auditFilters, headers])

  const openAuditLog = (user?: ManagedUser) => {
    const target = user || null
    setAuditTarget(target)
    setAuditOpen(true)
    void loadAuditLogs(target?.id)
  }

  const loadAskHistory = useCallback(async (targetUserId?: string | null) => {
    setAskHistoryLoading(true)
    setAskHistoryError(null)
    try {
      const apiUrl = await getApiUrl()
      const query = new URLSearchParams({ limit: '200' })
      if (targetUserId) query.set('user_id', targetUserId)
      if (askHistoryFilters.domain) query.set('domain', askHistoryFilters.domain)
      if (askHistoryFilters.groundingStatus) query.set('grounding_status', askHistoryFilters.groundingStatus)
      if (askHistoryFilters.dateFrom) query.set('date_from', `${askHistoryFilters.dateFrom}T00:00:00+07:00`)
      if (askHistoryFilters.dateTo) query.set('date_to', `${askHistoryFilters.dateTo}T23:59:59+07:00`)
      const response = await fetch(`${apiUrl}/api/users/ask-history?${query}`, {
        headers,
        cache: 'no-store',
      })
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không tải được lịch sử hỏi đáp.'))
      setAskHistoryRows(Array.isArray(data) ? data : [])
    } catch (error) {
      setAskHistoryRows([])
      setAskHistoryError(formatApiError(error, 'Không tải được lịch sử hỏi đáp.'))
    } finally {
      setAskHistoryLoading(false)
    }
  }, [askHistoryFilters, headers])

  const openAskHistory = (user?: ManagedUser) => {
    const target = user || null
    setAskHistoryTarget(target)
    setAskHistoryOpen(true)
    void loadAskHistory(target?.id)
  }

  const filteredUsers = useMemo(() => {
    const keyword = searchText.trim().toLocaleLowerCase()
    return users.filter((user) => {
      if (statusFilter === 'active' && (!user.is_active || user.is_deleted)) return false
      if (statusFilter === 'locked' && (user.is_active || user.is_deleted)) return false
      if (statusFilter === 'deleted' && !user.is_deleted) return false
      if (statusFilter === 'password-change' && (!user.profile?.must_change_password || user.is_deleted)) return false
      if (roleFilter !== 'all' && user.role !== roleFilter) return false
      if (!keyword) return true
      return [
        user.username,
        user.email,
        user.profile?.full_name,
        user.profile?.phone,
        user.profile?.department,
        user.profile?.job_title,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(keyword))
    })
  }, [roleFilter, searchText, statusFilter, users])

  const usersById = useMemo(
    () => new Map(users.map((user) => [user.id, user])),
    [users],
  )

  const counts = useMemo(() => ({
    total: users.length,
    active: users.filter((user) => user.is_active && !user.is_deleted).length,
    locked: users.filter((user) => !user.is_active && !user.is_deleted).length,
    passwordChange: users.filter((user) => user.profile?.must_change_password && !user.is_deleted).length,
    deleted: users.filter((user) => user.is_deleted).length,
  }), [users])

  const pageSize = 20
  const pageCount = Math.max(1, Math.ceil(filteredUsers.length / pageSize))
  const pagedUsers = filteredUsers.slice((page - 1) * pageSize, page * pageSize)

  useEffect(() => {
    setPage(1)
  }, [roleFilter, searchText, statusFilter])

  const resetForm = () => {
    setForm(createEmptyForm())
    setEditingUserId(null)
    setEditReason('')
    setUnitGrants([])
    setGrantUnitId('')
    setGrantReason('')
    setGrantExpiresAt('')
  }

  const openCreateForm = () => {
    resetForm()
    setMessage(null)
    setFormOpen(true)
  }

  const openEditForm = (user: ManagedUser) => {
    const department = user.profile?.department || ''
    setEditingUserId(user.id)
    setForm({
      username: user.username,
      email: user.email,
      password: '',
      role: user.role,
      full_name: user.profile?.full_name || '',
      phone: user.profile?.phone || '',
      ward: user.profile?.ward || DEFAULT_WARD,
      department,
      organization_unit_id: user.profile?.organization_unit_id || '',
      allowed_domains: user.profile?.allowed_domains || [],
      job_title: user.profile?.job_title || '',
      notes: user.profile?.notes || '',
    })
    setEditReason('')
    setMessage(null)
    setFormOpen(true)
    setUnitGrants([])
    if (user.role === 'officer' && organizationRoutingMode !== 'legacy') {
      void loadUnitGrants(user.id)
    }
  }

  const loadUnitGrants = async (userId: string) => {
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/users/${userId}/unit-grants`, {
        headers,
        cache: 'no-store'
      })
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không tải được quyền hỗ trợ liên phòng.'))
      setUnitGrants(Array.isArray(data) ? data : [])
    } catch (error) {
      setMessage(formatApiError(error, 'Không tải được quyền hỗ trợ liên phòng.'))
    }
  }

  const addUnitGrant = async () => {
    if (!editingUserId || !grantUnitId || grantReason.trim().length < 5 || !grantExpiresAt) {
      setMessage('Chọn phòng ban, thời hạn và nhập lý do cấp quyền hỗ trợ.')
      return
    }
    const unit = organizationUnits.find((item) => item.id === grantUnitId)
    if (!unit) return
    setSavingGrant(true)
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(`${apiUrl}/api/users/${editingUserId}/unit-grants`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...headers, ...sessionSecurityHeaders('POST') },
        body: JSON.stringify({
          organization_unit_id: unit.id,
            domain_codes: grantDomains,
          reason: grantReason.trim(),
          expires_at: new Date(grantExpiresAt).toISOString()
        })
      })
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không thể cấp quyền hỗ trợ liên phòng.'))
        setGrantUnitId('')
        setGrantDomains([])
      setGrantReason('')
      setGrantExpiresAt('')
      await loadUnitGrants(editingUserId)
      toast.success('Đã cấp quyền hỗ trợ liên phòng có thời hạn.')
    } catch (error) {
      setMessage(formatApiError(error, 'Không thể cấp quyền hỗ trợ liên phòng.'))
    } finally {
      setSavingGrant(false)
    }
  }

  const revokeUnitGrant = async (grant: OfficerUnitGrant) => {
    if (!editingUserId || !window.confirm('Thu hồi quyền hỗ trợ liên phòng này?')) return
    try {
      const apiUrl = await getApiUrl()
      const response = await fetch(
        `${apiUrl}/api/users/${editingUserId}/unit-grants/${encodeURIComponent(grant.id)}`,
        {
          method: 'DELETE',
          credentials: 'include',
          headers: {
            ...headers,
            'X-Business-Reason': encodeBusinessReason('Thu hồi quyền hỗ trợ liên phòng theo quyết định quản trị'),
            ...sessionSecurityHeaders('DELETE')
          }
        }
      )
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không thể thu hồi quyền hỗ trợ.'))
      await loadUnitGrants(editingUserId)
      toast.success('Đã thu hồi quyền hỗ trợ liên phòng.')
    } catch (error) {
      setMessage(formatApiError(error, 'Không thể thu hồi quyền hỗ trợ.'))
    }
  }

  const updateRole = (nextRole: UserRole) => {
    setForm((current) => ({
      ...current,
      role: nextRole,
      department: nextRole === 'officer' ? current.department : '',
      organization_unit_id: nextRole === 'officer' ? current.organization_unit_id : '',
      allowed_domains: nextRole === 'officer' ? current.allowed_domains : [],
      job_title: nextRole === 'officer' ? current.job_title : '',
    }))
  }

  const handleSave = async () => {
    const isEditing = Boolean(editingUserId)
    if (submitting || submitLockRef.current) return
    if (form.username.trim().length < 3) {
      setMessage('Tên đăng nhập cần có ít nhất 3 ký tự.')
      return
    }
    const contactError = validateAccountContactInput(form.email, form.phone)
    if (contactError) {
      setMessage(contactError)
      return
    }
    if (!isEditing && form.password.length < 12) {
      setMessage('Mật khẩu khởi tạo cần có ít nhất 12 ký tự.')
      return
    }
    if (
      form.role === 'officer' &&
      !form.organization_unit_id
    ) {
      setMessage('Vui lòng chọn phòng ban chính của cán bộ.')
      return
    }
    if (isEditing && editReason.trim().length < 3) {
      setMessage('Vui lòng nêu lý do cập nhật tài khoản.')
      return
    }

    submitLockRef.current = true
    setSubmitting(true)
    setMessage(null)
    try {
      const apiUrl = await getApiUrl()
      const payload = {
        username: form.username.trim(),
        email: form.email.trim(),
        ...(isEditing ? {} : { password: form.password }),
        role: form.role,
        full_name: form.full_name.trim(),
        phone: form.phone.trim(),
        ward: form.ward.trim() || DEFAULT_WARD,
      department: form.role === 'officer' ? form.department.trim() : '',
        organization_unit_id: form.role === 'officer' ? form.organization_unit_id || null : null,
        allowed_domains: form.role === 'officer' ? form.allowed_domains : [],
        job_title: form.role === 'officer' ? form.job_title.trim() : '',
        notes: form.notes.trim(),
      }
      const response = await fetch(
        isEditing ? `${apiUrl}/api/users/${editingUserId}` : `${apiUrl}/api/users`,
        {
          method: isEditing ? 'PUT' : 'POST',
          credentials: 'include',
          headers: {
            ...headers,
            ...(isEditing ? { 'X-Business-Reason': encodeBusinessReason(editReason) } : {}),
            ...sessionSecurityHeaders(isEditing ? 'PUT' : 'POST'),
          },
          body: JSON.stringify(payload),
        },
      )
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không thể lưu tài khoản.'))
      const successMessage = isEditing
        ? `Đã cập nhật tài khoản ${payload.username} thành công.`
        : `Đã tạo tài khoản ${payload.username} thành công.`
      setMessage(successMessage)
      toast.success(successMessage)
      resetForm()
      setFormOpen(false)
      await loadUsers()
    } catch (error) {
      setMessage(formatApiError(error, 'Không thể lưu tài khoản.'))
    } finally {
      submitLockRef.current = false
      setSubmitting(false)
    }
  }

  const openAction = (type: AccountAction, user: ManagedUser) => {
    setAction({ type, user })
    setActionReason('')
    setTemporaryPassword('')
  }

  const executeAction = async () => {
    if (!action) return
    if (actionReason.trim().length < 3) {
      setMessage('Vui lòng nêu lý do nghiệp vụ cho thao tác này.')
      return
    }
    if (action.type === 'reset-password' && temporaryPassword.length < 12) {
      setMessage('Mật khẩu tạm cần có ít nhất 12 ký tự.')
      return
    }

    setSubmitting(true)
    setMessage(null)
    try {
      const apiUrl = await getApiUrl()
      const request = buildAccountActionRequest(
        apiUrl,
        action.user.id,
        action.type,
        temporaryPassword,
      )
      const response = await fetch(request.url, {
          method: request.method,
          credentials: 'include',
          headers: {
            ...headers,
            'X-Business-Reason': encodeBusinessReason(actionReason),
            ...sessionSecurityHeaders(request.method),
          },
          body: request.body,
        })
      const data = await response.json()
      if (!response.ok) throw new Error(errorMessage(data.detail, 'Không thể thực hiện thao tác.'))
      setMessage(
        action.type === 'activate'
          ? 'Đã mở khóa tài khoản.'
          : action.type === 'deactivate'
            ? 'Đã khóa tài khoản và đăng xuất các phiên đang hoạt động.'
            : action.type === 'soft-delete'
              ? `Đã xóa mềm tài khoản ${action.user.username}; lịch sử quản trị vẫn được lưu giữ.`
              : `Đã đặt mật khẩu tạm cho ${action.user.username}; người dùng phải đổi mật khẩu khi đăng nhập.`,
      )
      setAction(null)
      await loadUsers()
    } catch (error) {
      setMessage(formatApiError(error, 'Không thể thực hiện thao tác.'))
    } finally {
      setSubmitting(false)
    }
  }

  if (authRequired !== false && role !== 'admin') {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
          <div className="mx-auto max-w-xl space-y-4">
            <h1 className="text-2xl font-semibold">Quản lý tài khoản</h1>
            <Card><CardContent className="pt-6">Chỉ quản trị viên mới có thể quản lý tài khoản.</CardContent></Card>
          </div>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
        <div className="mx-auto max-w-[1440px] space-y-5" data-testid="user-management-page">
      <section className="relative overflow-hidden rounded-2xl border border-border/70 bg-gradient-to-br from-card via-card to-primary/[0.06] p-5 shadow-sm md:p-6">
        <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-primary/10 blur-3xl" />
        <div className="relative flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 items-start gap-4">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-md shadow-primary/20">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Quản lý tài khoản</h1>
                <Badge variant="secondary" className="rounded-full px-2.5">Quản trị hệ thống</Badge>
              </div>
              <p className="mt-1.5 max-w-2xl text-sm leading-6 text-muted-foreground">Quản lý hồ sơ, phân quyền và bảo mật người dùng tại một nơi.</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" className="bg-background/70" onClick={() => openAskHistory()} data-testid="open-ask-history"><BookOpenText className="mr-2 h-4 w-4" />Lịch sử hỏi đáp</Button>
            <Button variant="outline" className="bg-background/70" onClick={() => openAuditLog()} data-testid="open-audit-log"><History className="mr-2 h-4 w-4" />Nhật ký</Button>
            <Button variant="outline" className="bg-background/70 px-3" onClick={() => void loadUsers()} disabled={loading} aria-label="Làm mới danh sách"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></Button>
            <Button className="shadow-sm" onClick={openCreateForm}><Plus className="mr-2 h-4 w-4" />Tạo tài khoản</Button>
          </div>
        </div>
      </section>

      {message && <div className="rounded-xl border border-primary/20 bg-primary/[0.06] px-4 py-3 text-sm text-foreground shadow-sm">{message}</div>}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {([
          { label: 'Tổng tài khoản', value: counts.total, filter: 'all', icon: UsersRound, tone: 'bg-sky-500/10 text-sky-600 dark:text-sky-400' },
          { label: 'Đang hoạt động', value: counts.active, filter: 'active', icon: UserCheck, tone: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
          { label: 'Đã khóa', value: counts.locked, filter: 'locked', icon: UserX, tone: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
          { label: 'Chờ đổi mật khẩu', value: counts.passwordChange, filter: 'password-change', icon: KeyRound, tone: 'bg-violet-500/10 text-violet-600 dark:text-violet-400' },
          { label: 'Đã xóa', value: counts.deleted, filter: 'deleted', icon: UserMinus, tone: 'bg-rose-500/10 text-rose-600 dark:text-rose-400' },
        ] as Array<{ label: string; value: number; filter: AccountStatusFilter; icon: typeof UsersRound; tone: string }>).map((item) => {
          const Icon = item.icon
          const selected = statusFilter === item.filter
          return (
            <button key={item.label} type="button" className="min-w-0 text-left" onClick={() => setStatusFilter(item.filter)} aria-pressed={selected}>
              <div className={`h-full rounded-2xl border p-4 transition-all ${selected ? 'border-primary/50 bg-primary/[0.05] shadow-sm ring-1 ring-primary/10' : 'border-border/70 bg-card hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-sm'}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0"><p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">{item.label}</p><p className="mt-2 text-2xl font-semibold tracking-tight">{item.value}</p></div>
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${item.tone}`}><Icon className="h-5 w-5" /></span>
                </div>
              </div>
            </button>
          )
        })}
      </section>

      <Dialog open={formOpen} onOpenChange={(open) => { setFormOpen(open); if (!open) resetForm() }}>
        <DialogContent className="max-h-[92vh] overflow-y-auto p-0 sm:max-w-3xl">
          <DialogHeader className="sticky top-0 z-10 border-b bg-background/95 px-6 py-5 backdrop-blur">
            <DialogTitle className="flex items-center gap-3 text-xl"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">{editingUserId ? <Pencil className="h-4 w-4" /> : <Plus className="h-4 w-4" />}</span>{editingUserId ? 'Cập nhật tài khoản' : 'Tạo tài khoản mới'}</DialogTitle>
            <DialogDescription>{editingUserId ? 'Chỉnh sửa hồ sơ và phân quyền. Mật khẩu được quản lý bằng thao tác riêng.' : 'Tạo hồ sơ đăng nhập mới và thiết lập quyền sử dụng ban đầu.'}</DialogDescription>
          </DialogHeader>
          <div className="space-y-6 px-6 py-5">
            <div>
              <div className="mb-4 flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-lg bg-muted"><UsersRound className="h-3.5 w-3.5" /></span><div><h3 className="text-sm font-semibold">Thông tin cơ bản</h3><p className="text-xs text-muted-foreground">Thông tin nhận diện và liên hệ của tài khoản.</p></div></div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2"><Label htmlFor="username">Tên đăng nhập</Label><Input id="username" value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} /></div>
              <div className="space-y-2"><Label htmlFor="email">Email</Label><Input id="email" type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></div>
              {!editingUserId && <div className="space-y-2"><Label htmlFor="password">Mật khẩu khởi tạo</Label><Input id="password" type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="Tối thiểu 12 ký tự" /></div>}
              <div className="space-y-2"><Label htmlFor="role">Loại tài khoản</Label><select id="role" className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={form.role} onChange={(event) => updateRole(event.target.value as UserRole)}><option value="citizen">Người dân</option><option value="officer">Cán bộ</option><option value="admin">Quản trị viên</option></select></div>
              <div className="space-y-2"><Label htmlFor="full-name">Họ và tên</Label><Input id="full-name" value={form.full_name} onChange={(event) => setForm({ ...form, full_name: event.target.value })} /></div>
              <div className="space-y-2"><Label htmlFor="phone">Số điện thoại</Label><Input id="phone" inputMode="tel" value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} /></div>
            </div>
            </div>

            {form.role === 'officer' && (
              <div className="space-y-4 rounded-xl border border-primary/15 bg-primary/[0.035] p-4">
                <div className="flex items-start gap-3"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><Building2 className="h-4 w-4" /></span><div><h3 className="font-semibold">Phân công cán bộ</h3><p className="text-sm text-muted-foreground">Đơn vị và phạm vi quyết định dữ liệu nghiệp vụ cán bộ được sử dụng.</p></div></div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2"><Label htmlFor="organization-unit">Đơn vị cấp 2 theo cơ cấu hệ thống</Label><select id="organization-unit" className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={form.organization_unit_id} onChange={(event) => { const unit = organizationUnits.find((item) => item.id === event.target.value); setForm((current) => ({ ...current, organization_unit_id: event.target.value, department: unit?.name || current.department, allowed_domains: unit?.domain_codes || current.allowed_domains })) }}><option value="">Chưa xác định — cần quản trị viên xác nhận</option>{organizationUnits.filter((unit) => unit.is_active).map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></div>
                  <div className="space-y-2"><Label>Phạm vi được kế thừa</Label><div className="min-h-10 rounded-md border bg-muted/30 px-3 py-2 text-sm">{organizationUnits.find(unit => unit.id === form.organization_unit_id && unit.is_active)?.domain_codes.map(domainLabel).join(', ') || 'Chọn phòng ban đang hoạt động để hệ thống tự xác định'}</div><p className="text-xs text-muted-foreground">Lĩnh vực được tính động từ phòng ban, không nhập độc lập tại tài khoản.</p></div>
                  <div className="space-y-2"><Label htmlFor="job-title">Chức vụ</Label><Input id="job-title" value={form.job_title} onChange={(event) => setForm({ ...form, job_title: event.target.value })} placeholder="Ví dụ: Công chức tư pháp - hộ tịch" /></div>
                </div>
                {editingUserId && organizationRoutingMode !== 'legacy' && (
                  <div className="space-y-3 rounded-lg border bg-background p-3">
                    <div>
                      <p className="text-sm font-medium">Hỗ trợ liên phòng có thời hạn</p>
                      <p className="text-xs text-muted-foreground">Không sửa lĩnh vực của tài khoản. Mỗi quyền đều có lý do, ngày hết hạn và lịch sử quản trị.</p>
                    </div>
                    {unitGrants.length > 0 ? (
                      <div className="space-y-2">
                        {unitGrants.map((grant) => (
                          <div key={grant.id} className="flex flex-col gap-2 rounded-md border p-2 text-xs sm:flex-row sm:items-center sm:justify-between">
                            <div>
                              <p className="font-medium">{organizationUnits.find((unit) => unit.id === grant.organization_unit_id)?.name || 'Phòng ban hỗ trợ'}</p>
                              <p className="text-muted-foreground">Đến {formatDateTime(grant.expires_at)} · {grant.reason}</p>
                              <p>{grant.domain_codes.length ? grant.domain_codes.join(', ') : 'Toàn bộ phạm vi của phòng ban'}</p>
                            </div>
                            <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => void revokeUnitGrant(grant)}>Thu hồi</Button>
                          </div>
                        ))}
                      </div>
                    ) : <p className="text-xs text-muted-foreground">Chưa có quyền hỗ trợ liên phòng còn hiệu lực.</p>}
                    <div className="grid gap-2 md:grid-cols-3">
                      <select className="h-10 rounded-md border bg-background px-3 text-sm" value={grantUnitId} onChange={(event) => { setGrantUnitId(event.target.value); setGrantDomains([]) }} aria-label="Phòng ban hỗ trợ">
                        <option value="">Chọn phòng ban hỗ trợ</option>
                        {organizationUnits.filter((unit) => unit.is_active && unit.id !== form.organization_unit_id).map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
                      </select>
                      <div className="space-y-2">
                        <Input type="datetime-local" value={grantExpiresAt} onChange={(event) => setGrantExpiresAt(event.target.value)} aria-label="Thời hạn quyền hỗ trợ" />
                        <div className="flex flex-wrap gap-1" role="group" aria-label="Chọn nhanh thời hạn quyền hỗ trợ">
                          {[1, 7, 30].map((days) => (
                            <Button
                              key={days}
                              type="button"
                              size="sm"
                              variant="ghost"
                              className="h-7 px-2 text-xs"
                              onClick={() => setGrantExpiresAt(futureDateTimeLocal(days))}
                            >
                              {days} ngày
                            </Button>
                          ))}
                        </div>
                      </div>
                      <Input value={grantReason} onChange={(event) => setGrantReason(event.target.value)} placeholder="Lý do cấp quyền (ít nhất 5 ký tự)" aria-label="Lý do cấp quyền" />
                    </div>
                    {grantUnitId && <fieldset className="space-y-2 text-sm"><legend>Giới hạn lĩnh vực hỗ trợ (không chọn để hỗ trợ toàn phòng)</legend>
                      {organizationUnits.find(unit => unit.id === grantUnitId)?.domain_codes.map(domain => <label key={domain} className="mr-3 inline-flex items-center gap-2"><input type="checkbox" checked={grantDomains.includes(domain)} onChange={event => setGrantDomains(current => event.target.checked ? [...current, domain] : current.filter(value => value !== domain))} />{domain}</label>)}
                    </fieldset>}
                    <Button type="button" size="sm" variant="outline" disabled={savingGrant} onClick={() => void addUnitGrant()}>{savingGrant ? 'Đang cấp quyền...' : 'Cấp quyền hỗ trợ'}</Button>
                  </div>
                )}
              </div>
            )}

            <details className="group rounded-xl border bg-muted/15 p-4"><summary className="cursor-pointer list-none font-medium"><span className="flex items-center justify-between">Thông tin bổ sung <span className="text-xs font-normal text-muted-foreground group-open:hidden">Mở rộng</span></span></summary><div className="mt-4 grid gap-4 border-t pt-4 md:grid-cols-2"><div className="space-y-2"><Label htmlFor="ward">Phường/xã quản lý</Label><Input id="ward" value={form.ward} onChange={(event) => setForm({ ...form, ward: event.target.value })} /></div><div className="space-y-2 md:col-span-2"><Label htmlFor="notes">Ghi chú nội bộ</Label><Textarea id="notes" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></div></div></details>

            {editingUserId && <div className="space-y-2"><Label htmlFor="edit-reason">Lý do cập nhật</Label><Textarea id="edit-reason" value={editReason} onChange={(event) => setEditReason(event.target.value)} placeholder="Ví dụ: điều chỉnh thông tin đơn vị theo quyết định phân công" /></div>}
          </div>
          <DialogFooter className="sticky bottom-0 z-10 border-t bg-background/95 px-6 py-4 backdrop-blur">
            <Button variant="outline" onClick={() => { resetForm(); setFormOpen(false) }}>Hủy</Button>
            <Button onClick={() => void handleSave()} disabled={submitting}>{submitting ? 'Đang lưu...' : editingUserId ? 'Lưu thay đổi' : 'Tạo tài khoản'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <section className="overflow-hidden rounded-2xl border border-border/70 bg-card shadow-sm">
        <div className="flex flex-col gap-4 border-b px-4 py-5 md:px-5 xl:flex-row xl:items-end xl:justify-between">
          <div><div className="flex items-center gap-2"><UsersRound className="h-5 w-5 text-primary" /><h2 className="text-lg font-semibold">Danh sách tài khoản</h2></div><p className="mt-1 text-sm text-muted-foreground">{filteredUsers.length} kết quả phù hợp · tối đa {pageSize} tài khoản mỗi trang</p></div>
          <div className="flex flex-wrap items-center gap-2">
            <SlidersHorizontal className="hidden h-4 w-4 text-muted-foreground sm:block" />
            <select aria-label="Lọc theo vai trò" className="h-10 min-w-[150px] rounded-xl border border-input bg-background px-3 text-sm shadow-sm outline-none focus:border-primary" value={roleFilter} onChange={(event) => setRoleFilter(event.target.value as typeof roleFilter)}><option value="all">Tất cả vai trò</option><option value="admin">Quản trị viên</option><option value="officer">Cán bộ</option><option value="citizen">Người dân</option></select>
            <select aria-label="Lọc theo trạng thái" className="h-10 min-w-[160px] rounded-xl border border-input bg-background px-3 text-sm shadow-sm outline-none focus:border-primary" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as AccountStatusFilter)}><option value="all">Tất cả trạng thái</option><option value="active">Đang hoạt động</option><option value="locked">Đã khóa</option><option value="password-change">Cần đổi mật khẩu</option><option value="deleted">Đã xóa</option></select>
            {(searchText || roleFilter !== 'all' || statusFilter !== 'all') && <Button variant="ghost" size="sm" onClick={() => { setSearchText(''); setRoleFilter('all'); setStatusFilter('all') }}><RotateCcw className="mr-2 h-3.5 w-3.5" />Xóa lọc</Button>}
          </div>
        </div>
        <div className="border-b bg-muted/20 p-4 md:px-5">
          <div className="relative max-w-2xl"><Search className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label="Tìm kiếm tài khoản" className="h-10 rounded-xl border-border/80 bg-background pl-10 shadow-sm" value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="Tìm tên, tài khoản, email, số điện thoại hoặc đơn vị..." /></div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-3 py-20 text-sm text-muted-foreground"><RefreshCw className="h-4 w-4 animate-spin" />Đang tải danh sách tài khoản...</div>
        ) : filteredUsers.length === 0 ? (
          <div className="flex flex-col items-center py-20 text-center"><span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted"><Search className="h-5 w-5 text-muted-foreground" /></span><p className="mt-4 font-medium">Không có tài khoản phù hợp</p><p className="mt-1 text-sm text-muted-foreground">Thử thay đổi từ khóa hoặc đặt lại bộ lọc.</p></div>
        ) : (
          <>
            <div className="hidden grid-cols-[minmax(240px,1.35fr)_minmax(210px,1.1fr)_minmax(170px,.9fr)_160px_112px] gap-4 border-b bg-muted/30 px-5 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground lg:grid">
              <span>Tài khoản</span><span>Liên hệ</span><span>Vai trò & trạng thái</span><span>Hoạt động</span><span className="text-right">Thao tác</span>
            </div>
            <div>
              {pagedUsers.map((user) => (
                <article key={user.id} className={`group grid gap-4 border-b px-4 py-4 transition-colors last:border-b-0 md:px-5 lg:grid-cols-[minmax(240px,1.35fr)_minmax(210px,1.1fr)_minmax(170px,.9fr)_160px_112px] lg:items-center ${user.is_deleted ? 'bg-muted/20 opacity-75' : 'hover:bg-muted/25'}`} data-testid={`managed-user-${user.username}`}>
                  <div className="flex min-w-0 items-center gap-3">
                    <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xs font-bold ${user.is_active && !user.is_deleted ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>{accountInitials(user)}</span>
                    <div className="min-w-0"><button type="button" className="block max-w-full truncate text-left font-semibold hover:text-primary hover:underline" onClick={() => setDetailUser(user)}>{user.profile?.full_name || user.username}</button><p className="truncate text-xs text-muted-foreground">@{user.username}</p>{user.role === 'officer' && user.profile?.department && <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground"><Building2 className="h-3 w-3 shrink-0" />{user.profile.department}</p>}</div>
                  </div>
                  <div className="min-w-0 space-y-1 text-xs text-muted-foreground"><p className="flex items-center gap-1.5 truncate"><Mail className="h-3.5 w-3.5 shrink-0" />{user.email}</p><p className="flex items-center gap-1.5"><Phone className="h-3.5 w-3.5 shrink-0" />{user.profile?.phone || 'Chưa cập nhật số điện thoại'}</p></div>
                  <div className="flex flex-wrap items-center gap-1.5"><Badge variant="secondary" className="rounded-full font-medium">{ROLE_LABEL[user.role]}</Badge><span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${user.is_deleted ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300' : user.is_active ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300' : 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300'}`}><span className="h-1.5 w-1.5 rounded-full bg-current" />{user.is_deleted ? 'Đã xóa' : user.is_active ? 'Hoạt động' : 'Đã khóa'}</span>{user.profile?.must_change_password && !user.is_deleted && <Badge variant="outline" className="rounded-full text-[10px]">Đổi mật khẩu</Badge>}</div>
                  <div className="space-y-1 text-xs text-muted-foreground"><p className="flex items-center gap-1.5"><Clock3 className="h-3.5 w-3.5" />{formatDateTime(user.last_login_at)}</p><p>Tạo {formatDateTime(user.created)}</p></div>
                  <div className="flex items-center justify-end gap-1">
                    <Button size="icon" variant="ghost" className="h-9 w-9" onClick={() => setDetailUser(user)} aria-label={`Xem hồ sơ ${user.username}`} title="Xem hồ sơ"><Eye className="h-4 w-4" /></Button>
                    {!user.is_deleted && <Button size="icon" variant="ghost" className="h-9 w-9" onClick={() => openEditForm(user)} aria-label={`Sửa ${user.username}`} title="Sửa thông tin"><Pencil className="h-4 w-4" /></Button>}
                    {/* This menu opens modal dialogs; non-modal avoids Radix's body pointer-lock race when the menu closes. */}
                    <DropdownMenu modal={false}><DropdownMenuTrigger asChild><Button size="icon" variant="ghost" className="h-9 w-9" aria-label={`Thao tác khác cho ${user.username}`}><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end" className="w-56"><DropdownMenuItem onSelect={() => openAskHistory(user)}><BookOpenText className="mr-2 h-4 w-4" />Lịch sử hỏi đáp</DropdownMenuItem><DropdownMenuItem onSelect={() => openAuditLog(user)}><History className="mr-2 h-4 w-4" />Lịch sử thao tác</DropdownMenuItem>{!user.is_deleted && <DropdownMenuItem onSelect={() => openAction('reset-password', user)}><KeyRound className="mr-2 h-4 w-4" />Đặt lại mật khẩu</DropdownMenuItem>}{!user.is_deleted && <DropdownMenuSeparator />}{!user.is_deleted && (user.is_active ? <DropdownMenuItem disabled={user.id === currentUserId} onSelect={() => openAction('deactivate', user)}><LockKeyhole className="mr-2 h-4 w-4" />Khóa tài khoản</DropdownMenuItem> : <DropdownMenuItem onSelect={() => openAction('activate', user)}><Unlock className="mr-2 h-4 w-4" />Mở khóa tài khoản</DropdownMenuItem>)}{!user.is_deleted && <DropdownMenuItem className="text-destructive focus:text-destructive" disabled={user.id === currentUserId} onSelect={() => openAction('soft-delete', user)}><Trash2 className="mr-2 h-4 w-4" />Xóa tài khoản</DropdownMenuItem>}</DropdownMenuContent></DropdownMenu>
                  </div>
                </article>
              ))}
            </div>
            <div className="flex flex-col items-center justify-between gap-3 border-t bg-muted/15 px-4 py-3 sm:flex-row md:px-5"><p className="text-xs text-muted-foreground">Trang <span className="font-medium text-foreground">{page}/{pageCount}</span> · {filteredUsers.length} tài khoản</p><div className="flex gap-1"><Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Trang trước</Button><Button size="sm" variant="ghost" disabled={page >= pageCount} onClick={() => setPage((current) => Math.min(pageCount, current + 1))}>Trang sau</Button></div></div>
          </>
        )}
      </section>

      <Dialog open={Boolean(detailUser)} onOpenChange={(open) => { if (!open) setDetailUser(null) }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Hồ sơ tài khoản</DialogTitle>
            <DialogDescription>Thông tin nhận diện, liên hệ, phân quyền và trạng thái sử dụng.</DialogDescription>
          </DialogHeader>
          {detailUser && (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-4">
                <div className="mr-auto min-w-0"><p className="text-lg font-semibold">{detailUser.profile?.full_name || detailUser.username}</p><p className="text-sm text-muted-foreground">@{detailUser.username}</p></div>
                <Badge variant="secondary">{ROLE_LABEL[detailUser.role]}</Badge>
                <Badge variant={detailUser.is_deleted || !detailUser.is_active ? 'destructive' : 'outline'}>{detailUser.is_deleted ? 'Đã xóa' : detailUser.is_active ? 'Đang hoạt động' : 'Đã khóa'}</Badge>
              </div>
              <dl className="grid gap-4 text-sm sm:grid-cols-2">
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Họ và tên</dt><dd className="mt-1 font-medium">{detailUser.profile?.full_name || 'Chưa cập nhật'}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Tên đăng nhập</dt><dd className="mt-1 break-all font-medium">@{detailUser.username}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Email</dt><dd className="mt-1 break-all font-medium">{detailUser.email || 'Chưa cập nhật'}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Số điện thoại</dt><dd className="mt-1 font-medium">{detailUser.profile?.phone || 'Chưa cập nhật'}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Đơn vị / chức vụ</dt><dd className="mt-1 font-medium">{[detailUser.profile?.department, detailUser.profile?.job_title].filter(Boolean).join(' · ') || 'Không áp dụng'}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Phường/xã quản lý</dt><dd className="mt-1 font-medium">{detailUser.profile?.ward || 'Chưa cập nhật'}</dd></div>
                <div className="rounded-lg border p-3 sm:col-span-2"><dt className="text-muted-foreground">Phạm vi nghiệp vụ</dt><dd className="mt-1 font-medium">{detailUser.profile?.allowed_domains?.length ? detailUser.profile.allowed_domains.map(domainLabel).join(', ') : 'Không có phạm vi nghiệp vụ riêng'}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Ngày tạo</dt><dd className="mt-1 font-medium">{formatDateTime(detailUser.created)}</dd></div>
                <div className="rounded-lg border p-3"><dt className="text-muted-foreground">Đăng nhập gần nhất</dt><dd className="mt-1 font-medium">{formatDateTime(detailUser.last_login_at)}</dd></div>
                <div className="rounded-lg border p-3 sm:col-span-2"><dt className="text-muted-foreground">Ghi chú nội bộ</dt><dd className="mt-1 whitespace-pre-wrap font-medium">{detailUser.profile?.notes || 'Không có ghi chú'}</dd></div>
              </dl>
              <DialogFooter className="gap-2 sm:justify-between">
                <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => { const selected = detailUser; setDetailUser(null); openAskHistory(selected) }}><BookOpenText className="mr-2 h-4 w-4" />Lịch sử hỏi đáp</Button><Button variant="outline" onClick={() => { const selected = detailUser; setDetailUser(null); openAuditLog(selected) }}><History className="mr-2 h-4 w-4" />Lịch sử thao tác</Button></div>
                {!detailUser.is_deleted && <Button onClick={() => { const selected = detailUser; setDetailUser(null); openEditForm(selected) }}><Pencil className="mr-2 h-4 w-4" />Sửa hồ sơ</Button>}
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(action)} onOpenChange={(open) => { if (!open) setAction(null) }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{action?.type === 'reset-password' ? 'Đổi mật khẩu' : action?.type === 'activate' ? 'Mở khóa tài khoản' : action?.type === 'soft-delete' ? 'Xóa tài khoản' : 'Khóa tài khoản'}</DialogTitle>
            <DialogDescription>{action?.type === 'reset-password' ? `Đặt mật khẩu tạm cho ${action?.user.username}. Người dùng bắt buộc đổi mật khẩu sau khi đăng nhập.` : action?.type === 'activate' ? `Cho phép ${action?.user.username} đăng nhập lại.` : action?.type === 'soft-delete' ? `Xóa mềm ${action?.user.username}. Tài khoản không thể đăng nhập hoặc mở khóa, nhưng nhật ký vẫn được lưu để kiểm tra.` : `Khóa ${action?.user.username} và kết thúc các phiên đăng nhập đang hoạt động.`}</DialogDescription>
          </DialogHeader>
          {action?.type === 'soft-delete' && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm"><strong>Hành động quan trọng:</strong> chỉ xóa tài khoản khi chắc chắn người dùng không còn sử dụng hệ thống.</div>}
          <div className="space-y-4">
            {action?.type === 'reset-password' && <div className="space-y-2"><Label htmlFor="temporary-password">Mật khẩu tạm mới</Label><Input id="temporary-password" type="password" autoComplete="new-password" value={temporaryPassword} onChange={(event) => setTemporaryPassword(event.target.value)} placeholder="Tối thiểu 12 ký tự" /></div>}
            <div className="space-y-2"><Label htmlFor="action-reason">Lý do nghiệp vụ</Label><Textarea id="action-reason" value={actionReason} onChange={(event) => setActionReason(event.target.value)} placeholder="Nhập lý do để lưu vào nhật ký quản trị" /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setAction(null)}>Hủy</Button><Button variant={action?.type === 'soft-delete' ? 'destructive' : 'default'} disabled={submitting} onClick={() => void executeAction()}>{submitting ? 'Đang thực hiện...' : action?.type === 'reset-password' ? 'Xác nhận đổi mật khẩu' : action?.type === 'activate' ? 'Xác nhận mở khóa' : action?.type === 'soft-delete' ? 'Xác nhận xóa' : 'Xác nhận khóa'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={askHistoryOpen} onOpenChange={setAskHistoryOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><BookOpenText className="h-5 w-5" />Lịch sử hỏi đáp{askHistoryTarget ? ` · ${askHistoryTarget.profile?.full_name || askHistoryTarget.username}` : ''}</DialogTitle>
            <DialogDescription>{askHistoryTarget ? 'Hiển thị câu hỏi, câu trả lời và nguồn pháp lý đã dùng của tài khoản này.' : 'Danh sách câu hỏi trên toàn hệ thống. Chọn một tài khoản để xem cả nội dung trả lời và nguồn pháp lý.'}</DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 rounded-lg border bg-muted/20 p-4 md:grid-cols-4">
            <select aria-label="Lọc lịch sử hỏi đáp theo lĩnh vực" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={askHistoryFilters.domain} onChange={(event) => setAskHistoryFilters((current) => ({ ...current, domain: event.target.value }))}><option value="">Tất cả lĩnh vực</option>{Object.entries(DOMAIN_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <select aria-label="Lọc lịch sử hỏi đáp theo mức căn cứ" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={askHistoryFilters.groundingStatus} onChange={(event) => setAskHistoryFilters((current) => ({ ...current, groundingStatus: event.target.value }))}><option value="">Mọi mức căn cứ</option>{Object.entries(GROUNDING_STATUS_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <div className="space-y-1"><Label htmlFor="ask-history-date-from">Từ ngày</Label><Input id="ask-history-date-from" type="date" value={askHistoryFilters.dateFrom} onChange={(event) => setAskHistoryFilters((current) => ({ ...current, dateFrom: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="ask-history-date-to">Đến ngày</Label><Input id="ask-history-date-to" type="date" value={askHistoryFilters.dateTo} onChange={(event) => setAskHistoryFilters((current) => ({ ...current, dateTo: event.target.value }))} /></div>
            <div className="flex gap-2 md:col-span-4 md:justify-end"><Button onClick={() => void loadAskHistory(askHistoryTarget?.id)} disabled={askHistoryLoading}>{askHistoryLoading ? 'Đang tải...' : 'Áp dụng bộ lọc'}</Button><Button variant="outline" onClick={() => setAskHistoryFilters({ domain: '', groundingStatus: '', dateFrom: '', dateTo: '' })}>Xóa lọc</Button></div>
          </div>

          {askHistoryError ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{askHistoryError}</div>
          ) : askHistoryLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">Đang tải lịch sử hỏi đáp...</p>
          ) : askHistoryRows.length === 0 ? (
            <div className="rounded-lg border border-dashed py-10 text-center"><p className="font-medium">Chưa có lượt hỏi đáp phù hợp</p><p className="mt-1 text-sm text-muted-foreground">Hãy thay đổi bộ lọc hoặc kiểm tra lại tài khoản.</p></div>
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">Tìm thấy {askHistoryRows.length} lượt hỏi đáp gần nhất.</p>
              {askHistoryRows.map((item, index) => (
                <article key={item.id} className="rounded-xl border p-4 shadow-sm">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Câu hỏi {index + 1}</p><h3 className="mt-1 whitespace-pre-wrap font-semibold leading-6">{item.question || 'Không có nội dung câu hỏi'}</h3></div>
                    <time className="shrink-0 text-xs text-muted-foreground">{formatDateTime(item.created)}</time>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs"><Badge variant="secondary">{item.owner_role ? ROLE_LABEL[item.owner_role] || item.owner_role : 'Chưa xác định vai trò'}</Badge>{item.domain && <Badge variant="outline">{domainLabel(item.domain)}</Badge>}<Badge variant="outline">{GROUNDING_STATUS_LABEL[item.grounding_status || 'unknown'] || item.grounding_status}</Badge>{typeof item.duration_ms === 'number' && <Badge variant="outline">{(item.duration_ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} giây</Badge>}</div>
                  {!askHistoryTarget && item.owner_user && <p className="mt-3 text-xs text-muted-foreground">Tài khoản: {usersById.get(item.owner_user)?.profile?.full_name || usersById.get(item.owner_user)?.username || item.owner_user}</p>}
                  {item.answer && <div className="mt-4 rounded-lg bg-muted/45 p-4"><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Câu trả lời</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6">{item.answer}</p></div>}
                  {item.sources && item.sources.length > 0 && (
                    <div className="mt-4"><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Nguồn pháp lý đã sử dụng</p><ul className="mt-2 space-y-2">{item.sources.map((source, sourceIndex) => { const sourceUrl = safeHttpUrl(source.source_url); const label = [source.document_title || source.law_number || `Nguồn ${sourceIndex + 1}`, source.article_number ? `Điều ${source.article_number}` : ''].filter(Boolean).join(' · '); return <li key={`${source.chunk_id || sourceIndex}-${sourceIndex}`} className="rounded-md border px-3 py-2 text-sm">{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{label}<ExternalLink className="h-3.5 w-3.5" /></a> : label}</li> })}</ul></div>
                  )}
                </article>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={auditOpen} onOpenChange={setAuditOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5" />Lịch sử thao tác{auditTarget ? ` · ${auditTarget.profile?.full_name || auditTarget.username}` : ''}</DialogTitle>
            <DialogDescription>Mọi thay đổi nhạy cảm được lưu cùng người thực hiện, thời gian và lý do nghiệp vụ.</DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 rounded-lg border bg-muted/20 p-4 md:grid-cols-3">
            <select aria-label="Lọc lịch sử theo hành động" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={auditFilters.action} onChange={(event) => setAuditFilters((current) => ({ ...current, action: event.target.value }))}><option value="">Tất cả hành động</option>{Object.entries(AUDIT_ACTION_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <select aria-label="Lọc lịch sử theo vai trò" className="h-10 rounded-md border border-input bg-background px-3 text-sm" value={auditFilters.actorRole} onChange={(event) => setAuditFilters((current) => ({ ...current, actorRole: event.target.value }))}><option value="">Mọi vai trò thực hiện</option><option value="admin">Quản trị viên</option><option value="officer">Cán bộ</option><option value="citizen">Người dân</option></select>
            <Input aria-label="Tìm theo lý do nghiệp vụ" value={auditFilters.reason} onChange={(event) => setAuditFilters((current) => ({ ...current, reason: event.target.value }))} placeholder="Tìm theo lý do nghiệp vụ" />
            <div className="space-y-1"><Label htmlFor="audit-date-from">Từ ngày</Label><Input id="audit-date-from" type="date" value={auditFilters.dateFrom} onChange={(event) => setAuditFilters((current) => ({ ...current, dateFrom: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="audit-date-to">Đến ngày</Label><Input id="audit-date-to" type="date" value={auditFilters.dateTo} onChange={(event) => setAuditFilters((current) => ({ ...current, dateTo: event.target.value }))} /></div>
            <div className="flex items-end gap-2"><Button className="flex-1" onClick={() => void loadAuditLogs(auditTarget?.id)} disabled={auditLoading}>{auditLoading ? 'Đang tải...' : 'Áp dụng lọc'}</Button><Button variant="outline" onClick={() => setAuditFilters({ action: '', actorRole: '', reason: '', dateFrom: '', dateTo: '' })}>Xóa lọc</Button></div>
          </div>

          {auditError ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{auditError}</div>
          ) : auditLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">Đang tải lịch sử thao tác...</p>
          ) : auditLogs.length === 0 ? (
            <div className="rounded-lg border border-dashed py-10 text-center"><p className="font-medium">Chưa có lịch sử phù hợp</p><p className="mt-1 text-sm text-muted-foreground">Hãy thay đổi bộ lọc hoặc kiểm tra lại tài khoản.</p></div>
          ) : (
            <div className="space-y-3">
              {auditLogs.map((log) => (
                <div key={log.id} className="rounded-lg border p-4">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div><p className="font-medium">{AUDIT_ACTION_LABEL[log.action] || activityActionLabel(log.action)}</p><p className="mt-1 text-sm text-muted-foreground">Người thực hiện: {log.actor_role ? roleLabel(log.actor_role) : 'Hệ thống'}{log.actor_user ? ` · ${log.actor_user}` : ''}</p></div><time className="text-xs text-muted-foreground">{formatDateTime(log.created)}</time></div>
                  {Boolean(log.details?.reason) && <p className="mt-3 rounded-md bg-muted px-3 py-2 text-sm"><span className="font-medium">Lý do:</span> {String(log.details?.reason)}</p>}
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>


    </div>
      </div>
    </AppShell>
  )
}
