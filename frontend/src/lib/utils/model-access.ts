export function shouldLoadAdminModelMetadata(role: string | null | undefined, ready: boolean): boolean {
  return ready && role === 'admin'
}
