'use client'

import { createContext, useContext, useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import dynamic from 'next/dynamic'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

const Viewer = dynamic(() => import('./LegalDocumentViewer').then(module => module.LegalDocumentViewer), {
  loading: () => <p className="p-6" role="status">Đang mở nội dung điều luật…</p>,
})
const PreviewContext = createContext<((href: string) => void) | null>(null)

export function useLegalPreview() {
  const open = useContext(PreviewContext)
  const router = useRouter()
  return open || ((href: string) => router.push(href))
}

export function LegalPreviewProvider({ children }: { children: ReactNode }) {
  const [href, setHref] = useState<string | null>(null)
  const target = href ? new URL(href, 'http://local') : null
  const open = (value: string) => {
    if (value.startsWith('/legal-documents/')) setHref(value)
  }
  return <PreviewContext.Provider value={open}>
    <div className="contents" onClickCapture={event => {
      const anchor = (event.target as HTMLElement).closest('a')
      const destination = anchor?.getAttribute('href')
      if (destination?.startsWith('/legal-documents/') && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
        event.preventDefault()
        event.stopPropagation()
        open(destination)
      }
    }}>{children}</div>
    <Dialog open={Boolean(target)} onOpenChange={value => { if (!value) setHref(null) }}>
      <DialogContent onClickCapture={event => {
        const anchor = (event.target as HTMLElement).closest('a')
        const destination = anchor?.getAttribute('href')
        if (destination?.startsWith('/legal-documents/') && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
          event.preventDefault()
          event.stopPropagation()
          open(destination)
        }
      }} className="!left-auto !right-0 !top-0 !translate-x-0 !translate-y-0 !h-dvh !w-full !max-w-full lg:!max-w-[min(85vw,1200px)] !rounded-none !gap-0 !p-0 flex min-w-0 flex-col">
        <div className="shrink-0 border-b bg-card px-5 py-4 pr-14">
          <DialogTitle className="text-xl">Đối chiếu điều luật</DialogTitle>
          <p className="mt-1 text-sm text-muted-foreground">Đóng khung này để tiếp tục cuộc trò chuyện tại vị trí đang đọc.</p>
        </div>
        {target && <Viewer key={href} embedded
          docId={decodeURIComponent(target.pathname.slice('/legal-documents/'.length)).replace(/^legal:/, '')}
          lawNumber={target.searchParams.get('law_number') || ''}
          articleParam={target.searchParams.get('article') || ''}
          clauseParam={target.searchParams.get('clause') || ''}
          pointParam={target.searchParams.get('point') || ''} />}
      </DialogContent>
    </Dialog>
  </PreviewContext.Provider>
}
