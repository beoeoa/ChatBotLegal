import type { AskResponse } from '@/lib/types/search'


type LegalCitation = NonNullable<AskResponse['citations']>[number]

const PUBLIC_CITATION_FIELDS = new Set([
  'evidence_ids',
  'law_number',
  'document_title',
  'article_number',
  'article_title',
  'clause_number',
  'point_number',
  'effective_status',
  'effective_date',
  'issuing_agency',
  'scope',
  'source_url',
  'internal_url',
  'viewer_url',
  'url',
  'title',
  'label',
  'authority_level',
  'authority_label',
  'validity_sync',
])

const PUBLIC_VALIDITY_FIELDS = new Set([
  'status',
  'serving_action',
  'verified_at',
  'source_url',
  'effective_from',
  'effective_to',
  'warning_code',
])

export type AskSseEvent =
  | { type: 'accepted'; turnId?: string; traceId?: string }
  | { type: 'status'; stage: string }
  | { type: 'sources'; citations: LegalCitation[]; provisional?: boolean; label?: string }
  | { type: 'text_delta'; text: string; provisional: true }
  | { type: 'final'; response: AskResponse }
  | { type: 'failed'; message: string; code?: string }
  | { type: 'completed' }

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

function sanitizePublicCitation(candidate: Record<string, unknown>): LegalCitation {
  const sanitized = Object.fromEntries(
    Object.entries(candidate).filter(([key, value]) => (
      PUBLIC_CITATION_FIELDS.has(key) && value !== null && value !== undefined
    )),
  )
  if (isRecord(candidate.validity_sync)) {
    sanitized.validity_sync = Object.fromEntries(
      Object.entries(candidate.validity_sync).filter(([key]) => PUBLIC_VALIDITY_FIELDS.has(key)),
    )
  }
  return sanitized as unknown as LegalCitation
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
      citations: Array.isArray(section.citations)
        ? section.citations.filter(isRecord).map(sanitizePublicCitation)
        : section.citations,
    }))
    : candidate.answer_sections
  const citations = Array.isArray(candidate.citations)
    ? candidate.citations.filter(isRecord).map(sanitizePublicCitation)
    : candidate.citations
  return {
    ...candidate,
    answer: stripInternalMarkerSyntax(String(candidate.answer || '')),
    question: typeof candidate.question === 'string' ? candidate.question : '',
    answer_sections: sections,
    citations,
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
      const sourceEvent: Extract<AskSseEvent, { type: 'sources' }> = {
        type: 'sources',
        citations: rawCitations.filter(isRecord).map(sanitizePublicCitation),
      }
      if (typeof value.provisional === 'boolean') {
        sourceEvent.provisional = value.provisional
      }
      if (typeof value.label === 'string' && value.label.trim()) {
        sourceEvent.label = value.label.trim().slice(0, 120)
      }
      return sourceEvent
    }
    case 'answer_delta':
    case 'text_delta':
      return typeof value.text === 'string' && value.provisional === true
        ? { type: 'text_delta', text: value.text, provisional: true } : null
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
    case 'failed':
      return {
        type: 'failed',
        message: typeof value.message === 'string' && value.message.trim()
          ? value.message
          : 'Luồng trả lời gặp lỗi.',
        code: typeof value.code === 'string' ? value.code : undefined,
      }
    case 'complete':
    case 'completed':
      return { type: 'completed' }
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
  let eventName = ''

  const dispatch = (): AskSseEvent[] => {
    if (dataLines.length === 0) {
      eventName = ''
      return []
    }
    const data = dataLines.join('\n')
    dataLines = []
    try {
      const parsed = JSON.parse(data)
      const candidate = (
        eventName
        && isRecord(parsed)
        && typeof parsed.type !== 'string'
      )
        ? { type: eventName, ...parsed }
        : parsed
      const event = normalizeEvent(candidate)
      return event ? [event] : []
    } catch {
      return []
    } finally {
      eventName = ''
    }
  }

  const consumeLine = (rawLine: string): AskSseEvent[] => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (!line) return dispatch()
    if (line.startsWith(':')) return []
    if (line.startsWith('event:')) {
      eventName = line.slice(6).replace(/^ /, '').trim()
    } else if (line === 'data') {
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
      if (event.type === 'failed') {
        throw new AskStreamTransportError(event.message, true)
      }
      if (event.type === 'completed' && !finalResponse) {
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
