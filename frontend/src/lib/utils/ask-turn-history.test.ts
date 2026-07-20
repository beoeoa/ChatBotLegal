import { describe, expect, it } from 'vitest'
import type { AskMessage } from '@/lib/types/search'
import { appendPendingAskTurn, cancelAskTurn, completeAskTurn, failAskTurn } from './ask-turn-history'

const at = '2026-07-11T12:00:00.000Z'

function user(id: string, content: string): AskMessage {
  return { id, role: 'user', content, created_at: at }
}

function pending(id: string): AskMessage {
  return { id, role: 'assistant', content: '', status: 'pending', created_at: at }
}

function complete(id: string, content: string): AskMessage {
  return {
    id,
    role: 'assistant',
    content,
    status: 'complete',
    citations: [{ chunk_id: 'legal:1', law_number: '123/2015/NĐ-CP', article_number: '12' }],
    recommended_forms: [{ name: 'Tờ khai', file_type: 'pdf', official_level: 'official', review_status: 'approved' }],
    created_at: at,
  }
}

describe('Ask turn history', () => {
  it('keeps exactly one completed assistant message per consecutive question', () => {
    const firstPending = pending('assistant-pending-1')
    const firstComplete = complete('assistant-1', 'Câu trả lời đẹp thứ nhất')
    let history = appendPendingAskTurn([], user('user-1', 'Câu hỏi thứ nhất'), firstPending)
    history = completeAskTurn(history, firstPending.id, firstComplete)

    const secondPending = pending('assistant-pending-2')
    const secondComplete = complete('assistant-2', 'Câu trả lời đẹp thứ hai')
    history = appendPendingAskTurn(history, user('user-2', 'Câu hỏi thứ hai'), secondPending)
    history = completeAskTurn(history, secondPending.id, secondComplete)

    const answers = history.filter((message) => message.role === 'assistant' && message.status === 'complete')
    expect(answers).toHaveLength(2)
    expect(answers.map((message) => message.content)).toEqual([
      'Câu trả lời đẹp thứ nhất',
      'Câu trả lời đẹp thứ hai',
    ])
    expect(history.some((message) => message.status === 'pending')).toBe(false)
    expect(answers[0].citations?.[0].law_number).toBe('123/2015/NĐ-CP')
    expect(answers[0].recommended_forms?.[0].name).toBe('Tờ khai')
  })

  it('keeps a failed assistant turn visible after the request fails', () => {
    const assistantPending = pending('assistant-pending-error')
    let history = appendPendingAskTurn(
      [],
      user('user-error', 'Câu hỏi bị timeout'),
      assistantPending,
    )
    history = failAskTurn(history, assistantPending.id, 'Yêu cầu quá thời gian 60 giây.')

    expect(history).toHaveLength(2)
    expect(history[0].role).toBe('user')
    expect(history[1]).toMatchObject({
      role: 'assistant',
      status: 'error',
      content: 'Yêu cầu quá thời gian 60 giây.',
    })
  })

  it('marks an aborted turn as cancelled rather than error', () => {
    const assistantPending = pending('assistant-pending-cancel')
    let history = appendPendingAskTurn([], user('user-cancel', 'Câu hỏi'), assistantPending)

    history = cancelAskTurn(history, assistantPending.id)

    expect(history[1]).toMatchObject({
      status: 'cancelled',
      content: 'Đã dừng tạo câu trả lời.',
    })
  })
})
