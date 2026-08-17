import { describe, expect, it } from 'vitest'

import { pickOfficerDefaultDomain } from './officer-domain-selection'

const options = [
  { id: 'civil', agency: 'Tư pháp - Hộ tịch', domain: 'civil_status' },
  { id: 'land', agency: 'Địa chính - Xây dựng', domain: 'land' },
] as const

describe('pickOfficerDefaultDomain', () => {
  it('keeps auto detection when an officer can work across multiple domains', () => {
    expect(
      pickOfficerDefaultDomain(
        'Feature 005 legal acceptance',
        ['civil_status', 'land'],
        options,
      ),
    ).toBeNull()
  })

  it('preselects the only permitted domain', () => {
    expect(
      pickOfficerDefaultDomain(null, ['land'], options),
    ).toEqual(options[1])
  })

  it('uses an explicit matching department before allowed-domain defaults', () => {
    expect(
      pickOfficerDefaultDomain(
        'Địa chính - Xây dựng',
        ['civil_status', 'land'],
        options,
      ),
    ).toEqual(options[1])
  })
})
