import { expect, test, type Page, type Response } from '@playwright/test'
import cases from '../../tests/fixtures/remediation-browser-40.json'
import { installIsolatedApi } from './isolated-api'

type BrowserCase = {
  case_id: string
  role: 'citizen' | 'officer'
  account: string
  domain: string
  question: string
}

type PersistedProjection = {
  userCount: number
  assistantCount: number
  answer: string
  answerStatus: string | null
  canonicalDomain: string | null
  citationCount: number
  formCount: number
  answerSectionCount: number
  presentationSectionCount: number
  caveatCount: number
}

type RenderProjection = {
  text: string
  answerCardCount: number
  statusCount: number
  citationSurfaceCount: number
  formSurfaceCount: number
  sectionCount: number
  caveatCount: number
}

const browserCases = (cases as { schema_version: string; cases: BrowserCase[] }).cases

async function readAskResponse(response: Response) {
  const url = new URL(response.url())
  if (url.pathname.endsWith('/ask/simple')) {
    return response.json() as Promise<Record<string, unknown>>
  }

  // The progress endpoint is SSE.  Only the final event is authoritative; no
  // raw question/answer is written to the gate artifact or test title.
  const text = (await response.body()).toString('utf8')
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith('data:')) continue
    try {
      const event = JSON.parse(line.slice(5).trim()) as Record<string, unknown>
      if (event.type === 'final' && event.response && typeof event.response === 'object') {
        return event.response as Record<string, unknown>
      }
    } catch {
      // Keep scanning bounded SSE frames; the final event is checked below.
    }
  }
  throw new Error('browser40_final_event_missing')
}

function normalizedText(value: unknown) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function arrayLength(value: unknown) {
  return Array.isArray(value) ? value.length : 0
}

function presentationSectionCount(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 0
  return Object.values(value as Record<string, unknown>).reduce((total, section) => {
    if (Array.isArray(section)) return total + section.length
    return total + (normalizedText(section) ? 1 : 0)
  }, 0)
}

function persistedProjection(payload: Record<string, unknown>): PersistedProjection {
  const messages = Array.isArray(payload.messages)
    ? payload.messages.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []
  const users = messages.filter(message => message.role === 'user')
  const assistants = messages.filter(message => message.role === 'assistant' && message.status === 'complete')
  const assistant = assistants[assistants.length - 1] || {}
  const sections = assistant.sections && typeof assistant.sections === 'object' && !Array.isArray(assistant.sections)
    ? assistant.sections as Record<string, unknown>
    : {}

  return {
    userCount: users.length,
    assistantCount: assistants.length,
    answer: normalizedText(assistant.content),
    answerStatus: typeof assistant.answer_status === 'string' ? assistant.answer_status : null,
    canonicalDomain: typeof assistant.canonical_domain === 'string' ? assistant.canonical_domain : null,
    citationCount: arrayLength(assistant.citations),
    formCount: arrayLength(assistant.recommended_forms),
    answerSectionCount: arrayLength(assistant.answer_sections),
    presentationSectionCount: presentationSectionCount(sections),
    caveatCount: arrayLength(sections.caveats) + arrayLength(sections.clarifying_questions),
  }
}

function responseProjection(payload: Record<string, unknown>): Omit<PersistedProjection, 'userCount' | 'assistantCount'> {
  const sections = payload.sections && typeof payload.sections === 'object' && !Array.isArray(payload.sections)
    ? payload.sections as Record<string, unknown>
    : {}
  return {
    answer: normalizedText(payload.answer),
    answerStatus: typeof payload.answer_status === 'string' ? payload.answer_status : null,
    canonicalDomain: typeof payload.canonical_domain === 'string' ? payload.canonical_domain : null,
    citationCount: arrayLength(payload.citations),
    formCount: arrayLength(payload.recommended_forms),
    answerSectionCount: arrayLength(payload.answer_sections),
    presentationSectionCount: presentationSectionCount(sections),
    caveatCount: arrayLength(sections.caveats) + arrayLength(sections.clarifying_questions),
  }
}

async function renderProjection(page: Page): Promise<RenderProjection> {
  const assistants = page.getByTestId('ask-assistant-message')
  await expect(assistants).toHaveCount(1)
  const assistant = assistants.first()
  // StreamingResponse is dynamically imported. Wait for its stable legal
  // presentation before taking the projection, otherwise a reload can race
  // the fallback loading placeholder and produce a false persistence failure.
  await expect(assistant.locator(
    '[data-testid="legal-answer-card"], [data-testid="legal-answer-short"], [data-testid="answer-section"]',
  ).first()).toBeVisible({ timeout: 20_000 })
  return {
    text: normalizedText(await assistant.innerText()),
    answerCardCount: await assistant.getByTestId('legal-answer-card').count(),
    statusCount: await assistant.locator(
      '[data-testid="verified-label"], [data-testid="answer-status-banner"], [data-testid="grounding-status-badge"]',
    ).count(),
    citationSurfaceCount: await assistant.locator(
      '[data-testid="legal-answer-bases"], [data-testid="answer-section-citations"], [data-testid="citation-validity-notice"]',
    ).count(),
    formSurfaceCount: await assistant.locator(
      '[data-testid="legal-answer-forms"], [data-testid="official-forms"], [data-testid="forms-unavailable"]',
    ).count(),
    sectionCount: await assistant.locator(
      '[data-testid="legal-answer-short"], [data-testid="answer-section"]',
    ).count(),
    caveatCount: await assistant.getByTestId('legal-answer-caveats').count(),
  }
}

for (const item of browserCases) {
  test(`remediation browser gate ${item.case_id}`, async ({ page }) => {
    test.setTimeout(120_000)
    await installIsolatedApi(page, item.role, { answer: { canonical_domain: item.domain } })
    let askRequestCount = 0
    let persistedMessageCount = 0
    let finalResponse: Promise<Record<string, unknown>> | null = null
    page.on('response', response => {
      const pathname = new URL(response.url()).pathname
      if (pathname.endsWith('/messages') && response.request().method() === 'POST' && response.ok()) {
        persistedMessageCount += 1
      }
      if (!pathname.endsWith('/ask/simple') && !pathname.endsWith('/ask/progress')) return
      askRequestCount += 1
      if (!finalResponse) {
        finalResponse = readAskResponse(response)
      }
    })

    await page.goto('/search?new=1')
    const input = page.locator('#ask-question')
    await expect(input).toBeVisible()
    await input.fill(item.question)
    await input.press('Control+Enter')
    await expect(page.getByTestId('ask-assistant-message').last()).toBeVisible({ timeout: 100_000 })

    expect(askRequestCount).toBe(1)
    expect(finalResponse).not.toBeNull()
    const payload = await finalResponse!
    expect(payload.canonical_domain).toBe(item.domain)
    expect(['verified', 'partial', 'cannot_verify']).toContain(payload.answer_status)

    const beforeReload = await renderProjection(page)
    expect(beforeReload.text).not.toBe('')

    // The browser persists the user turn once. The Ask endpoint persists the
    // assistant snapshot server-side, so it does not create a second browser
    // POST /messages; reload verifies the durable conversation projection.
    await expect.poll(() => persistedMessageCount, { timeout: 20_000 }).toBe(1)
    // Creating the first session also removes ?new=1. Reload only after that
    // navigation commits, otherwise the intentional new-chat flag clears it.
    await expect(page).toHaveURL(/\/search$/, { timeout: 20_000 })

    await page.reload()
    await expect(page.getByTestId('ask-assistant-message')).toHaveCount(1, { timeout: 20_000 })
    await expect(page.getByTestId('ask-user-message')).toHaveCount(1)
    const afterReload = await renderProjection(page)
    expect(afterReload).toEqual(beforeReload)
  })
}
