import type { AskResponse } from '@/lib/types/search'


type LegalCitation = NonNullable<AskResponse['citations']>[number]

export type AskSseEvent =
  | { type: 'accepted'; turnId?: string; traceId?: string }
  | { type: 'status'; stage: string }
  | { type: 'sources'; citations: LegalCitation[] }
  | { type: 'final'; response: AskResponse }
  | { type: 'error'; message: string; code?: string }
  | { type: 'complete' }

export interface AskSseHandlers {
  onEvent: (event: AskSseEvent) => void
}

export class AskStreamTransportError extends Error {
  readonly streamOpened: boolean

  constructor(message: string, streamOpened: boolean) {
    super(message)
    this.name = 'AskStreamTransportError'
    this.streamOpened = streamOpened
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function stripInternalMarkerSyntax(value: string): string {
  return value
    .replace(/#ref-source-[\w-]+/gi, '')
    .replace(/\blegal\s*:\s*\d+\b/gi, '')
    .replace(/\b(?:chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id)\s*[:=]\s*[\w.-]+/gi, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

function sanitizeFinalCandidate(candidate: Record<string, unknown>): AskResponse {
  const sections = Array.isArray(candidate.answer_sections)
    ? candidate.answer_sections.filter(isRecord).map((section) => ({
      ...section,
      answer: typeof section.answer === 'string' ? stripInternalMarkerSyntax(section.answer) : section.answer,
      guidance: typeof section.guidance === 'string' ? stripInternalMarkerSyntax(section.guidance) : section.guidance,
      limitation: typeof section.limitation === 'string' ? stripInternalMarkerSyntax(section.limitation) : section.limitation,
      clarifying_question: typeof section.clarifying_question === 'string'
        ? stripInternalMarkerSyntax(section.clarifying_question)
        : section.clarifying_question,
    }))
    : candidate.answer_sections
  return {
    ...candidate,
    answer: stripInternalMarkerSyntax(String(candidate.answer || '')),
    question: typeof candidate.question === 'string' ? candidate.question : '',
    answer_sections: sections,
  } as AskResponse
}

function normalizeEvent(value: unknown): AskSseEvent | null {
  if (!isRecord(value) || typeof value.type !== 'string') return null

  switch (value.type) {
    case 'accepted':
      return {
        type: 'accepted',
        turnId: typeof value.turn_id === 'string' ? value.turn_id : undefined,
        traceId: typeof value.trace_id === 'string' ? value.trace_id : undefined,
      }
    case 'status':
      return typeof value.stage === 'string'
        ? { type: 'status', stage: value.stage }
        : null
    case 'sources': {
      const rawCitations = Array.isArray(value.citations)
        ? value.citations
        : Array.isArray(value.sources)
          ? value.sources
          : []
      return {
        type: 'sources',
        citations: rawCitations.filter(isRecord) as LegalCitation[],
      }
    }
    case 'final': {
      const nested = isRecord(value.response)
        ? value.response
        : isRecord(value.payload)
          ? value.payload
          : null
      const candidate = nested || Object.fromEntries(
        Object.entries(value).filter(([key]) => key !== 'type'),
      )
      if (typeof candidate.answer !== 'string' || !candidate.answer.trim()) return null
      return {
        type: 'final',
        response: sanitizeFinalCandidate(candidate),
      }
    }
    case 'error':
      return {
        type: 'error',
        message: typeof value.message === 'string' && value.message.trim()
          ? value.message
          : 'Luồng trả lời gặp lỗi.',
        code: typeof value.code === 'string' ? value.code : undefined,
      }
    case 'complete':
      return { type: 'complete' }
    // Never expose legacy model drafts. Only the structured `final` event may
    // carry a user-visible legal answer.
    case 'answer':
    case 'draft':
    case 'strategy':
    case 'final_answer':
    default:
      return null
  }
}

export interface AskSseParser {
  push: (chunk: string) => AskSseEvent[]
  finish: () => AskSseEvent[]
}

export function createAskSseParser(): AskSseParser {
  let lineBuffer = ''
  let dataLines: string[] = []

  const dispatch = (): AskSseEvent[] => {
    if (dataLines.length === 0) return []
    const data = dataLines.join('\n')
    dataLines = []
    try {
      const event = normalizeEvent(JSON.parse(data))
      return event ? [event] : []
    } catch {
      return []
    }
  }

  const consumeLine = (rawLine: string): AskSseEvent[] => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (!line) return dispatch()
    if (line.startsWith(':')) return []
    if (line === 'data') {
      dataLines.push('')
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
    return []
  }

  return {
    push(chunk: string) {
      lineBuffer += chunk
      const events: AskSseEvent[] = []
      let newline = lineBuffer.indexOf('\n')
      while (newline >= 0) {
        events.push(...consumeLine(lineBuffer.slice(0, newline)))
        lineBuffer = lineBuffer.slice(newline + 1)
        newline = lineBuffer.indexOf('\n')
      }
      return events
    },
    finish() {
      const events: AskSseEvent[] = []
      if (lineBuffer) {
        events.push(...consumeLine(lineBuffer))
        lineBuffer = ''
      }
      events.push(...dispatch())
      return events
    },
  }
}

export async function consumeAskSseStream(
  stream: ReadableStream<Uint8Array>,
  handlers: AskSseHandlers,
  signal?: AbortSignal,
): Promise<AskResponse> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  const parser = createAskSseParser()
  let finalResponse: AskResponse | null = null

  const accept = (events: AskSseEvent[]) => {
    for (const event of events) {
      handlers.onEvent(event)
      if (event.type === 'final') finalResponse = event.response
      if (event.type === 'error') {
        throw new AskStreamTransportError(event.message, true)
      }
      if (event.type === 'complete' && !finalResponse) {
        throw new AskStreamTransportError('Luồng kết thúc trước khi có câu trả lời cuối.', true)
      }
    }
  }

  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      const { value, done } = await reader.read()
      if (done) break
      accept(parser.push(decoder.decode(value, { stream: true })))
    }
    accept(parser.push(decoder.decode()))
    accept(parser.finish())
  } finally {
    reader.releaseLock()
  }

  if (!finalResponse) {
    throw new AskStreamTransportError('Luồng không trả về kết quả cuối đã kiểm tra.', true)
  }
  return finalResponse
}
