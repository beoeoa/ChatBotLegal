'use client'

import { useCallback, useEffect, useState } from 'react'
import { History } from 'lucide-react'
import { apiClient } from '@/lib/api/client'

type MemoryItem = {
  id: string
  memory_key: string
  value: unknown
  label?: string | null
}

type MemorySettings = {
  enabled: boolean
  runtime_available: boolean
}

export function ChatMemoryContinuations({
  onSelect,
}: {
  onSelect: (question: string, memoryItemId: string) => void
}) {
  const [items, setItems] = useState<MemoryItem[]>([])

  const load = useCallback(async () => {
    try {
      const [settingsResponse, itemsResponse] = await Promise.all([
        apiClient.get<MemorySettings>('/chat-memory/settings'),
        apiClient.get<MemoryItem[]>('/chat-memory/items'),
      ])
      if (!settingsResponse.data.runtime_available || !settingsResponse.data.enabled) {
        setItems([])
        return
      }
      setItems(itemsResponse.data.filter((item) => item.memory_key === 'tracked_procedure').slice(0, 3))
    } catch {
      setItems([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (items.length === 0) return null

  return (
    <div className="mt-4" data-testid="saved-memory-continuations">
      <p className="mb-2 flex items-center justify-center gap-1.5 text-xs font-medium text-muted-foreground">
        <History className="h-3.5 w-3.5" /> Nội dung anh/chị đang theo dõi
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {items.map((item) => {
          const value = item.value && typeof item.value === 'object' ? item.value as { name?: string; id?: string } : null
          const label = item.label || value?.name || value?.id || 'Thủ tục đã lưu'
          return (
            <button
              key={item.id}
              type="button"
              className="rounded-full border bg-background px-3 py-1.5 text-xs transition-colors hover:border-primary hover:bg-primary/5"
              onClick={() => onSelect(`Tôi muốn tiếp tục hỏi về ${label}.`, item.id)}
            >
              Tiếp tục: {label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
