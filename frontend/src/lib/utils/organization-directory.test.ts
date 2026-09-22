import { describe, expect, it } from 'vitest'
import { domainsForUnit, fieldForDomain } from './organization-directory'

const settings = {
  legal_domains: [
    { code: 'cu_tru_an_ninh', name: 'Cư trú - Căn cước - ANTT', aliases: ['An ninh trật tự'], is_active: true, sort_order: 1 },
    { code: 'quoc_phong_quan_su', name: 'Quốc phòng - Nghĩa vụ quân sự', aliases: ['Quốc phòng'], is_active: true, sort_order: 2 },
    { code: 'retired', name: 'Đã ngừng', aliases: [], is_active: false, sort_order: 3 },
  ],
  organization_units: [
    { id: 'security', code: 'security', name: 'Văn phòng', domain_codes: ['cu_tru_an_ninh'], support_enabled: true, is_active: true, sort_order: 1 },
  ],
}

describe('organization directory selectors', () => {
  it('filters fields by the selected active department', () => {
    expect(domainsForUnit(settings, 'security').map(item => item.code)).toEqual(['cu_tru_an_ninh'])
    expect(domainsForUnit(settings).map(item => item.code)).toEqual(['cu_tru_an_ninh', 'quoc_phong_quan_su'])
  })

  it('resolves the legacy storage field through managed aliases', () => {
    expect(fieldForDomain(settings.legal_domains[0], [{ id: 9, name: 'An ninh trật tự' }])).toEqual({ id: 9, name: 'An ninh trật tự' })
  })

  it('maps the managed economy domain to its approved storage field', () => {
    const economy = { code: 'kinh_te', name: 'Kinh tế', aliases: [], is_active: true, sort_order: 4 }
    expect(fieldForDomain(economy, [
      { id: 34, name: 'Công Thương' },
      { id: 366, name: 'Kinh tế xây dựng' },
    ])).toEqual({ id: 366, name: 'Kinh tế xây dựng' })
  })
})
