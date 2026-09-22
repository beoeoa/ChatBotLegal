'use client'

import { useEffect, useMemo, useState } from 'react'
import { Plus, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useSettings } from '@/lib/hooks/use-settings'
import { apiClient } from '@/lib/api/client'
import type {
  OrganizationRoutingMode,
  OrganizationUnitConfig,
  SettingsResponse
} from '@/lib/types/api'
import { formatApiError, getApiErrorMessage } from '@/lib/utils/error-handler'
import { resetConfig } from '@/lib/config'

type OrganizationReadiness = {
  ready_for_unit_primary: boolean
  active_officers_without_unit: number
  open_support_without_unit: number
  pending_candidates_without_assignment: number
  approved_candidates_without_assignment: number
  procedures_without_unit: number
  document_assignments_pending_sync: number
  document_assignments_needing_confirmation?: number
  imported_documents_without_assignment: number
  imported_documents_unassigned: number
  legacy_documents_unassigned?: number
  corpus_assignments_pending_sync?: number
  corpus_assignments_needing_confirmation?: number
  corpus_assignment_disagreements?: number
  corpus_unit_readiness_unavailable?: number
  active_form_release_without_unit: number
  form_release_readiness_unavailable: number
}

const READINESS_LABELS: Array<[
  keyof Omit<OrganizationReadiness, 'ready_for_unit_primary'>,
  string
]> = [
  ['active_officers_without_unit', 'Cán bộ chưa gắn phòng ban'],
  ['open_support_without_unit', 'Yêu cầu hỗ trợ đang mở chưa có phòng ban'],
  ['approved_candidates_without_assignment', 'Văn bản đã duyệt nhưng chưa phân công'],
  ['procedures_without_unit', 'Thủ tục chưa có phòng ban chủ trì'],
  ['document_assignments_pending_sync', 'Phân công văn bản chưa đồng bộ'],
  ['document_assignments_needing_confirmation', 'Phân công văn bản cần xác nhận'],
  ['imported_documents_without_assignment', 'Văn bản đã nhập chưa có phân công'],
  ['imported_documents_unassigned', 'Văn bản đã nhập đang chờ chọn phòng ban'],
  ['legacy_documents_unassigned', 'Văn bản trong toàn bộ kho chưa phân công'],
  ['corpus_assignments_pending_sync', 'Phân công trong kho tìm kiếm chưa đồng bộ'],
  ['corpus_assignments_needing_confirmation', 'Phân công trong kho tìm kiếm cần xác nhận'],
  ['corpus_assignment_disagreements', 'Phân công không khớp giữa kho quản trị và kho tìm kiếm'],
  ['corpus_unit_readiness_unavailable', 'Chưa kiểm tra được phân công toàn bộ kho'],
  ['active_form_release_without_unit', 'Thủ tục trong bản phát hành chưa có phòng ban'],
  ['form_release_readiness_unavailable', 'Chưa kiểm tra được bản phát hành thủ tục']
]

function newUnit(sortOrder: number): OrganizationUnitConfig {
  const id = `org-unit-${Date.now()}-${sortOrder}`
  return {
    id,
    code: `don_vi_moi_${sortOrder}`,
    name: 'Đơn vị mới',
    short_name: '',
    aliases: [],
    domain_codes: [],
    domain_assignments: [],
    support_enabled: true,
    is_active: true,
    sort_order: sortOrder
  }
}

function normalizeUnit(unit: OrganizationUnitConfig): OrganizationUnitConfig {
  const assignments = unit.domain_assignments?.length
    ? unit.domain_assignments
    : (unit.domain_codes || []).map((domain_code) => ({
        domain_code,
        responsibility: 'primary' as const
      }))
  return {
    ...unit,
    aliases: unit.aliases || [],
    domain_assignments: assignments,
    domain_codes: assignments.map((item) => item.domain_code)
  }
}

export function SystemSettingsForm({ showOrganizationUnits = true }: { showOrganizationUnits?: boolean }) {
  const { data, isLoading, refetch } = useSettings()
  const [systemName, setSystemName] = useState('Pháp luật Hải Phòng')
  const [organizationName, setOrganizationName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [units, setUnits] = useState<OrganizationUnitConfig[]>([])
  const [routingMode, setRoutingMode] =
    useState<OrganizationRoutingMode>('legacy')
  const [persistedUnitIds, setPersistedUnitIds] = useState<Set<string>>(
    new Set()
  )
  const [promptCheck, setPromptCheck] = useState<{
    valid: boolean
    message: string
    warnings: string[]
    applied_style?: string
    style_compilation?: { rejected: Array<{ text: string; reason: string }> }
    runtime_prompt_previews?: Record<string, string>
    branches_using_prompt?: string[]
  } | null>(null)
  const [savingSection, setSavingSection] = useState<
    'identity' | 'prompt' | 'units' | null
  >(null)
  const [deletingUnitId, setDeletingUnitId] = useState<string | null>(null)
  const [readiness, setReadiness] = useState<OrganizationReadiness | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(false)

  useEffect(() => {
    if (!data) return
    setSystemName(data.system_name || 'Pháp luật Hải Phòng')
    setOrganizationName(data.organization_name || '')
    setPrompt(data.system_prompt_addendum || '')
    setUnits((data.organization_units || []).map(normalizeUnit))
    setRoutingMode(data.organization_routing_mode || 'legacy')
    setPersistedUnitIds(
      new Set((data.organization_units || []).map((unit) => unit.id))
    )
  }, [data])

  useEffect(() => {
    let cancelled = false
    const loadReadiness = async () => {
      setReadinessLoading(true)
      try {
        const response = await apiClient.get<OrganizationReadiness>(
          '/settings/organization-units/readiness'
        )
        if (!cancelled) setReadiness(response.data)
      } catch {
        if (!cancelled) setReadiness(null)
      } finally {
        if (!cancelled) setReadinessLoading(false)
      }
    }
    void loadReadiness()
    return () => {
      cancelled = true
    }
  }, [data?.config_revision])

  const activeDomains = useMemo(
    () =>
      new Set(
        units
          .filter((unit) => unit.is_active && unit.support_enabled)
          .flatMap((unit) =>
            (unit.domain_assignments || [])
              .filter((item) => item.responsibility === 'primary')
              .map((item) => item.domain_code)
          )
      ),
    [units]
  )

  const domainOptions = useMemo(() => {
    const options = new Map(
      (data?.legal_domains || [])
        .filter((domain) => domain.is_active)
        .map((domain) => [domain.code, domain.name])
    )
    for (const unit of units) {
      for (const code of unit.domain_codes || []) {
        if (!options.has(code)) {
          const configured = data?.legal_domains?.find(
            (domain) => domain.code === code
          )
          options.set(
            code,
            configured
              ? `${configured.name} · Đã ngừng sử dụng`
              : code
          )
        }
      }
    }
    return [...options.entries()]
  }, [data?.legal_domains, units])

  const updateUnit = (id: string, patch: Partial<OrganizationUnitConfig>) => {
    setUnits((current) =>
      current.map((unit) => (unit.id === id ? { ...unit, ...patch } : unit))
    )
  }

  const setDomainResponsibility = (
    unit: OrganizationUnitConfig,
    domain: string,
    responsibility: '' | 'primary' | 'support'
  ) => {
    const assignments = (unit.domain_assignments || []).filter(
      (item) => item.domain_code !== domain
    )
    if (responsibility) {
      assignments.push({ domain_code: domain, responsibility })
    }
    updateUnit(unit.id, {
      domain_assignments: assignments,
      domain_codes: assignments.map((item) => item.domain_code)
    })
  }

  const persistSettings = async (
    patch: Partial<SettingsResponse>,
    successMessage: string
  ) => {
    const payload = {
      ...patch,
      ...(data?.config_revision
        ? { expected_config_revision: data.config_revision }
        : {})
    }
    try {
      await apiClient.put<SettingsResponse>('/settings', payload)
      await refetch()
      resetConfig()
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('system-settings-updated'))
      }
      toast.success(successMessage)
      return true
    } catch (error: unknown) {
      toast.error(
        getApiErrorMessage(
          error,
          (key) => key,
          'Không thể lưu cấu hình hệ thống.'
        )
      )
      return false
    }
  }

  const saveIdentity = async () => {
    const normalizedName = systemName.trim()
    if (normalizedName.length < 2) {
      toast.error('Tên hệ thống phải có ít nhất 2 ký tự.')
      return false
    }
    setSavingSection('identity')
    try {
      return await persistSettings(
        {
          system_name: normalizedName,
          organization_name: organizationName.trim()
        },
        'Đã lưu nhận diện hệ thống.'
      )
    } finally {
      setSavingSection(null)
    }
  }

  const savePrompt = async () => {
    setSavingSection('prompt')
    try {
      const check = await apiClient.post<{
        valid: boolean
        message: string
        warnings: string[]
        applied_style?: string
        style_compilation?: { rejected: Array<{ text: string; reason: string }> }
        runtime_prompt_previews?: Record<string, string>
        branches_using_prompt?: string[]
      }>('/settings/system/prompt/check', {
        system_prompt_addendum: prompt.trim()
      })
      setPromptCheck(check.data)
      if (!check.data.valid) {
        toast.error(
          formatApiError(check.data.warnings[0], 'Hướng dẫn bổ sung chưa đạt yêu cầu kiểm tra.')
        )
        return false
      }
      return await persistSettings(
        { system_prompt_addendum: prompt.trim() },
        'Đã lưu hướng dẫn chatbot và áp dụng cho lượt hỏi mới.'
      )
    } catch (error: unknown) {
      toast.error(
        getApiErrorMessage(
          error,
          (key) => key,
          'Không thể kiểm tra hướng dẫn bổ sung.'
        )
      )
      return false
    } finally {
      setSavingSection(null)
    }
  }

  const saveUnits = async () => {
    const activeSupportDomains = units
      .filter((unit) => unit.is_active && unit.support_enabled)
      .flatMap((unit) =>
        (unit.domain_assignments || [])
          .filter((item) => item.responsibility === 'primary')
          .map((item) => item.domain_code)
      )
    if (
      units.some(
        (unit) =>
          unit.is_active &&
          unit.support_enabled &&
          (unit.domain_assignments || []).length === 0
      )
    ) {
      toast.error(
        'Đơn vị đang hoạt động và nhận hỗ trợ phải phụ trách hoặc phối hợp ít nhất một lĩnh vực.'
      )
      return false
    }
    if (new Set(activeSupportDomains).size !== activeSupportDomains.length) {
      toast.error(
        'Mỗi lĩnh vực chỉ được có một đơn vị chủ trì đang hoạt động; các đơn vị phối hợp không bị giới hạn.'
      )
      return false
    }
    setSavingSection('units')
    try {
      const saved = await persistSettings(
        {
          organization_units: units.map((unit, index) => ({
            ...unit,
            sort_order: index + 1
          })),
          organization_routing_mode: routingMode
        },
        'Đã lưu cơ cấu đơn vị cấp 2.'
      )
      if (saved) {
        setPersistedUnitIds(new Set(units.map((unit) => unit.id)))
      }
      return saved
    } finally {
      setSavingSection(null)
    }
  }

  const removeUnit = async (unit: OrganizationUnitConfig) => {
    const isLocalOnly = !persistedUnitIds.has(unit.id)
    if (isLocalOnly) {
      setUnits((current) => current.filter((item) => item.id !== unit.id))
      return
    }
    if (
      typeof window !== 'undefined' &&
      !window.confirm(
        `Ngừng hoạt động đơn vị “${unit.name}”? Dữ liệu và lịch sử liên quan vẫn được giữ nguyên. Cần chuyển cán bộ đang được gán trước.`
      )
    ) {
      return
    }
    setDeletingUnitId(unit.id)
    try {
      const response = await apiClient.delete<OrganizationUnitConfig>(
        `/settings/organization-units/${encodeURIComponent(unit.id)}`
      )
      setUnits((current) =>
        current.map((item) =>
          item.id === unit.id ? normalizeUnit(response.data) : item
        )
      )
      await refetch()
      toast.success(
        'Đã ngừng hoạt động đơn vị và giữ nguyên dữ liệu lịch sử.'
      )
    } catch (error: unknown) {
      toast.error(
        getApiErrorMessage(error, (key) => key, 'Không thể xóa đơn vị.')
      )
    } finally {
      setDeletingUnitId(null)
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Cấu hình nhận diện hệ thống</CardTitle>
          <CardDescription>
            Tên này được dùng ở sidebar và màn hình chat của người dân, cán bộ.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="system-name">Tên hệ thống</Label>
              <Input
                id="system-name"
                value={systemName}
                onChange={(event) => setSystemName(event.target.value)}
                disabled={isLoading}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="organization-name">Đơn vị chủ quản</Label>
              <Input
                id="organization-name"
                value={organizationName}
                onChange={(event) => setOrganizationName(event.target.value)}
                disabled={isLoading}
                placeholder="Ví dụ: UBND phường/xã"
              />
            </div>
          </div>
          <div className="flex justify-end">
            <Button
              type="button"
              onClick={() => void saveIdentity()}
              disabled={isLoading || savingSection !== null}
            >
              <Save className="mr-2 h-4 w-4" />{' '}
              {savingSection === 'identity' ? 'Đang lưu…' : 'Lưu nhận diện'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Hành vi chatbot</CardTitle>
          <CardDescription>
            Chỉ cấu hình giọng điệu, cách xưng hô, độ dài và cách trình bày.
            Các yêu cầu về facet, retrieval, nguồn và kết luận pháp lý sẽ bị loại.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <Label htmlFor="system-prompt-addendum">Hướng dẫn bổ sung cho chatbot</Label>
          <Textarea
            id="system-prompt-addendum"
            value={prompt}
            onChange={(event) => {
              setPrompt(event.target.value)
              setPromptCheck(null)
            }}
            maxLength={8000}
            rows={8}
            placeholder="Ví dụ: Dùng câu ngắn, giọng thân thiện, xưng hô anh/chị và trình bày bằng gạch đầu dòng khi cần."
          />
          <p className="text-xs text-muted-foreground">
            {prompt.length}/8000 ký tự · Phiên bản hiện tại:{' '}
            {data?.active_prompt_revision || 1}. Đây là phần hướng dẫn diễn đạt
            có thể thay đổi; các quy tắc nguồn và an toàn pháp lý nền vẫn được
            khóa trong hệ thống.
          </p>
          {promptCheck && (
            <div className="space-y-2 text-xs">
              <p className={promptCheck.valid ? 'text-emerald-600' : 'text-destructive'} role="status">
                {formatApiError(promptCheck.message, promptCheck.valid ? 'Hướng dẫn đã đạt yêu cầu.' : 'Hướng dẫn chưa đạt yêu cầu.')}
                {promptCheck.warnings.length
                  ? ` ${promptCheck.warnings.map((warning) => formatApiError(warning, 'Có nội dung cần chỉnh sửa.')).join(' ')}`
                  : ''}
              </p>
              {promptCheck.branches_using_prompt?.length ? (
                <p className="text-muted-foreground">
                  Nhánh sử dụng: {promptCheck.branches_using_prompt.join(', ')}
                </p>
              ) : null}
              {promptCheck.runtime_prompt_previews && (
                <details className="rounded-md border p-3">
                  <summary className="cursor-pointer font-medium">Xem prompt runtime thực tế</summary>
                  {Object.entries(promptCheck.runtime_prompt_previews).map(([branch, value]) => (
                    <div className="mt-3" key={branch}>
                      <p className="mb-1 font-medium">{branch}</p>
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 font-mono text-[11px]">{value}</pre>
                    </div>
                  ))}
                </details>
              )}
              {!!promptCheck.style_compilation?.rejected.length && (
                <details className="rounded-md border p-3">
                  <summary className="cursor-pointer font-medium">Các phần không được áp dụng</summary>
                  <ul className="mt-2 list-disc space-y-2 pl-4">
                    {promptCheck.style_compilation.rejected.map((item, index) => (
                      <li key={index}>
                        <p>{item.text}</p>
                        <p className="text-muted-foreground">{item.reason === 'style_length_limit'
                          ? 'Vượt giới hạn độ dài hướng dẫn.'
                          : item.reason === 'outside_presentation_scope'
                            ? 'Nội dung này điều chỉnh quy tắc nghiệp vụ hoặc nguồn, vượt phạm vi hướng dẫn diễn đạt.'
                            : 'Chưa xác định được hướng dẫn về giọng điệu hoặc cách trình bày.'}</p>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
          <div className="flex justify-end pt-2">
            <Button
              type="button"
              onClick={() => void savePrompt()}
              disabled={isLoading || savingSection !== null}
            >
              <Save className="mr-2 h-4 w-4" />{' '}
              {savingSection === 'prompt'
                ? 'Đang lưu…'
                : 'Lưu hướng dẫn chatbot'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {showOrganizationUnits && <Card>
        <CardHeader className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <CardTitle>Phòng ban và lĩnh vực phường/xã</CardTitle>
            <CardDescription>
              Phòng ban là nơi nhận việc và quản lý quyền; lĩnh vực vẫn dùng để
              phân loại nội dung. Mỗi lĩnh vực có một đơn vị chủ trì và có thể
              có nhiều đơn vị phối hợp.
            </CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              setUnits((current) => [...current, newUnit(current.length + 1)])
            }
          >
            <Plus className="mr-1 h-4 w-4" /> Thêm phòng ban trong danh mục
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="rounded-lg border bg-muted/30 p-3">
            <Label htmlFor="organization-routing-mode">Chế độ chuyển đổi</Label>
            <select
              id="organization-routing-mode"
              className="mt-2 h-10 w-full rounded-md border bg-background px-3 text-sm md:max-w-md"
              value={routingMode}
              onChange={(event) =>
                setRoutingMode(event.target.value as OrganizationRoutingMode)
              }
            >
              <option value="legacy">Hệ thống hiện tại</option>
              <option value="shadow">Đối chiếu ngầm, chưa đổi quyền</option>
              <option value="hybrid">Ưu tiên phòng ban, có dữ liệu cũ dự phòng</option>
              <option
                value="unit_primary"
                disabled={
                  routingMode !== 'unit_primary' &&
                  readiness?.ready_for_unit_primary !== true
                }
              >
                Phòng ban là dữ liệu chính
              </option>
            </select>
            <p className="mt-2 text-xs text-muted-foreground">
              Nên chuyển lần lượt theo thứ tự trên. Hệ thống sẽ từ chối chế độ
              “Phòng ban là dữ liệu chính” nếu còn cán bộ, yêu cầu hỗ trợ hoặc dữ
              liệu đã duyệt chưa được phân công. Đề xuất mới được phép nằm trong
              hàng chờ nhưng phải chọn phòng ban hoặc xác nhận dùng chung trước khi duyệt.
            </p>
            <div aria-live="polite" className="mt-3 rounded-md border bg-background p-3 text-sm">
              {readinessLoading ? (
                <p className="text-muted-foreground">Đang kiểm tra điều kiện chuyển đổi…</p>
              ) : !readiness ? (
                <p className="text-amber-700">Chưa đọc được trạng thái chuyển đổi. Hệ thống tạm khóa chế độ dùng phòng ban làm dữ liệu chính.</p>
              ) : readiness.ready_for_unit_primary ? (
                <p className="font-medium text-emerald-700">Đối soát dữ liệu đã đạt. Chỉ chuyển sang dùng phòng ban làm dữ liệu chính sau khi hoàn tất nghiệm thu các luồng nghiệp vụ.</p>
              ) : (
                <div className="space-y-2">
                  <p className="font-medium text-amber-800">Cần hoàn tất các mục sau trước khi chuyển hoàn toàn:</p>
                  <ul className="grid gap-1 sm:grid-cols-2">
                    {READINESS_LABELS.filter(([key]) => Number(readiness[key] || 0) > 0).map(([key, label]) => (
                      <li key={key} className="flex items-center justify-between gap-3 rounded border px-2 py-1.5">
                        <span>{label}</span>
                        <span className="font-semibold tabular-nums">{readiness[key]}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="text-muted-foreground">Một văn bản có thể xuất hiện ở nhiều mục; các số này là số điều kiện còn vướng, không phải tổng văn bản riêng biệt.</p>
                </div>
              )}
              {readiness && readiness.pending_candidates_without_assignment > 0 && (
                <p className="mt-3 border-t pt-3 text-muted-foreground">
                  Có {readiness.pending_candidates_without_assignment} đề xuất đang chờ phân công.
                  Đây là hàng việc cần xem xét, không phải lỗi chuyển đổi.
                  {' '}<a href="/legal-import" className="underline underline-offset-4">Mở danh sách đề xuất</a>
                </p>
              )}
            </div>
          </div>
          {units.map((unit) => (
            <details key={unit.id} className="rounded-lg border p-3 space-y-3">
              <summary className="cursor-pointer rounded-md p-2 text-sm">
                <span className="font-semibold">{unit.name || 'Phòng ban mới'}</span>
                <span className="ml-3 text-muted-foreground">{unit.is_active ? 'Đang hoạt động' : 'Tạm ngừng'} · {(unit.domain_assignments || []).length} lĩnh vực</span>
                <span className="ml-3 text-primary">Chỉnh sửa / phân công</span>
              </summary>
              <div className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]">
                <label className="space-y-1 text-sm"><span>Tên phòng ban</span>
                <Input
                  aria-label={`Tên ${unit.code}`}
                  value={unit.name}
                  onChange={(event) =>
                    updateUnit(unit.id, { name: event.target.value })
                  }
                />
                </label>
                <label className="space-y-1 text-sm"><span>Mã liên kết</span>
                <Input
                  aria-label={`Mã ${unit.code}`}
                  value={unit.code}
                  readOnly
                />
                </label>
                <label className="space-y-1 text-sm"><span>Tên hiển thị ngắn</span>
                <Input
                  aria-label={`Tên ngắn ${unit.code}`}
                  value={unit.short_name || ''}
                  onChange={(event) =>
                    updateUnit(unit.id, { short_name: event.target.value })
                  }
                  placeholder="Tên ngắn"
                />
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="self-end text-destructive"
                  onClick={() => void removeUnit(unit)}
                  disabled={
                    deletingUnitId === unit.id || savingSection === 'units'
                  }
                  aria-label="Ngừng phòng ban"
                  title="Ngừng phòng ban"
                >
                  <Trash2 className="mr-1 h-4 w-4" /> Ngừng phòng ban
                </Button>
              </div>
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {domainOptions.map(([domain, label]) => {
                  const responsibility =
                    unit.domain_assignments?.find(
                      (item) => item.domain_code === domain
                    )?.responsibility || ''
                  const ownedElsewhere =
                    activeDomains.has(domain) &&
                    responsibility !== 'primary' &&
                    unit.is_active &&
                    unit.support_enabled
                  return (
                    <label
                      key={domain}
                      className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs"
                    >
                      <span>{label}</span>
                      <select
                        aria-label={`Vai trò của ${unit.name} với ${label}`}
                        className="h-8 rounded-md border bg-background px-2"
                        value={responsibility}
                        onChange={(event) =>
                          setDomainResponsibility(
                            unit,
                            domain,
                            event.target.value as '' | 'primary' | 'support'
                          )
                        }
                      >
                        <option value="">Không phụ trách</option>
                        <option value="primary" disabled={ownedElsewhere}>
                          Chủ trì
                        </option>
                        <option value="support">Phối hợp</option>
                      </select>
                    </label>
                  )
                })}
              </div>
              <div className="space-y-1">
                <Label htmlFor={`aliases-${unit.id}`}>Tên cũ hoặc tên thường gọi</Label>
                <Input
                  id={`aliases-${unit.id}`}
                  value={(unit.aliases || []).join(', ')}
                  onChange={(event) =>
                    updateUnit(unit.id, {
                      aliases: event.target.value
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean)
                    })
                  }
                  placeholder="Ngăn cách nhiều tên bằng dấu phẩy"
                />
                <p className="text-xs text-muted-foreground">
                  Dùng để nhận diện dữ liệu cũ và tên phòng ban xuất hiện trên
                  website nguồn; đổi tên không làm thay đổi mã liên kết.
                </p>
              </div>
              <div className="flex flex-wrap gap-4 text-xs">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={unit.is_active}
                    onChange={(event) =>
                      updateUnit(unit.id, { is_active: event.target.checked })
                    }
                  />{' '}
                  Đang hoạt động
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={unit.support_enabled}
                    onChange={(event) =>
                      updateUnit(unit.id, {
                        support_enabled: event.target.checked
                      })
                    }
                  />{' '}
                  Nhận hỗ trợ trực tuyến
                </label>
              </div>
              {unit.is_active &&
                unit.support_enabled &&
                (unit.domain_assignments || []).length === 0 && (
                  <p className="text-xs text-amber-700">
                    Chưa gán lĩnh vực nên đơn vị này chưa nhận được yêu cầu hỗ
                    trợ.
                  </p>
                )}
            </details>
          ))}
          {units.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Chưa có đơn vị. Bấm “Thêm phòng ban trong danh mục” để bắt đầu.
            </p>
          )}
          <div className="flex justify-end pt-2">
            <Button
              type="button"
              onClick={() => void saveUnits()}
              disabled={
                isLoading || savingSection !== null || deletingUnitId !== null
              }
            >
              <Save className="mr-2 h-4 w-4" />{' '}
              {savingSection === 'units' ? 'Đang lưu…' : 'Lưu cơ cấu đơn vị'}
            </Button>
          </div>
        </CardContent>
      </Card>}
    </div>
  )
}
