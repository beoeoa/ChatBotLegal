import type { AskMessage } from '@/lib/types/search'

/**
 * Citizens should see an escalation affordance whenever the last answer did
 * not provide a verifiable answer.  ``source_view_only`` is a deliberate
 * fail-closed response mode and must not be treated as a successful answer.
 */
export function shouldOfferOfficerSupport(message: Pick<AskMessage, 'grounding_status' | 'answer_status' | 'answer_mode' | 'outcome' | 'reason_code'> | null | undefined): boolean {
  if (!message) return false
  const grounding = String(message.grounding_status || '').toLowerCase()
  const answerStatus = String(message.answer_status || '').toLowerCase()
  const answerMode = String(message.answer_mode || '').toLowerCase()
  const outcome = String(message.outcome || '').toLowerCase()
  const reason = String(message.reason_code || '').toLowerCase()

  if (grounding === 'insufficient_evidence' || grounding === 'ungrounded') return true
  if (answerMode === 'source_view_only' || answerStatus === 'cannot_verify') return true
  if (reason === 'soft_grounding_warning') return true
  if (outcome === 'partial' && (grounding === 'not_assessed' || answerStatus === 'unverified')) return true
  if (outcome === 'failed' || outcome === 'blocked' || outcome === 'support') return true
  return /insufficient|not[_-]?found|no[_-]?retrieval|cannot[_-]?verify|provider[_-]?output/.test(reason)
}
