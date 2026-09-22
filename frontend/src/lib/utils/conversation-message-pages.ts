import type { AskMessage } from '@/lib/types/search'

export function normalizeConversationMessages(messages: AskMessage[]): AskMessage[] {
  return messages.reduce<AskMessage[]>((acc, message) => {
    const previous = acc[acc.length - 1]
    const previousUser = acc[acc.length - 2]
    const normalizeMessage = (value?: string | null) => (value || '').replace(/\s+/g, ' ').trim()
    const messageTime = Date.parse(message.created_at || '')
    const previousUserTime = Date.parse(previousUser?.created_at || '')
    const duplicateFailedTurn =
      message.role === 'user' &&
      previous?.role === 'assistant' &&
      previous.status === 'error' &&
      previousUser?.role === 'user' &&
      normalizeMessage(previousUser.content) === normalizeMessage(message.content) &&
      Number.isFinite(messageTime) &&
      Number.isFinite(previousUserTime) &&
      Math.abs(messageTime - previousUserTime) <= 10000
    if (duplicateFailedTurn) return acc
    if (message.role === 'assistant' && previous?.role === 'assistant') {
      acc[acc.length - 1] = message
    } else {
      acc.push(message)
    }
    return acc
  }, [])
}

export function mergeConversationMessagePage(current: AskMessage[], older: AskMessage[]): AskMessage[] {
  const byId = new Map<string, AskMessage>()
  for (const message of [...older, ...current]) byId.set(message.id, message)
  return normalizeConversationMessages(
    [...byId.values()].sort((left, right) => {
      const timeDelta = Date.parse(left.created_at || '') - Date.parse(right.created_at || '')
      return timeDelta || left.id.localeCompare(right.id)
    }),
  )
}
