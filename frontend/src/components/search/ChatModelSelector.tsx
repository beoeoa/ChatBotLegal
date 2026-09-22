'use client'

import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export type ChatModelOption = { option_id: string; display_name: string; audiences: Array<'citizen' | 'officer'>; is_default: boolean; is_active: boolean }
export type AnswerDepth = 'quick' | 'balanced' | 'deep'
const DEPTHS: Array<[AnswerDepth, string, string]> = [
  ['quick', 'Nhanh', 'Trả lời gọn, ưu tiên thời gian'],
  ['balanced', 'Cân bằng', 'Giải thích và đối chiếu căn cứ'],
  ['deep', 'Chuyên sâu', 'Phân tích điều kiện, ngoại lệ và tài liệu'],
]
interface Props { options: ChatModelOption[]; value: string; onValueChange: (value: string) => void; busy?: boolean; compact?: boolean; depth?: AnswerDepth; onDepthChange?: (depth: AnswerDepth) => void }
export function ChatModelSelector({ options, value, onValueChange, busy = false, depth = 'balanced', onDepthChange }: Props) {
 const selected = options.find(option => option.option_id === value)
 return <DropdownMenu><DropdownMenuTrigger asChild>
  <Button variant="ghost" disabled={busy || !options.length} aria-label="Chọn mô hình cho lượt hỏi" data-testid="chat-model-selector" className="h-11 max-w-[19rem] gap-2 rounded-full px-3 text-sm font-normal">
    <span className="truncate">{selected?.display_name || (options.length ? 'Chọn mô hình' : 'Chưa được cấp mô hình')}</span><span className="text-muted-foreground">· {DEPTHS.find(item => item[0] === depth)?.[1]}</span><ChevronDown className="h-3.5 w-3.5 shrink-0" />
  </Button>
 </DropdownMenuTrigger><DropdownMenuContent align="end" className="max-h-[min(32rem,75dvh)] w-72 overflow-y-auto">
  <DropdownMenuLabel>Mô hình trả lời</DropdownMenuLabel>
  <DropdownMenuRadioGroup value={value} onValueChange={onValueChange}>
    {options.map(option => (
      <DropdownMenuRadioItem key={option.option_id} value={option.option_id} className="min-h-11 pr-3">
        <span className="min-w-0 flex-1 whitespace-normal break-words">{option.display_name}</span>
        {option.is_default && <span className="text-xs text-muted-foreground">Mặc định</span>}
      </DropdownMenuRadioItem>
    ))}
  </DropdownMenuRadioGroup>
  <DropdownMenuSeparator />
  <DropdownMenuLabel>Mức phân tích</DropdownMenuLabel>
  <DropdownMenuRadioGroup value={depth} onValueChange={(next) => onDepthChange?.(next as AnswerDepth)}>
    {DEPTHS.map(([key, label, hint]) => (
      <DropdownMenuRadioItem key={key} value={key} className="min-h-12 items-start py-2.5 pr-3">
        <span className="min-w-0 flex-1">
          <span className="block">{label}</span>
          <span className="block whitespace-normal text-xs leading-5 text-muted-foreground">{hint}</span>
        </span>
      </DropdownMenuRadioItem>
    ))}
  </DropdownMenuRadioGroup>
 </DropdownMenuContent></DropdownMenu>
}
