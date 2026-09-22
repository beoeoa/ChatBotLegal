'use client'

import { useEffect, useRef, useState } from 'react'
import { Building2, Pencil, Plus, RotateCcw, Tags, Trash2 } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiClient } from '@/lib/api/client'
import type {
  LegalDomainConfig,
  OrganizationUnitConfig,
  OrganizationUnitDomainConfig,
} from '@/lib/types/api'

type Department = OrganizationUnitConfig
type Assignment = OrganizationUnitDomainConfig

type DomainDraft = {
  code: string
  name: string
  aliases: string
}

const blankDepartment = (): Department => ({
  id: '',
  code: '',
  name: '',
  short_name: '',
  aliases: [],
  parent_id: null,
  domain_codes: [],
  domain_assignments: [],
  support_enabled: true,
  is_active: true,
  sort_order: 0,
})

const blankDomain = (): DomainDraft => ({ code: '', name: '', aliases: '' })

function domainCodeFromName(name: string): string {
  return name
    .trim()
    .toLocaleLowerCase('vi')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 120)
}

function splitAliases(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
}

function assignmentFor(department: Department, code: string): Assignment {
  return (
    department.domain_assignments?.find((item) => item.domain_code === code) ?? {
      domain_code: code,
      responsibility: 'primary',
    }
  )
}

function domainName(domains: LegalDomainConfig[], code: string): string {
  return domains.find((domain) => domain.code === code)?.name ?? code
}

function apiErrorMessage(error: unknown, domains: LegalDomainConfig[]): string {
  const detail = (
    error as {
      response?: { data?: { detail?: unknown } }
      userMessage?: string
    }
  )?.response?.data?.detail

  if (typeof detail === 'object' && detail) {
    const payload = detail as { code?: string; message?: string; domains?: string[] }
    if (payload.code === 'legal_domain_in_use') {
      const labels = (payload.domains ?? []).map((code) => domainName(domains, code))
      return `Chưa thể xóa khỏi sử dụng${labels.length ? ` “${labels.join(', ')}”` : ''}. Hãy gỡ lĩnh vực khỏi các phòng ban đang hoạt động trước.`
    }
    if (payload.message) return payload.message
  }

  if (typeof detail === 'string') {
    if (detail.startsWith('organization_domain_multiple_primary:')) {
      const code = detail.split(':')[1]
      return `Lĩnh vực “${domainName(domains, code)}” đã có phòng ban chủ trì. Hãy chọn “Phối hợp” hoặc chuyển lĩnh vực khỏi phòng ban cũ trước.`
    }
    if (detail === 'organization_unit_has_active_officers') {
      return 'Chưa thể ngừng phòng ban vì vẫn còn cán bộ đang hoạt động. Hãy chuyển cán bộ sang phòng ban khác trước.'
    }
    if (detail === 'organization_unit_duplicate' || detail === 'organization_unit_code_duplicate') {
      return 'Mã phòng ban đã tồn tại. Hãy đóng và mở lại biểu mẫu để hệ thống tạo mã khác.'
    }
    if (detail === 'legal_domain_code_duplicate') {
      return 'Mã lĩnh vực đã tồn tại. Hãy chọn mã khác.'
    }
    if (detail === 'legal_domain_name_duplicate') {
      return 'Tên lĩnh vực đang được sử dụng. Hãy chọn tên khác hoặc sửa lĩnh vực hiện có.'
    }
    if (detail === 'legal_domain_code_immutable') {
      return 'Mã lĩnh vực là định danh liên kết nên không thể đổi sau khi tạo.'
    }
    if (detail === 'legal_domain_not_found') {
      return 'Lĩnh vực không còn tồn tại. Hãy tải lại danh mục.'
    }
    return detail
  }

  const fallback = (error as { userMessage?: string })?.userMessage
  return fallback || 'Không thể lưu thay đổi. Kiểm tra kết nối rồi thử lại; dữ liệu đã nhập vẫn được giữ.'
}

function normalizedDepartment(item: Department): Department {
  const domainCodes = [...new Set(item.domain_codes ?? [])]
  return {
    ...item,
    aliases: item.aliases ?? [],
    domain_codes: domainCodes,
    domain_assignments: domainCodes.map((code) => assignmentFor(item, code)),
  }
}

export default function DepartmentsPage() {
  const [departments, setDepartments] = useState<Department[]>([])
  const [domains, setDomains] = useState<LegalDomainConfig[]>([])
  const [departmentForm, setDepartmentForm] = useState<Department>(blankDepartment)
  const [departmentOpen, setDepartmentOpen] = useState(false)
  const [editingDepartment, setEditingDepartment] = useState(false)
  const [retiringDepartment, setRetiringDepartment] = useState<Department | null>(null)

  const [newDomainOpen, setNewDomainOpen] = useState(false)
  const [newDomain, setNewDomain] = useState<DomainDraft>(blankDomain)
  const [newDomainCodeEdited, setNewDomainCodeEdited] = useState(false)
  const [editingDomain, setEditingDomain] = useState<LegalDomainConfig | null>(null)
  const [domainDraft, setDomainDraft] = useState<DomainDraft>(blankDomain)
  const [retiringDomain, setRetiringDomain] = useState<LegalDomainConfig | null>(null)

  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [pageError, setPageError] = useState('')
  const [departmentError, setDepartmentError] = useState('')
  const [domainError, setDomainError] = useState('')
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  const [departmentFilter, setDepartmentFilter] = useState('active')
  const [domainFilter, setDomainFilter] = useState('active')
  const operationLock = useRef(false)

  const load = async (showSpinner = true) => {
    if (showSpinner) setLoading(true)
    try {
      const [departmentResponse, domainResponse] = await Promise.all([
        apiClient.get<Department[]>('/settings/organization-units'),
        apiClient.get<LegalDomainConfig[]>('/settings/legal-domains'),
      ])
      setDepartments(departmentResponse.data.map(normalizedDepartment))
      setDomains(domainResponse.data)
      setPageError('')
    } catch (error) {
      setPageError(apiErrorMessage(error, domains) || 'Không tải được cơ cấu tổ chức.')
    } finally {
      if (showSpinner) setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // The page owns the refresh lifecycle; users can explicitly reload later.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const startDepartment = (item?: Department) => {
    setDepartmentError('')
    setDomainError('')
    setMessage('')
    setNewDomainOpen(false)
    setNewDomain(blankDomain())
    setNewDomainCodeEdited(false)
    setEditingDepartment(Boolean(item))
    setDepartmentForm(item ? normalizedDepartment(item) : blankDepartment())
    setDepartmentOpen(true)
  }

  const updateDomainSelection = (code: string, selected: boolean) => {
    const owner = departments.find(
      (department) =>
        department.id !== departmentForm.id &&
        department.is_active &&
        department.support_enabled &&
        department.domain_codes.includes(code) &&
        assignmentFor(department, code).responsibility === 'primary',
    )
    setDepartmentForm((current) => ({
      ...current,
      domain_codes: selected
        ? [...new Set([...current.domain_codes, code])]
        : current.domain_codes.filter((domainCode) => domainCode !== code),
      domain_assignments: selected
        ? [
            ...(current.domain_assignments ?? []).filter(
              (assignment) => assignment.domain_code !== code,
            ),
            { domain_code: code, responsibility: owner ? 'support' : 'primary' },
          ]
        : (current.domain_assignments ?? []).filter(
            (assignment) => assignment.domain_code !== code,
          ),
    }))
  }

  const saveDepartment = async () => {
    if (operationLock.current) return
    if (departmentForm.name.trim().length < 2) {
      setDepartmentError('Tên phòng ban cần ít nhất 2 ký tự.')
      return
    }
    if (!departmentForm.domain_codes.length) {
      setDepartmentError('Chọn ít nhất một lĩnh vực phụ trách.')
      return
    }

    operationLock.current = true
    setBusy(true)
    setDepartmentError('')
    const generatedId = `pb_${crypto.randomUUID().replaceAll('-', '')}`
    const payload: Department = {
      ...departmentForm,
      id: departmentForm.id || generatedId,
      code: departmentForm.code || generatedId,
      name: departmentForm.name.trim(),
      short_name: departmentForm.short_name?.trim() || null,
      domain_assignments: departmentForm.domain_codes.map((code) =>
        assignmentFor(departmentForm, code),
      ),
    }

    try {
      if (editingDepartment) {
        await apiClient.put<Department>(
          `/settings/organization-units/${encodeURIComponent(departmentForm.id)}`,
          payload,
        )
      } else {
        await apiClient.post<Department>('/settings/organization-units', payload)
      }
      await load(false)
      setDepartmentOpen(false)
      setDepartmentFilter(payload.is_active ? 'active' : 'inactive')
      setQuery('')
      setMessage(
        editingDepartment
          ? 'Đã lưu thay đổi phòng ban và cập nhật các liên kết nghiệp vụ.'
          : 'Đã tạo phòng ban và cập nhật các liên kết nghiệp vụ.',
      )
    } catch (error) {
      setDepartmentError(apiErrorMessage(error, domains))
    } finally {
      operationLock.current = false
      setBusy(false)
    }
  }

  const changeDepartmentStatus = async (item: Department, active: boolean) => {
    if (operationLock.current) return
    operationLock.current = true
    setBusy(true)
    setPageError('')
    try {
      const path = `/settings/organization-units/${encodeURIComponent(item.id)}`
      if (active) {
        await apiClient.put<Department>(path, {
          ...item,
          is_active: true,
          support_enabled: true,
        })
      } else {
        await apiClient.delete<Department>(path)
      }
      await load(false)
      setRetiringDepartment(null)
      setDepartmentFilter(active ? 'active' : 'inactive')
      setMessage(
        active
          ? 'Đã khôi phục phòng ban và phạm vi nghiệp vụ đã lưu.'
          : 'Đã ngừng hoạt động. Phòng ban và lịch sử liên kết vẫn được giữ để có thể khôi phục.',
      )
    } catch (error) {
      setPageError(apiErrorMessage(error, domains))
    } finally {
      operationLock.current = false
      setBusy(false)
    }
  }

  const createDomain = async () => {
    if (operationLock.current) return
    const name = newDomain.name.trim()
    const code = newDomain.code.trim()
    if (name.length < 2) {
      setDomainError('Tên lĩnh vực cần ít nhất 2 ký tự.')
      return
    }
    if (!/^[a-z0-9][a-z0-9_-]{1,119}$/.test(code)) {
      setDomainError('Mã lĩnh vực cần 2–120 ký tự, chỉ gồm chữ thường không dấu, số, dấu gạch ngang hoặc gạch dưới.')
      return
    }

    operationLock.current = true
    setBusy(true)
    setDomainError('')
    try {
      const response = await apiClient.post<LegalDomainConfig>('/settings/legal-domains', {
        code,
        name,
        aliases: splitAliases(newDomain.aliases),
        is_active: true,
        sort_order: domains.length + 1,
      })
      setDomains((current) => [...current, response.data])
      updateDomainSelection(response.data.code, true)
      setNewDomain(blankDomain())
      setNewDomainCodeEdited(false)
      setNewDomainOpen(false)
      setMessage(`Đã lưu lĩnh vực “${response.data.name}” và chọn cho phòng ban đang nhập.`)
    } catch (error) {
      setDomainError(apiErrorMessage(error, domains))
    } finally {
      operationLock.current = false
      setBusy(false)
    }
  }

  const startDomainEdit = (domain: LegalDomainConfig) => {
    setDomainError('')
    setEditingDomain(domain)
    setDomainDraft({
      code: domain.code,
      name: domain.name,
      aliases: domain.aliases.join(', '),
    })
  }

  const saveDomain = async () => {
    if (!editingDomain || operationLock.current) return
    const name = domainDraft.name.trim()
    if (name.length < 2) {
      setDomainError('Tên lĩnh vực cần ít nhất 2 ký tự.')
      return
    }
    operationLock.current = true
    setBusy(true)
    setDomainError('')
    try {
      await apiClient.put<LegalDomainConfig>(
        `/settings/legal-domains/${encodeURIComponent(editingDomain.code)}`,
        {
          ...editingDomain,
          name,
          aliases: splitAliases(domainDraft.aliases),
        },
      )
      await load(false)
      setEditingDomain(null)
      setMessage(`Đã sửa lĩnh vực “${name}”. Tên mới được dùng tại các phòng ban liên quan.`)
    } catch (error) {
      setDomainError(apiErrorMessage(error, domains))
    } finally {
      operationLock.current = false
      setBusy(false)
    }
  }

  const changeDomainStatus = async (domain: LegalDomainConfig, active: boolean) => {
    if (operationLock.current) return
    operationLock.current = true
    setBusy(true)
    setPageError('')
    setDomainError('')
    try {
      if (active) {
        await apiClient.put<LegalDomainConfig>(
          `/settings/legal-domains/${encodeURIComponent(domain.code)}`,
          { ...domain, is_active: true },
        )
      } else {
        await apiClient.delete<LegalDomainConfig>(
          `/settings/legal-domains/${encodeURIComponent(domain.code)}`,
        )
      }
      await load(false)
      setRetiringDomain(null)
      setDomainFilter(active ? 'active' : 'inactive')
      setMessage(
        active
          ? `Đã khôi phục lĩnh vực “${domain.name}”.`
          : `Đã xóa “${domain.name}” khỏi danh mục đang sử dụng; mã và lịch sử liên kết vẫn được bảo toàn.`,
      )
    } catch (error) {
      const message = apiErrorMessage(error, domains)
      setDomainError(message)
      setPageError(message)
    } finally {
      operationLock.current = false
      setBusy(false)
    }
  }

  const visibleDepartments = departments.filter((department) => {
    const statusMatches =
      departmentFilter === 'all' ||
      department.is_active === (departmentFilter === 'active')
    const text = `${department.name} ${department.short_name ?? ''} ${department.domain_codes
      .map((code) => domainName(domains, code))
      .join(' ')}`.toLocaleLowerCase('vi')
    return statusMatches && text.includes(query.toLocaleLowerCase('vi'))
  })

  const availableDomains = domains.filter(
    (domain) => domain.is_active || departmentForm.domain_codes.includes(domain.code),
  )
  const visibleDomains = domains.filter(
    (domain) => domainFilter === 'all' || domain.is_active === (domainFilter === 'active'),
  )

  return (
    <AppShell>
      <main className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="mx-auto max-w-6xl space-y-6">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-primary">
                Quản trị vận hành
              </p>
              <h1 className="mt-1 font-display text-3xl font-bold">Quản lý phòng ban</h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                Quản lý cơ cấu và danh mục lĩnh vực dùng chung. Mọi thay đổi được lưu trên hệ
                thống và dùng lại ở các màn hình phân công, biểu mẫu và tài khoản.
              </p>
            </div>
            <Button onClick={() => startDepartment()} disabled={loading || busy}>
              <Plus aria-hidden="true" />
              Thêm phòng ban
            </Button>
          </header>

          {message && (
            <p role="status" className="rounded-lg border bg-card p-4 text-sm">
              {message}
            </p>
          )}
          {pageError && !retiringDepartment && !retiringDomain && (
            <p role="alert" className="rounded-lg border border-destructive p-4 text-sm text-destructive">
              {pageError}
            </p>
          )}

          <section className="overflow-hidden rounded-xl border bg-card" aria-labelledby="departments-heading">
            <div className="flex flex-wrap items-center gap-3 border-b p-4">
              <h2 id="departments-heading" className="mr-auto text-lg font-semibold">
                Phòng ban
              </h2>
              <Input
                aria-label="Tìm phòng ban hoặc lĩnh vực"
                placeholder="Tìm phòng ban hoặc lĩnh vực…"
                className="min-w-48 md:max-w-sm"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <select
                aria-label="Lọc trạng thái phòng ban"
                className="h-10 rounded-md border bg-background px-3 text-sm"
                value={departmentFilter}
                onChange={(event) => setDepartmentFilter(event.target.value)}
              >
                <option value="active">
                  Đang hoạt động ({departments.filter((item) => item.is_active).length})
                </option>
                <option value="inactive">
                  Đã ngừng ({departments.filter((item) => !item.is_active).length})
                </option>
                <option value="all">Tất cả ({departments.length})</option>
              </select>
              <Button variant="outline" onClick={() => void load()} disabled={loading || busy}>
                Làm mới
              </Button>
            </div>

            {loading ? (
              <p role="status" className="p-8 text-center text-muted-foreground">
                Đang tải phòng ban và lĩnh vực…
              </p>
            ) : (
              <div className="divide-y">
                {visibleDepartments.map((department) => (
                  <article
                    key={department.id}
                    aria-label={department.name}
                    className="flex flex-wrap items-center gap-4 p-5"
                  >
                    <Building2
                      aria-hidden="true"
                      className="h-10 w-10 shrink-0 rounded-lg bg-muted p-2 text-primary"
                    />
                    <div className="min-w-48 flex-1">
                      <h3 className="font-semibold">{department.name}</h3>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {department.is_active ? 'Đang hoạt động' : 'Đã ngừng hoạt động'}
                        {department.short_name ? ` · ${department.short_name}` : ''}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {department.domain_codes.map((code) => (
                          <span key={code} className="rounded-md border px-2 py-1 text-xs">
                            {domainName(domains, code)}
                            {assignmentFor(department, code).responsibility === 'support'
                              ? ' · Phối hợp'
                              : ' · Chủ trì'}
                          </span>
                        ))}
                        {!department.domain_codes.length && (
                          <span className="text-sm text-muted-foreground">Chưa gán lĩnh vực</span>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        disabled={busy}
                        aria-label={`Sửa phòng ban ${department.name}`}
                        onClick={() => startDepartment(department)}
                      >
                        Sửa
                      </Button>
                      {department.is_active ? (
                        <Button
                          variant="outline"
                          disabled={busy}
                          aria-label={`Ngừng hoạt động phòng ban ${department.name}`}
                          onClick={() => {
                            setPageError('')
                            setRetiringDepartment(department)
                          }}
                        >
                          Ngừng hoạt động
                        </Button>
                      ) : (
                        <Button
                          variant="outline"
                          disabled={busy}
                          onClick={() => void changeDepartmentStatus(department, true)}
                        >
                          <RotateCcw aria-hidden="true" />
                          Khôi phục
                        </Button>
                      )}
                    </div>
                  </article>
                ))}
                {!visibleDepartments.length && (
                  <div className="p-10 text-center">
                    <p className="font-medium">Không có phòng ban phù hợp</p>
                    <p className="mt-2 text-sm text-muted-foreground">
                      Thay đổi bộ lọc hoặc thêm phòng ban theo cơ cấu địa phương.
                    </p>
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="overflow-hidden rounded-xl border bg-card" aria-labelledby="domains-heading">
            <div className="flex flex-wrap items-center gap-3 border-b p-4">
              <div className="mr-auto flex items-center gap-3">
                <Tags aria-hidden="true" className="text-primary" />
                <div>
                  <h2 id="domains-heading" className="text-lg font-semibold">
                    Danh mục lĩnh vực
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    Dùng chung cho phòng ban và các chức năng phân công liên quan.
                  </p>
                </div>
              </div>
              <select
                aria-label="Lọc trạng thái lĩnh vực"
                className="h-10 rounded-md border bg-background px-3 text-sm"
                value={domainFilter}
                onChange={(event) => setDomainFilter(event.target.value)}
              >
                <option value="active">
                  Đang sử dụng ({domains.filter((domain) => domain.is_active).length})
                </option>
                <option value="inactive">
                  Đã ngừng ({domains.filter((domain) => !domain.is_active).length})
                </option>
                <option value="all">Tất cả ({domains.length})</option>
              </select>
            </div>
            <div className="divide-y">
              {visibleDomains.map((domain) => {
                const assignedDepartments = departments.filter(
                  (department) => department.is_active && department.domain_codes.includes(domain.code),
                )
                return (
                  <article key={domain.code} className="flex flex-wrap items-center gap-4 p-5">
                    <div className="min-w-52 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-semibold">{domain.name}</h3>
                        <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                          {domain.code}
                        </span>
                        {!domain.is_active && (
                          <span className="rounded border px-2 py-0.5 text-xs">Đã ngừng</span>
                        )}
                      </div>
                      <p className="mt-2 text-sm text-muted-foreground">
                        {assignedDepartments.length
                          ? `Đang dùng tại: ${assignedDepartments.map((item) => item.name).join(', ')}`
                          : 'Chưa gán cho phòng ban đang hoạt động.'}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        disabled={busy}
                        aria-label={`Sửa lĩnh vực ${domain.name}`}
                        onClick={() => startDomainEdit(domain)}
                      >
                        <Pencil aria-hidden="true" />
                        Sửa
                      </Button>
                      {domain.is_active ? (
                        <Button
                          variant="outline"
                          disabled={busy}
                          aria-label={`Xóa lĩnh vực ${domain.name} khỏi sử dụng`}
                          title={
                            assignedDepartments.length
                              ? 'Cần gỡ lĩnh vực khỏi các phòng ban đang hoạt động trước'
                              : undefined
                          }
                          onClick={() => {
                            setPageError('')
                            setDomainError('')
                            setRetiringDomain(domain)
                          }}
                        >
                          <Trash2 aria-hidden="true" />
                          Xóa khỏi sử dụng
                        </Button>
                      ) : (
                        <Button
                          variant="outline"
                          disabled={busy}
                          onClick={() => void changeDomainStatus(domain, true)}
                        >
                          <RotateCcw aria-hidden="true" />
                          Khôi phục
                        </Button>
                      )}
                    </div>
                  </article>
                )
              })}
              {!visibleDomains.length && !loading && (
                <p className="p-8 text-center text-sm text-muted-foreground">
                  Không có lĩnh vực ở trạng thái này.
                </p>
              )}
            </div>
          </section>

          <p className="text-xs text-muted-foreground">
            “Ngừng hoạt động” và “Xóa khỏi sử dụng” không xóa lịch sử. Hệ thống giữ mã định
            danh để hồ sơ, tài khoản và biểu mẫu cũ không bị mất liên kết.
          </p>
        </div>
      </main>

      <Dialog
        open={departmentOpen}
        onOpenChange={(value) => {
          if (!busy) {
            setDepartmentOpen(value)
            if (!value) {
              setDepartmentError('')
              setDomainError('')
            }
          }
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl" showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>{editingDepartment ? 'Sửa phòng ban' : 'Thêm phòng ban'}</DialogTitle>
            <DialogDescription>
              Nhập tên theo quyết định địa phương và chọn lĩnh vực phụ trách. Mã phòng ban được
              tạo tự động và giữ ổn định cho các liên kết sau này.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void saveDepartment()
            }}
            className="space-y-5"
          >
            <div className="space-y-2">
              <Label htmlFor="department-name">Tên phòng ban *</Label>
              <Input
                id="department-name"
                maxLength={200}
                autoFocus
                disabled={busy}
                value={departmentForm.name}
                onChange={(event) =>
                  setDepartmentForm((current) => ({ ...current, name: event.target.value }))
                }
                placeholder="Ví dụ: Phòng Kinh tế, Hạ tầng và Đô thị"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="department-short-name">Tên viết tắt (không bắt buộc)</Label>
              <Input
                id="department-short-name"
                maxLength={120}
                disabled={busy}
                value={departmentForm.short_name ?? ''}
                onChange={(event) =>
                  setDepartmentForm((current) => ({
                    ...current,
                    short_name: event.target.value,
                  }))
                }
              />
            </div>

            <fieldset disabled={busy} className="space-y-3">
              <legend className="text-sm font-medium">Lĩnh vực phụ trách *</legend>
              <p className="text-xs text-muted-foreground">
                Một lĩnh vực có một phòng ban chủ trì. Phòng ban khác có thể tham gia phối hợp.
              </p>
              <div className="divide-y rounded-lg border">
                {availableDomains.map((domain) => {
                  const selected = departmentForm.domain_codes.includes(domain.code)
                  const owner = departments.find(
                    (department) =>
                      department.id !== departmentForm.id &&
                      department.is_active &&
                      department.support_enabled &&
                      department.domain_codes.includes(domain.code) &&
                      assignmentFor(department, domain.code).responsibility === 'primary',
                  )
                  const responsibility = assignmentFor(departmentForm, domain.code).responsibility
                  return (
                    <div
                      key={domain.code}
                      className="flex flex-wrap items-center justify-between gap-2 p-3"
                    >
                      <label className="flex min-w-40 flex-1 cursor-pointer items-center gap-3 text-sm">
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={(event) => updateDomainSelection(domain.code, event.target.checked)}
                        />
                        <span>
                          {domain.name}
                          {!domain.is_active && ' · Đã ngừng sử dụng'}
                          {owner && (
                            <span className="mt-1 block text-xs text-muted-foreground">
                              Chủ trì: {owner.name}
                            </span>
                          )}
                        </span>
                      </label>
                      {selected && (
                        <select
                          aria-label={`Vai trò ${domain.name}`}
                          className="h-9 rounded border bg-background px-2 text-sm"
                          value={responsibility}
                          onChange={(event) =>
                            setDepartmentForm((current) => ({
                              ...current,
                              domain_assignments: (current.domain_assignments ?? []).map(
                                (assignment) =>
                                  assignment.domain_code === domain.code
                                    ? {
                                        ...assignment,
                                        responsibility: event.target.value as Assignment['responsibility'],
                                      }
                                    : assignment,
                              ),
                            }))
                          }
                        >
                          <option value="primary" disabled={Boolean(owner)}>
                            Chủ trì
                          </option>
                          <option value="support">Phối hợp</option>
                        </select>
                      )}
                    </div>
                  )
                })}
                {!availableDomains.length && (
                  <p className="p-4 text-sm text-muted-foreground">
                    Chưa có lĩnh vực đang sử dụng. Hãy thêm lĩnh vực bên dưới.
                  </p>
                )}
              </div>

              {!newDomainOpen ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    setDomainError('')
                    setNewDomainOpen(true)
                  }}
                >
                  <Plus aria-hidden="true" />
                  Thêm lĩnh vực
                </Button>
              ) : (
                <div className="space-y-4 rounded-lg border bg-muted/20 p-4">
                  <div>
                    <h3 className="font-medium">Thêm lĩnh vực mới</h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Lĩnh vực được lưu ngay vào danh mục dùng chung và tự động chọn cho phòng ban
                      này.
                    </p>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="new-domain-name">Tên lĩnh vực *</Label>
                      <Input
                        id="new-domain-name"
                        autoFocus
                        maxLength={200}
                        value={newDomain.name}
                        onChange={(event) => {
                          const name = event.target.value
                          setNewDomain((current) => ({
                            ...current,
                            name,
                            code: newDomainCodeEdited ? current.code : domainCodeFromName(name),
                          }))
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            void createDomain()
                          }
                        }}
                        placeholder="Ví dụ: Chuyển đổi số"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="new-domain-code">Mã ổn định *</Label>
                      <Input
                        id="new-domain-code"
                        maxLength={120}
                        value={newDomain.code}
                        onChange={(event) => {
                          setNewDomainCodeEdited(true)
                          setNewDomain((current) => ({
                            ...current,
                            code: event.target.value.toLocaleLowerCase('vi'),
                          }))
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            void createDomain()
                          }
                        }}
                        placeholder="chuyen_doi_so"
                      />
                      <p className="text-xs text-muted-foreground">Không thể đổi mã sau khi lưu.</p>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="new-domain-aliases">Tên gọi khác (không bắt buộc)</Label>
                    <Input
                      id="new-domain-aliases"
                      value={newDomain.aliases}
                      onChange={(event) =>
                        setNewDomain((current) => ({ ...current, aliases: event.target.value }))
                      }
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault()
                          void createDomain()
                        }
                      }}
                      placeholder="Phân cách bằng dấu phẩy"
                    />
                  </div>
                  {domainError && (
                    <p role="alert" className="text-sm text-destructive">
                      {domainError}
                    </p>
                  )}
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        setNewDomainOpen(false)
                        setNewDomain(blankDomain())
                        setNewDomainCodeEdited(false)
                        setDomainError('')
                      }}
                    >
                      Hủy thêm lĩnh vực
                    </Button>
                    <Button type="button" disabled={busy} onClick={() => void createDomain()}>
                      {busy ? 'Đang lưu…' : 'Lưu lĩnh vực'}
                    </Button>
                  </div>
                </div>
              )}
            </fieldset>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                disabled={busy}
                checked={departmentForm.support_enabled}
                onChange={(event) =>
                  setDepartmentForm((current) => ({
                    ...current,
                    support_enabled: event.target.checked,
                  }))
                }
              />
              Tiếp nhận phân công hỗ trợ người dân
            </label>
            {departmentError && (
              <p role="alert" className="rounded-md border border-destructive p-3 text-sm text-destructive">
                {departmentError}
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setDepartmentOpen(false)
                  setDepartmentError('')
                  setDomainError('')
                }}
              >
                Hủy
              </Button>
              <Button type="submit" disabled={busy || newDomainOpen}>
                {busy ? 'Đang lưu…' : editingDepartment ? 'Lưu thay đổi' : 'Tạo phòng ban'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(editingDomain)}
        onOpenChange={(value) => {
          if (!busy && !value) {
            setEditingDomain(null)
            setDomainError('')
          }
        }}
      >
        <DialogContent className="sm:max-w-lg" showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>Sửa lĩnh vực</DialogTitle>
            <DialogDescription>
              Đổi tên sẽ cập nhật cách hiển thị ở mọi phòng ban đang liên kết. Mã định danh được
              giữ nguyên để bảo toàn dữ liệu.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              void saveDomain()
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="edit-domain-name">Tên lĩnh vực *</Label>
              <Input
                id="edit-domain-name"
                autoFocus
                maxLength={200}
                disabled={busy}
                value={domainDraft.name}
                onChange={(event) =>
                  setDomainDraft((current) => ({ ...current, name: event.target.value }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-domain-code">Mã định danh</Label>
              <Input id="edit-domain-code" value={domainDraft.code} disabled readOnly />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-domain-aliases">Tên gọi khác</Label>
              <Input
                id="edit-domain-aliases"
                disabled={busy}
                value={domainDraft.aliases}
                onChange={(event) =>
                  setDomainDraft((current) => ({ ...current, aliases: event.target.value }))
                }
                placeholder="Phân cách bằng dấu phẩy"
              />
            </div>
            {domainError && (
              <p role="alert" className="text-sm text-destructive">
                {domainError}
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setEditingDomain(null)
                  setDomainError('')
                }}
              >
                Hủy
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? 'Đang lưu…' : 'Lưu lĩnh vực'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(retiringDepartment)}
        onOpenChange={(value) => {
          if (!busy && !value) {
            setRetiringDepartment(null)
            setPageError('')
          }
        }}
      >
        <DialogContent className="sm:max-w-lg" showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>Ngừng hoạt động phòng ban?</DialogTitle>
            <DialogDescription>{retiringDepartment?.name}</DialogDescription>
          </DialogHeader>
          <p className="text-sm">
            Phòng ban sẽ không nhận phân công mới. Tài khoản, hồ sơ và lịch sử không bị xóa; bạn
            có thể khôi phục sau. Nếu còn cán bộ đang hoạt động, hệ thống sẽ yêu cầu chuyển cán bộ
            trước.
          </p>
          {pageError && (
            <p role="alert" className="text-sm text-destructive">
              {pageError}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setRetiringDepartment(null)}>
              Hủy
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() =>
                retiringDepartment && void changeDepartmentStatus(retiringDepartment, false)
              }
            >
              {busy ? 'Đang xử lý…' : 'Xác nhận ngừng hoạt động'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(retiringDomain)}
        onOpenChange={(value) => {
          if (!busy && !value) {
            setRetiringDomain(null)
            setDomainError('')
            setPageError('')
          }
        }}
      >
        <DialogContent className="sm:max-w-lg" showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>Xóa lĩnh vực khỏi sử dụng?</DialogTitle>
            <DialogDescription>{retiringDomain?.name}</DialogDescription>
          </DialogHeader>
          <p className="text-sm">
            Lĩnh vực sẽ không xuất hiện khi tạo liên kết mới. Mã và lịch sử vẫn được giữ để dữ liệu
            cũ không bị hỏng. Nếu lĩnh vực còn thuộc phòng ban đang hoạt động, hãy sửa phòng ban và
            gỡ lĩnh vực trước.
          </p>
          {domainError && (
            <p role="alert" className="text-sm text-destructive">
              {domainError}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                setRetiringDomain(null)
                setDomainError('')
              }}
            >
              Hủy
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => retiringDomain && void changeDomainStatus(retiringDomain, false)}
            >
              {busy ? 'Đang xử lý…' : 'Xác nhận xóa khỏi sử dụng'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}
