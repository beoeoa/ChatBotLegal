import type { Page } from '@playwright/test'

export type IsolatedRole = 'citizen' | 'officer' | 'admin'

export type IsolatedConversation = {
  id: string
  title: string
  role_context: IsolatedRole
  status: string
  owner_user_id: string
  created_at: string
  last_message_at: string
  expires_at: string
  message_count: number
  messages: Array<Record<string, unknown>>
  next_cursor: string | null
  has_older_messages: boolean
}

type InstallOptions = {
  conversation?: IsolatedConversation
  answer?: Record<string, unknown>
}

function answerFor(role: IsolatedRole, question = ''): Record<string, unknown> {
  const normalized = question.toLocaleLowerCase('vi-VN')
  const domain = normalized.includes('đất') || normalized.includes('xây dựng')
    ? 'dat_dai_xay_dung'
    : normalized.includes('trợ cấp') || normalized.includes('người có công')
      ? 'an_sinh_y_te_giao_duc'
      : normalized.includes('khiếu nại') || normalized.includes('xử phạt')
        ? 'hanh_chinh_cong'
        : 'ho_tich_chung_thuc'
  const citation = domain === 'dat_dai_xay_dung'
    ? { document_title: 'Luật Xây dựng', law_number: '50/2014/QH13', article_number: '1', effective_status: 'active' }
    : { document_title: 'Luật Hộ tịch', law_number: '60/2014/QH13', article_number: '13', effective_status: 'active' }
  const section = {
    issue_id: 'isolated-issue-1',
    title: 'Căn cứ và hướng xử lý',
    status: 'sufficiently_evidenced',
    facet: 'documents',
    claim_types: ['procedure'],
    answer: 'Hệ thống đã đối chiếu hồ sơ, thẩm quyền và căn cứ trong nguồn kiểm thử.',
    citations: [citation],
  }
  return {
    question,
    answer: role === 'officer'
      ? 'Kết quả kiểm thử: cần đối chiếu hồ sơ, thẩm quyền và căn cứ pháp lý trước khi xử lý.'
      : 'Kết quả kiểm thử: bạn cần chuẩn bị hồ sơ và liên hệ cơ quan có thẩm quyền.',
    answer_status: 'verified',
    grounding_status: 'grounded',
    canonical_domain: domain,
    citations: [citation],
    answer_sections: [section],
    rag_trace: {
      input_question: question,
      selected_domain: domain,
      detected_domain: { name: domain },
      evidence_coverage: { status: 'verified', coverage_ratio: 1 },
      form_provenance: { requested: false, status: 'not_requested' },
      retrieved_chunks: [{ chunk_id: 'isolated-chunk-1', score: 1, domain, law_number: citation.law_number, article_number: citation.article_number, article_title: 'Nguồn kiểm thử', document_title: citation.document_title, content_preview: 'Metadata-only isolated test evidence.' }],
    },
    presentation_version: null,
  }
}

function cloneConversation(value: IsolatedConversation): IsolatedConversation {
  return { ...value, messages: value.messages.map((message) => ({ ...message })) }
}

export async function installIsolatedApi(
  page: Page,
  role: IsolatedRole,
  options: InstallOptions = {},
) {
  const account = `isolated-${role}`
  const defaultConversation: IsolatedConversation = {
    id: `conversation-${role}`,
    title: 'Cuộc trò chuyện kiểm thử',
    role_context: role,
    status: 'active',
    owner_user_id: account,
    created_at: '2026-08-13T08:00:00Z',
    last_message_at: '2026-08-13T08:30:00Z',
    expires_at: '2027-08-13T08:00:00Z',
    message_count: 0,
    messages: [],
    next_cursor: null,
    has_older_messages: false,
  }
  let conversation = cloneConversation(options.conversation || defaultConversation)
  let answer = options.answer || answerFor(role)

  await page.addInitScript(({ sessionRole, sessionAccount }) => {
    localStorage.setItem('auth-storage', JSON.stringify({
      state: {
        token: `isolated-token-${sessionRole}`,
        userId: sessionAccount,
        role: sessionRole,
        isAuthenticated: true,
        hasHydrated: true,
      },
      version: 0,
    }))
  }, { sessionRole: role, sessionAccount: account })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })

    if (path === '/api/auth/status') return json({ auth_enabled: true })
    if (path === '/api/users/me') return json({ id: account, role, email: `${account}@example.invalid` })
    if (path === '/api/config') return json({ systemName: 'Pháp luật Hải Phòng', organizationName: 'Môi trường kiểm thử cô lập' })
    if (path === '/api/models/defaults') return json({ default_chat_model: 'isolated-model' })
    if (path === '/api/chat/model-options') return json([])
    if (path === '/api/models') return json([{ id: 'isolated-model', name: 'Isolated model', provider: 'openrouter', type: 'language' }])
    if (path === '/api/legal/domains') return json({ domains: [] })

    if ((path === '/api/conversations' || path === '/api/conversations/') && method === 'GET') {
      return json(conversation.message_count > 0 || conversation.messages.length > 0 ? [conversation] : [])
    }
    if ((path === '/api/conversations' || path === '/api/conversations/') && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown> | null
      conversation = {
        ...defaultConversation,
        id: `conversation-${role}-new`,
        title: typeof body?.title === 'string' ? body.title : 'Cuộc trò chuyện mới',
        message_count: 0,
        messages: [],
      }
      return json(conversation, 201)
    }
    if (path === '/api/conversations/latest' && method === 'GET') return json(conversation.message_count ? conversation : null)
    if (path === `/api/conversations/${conversation.id}` && method === 'GET') return json(conversation)
    if (path === `/api/conversations/${conversation.id}/messages` && method === 'GET') {
      return json({ messages: conversation.messages, next_cursor: conversation.next_cursor, has_more: conversation.has_older_messages })
    }
    if (path.startsWith('/api/conversations/') && path.endsWith('/messages') && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown> | null
      if (body) {
        conversation.messages.push({ id: `message-${conversation.messages.length + 1}`, created_at: new Date().toISOString(), ...body })
        conversation.message_count = conversation.messages.length
      }
      return json({ ok: true })
    }
    if (path.startsWith('/api/conversations/') && method === 'PATCH') return json({ ...conversation, ...(request.postDataJSON() || {}) })
    if (path.startsWith('/api/search/ask/')) {
      const body = request.postDataJSON() as Record<string, unknown> | null
      answer = { ...answerFor(role, String(body?.question || '')), ...options.answer }
      // The browser persists the user turn before Ask. The live Ask route
      // persists the completed assistant snapshot on the server, so the
      // isolated API must model that same boundary for reload assertions.
      const assistantId = `message-assistant-${String(body?.turn_id || conversation.messages.length + 1)}`
      const assistantMessage = {
        id: assistantId,
        role: 'assistant',
        content: String(answer.answer || ''),
        status: 'complete',
        created_at: new Date().toISOString(),
        ...answer,
        turn_id: body?.turn_id || null,
      }
      conversation.messages = conversation.messages.filter((message) => message.id !== assistantId)
      conversation.messages.push(assistantMessage)
      conversation.message_count = conversation.messages.length
      conversation.last_message_at = assistantMessage.created_at
      if (path.endsWith('/simple')) return json(answer)
      const sse = [
        `data: ${JSON.stringify({ type: 'accepted' })}`,
        '',
        `data: ${JSON.stringify({ type: 'status', stage: 'retrieval' })}`,
        '',
        `data: ${JSON.stringify({ type: 'sources', citations: answer.citations })}`,
        '',
        `data: ${JSON.stringify({ type: 'final', response: answer })}`,
        '',
        `data: ${JSON.stringify({ type: 'complete' })}`,
        '',
      ].join('\n')
      return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse })
    }
    return json([])
  })
}
