import type { AskMessage } from '@/lib/types/search'

/**
 * Appends one user/pending-assistant pair without replacing completed history.
 * Kept pure so the Ask UI contract is easy to test independently of network state.
 */
export function appendPendingAskTurn(
  history: AskMessage[],
  userMessage: AskMessage,
  pendingAssistantMessage: AskMessage,
): AskMessage[] {
  return [...history, userMessage, pendingAssistantMessage]
}

/** Replaces only the pending placeholder for the submitted turn. */
export function completeAskTurn(
  history: AskMessage[],
  pendingAssistantId: string,
  completedAssistantMessage: AskMessage,
): AskMessage[] {
  return history.map((message) =>
    message.id === pendingAssistantId
      ? { ...completedAssistantMessage, id: message.id, created_at: message.created_at }
      : message,
  )
}

/** Replaces a pending placeholder with a recoverable error in the same turn. */
export function failAskTurn(
  history: AskMessage[],
  pendingAssistantId: string,
  errorContent: string,
): AskMessage[] {
  return history.map((message) =>
    message.id === pendingAssistantId
      ? { ...message, status: 'error', content: errorContent }
      : message,
  )
}

/** Keeps a cancelled turn visible without classifying it as a request error. */
export function cancelAskTurn(
  history: AskMessage[],
  pendingAssistantId: string,
  message = 'Đã dừng tạo câu trả lời.',
): AskMessage[] {
  return history.map((item) =>
    item.id === pendingAssistantId
      ? { ...item, status: 'cancelled', content: message }
      : item,
  )
}
