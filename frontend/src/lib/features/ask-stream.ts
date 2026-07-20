export type AskProgressStage =
  | 'preparing'
  | 'accepted'
  | 'retrieval'
  | 'generation'
  | 'validation'
  | 'finalizing'
  | 'complete'

const STAGE_LABELS: Record<AskProgressStage, string> = {
  preparing: 'Đang chuẩn bị gửi câu hỏi',
  accepted: 'Hệ thống đã tiếp nhận câu hỏi',
  retrieval: 'Đang tìm nguồn pháp luật phù hợp',
  generation: 'Đang soạn câu trả lời',
  validation: 'Đang kiểm tra căn cứ pháp lý',
  finalizing: 'Đang hoàn thiện câu trả lời',
  complete: 'Đã hoàn tất câu trả lời',
}

export function isAskSseEnabled(
  value: string | undefined = process.env.NEXT_PUBLIC_ASK_SSE_ENABLED,
): boolean {
  return value?.trim().toLowerCase() === 'true'
}

export function askStageLabel(stage: string): string {
  const normalized = stage.trim().toLowerCase()
  if (['accepted', 'queued', 'queue'].includes(normalized)) return STAGE_LABELS.accepted
  if (['retrieval', 'retrieving', 'search', 'sources'].includes(normalized)) return STAGE_LABELS.retrieval
  if (['generation', 'generating', 'answer'].includes(normalized)) return STAGE_LABELS.generation
  if (['validation', 'validating', 'grounding', 'repair'].includes(normalized)) return STAGE_LABELS.validation
  if (['finalizing', 'persistence', 'persisting'].includes(normalized)) return STAGE_LABELS.finalizing
  if (['complete', 'completed', 'done'].includes(normalized)) return STAGE_LABELS.complete
  if (normalized === 'preparing') return STAGE_LABELS.preparing
  return 'Hệ thống đang xử lý câu hỏi'
}
