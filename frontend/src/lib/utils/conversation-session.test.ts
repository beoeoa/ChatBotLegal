import { describe, expect, it } from 'vitest'
import { isCurrentSessionEpoch, nextSessionEpoch, shouldAutoLoadLatestConversation } from './conversation-session'

describe('conversation session selection', () => {
  it('does not auto-select an old conversation in explicit new-chat mode', () => {
    expect(shouldAutoLoadLatestConversation({
      hasHydrated: true,
      isAuthenticated: true,
      latestConversationLoaded: false,
      currentSessionId: null,
      explicitNewConversation: true,
    })).toBe(false)
  })

  it('loads the latest conversation only during a normal initial visit', () => {
    expect(shouldAutoLoadLatestConversation({
      hasHydrated: true,
      isAuthenticated: true,
      latestConversationLoaded: false,
      currentSessionId: null,
      explicitNewConversation: false,
    })).toBe(true)
  })

  it('does not replace an already selected conversation', () => {
    expect(shouldAutoLoadLatestConversation({
      hasHydrated: true,
      isAuthenticated: true,
      latestConversationLoaded: false,
      currentSessionId: 'conversation-1',
      explicitNewConversation: false,
    })).toBe(false)
  })

  it('advances and rejects stale session epochs', () => {
    const epoch = nextSessionEpoch(4)
    expect(epoch).toBe(5)
    expect(isCurrentSessionEpoch(4, epoch)).toBe(false)
    expect(isCurrentSessionEpoch(epoch, epoch)).toBe(true)
  })
})
