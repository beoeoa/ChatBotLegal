'use client'

import { ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ForbiddenStateProps {
  title?: string
  description?: string
  onBack?: () => void
  backLabel?: string
}

export function ForbiddenState({
  title = 'Bạn không có quyền truy cập',
  description = 'Nội dung này chỉ hiển thị cho người dùng được phân quyền.',
  onBack,
  backLabel = 'Về trang Hỏi đáp',
}: ForbiddenStateProps) {
  return (
    <div className="flex min-h-[360px] items-center justify-center p-6">
      <div className="max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
        <ShieldAlert className="mx-auto mb-4 h-10 w-10 text-destructive" aria-hidden="true" />
        <h1 className="text-xl font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{description}</p>
        {onBack && (
          <Button className="mt-6" onClick={onBack}>
            {backLabel}
          </Button>
        )}
      </div>
    </div>
  )
}
