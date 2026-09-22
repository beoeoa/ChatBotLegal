'use client'

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Check, X } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { ModelTestResult } from '@/lib/types/models'
import { formatApiError } from '@/lib/utils/error-handler'

export function ModelTestResultDialog({
  open,
  onOpenChange,
  result,
  modelName,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  result: ModelTestResult | null
  modelName: string
}) {
  const { t } = useTranslation()

  if (!result) return null

  const checks = [
    ['Kết nối nhà cung cấp', result.checks?.provider_connectivity],
    ['Sinh nội dung văn bản', result.checks?.text_generation],
    ['Trả Markdown thông thường', result.checks?.markdown_output],
    ['Giữ mã trích dẫn nguồn', result.checks?.citation_marker],
  ].filter((item): item is [string, boolean] => typeof item[1] === 'boolean')
  const latency = result.latency_ms ?? result.checks?.latency_ms

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {result.success ? (
              <Check className="h-5 w-5 text-emerald-500" />
            ) : (
              <X className="h-5 w-5 text-destructive" />
            )}
            {result.success ? t('models.testModelSuccess') : t('models.testModelFailed')}
          </DialogTitle>
        </DialogHeader>

        <div
          className="space-y-3"
          role={result.success ? 'status' : 'alert'}
          aria-live="polite"
        >
          <p className="text-sm text-muted-foreground">{modelName}</p>
          <p className="text-sm">
            {formatApiError(
              result.message || result.details,
              result.success
                ? 'Mô hình đáp ứng hợp đồng RAG.'
                : 'Mô hình chưa đáp ứng hợp đồng RAG. Hãy kiểm tra cấu hình và thử lại.',
            )}
          </p>
          {checks.length > 0 && (
            <ul className="space-y-2 rounded-md border p-3 text-sm">
              {checks.map(([label, passed]) => (
                <li key={label} className="flex items-center justify-between gap-3">
                  <span>{label}</span>
                  <span className={passed ? 'text-emerald-600' : 'text-destructive'}>
                    {passed ? 'Đạt' : 'Không đạt'}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {typeof latency === 'number' && latency > 0 && (
            <p className="text-xs text-muted-foreground">
              Thời gian kiểm tra: {(latency / 1000).toFixed(1)} giây
            </p>
          )}
          {result.checks?.json_mode_required === false && (
            <p className="text-xs text-muted-foreground">
              Luồng trả lời mới không yêu cầu JSON mode hoặc tool calling.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.done')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
