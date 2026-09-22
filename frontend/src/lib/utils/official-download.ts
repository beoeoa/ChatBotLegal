/** Never attach application credentials to a URL supplied by document metadata. */
export function officialDownloadTarget(raw: string, pageOrigin: string, apiBaseUrl: string) {
  const api = new URL(apiBaseUrl || pageOrigin, pageOrigin)
  const target = new URL(raw, api)
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) {
    throw new Error('Đường dẫn tải biểu mẫu không hợp lệ.')
  }
  const trustedApi = (target.origin === api.origin || target.origin === pageOrigin)
    && target.pathname.startsWith('/api/')
  if (!trustedApi && target.protocol !== 'https:') {
    throw new Error('Nguồn tải biểu mẫu cần dùng kết nối HTTPS an toàn.')
  }
  return { href: target.href, authenticated: trustedApi }
}
