import { describe, expect, it } from 'vitest'
import { buildConversationPromptContext } from '@/lib/utils/conversation-context'

describe('buildConversationPromptContext', () => {
  it('keeps recent conclusions and citations under a char budget', () => {
    const messages = Array.from({ length: 10 }).flatMap((_, i) => ([
      { id: `u${i}`, role: 'user' as const, content: `Câu hỏi ${i} ` + 'a'.repeat(200), created_at: '2026-07-11T00:00:00.000Z' },
      {
        id: `a${i}`,
        role: 'assistant' as const,
        content: `Kết luận ${i}: ` + 'b'.repeat(400),
        created_at: '2026-07-11T00:00:00.000Z',
        citations: [{ chunk_id: `c${i}`, law_number: `${i}/2014/QH13`, article_number: `${i}` }],
      },
    ]))
    const context = buildConversationPromptContext(messages, { maxChars: 1800, maxMessages: 6 })
    expect(context.length).toBeGreaterThan(0)
    expect(context.length).toBeLessThanOrEqual(6)
    const text = context.map((item) => item.content).join('\n')
    expect(text.includes('Kết luận 9') || text.includes('9/2014/QH13')).toBe(true)
    expect(text.length).toBeLessThanOrEqual(1900)
  })
})
