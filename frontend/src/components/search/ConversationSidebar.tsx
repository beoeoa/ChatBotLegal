'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { MessageSquarePlus, Pencil, Trash2, X, Menu } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'
import { apiClient } from '@/lib/api/client'
import type { UserRole } from '@/lib/stores/auth-store'
import type { AskMessage } from '@/lib/types/search'

export interface ConversationSummary {
  id: string
  title: string
  domain?: string | null
  role_context: string
  status: string
  owner_user_id?: string | null
  created_at: string
  last_message_at: string
  expires_at?: string
  message_count: number
}

export type ConversationMessage = AskMessage

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[]
}

interface ConversationSidebarProps {
  role: UserRole
  currentId: string | null
  onSelect: (conversation: ConversationDetail) => void
  onCreate: (conversation: ConversationDetail) => void
  onDeleted: (id: string) => void
  className?: string
  mobileOpen?: boolean
  onMobileOpenChange?: (open: boolean) => void
  refreshKey?: number
}

export function ConversationSidebar({
  role,
  currentId,
  onSelect,
  onCreate,
  onDeleted,
  className,
  mobileOpen = false,
  onMobileOpenChange,
  refreshKey = 0,
}: ConversationSidebarProps) {
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get('/conversations/')
      setItems(res.data || [])
    } catch {
      // Keep empty list if backend is unavailable
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, role, refreshKey])

  const create = useCallback(async () => {
    try {
      const res = await apiClient.post('/conversations/', {
        title: 'Cuộc trò chuyện mới',
      })
      const created = res.data as ConversationDetail
      setItems((prev) => [created, ...prev.filter((item) => item.id !== created.id)])
      onCreate(created)
      onMobileOpenChange?.(false)
    } catch {
      toast.error('Không tạo được cuộc trò chuyện mới.')
    }
  }, [onCreate, onMobileOpenChange])

  const open = useCallback(async (id: string) => {
    try {
      const res = await apiClient.get(`/conversations/${id}`)
      onSelect(res.data as ConversationDetail)
      onMobileOpenChange?.(false)
    } catch {
      toast.error('Không tải được cuộc trò chuyện.')
    }
  }, [onSelect, onMobileOpenChange])

  const rename = useCallback(async (id: string) => {
    const title = renameValue.trim()
    if (!title) return
    try {
      const res = await apiClient.patch(`/conversations/${id}`, { title })
      setItems((prev) => prev.map((item) => (item.id === id ? { ...item, title: res.data.title } : item)))
      setRenamingId(null)
    } catch {
      toast.error('Không đổi được tên cuộc trò chuyện.')
    }
  }, [renameValue])

  const remove = useCallback(async (id: string) => {
    try {
      await apiClient.delete(`/conversations/${id}`)
      setItems((prev) => prev.filter((item) => item.id !== id))
      onDeleted(id)
    } catch {
      toast.error('Không xóa được cuộc trò chuyện.')
    }
  }, [onDeleted])

  const body = useMemo(() => (
    <div className="flex h-full flex-col border-r bg-background">
      <div className="flex items-center justify-between gap-2 border-b p-3">
        <div>
          <p className="text-sm font-semibold">Lịch sử chat</p>
          <p className="text-[11px] text-muted-foreground">Riêng theo tài khoản / vai trò {role}</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void create()} className="gap-1">
          <MessageSquarePlus className="h-3.5 w-3.5" />
          Mới
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading && <p className="px-2 py-3 text-xs text-muted-foreground">Đang tải…</p>}
        {!loading && items.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted-foreground">Chưa có cuộc trò chuyện. Bấm “Mới” để bắt đầu.</p>
        )}
        {items.map((item) => (
          <div
            key={item.id}
            className={cn(
              'group rounded-lg border px-2 py-2 text-left transition-colors',
              currentId === item.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
            )}
          >
            {renamingId === item.id ? (
              <div className="flex items-center gap-1">
                <Input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  className="h-8 text-xs"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void rename(item.id)
                    if (e.key === 'Escape') setRenamingId(null)
                  }}
                />
                <Button size="sm" className="h-8 px-2" onClick={() => void rename(item.id)}>OK</Button>
              </div>
            ) : (
              <button type="button" className="w-full text-left" onClick={() => void open(item.id)}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{item.title}</p>
                    <p className="text-[11px] text-muted-foreground">
                      {item.message_count || 0} tin · {item.last_message_at ? new Date(item.last_message_at).toLocaleString('vi-VN') : '—'}
                    </p>
                  </div>
                  {item.domain ? <Badge variant="outline" className="shrink-0 text-[10px]">{item.domain}</Badge> : null}
                </div>
              </button>
            )}
            <div className="mt-1 flex justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                onClick={() => {
                  setRenamingId(item.id)
                  setRenameValue(item.title)
                }}
                aria-label="Đổi tên"
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-destructive"
                onClick={() => void remove(item.id)}
                aria-label="Xóa"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  ), [create, currentId, items, loading, open, remove, rename, renameValue, renamingId, role])

  return (
    <>
      {/* Desktop sidebar */}
      <aside className={cn('hidden md:block w-72 shrink-0', className)}>
        {body}
      </aside>

      {/* Mobile trigger + drawer */}
      <div className="md:hidden">
        <Button
          variant="outline"
          size="sm"
          className="mb-2 gap-1"
          onClick={() => onMobileOpenChange?.(true)}
        >
          <Menu className="h-4 w-4" />
          Lịch sử chat
        </Button>
        {mobileOpen ? (
          <div className="fixed inset-0 z-50 bg-black/40" onClick={() => onMobileOpenChange?.(false)}>
            <div
              className="absolute left-0 top-0 h-full w-[85%] max-w-sm bg-background shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-end p-2">
                <Button size="icon" variant="ghost" onClick={() => onMobileOpenChange?.(false)}>
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <div className="h-[calc(100%-48px)]">{body}</div>
            </div>
          </div>
        ) : null}
      </div>
    </>
  )
}
