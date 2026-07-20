import type { AskMessage } from '@/lib/types/search'

export interface ConversationContextOptions {
  maxChars?: number
  maxMessages?: number
}

export function buildConversationPromptContext(
  messages: AskMessage[],
  options: ConversationContextOptions = {},
) {
  const maxChars = options.maxChars ?? 3500
  const maxMessages = options.maxMessages ?? 12
  const selected: Array<{ role: string; content: string }> = []
  let used = 0
  for (const message of [...messages].reverse()) {
    if (!message.content?.trim()) continue
    let content = message.content.trim()
    if (message.role === 'assistant') {
      content = content.slice(0, 1200)
      const cites = (message.citations || []).slice(0, 3).map((cite) => {
        const law = cite.law_number || ''
        const art = cite.article_number || ''
        return art ? `${law}, Điều ${art}` : law
      }).filter(Boolean)
      if (cites.length) content = `${content}\nCăn cứ đã dùng: ${cites.join('; ')}`
    } else {
      content = content.slice(0, 500)
    }
    if (selected.length && used + content.length > maxChars) break
    selected.push({ role: message.role, content })
    used += content.length
    if (selected.length >= maxMessages) break
  }
  return selected.reverse()
}
