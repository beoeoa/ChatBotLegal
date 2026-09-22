import { describe, expect, it } from 'vitest'
import { createAskSseParser } from './ask-sse'


describe('Ask SSE parser', () => {
  it('parses standard named SSE events when data does not repeat type', () => {
    const parser = createAskSseParser()
    const events = [
      ...parser.push([
        'event: accepted',
        'data: {"trace_id":"trace-1"}',
        '',
        'event: final',
        'data: {"response":{"answer":"Káº¿t quáº£ Ä‘Ã£ kiá»ƒm tra","question":"fixture"}}',
        '',
        'event: complete',
        'data: {"outcome":"success"}',
        '',
      ].join('\n')),
      ...parser.finish(),
    ]

    expect(events.map((event) => event.type)).toEqual([
      'accepted',
      'final',
      'completed',
    ])
    expect(events[1]).toMatchObject({
      type: 'final',
      response: { answer: 'Káº¿t quáº£ Ä‘Ã£ kiá»ƒm tra' },
    })
  })

  it('parses accepted, status, sources, final, error and complete across chunks', () => {
    const parser = createAskSseParser()
    const events = [
      ...parser.push('data: {"type":"accepted","turn_id":"turn-1"}\r\n\r\ndata: {"type":"status","stage":"retrieval"}'),
      ...parser.push('\r\n\r\ndata: {"type":"sources","citations":[{"chunk_id":"12","document_id":"doc-1","trace_id":"trace-1","law_number":"60/2014/QH13"}]}\n\n'),
      ...parser.push('data: {"type":"final","response":{"answer":"Bản đã kiểm tra","question":"fixture"}}\n\n'),
      ...parser.push('data: {"type":"error","message":"Lỗi an toàn"}\n\ndata: {"type":"complete"}\n\n'),
      ...parser.finish(),
    ]

    expect(events.map((event) => event.type)).toEqual([
      'accepted',
      'status',
      'sources',
      'final',
      'failed',
      'completed',
    ])
    expect(events[2]).toMatchObject({
      type: 'sources',
      citations: [{ law_number: '60/2014/QH13' }],
    })
    expect(JSON.stringify(events[2])).not.toMatch(/chunk_id|document_id|trace_id/i)
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

  it('allow-lists public validity metadata and strips Admin fields', () => {
    const parser = createAskSseParser()
    const events = parser.push(
      'data: {"type":"sources","citations":[{"law_number":"31/2024/QH15","validity_sync":{"status":"active","serving_action":"allow","verified_at":"2026-08-08T01:00:00Z","source_url":"https://vbpl.vn/example","actor_user_id":"admin-secret","event_id":"legal_validity_event:secret"}}]}\n\n',
    )

    expect(events[0]).toMatchObject({
      type: 'sources',
      citations: [{
        law_number: '31/2024/QH15',
        validity_sync: {
          status: 'active',
          serving_action: 'allow',
          verified_at: '2026-08-08T01:00:00Z',
        },
      }],
    })
    expect(JSON.stringify(events[0])).not.toMatch(/actor_user_id|event_id|admin-secret/)
  })

  it('exposes only the public authority label and strips ranking internals', () => {
    const parser = createAskSseParser()
    const events = parser.push(
      'data: {"type":"sources","citations":[{"law_number":"31/2024/QH15","authority_level":"national_assembly","authority_label":"Luật của Quốc hội","authority_rank":130,"authority_reason":"issuer metadata","hierarchy_rule":"authority_rank","score":0.99}]}\n\n',
    )

    expect(events[0]).toMatchObject({
      type: 'sources',
      citations: [{
        law_number: '31/2024/QH15',
        authority_level: 'national_assembly',
        authority_label: 'Luật của Quốc hội',
      }],
    })
    expect(JSON.stringify(events[0])).not.toMatch(/authority_rank|authority_reason|hierarchy_rule|"score"/)
  })

  it('sanitizes authority internals from citations in the final event', () => {
    const parser = createAskSseParser()
    const events = parser.push(
      'data: {"type":"final","response":{"answer":"Nội dung đã kiểm tra","question":"fixture","citations":[{"law_number":"31/2024/QH15","authority_level":"national_assembly","authority_label":"Luật của Quốc hội","authority_rank":130,"score":0.99}],"answer_sections":[{"answer":"Chi tiết","citations":[{"law_number":"31/2024/QH15","authority_level":"national_assembly","authority_label":"Luật của Quốc hội","hierarchy_rule":"authority_rank","score":0.99}]}]}}\n\n',
    )

    expect(events[0]).toMatchObject({
      type: 'final',
      response: {
        citations: [{
          law_number: '31/2024/QH15',
          authority_level: 'national_assembly',
          authority_label: 'Luật của Quốc hội',
        }],
        answer_sections: [{
          citations: [{
            law_number: '31/2024/QH15',
            authority_level: 'national_assembly',
            authority_label: 'Luật của Quốc hội',
          }],
        }],
      },
    })
    expect(JSON.stringify(events[0])).not.toMatch(/authority_rank|hierarchy_rule|"score"/)
  })
})
