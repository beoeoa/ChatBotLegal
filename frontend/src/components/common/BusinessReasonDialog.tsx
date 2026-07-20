'use client'

import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'

interface BusinessReasonDialogProps {
  open: boolean
  resourceKey: string
  onOpenChange?: (open: boolean) => void
  onConfirmed: () => void
}

export function getBusinessReason(resourceKey: string): string | null {
  if (typeof window === 'undefined') return null
  return sessionStorage.getItem(`business-reason:${resourceKey}`)
}

export function BusinessReasonDialog({ open, resourceKey, onOpenChange, onConfirmed }: BusinessReasonDialogProps) {
  const [reason, setReason] = useState('')
  useEffect(() => {
    if (open) setReason('')
  }, [open])

  const confirm = () => {
    const normalized = reason.trim()
    if (normalized.length < 10) return
    sessionStorage.setItem(`business-reason:${resourceKey}`, normalized)
    onConfirmed()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Nhập lý do nghiệp vụ</DialogTitle>
          <DialogDescription>
            Việc mở nội dung chi tiết sẽ được ghi vào nhật ký truy cập để phục vụ kiểm tra nghiệp vụ.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Ví dụ: Kiểm tra hồ sơ theo yêu cầu xử lý vụ việc số..."
          rows={4}
          autoFocus
        />
        <p className="text-xs text-muted-foreground">Tối thiểu 10 ký tự.</p>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange?.(false)}>Hủy</Button>
          <Button disabled={reason.trim().length < 10} onClick={confirm}>Mở hồ sơ</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
