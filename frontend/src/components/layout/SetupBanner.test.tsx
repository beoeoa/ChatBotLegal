import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ role: 'citizen' as string | null }))
const credentialStatus = vi.hoisted(() => vi.fn())
const envStatus = vi.hoisted(() => vi.fn())
vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: (selector: (value: typeof state) => unknown) => selector(state),
}))
vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredentialStatus: credentialStatus,
  useEnvStatus: envStatus,
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

import { SetupBanner } from './SetupBanner'

describe('SetupBanner role boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    credentialStatus.mockReturnValue({ data: { encryption_configured: false, source: {} } })
    envStatus.mockReturnValue({ data: {} })
  })

  it.each(['citizen', 'officer', null])('does not request admin configuration for %s', (role) => {
    state.role = role
    const { container } = render(<SetupBanner />)
    expect(container).toBeEmptyDOMElement()
    expect(credentialStatus).not.toHaveBeenCalled()
    expect(envStatus).not.toHaveBeenCalled()
  })

  it('retains the setup warning for administrators', () => {
    state.role = 'admin'
    render(<SetupBanner />)
    expect(screen.getByText('setupBanner.encryptionRequired')).toBeInTheDocument()
    expect(credentialStatus).toHaveBeenCalled()
    expect(envStatus).toHaveBeenCalled()
  })
})
