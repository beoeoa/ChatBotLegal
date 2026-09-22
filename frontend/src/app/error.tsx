'use client'

import { useEffect } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function ApplicationError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error('Application route failed:', error)
  }, [error])

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4" role="alert">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <AlertTriangle className="mx-auto mb-3 h-10 w-10 text-destructive" aria-hidden="true" />
          <CardTitle>Trang này đang gặp sự cố</CardTitle>
          <CardDescription>
            Dữ liệu bạn đã nhập chưa được xác nhận gửi đi. Vui lòng thử lại hoặc tải lại trang.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button className="w-full" onClick={reset}>
            <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
            Thử lại
          </Button>
          <Button className="w-full" variant="outline" onClick={() => window.location.reload()}>
            Tải lại trang
          </Button>
        </CardContent>
      </Card>
    </main>
  )
}
