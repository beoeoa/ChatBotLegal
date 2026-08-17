export type OfficerDomainOption = {
  id: string
  agency: string
  domain: string
}

export function pickOfficerDefaultDomain<T extends OfficerDomainOption>(
  department: string | null | undefined,
  allowedDomains: readonly string[],
  options: readonly T[],
): T | null {
  const normalizedDepartment = String(department || '').trim().toLocaleLowerCase()
  const departmentMatch = normalizedDepartment
    ? options.find((item) => {
        const agency = item.agency.toLocaleLowerCase()
        return (
          agency.includes(normalizedDepartment)
          || normalizedDepartment.includes(agency)
        )
      })
    : undefined

  if (departmentMatch) {
    return departmentMatch
  }
  if (allowedDomains.length !== 1) {
    return null
  }
  return options.find((item) => item.domain === allowedDomains[0]) || null
}
