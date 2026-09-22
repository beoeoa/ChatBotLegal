'use client'

import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Database, Server } from 'lucide-react'
import { ConnectionError } from '@/lib/types/config'

interface ConnectionErrorOverlayProps {
  error: ConnectionError
  onRetry: () => void
}

export function ConnectionErrorOverlay({
  error,
  onRetry,
}: ConnectionErrorOverlayProps) {
  const isApiError = error.type === 'api-unreachable'

  return (
    <div
      className="fixed inset-0 bg-background z-50 flex items-center justify-center p-4"
      role="alert"
      aria-live="assertive"
      aria-atomic="true"
    >
      <Card className="max-w-2xl w-full p-8 space-y-6">
        {/* Error icon and title */}
        <div className="flex items-center gap-4">
          {isApiError ? (
            <Server className="w-12 h-12 text-destructive" aria-hidden="true" />
          ) : (
            <Database className="w-12 h-12 text-destructive" aria-hidden="true" />
          )}
          <div>
            <h1 className="text-2xl font-bold" id="error-title">
              {isApiError ? 'Không thể kết nối tới hệ thống' : 'Kho dữ liệu chưa sẵn sàng'}
            </h1>
            <p className="text-muted-foreground">
              {isApiError
                ? 'Ứng dụng chưa nhận được phản hồi. Dữ liệu bạn đang nhập trên màn hình chưa bị gửi đi.'
                : 'Hệ thống tạm thời chưa đọc được dữ liệu. Vui lòng thử lại sau ít phút.'}
            </p>
          </div>
        </div>

        <div className="space-y-4 border-l-4 border-primary pl-4">
          <h2 className="font-semibold">Bạn có thể làm gì?</h2>
          <ul className="list-disc list-inside space-y-2 text-sm">
            <li>Kiểm tra kết nối mạng của thiết bị.</li>
            <li>Chờ khoảng 30 giây rồi bấm “Thử lại”.</li>
            <li>Nếu lỗi tiếp diễn, liên hệ quản trị viên hệ thống.</li>
          </ul>
        </div>

        {/* Retry button */}
        <div className="pt-4 border-t">
          <Button onClick={onRetry} className="w-full" size="lg">
            Thử kết nối lại
          </Button>
          <p className="text-xs text-muted-foreground text-center mt-2">
            Bạn cũng có thể nhấn phím R để thử lại.
          </p>
        </div>
      </Card>
    </div>
  )
}
