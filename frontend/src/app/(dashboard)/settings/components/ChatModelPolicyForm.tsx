'use client'

import { useEffect, useMemo, useState } from 'react'
import { Save } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from '@/components/ui/card'
import { useSettings } from '@/lib/hooks/use-settings'
import { useModels } from '@/lib/hooks/use-models'
import { apiClient } from '@/lib/api/client'
import type { ChatModelPolicyConfig } from '@/lib/types/api'
import { getApiErrorMessage } from '@/lib/utils/error-handler'

type Audience = 'citizen' | 'officer'

export function ChatModelPolicyForm() {
  const { data: settings, refetch: refetchSettings } = useSettings()
  const { data: models = [], isLoading } = useModels()
  const languageModels = useMemo(
    () => models.filter((model) => model.type === 'language'),
    [models]
  )
  const [policy, setPolicy] = useState<ChatModelPolicyConfig[]>([])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setPolicy(settings?.chat_model_policy || [])
  }, [settings?.chat_model_policy])

  const modelForPolicyId = (modelId: string) => {
    const shortId = modelId.includes(':') ? modelId.split(':').pop() : modelId
    return languageModels.find(
      (model) =>
        model.id === modelId || model.id === shortId || model.name === modelId
    )
  }

  const rowFor = (modelId: string) => {
    return policy.find((row) => {
      if (row.model_id === modelId) return true
      return Boolean(modelForPolicyId(row.model_id)?.id === modelId)
    })
  }

  const hasStalePolicy = policy.some(
    (row) => row.audiences.length > 0 && !modelForPolicyId(row.model_id)
  )

  const updateModel = (
    modelId: string,
    patch: Partial<ChatModelPolicyConfig>
  ) => {
    setPolicy((current) => {
      const existing = current.find((row) => {
        if (row.model_id === modelId) return true
        return Boolean(modelForPolicyId(row.model_id)?.id === modelId)
      })
      if (existing)
        return current.map((row) =>
          row.model_id === existing.model_id ? { ...row, ...patch } : row
        )
      const model = languageModels.find((item) => item.id === modelId)
      return [
        ...current,
        {
          option_id: `model-${modelId}`,
          model_id: modelId,
          display_name: model?.name || modelId,
          audiences: [],
          default_for: [],
          is_active: true
        }
      ]
    })
  }

  const toggleAudience = (modelId: string, audience: Audience) => {
    const row = rowFor(modelId)
    const audiences = new Set(row?.audiences || [])
    if (audiences.has(audience)) audiences.delete(audience)
    else audiences.add(audience)
    const defaultFor = (row?.default_for || []).filter((item) =>
      audiences.has(item)
    )
    updateModel(modelId, {
      audiences: [...audiences] as Audience[],
      default_for: defaultFor as Audience[]
    })
  }

  const setDefault = (modelId: string, audience: Audience) => {
    const row = rowFor(modelId)
    if (!row?.audiences.includes(audience)) return
    setPolicy((current) =>
      current.map((item) => ({
        ...item,
        default_for:
          item.model_id === row.model_id
            ? [...new Set([...item.default_for, audience])]
            : item.default_for.filter((value) => value !== audience)
      }))
    )
  }

  const save = async () => {
    setSaving(true)
    try {
      const modelsById = new Map(
        languageModels.map((model) => [model.id, model])
      )
      const modelsByShortId = new Map(
        languageModels
          .filter((model) => model.id.includes(':'))
          .map((model) => [model.id.split(':').pop() as string, model])
      )
      const modelsByName = new Map(
        languageModels.map((model) => [model.name.toLowerCase(), model])
      )
      const canonicalRows = new Map<string, ChatModelPolicyConfig>()
      for (const row of policy) {
        if (row.audiences.length === 0) continue
        const model =
          modelsById.get(row.model_id) ||
          modelsByShortId.get(row.model_id) ||
          modelsByName.get(row.model_id.toLowerCase())
        if (!model) continue
        const existing = canonicalRows.get(model.id)
        const audiences = [
          ...new Set([...(existing?.audiences || []), ...row.audiences])
        ] as Audience[]
        const defaultFor = [
          ...new Set([...(existing?.default_for || []), ...row.default_for])
        ].filter((audience) => audiences.includes(audience)) as Audience[]
        canonicalRows.set(model.id, {
          ...(existing || row),
          option_id: `model-${model.id}`,
          model_id: model.id,
          display_name: model.name,
          audiences,
          default_for: defaultFor,
          is_active: true
        })
      }
      const payload = [...canonicalRows.values()]
      // Older settings can contain more than one default for an audience.
      // Keep the last model selected in the UI as the winner so a migrated
      // policy can be saved and repaired instead of failing with a generic
      // server error.
      for (const audience of ['citizen', 'officer'] as Audience[]) {
        const defaults = payload.filter((row) => row.default_for.includes(audience))
        const winner = defaults.at(-1)?.model_id
        if (!winner) continue
        for (const row of payload) {
          if (row.model_id !== winner) {
            row.default_for = row.default_for.filter((value) => value !== audience)
          }
        }
      }
      const response = await apiClient.put<ChatModelPolicyConfig[]>(
        '/settings/model-policy',
        payload
      )
      setPolicy(response.data)
      await refetchSettings()
      toast.success('Đã lưu danh sách model được phép dùng.')
    } catch (error: unknown) {
      toast.error(
        getApiErrorMessage(
          error,
          (key) => key,
          'Không thể lưu chính sách model.'
        )
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Model hiển thị cho người dùng</CardTitle>
        <CardDescription>
          Chỉ model ngôn ngữ được cấp trong bảng dưới đây mới xuất hiện ở giao
          diện hỏi đáp. Thông tin bí mật của khóa kết nối không được hiển thị.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Đang tải model…</p>
        )}
        {!isLoading && languageModels.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Chưa có model ngôn ngữ nào.
          </p>
        )}
        {!isLoading && hasStalePolicy && (
          <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
            Một số model cũ trong chính sách không còn trong danh sách model
            hiện tại. Khi lưu, các dòng đó sẽ được bỏ qua.
          </p>
        )}
        {languageModels.map((model) => {
          const row = rowFor(model.id)
          return (
            <div
              key={model.id}
              className="grid gap-3 rounded-lg border p-3 md:grid-cols-[1fr_auto_auto] md:items-center"
            >
              <div>
                <p className="font-medium">{row?.display_name || model.name}</p>
                <p className="text-xs text-muted-foreground">
                  {model.provider}
                </p>
              </div>
              {(['citizen', 'officer'] as Audience[]).map((audience) => (
                <label
                  key={audience}
                  className="flex items-center gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={row?.audiences.includes(audience) || false}
                    onChange={() => toggleAudience(model.id, audience)}
                  />
                  {audience === 'citizen' ? 'Người dân' : 'Cán bộ'}
                  <Button
                    type="button"
                    size="sm"
                    variant={
                      row?.default_for.includes(audience)
                        ? 'default'
                        : 'outline'
                    }
                    disabled={!row?.audiences.includes(audience)}
                    onClick={() => setDefault(model.id, audience)}
                  >
                    {row?.default_for.includes(audience)
                      ? 'Mặc định'
                      : 'Đặt mặc định'}
                  </Button>
                </label>
              ))}
            </div>
          )
        })}
        <div className="flex justify-end pt-2">
          <Button
            type="button"
            onClick={() => void save()}
            disabled={saving || isLoading}
          >
            <Save className="mr-2 h-4 w-4" />{' '}
            {saving ? 'Đang lưu…' : 'Lưu chính sách model'}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
