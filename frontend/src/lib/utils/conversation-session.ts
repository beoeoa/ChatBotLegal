export interface ConversationAutoLoadState {
  hasHydrated: boolean
  isAuthenticated: boolean
  latestConversationLoaded: boolean
  currentSessionId: string | null
  explicitNewConversation: boolean
}

export function shouldAutoLoadLatestConversation(state: ConversationAutoLoadState): boolean {
  return state.hasHydrated &&
    state.isAuthenticated &&
    !state.latestConversationLoaded &&
    !state.currentSessionId &&
    !state.explicitNewConversation
}

/** Monotonic token used to discard responses belonging to an older chat. */
export function nextSessionEpoch(current: number): number {
  return Number.isFinite(current) && current >= 0 ? current + 1 : 1
}

export function isCurrentSessionEpoch(expected: number, current: number): boolean {
  return expected === current
}
