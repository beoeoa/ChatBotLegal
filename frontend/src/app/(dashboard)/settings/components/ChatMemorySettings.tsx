'use client'

import { useCallback, useEffect, useState } from 'react'
import { Brain, Pencil, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'

type MemorySettings = {
  enabled: boolean
  runtime_available: boolean
  retention_days: number
}

type MemoryItem = {
  id: string
  memory_key: string
  value: unknown
  label?: string | null
  expires_at?: string | null
}

function displayValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') {
    const object = value as { name?: string; id?: string }
    return object.name || object.id || JSON.stringify(value)
  }
  return String(value ?? '')
}

export function ChatMemorySettings() {
  const role = useAuthStore((state) => state.role)
  const [settings, setSettings] = useState<MemorySettings | null>(null)
  const [items, setItems] = useState<MemoryItem[]>([])
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<{ id: string; value: string } | null>(null)

  const load = useCallback(async () => {
    if (role !== 'citizen' && role !== 'officer') return
    try {
      const [settingsResponse, itemsResponse] = await Promise.all([
        apiClient.get<MemorySettings>('/chat-memory/settings'),
        apiClient.get<MemoryItem[]>('/chat-memory/items'),
      ])
      setSettings(settingsResponse.data)
      setItems(itemsResponse.data)
    } catch {
      setSettings(null)
      setItems([])
    }
  }, [role])

  useEffect(() => {
    void load()
  }, [load])

  if (role !== 'citizen' && role !== 'officer') return null

  const toggle = async (enabled: boolean) => {
    setBusy(true)
    try {
      const response = await apiClient.patch<MemorySettings>('/chat-memory/settings', { enabled })
      setSettings(response.data)
      toast.success(enabled ? 'Đã bật trí nhớ dài hạn.' : 'Đã tắt trí nhớ dài hạn.')
    } catch {
      toast.error('Không thể cập nhật cài đặt trí nhớ.')
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!editing) return
    setBusy(true)
    try {
      await apiClient.patch(`/chat-memory/items/${editing.id}`, { value: editing.value.trim() })
      setEditing(null)
      await load()
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id: string) => {
    setBusy(true)
    try {
      await apiClient.delete(`/chat-memory/items/${id}`)
      await load()
    } finally {
      setBusy(false)
    }
  }

  const clearAll = async () => {
    setBusy(true)
    try {
      await apiClient.delete('/chat-memory/items')
      await load()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card data-testid="chat-memory-settings">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Brain className="h-5 w-5" /> Trí nhớ hội thoại</CardTitle>
        <CardDescription>
          Ghi nhớ cách xưng hô, địa bàn và thủ tục đang theo dõi. Chatbot không lưu kết luận pháp luật do mô hình tự tạo.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between rounded-lg border p-3">
          <div className="space-y-1">
            <Label htmlFor="long-term-memory">Trí nhớ dài hạn</Label>
            <p className="text-xs text-muted-foreground">
              Mặc định tắt · tự hết hạn sau {settings?.retention_days || 365} ngày không sử dụng.
            </p>
          </div>
          <Checkbox
            id="long-term-memory"
            checked={Boolean(settings?.enabled)}
            disabled={busy || !settings?.runtime_available}
            onCheckedChange={(checked) => void toggle(checked === true)}
          />
        </div>
        {settings && !settings.runtime_available && (
          <p className="text-xs text-amber-700">Tính năng đang tắt ở cấu hình hệ thống. Cài đặt của anh/chị chưa được kích hoạt.</p>
        )}
        {items.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">Thông tin đang nhớ</p>
              <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void clearAll()}>
                Xóa tất cả
              </Button>
            </div>
            {items.map((item) => (
              <div key={item.id} className="flex items-center gap-2 rounded-lg border p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-muted-foreground">{item.label || item.memory_key}</p>
                  {editing?.id === item.id ? (
                    <Input value={editing.value} onChange={(event) => setEditing({ id: item.id, value: event.target.value })} />
                  ) : (
                    <p className="truncate text-sm">{displayValue(item.value)}</p>
                  )}
                </div>
                {editing?.id === item.id ? (
                  <Button type="button" size="sm" disabled={busy || !editing.value.trim()} onClick={() => void save()}>Lưu</Button>
                ) : (
                  <Button type="button" variant="ghost" size="icon" aria-label="Sửa memory" onClick={() => setEditing({ id: item.id, value: displayValue(item.value) })}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                )}
                <Button type="button" variant="ghost" size="icon" aria-label="Xóa memory" disabled={busy} onClick={() => void remove(item.id)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
