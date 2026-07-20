import { describe, expect, it } from 'vitest'
import { createAskSseParser } from './ask-sse'


describe('Ask SSE parser', () => {
  it('parses accepted, status, sources, final, error and complete across chunks', () => {
    const parser = createAskSseParser()
    const events = [
      ...parser.push('data: {"type":"accepted","turn_id":"turn-1"}\r\n\r\ndata: {"type":"status","stage":"retrieval"}'),
      ...parser.push('\r\n\r\ndata: {"type":"sources","citations":[{"chunk_id":"12","law_number":"60/2014/QH13"}]}\n\n'),
      ...parser.push('data: {"type":"final","response":{"answer":"Bản đã kiểm tra","question":"fixture"}}\n\n'),
      ...parser.push('data: {"type":"error","message":"Lỗi an toàn"}\n\ndata: {"type":"complete"}\n\n'),
      ...parser.finish(),
    ]

    expect(events.map((event) => event.type)).toEqual([
      'accepted',
      'status',
      'sources',
      'final',
      'error',
      'complete',
    ])
    expect(events[2]).toMatchObject({
      type: 'sources',
      citations: [{ chunk_id: '12', law_number: '60/2014/QH13' }],
    })
    expect(events[3]).toMatchObject({
      type: 'final',
      response: { answer: 'Bản đã kiểm tra' },
    })
  })

  it('drops every draft-like event and never promotes legacy content to final', () => {
    const parser = createAskSseParser()
    const events = [
      ...parser.push([
      'data: {"type":"answer","content":"Bản nháp một"}',
      '',
      'data: {"type":"draft","content":"Bản nháp hai"}',
      '',
      'data: {"type":"final_answer","content":"Legacy chưa đủ contract"}',
      '',
      'data: {"type":"status","stage":"validation"}',
      '',
      ].join('\n')),
      ...parser.finish(),
    ]

    expect(events).toEqual([{ type: 'status', stage: 'validation' }])
    expect(JSON.stringify(events)).not.toContain('Bản nháp')
    expect(JSON.stringify(events)).not.toContain('Legacy')
  })

  it('supports multi-line SSE data and ignores heartbeats', () => {
    const parser = createAskSseParser()
    const events = parser.push(': keep-alive\n\ndata: {"type":"status",\ndata: "stage":"generation"}\n\n')

    expect(events).toEqual([{ type: 'status', stage: 'generation' }])
  })

  it('does not forward technical marker syntax from a final event', () => {
    const parser = createAskSseParser()
    const events = parser.push(
      'data: {"type":"final","response":{"answer":"Nội dung #ref-source-1 legal:1 chunk_id=x","question":"fixture"}}\n\n',
    )

    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ type: 'final' })
    expect(JSON.stringify(events[0])).not.toMatch(/#ref-source|legal:|chunk_id/i)
  })
})
