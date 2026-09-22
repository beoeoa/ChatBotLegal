import { describe, expect, it } from 'vitest'

import type { AskMessage } from '@/lib/types/search'
import { mergeConversationMessagePage } from './conversation-message-pages'

const message = (id: string, createdAt: string): AskMessage => ({
  id,
  role: Number(id.replace(/\D/g, '')) % 2 ? 'user' : 'assistant',
  content: id,
  status: 'complete',
  created_at: createdAt,
})

describe('mergeConversationMessagePage', () => {
  it('prepends older messages chronologically and removes cursor overlap', () => {
    const current = [
      message('m3', '2026-08-25T00:00:03Z'),
      message('m4', '2026-08-25T00:00:04Z'),
    ]
    const older = [
      message('m1', '2026-08-25T00:00:01Z'),
      message('m2', '2026-08-25T00:00:02Z'),
      message('m3', '2026-08-25T00:00:03Z'),
    ]

    expect(mergeConversationMessagePage(current, older).map((item) => item.id)).toEqual([
      'm1', 'm2', 'm3', 'm4',
    ])
  })

  it('reconstructs all 2,000 messages from cursor pages without loss or duplicates', () => {
    const all = Array.from({ length: 2000 }, (_, index) => {
      const number = index + 1
      return message(
        `m${number.toString().padStart(4, '0')}`,
        new Date(Date.UTC(2026, 0, 1, 0, 0, number)).toISOString(),
      )
    })
    let hydrated = all.slice(-30)
    for (let end = all.length - 30; end > 0; end -= 30) {
      const start = Math.max(0, end - 30)
      const overlap = hydrated[0]
      hydrated = mergeConversationMessagePage(
        hydrated,
        [...all.slice(start, end), overlap],
      )
    }

    expect(hydrated).toHaveLength(2000)
    expect(new Set(hydrated.map((item) => item.id)).size).toBe(2000)
    expect(hydrated[0].id).toBe('m0001')
    expect(hydrated.at(-1)?.id).toBe('m2000')
  })
})
