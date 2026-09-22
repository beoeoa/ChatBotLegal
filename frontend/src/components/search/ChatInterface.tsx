'use client'

import type { ReactNode, UIEventHandler } from 'react'
import { MessageSquarePlus } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ChatInterfaceProps {
  children: ReactNode
  composer: ReactNode
  title: string
  description?: string
  onNewChat: () => void
  onLoadOlder?: () => void
  hasOlder?: boolean
  loadingOlder?: boolean
  messageViewportRef?: React.RefObject<HTMLDivElement | null>
  onViewportScroll?: UIEventHandler<HTMLDivElement>
  headerExtra?: ReactNode
}

export function ChatInterface({
  children,
  composer,
  title,
  description,
  onNewChat,
  onLoadOlder,
  hasOlder = false,
  loadingOlder = false,
  messageViewportRef,
  onViewportScroll,
  headerExtra,
}: ChatInterfaceProps) {
  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background" data-testid="chat-interface">
      <header className="shrink-0 border-b border-border/70 bg-card/90 px-4 py-4 backdrop-blur md:px-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-xl font-semibold text-foreground">{title}</h2>
            {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
          </div>
          <Button type="button" variant="secondary" size="sm" className="shrink-0 gap-1" onClick={onNewChat}>
            <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
            <span className="hidden sm:inline">+ Cuộc trò chuyện mới</span>
            <span className="sm:hidden">+ Mới</span>
          </Button>
        </div>
        {headerExtra}
      </header>

      <div
        ref={messageViewportRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-background/60 px-3 py-5 md:px-5"
        data-testid="chat-message-viewport"
        onScroll={onViewportScroll}
        tabIndex={0}
        aria-label="Nội dung cuộc trò chuyện"
      >
        {hasOlder && (
          <div className="mb-4 flex justify-center">
            <Button type="button" variant="ghost" size="sm" onClick={onLoadOlder} disabled={loadingOlder}>
              {loadingOlder ? 'Đang tải tin cũ…' : 'Tải tin nhắn cũ hơn'}
            </Button>
          </div>
        )}
        {children}
      </div>

      <div className="shrink-0 border-t border-border/70 bg-card/95 p-4 backdrop-blur supports-[backdrop-filter]:bg-card/85 md:px-6" data-testid="chat-composer">
        {composer}
      </div>
    </section>
  )
}
