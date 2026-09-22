import type { LegalField } from '@/lib/api/legal-import'
import type { LegalDomainConfig, OrganizationUnitConfig, SettingsResponse } from '@/lib/types/api'

export type DirectoryUnit = OrganizationUnitConfig
export type DirectoryDomain = LegalDomainConfig

// Compatibility between managed domain codes and the existing legal-import
// storage taxonomy. Keep this explicit: fuzzy name matching could silently
// assign a document to the wrong legal field.
const STORAGE_FIELD_NAMES_BY_DOMAIN: Readonly<Record<string, readonly string[]>> = {
  kinh_te: ['Kinh tế xây dựng'],
}

export function activeDirectoryUnits(settings?: Pick<SettingsResponse, 'organization_units'> | null): DirectoryUnit[] {
  return (settings?.organization_units || []).filter(unit => unit.is_active)
}

export function activeDirectoryDomains(settings?: Pick<SettingsResponse, 'legal_domains'> | null): DirectoryDomain[] {
  return (settings?.legal_domains || []).filter(domain => domain.is_active)
}

export function unitDomainCodes(unit: DirectoryUnit | null | undefined): string[] {
  if (!unit) return []
  return Array.from(new Set([
    ...(unit.domain_codes || []),
    ...(unit.domain_assignments || []).map(assignment => assignment.domain_code),
  ].filter(Boolean)))
}

export function domainsForUnit(
  settings: Pick<SettingsResponse, 'legal_domains' | 'organization_units'> | null | undefined,
  unitId?: string | null,
): DirectoryDomain[] {
  const domains = activeDirectoryDomains(settings)
  if (!unitId) return domains
  const unit = activeDirectoryUnits(settings).find(item => item.id === unitId)
  const allowed = new Set(unitDomainCodes(unit))
  return domains.filter(domain => allowed.has(domain.code))
}

export function fieldForDomain(domain: DirectoryDomain, fields: LegalField[]): LegalField | undefined {
  const labels = [
    domain.name,
    ...(domain.aliases || []),
    ...(STORAGE_FIELD_NAMES_BY_DOMAIN[domain.code] || []),
  ]
    .map(value => value.trim().toLocaleLowerCase('vi-VN'))
    .filter(Boolean)
  return fields.find(field => labels.includes(field.name.trim().toLocaleLowerCase('vi-VN')))
}

export function domainLabel(
  code: string,
  settings?: Pick<SettingsResponse, 'legal_domains'> | null,
): string {
  return activeDirectoryDomains(settings).find(domain => domain.code === code)?.name || code
}
