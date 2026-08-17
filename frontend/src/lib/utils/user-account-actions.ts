export type AccountAction = 'activate' | 'deactivate' | 'reset-password' | 'soft-delete'

type AccountActionRequest = {
  url: string
  method: 'DELETE' | 'POST' | 'PUT'
  body: string | undefined
}

export function encodeBusinessReason(value: string): string {
  return encodeURIComponent(value.trim())
}

export function validateAccountContactInput(email: string, phone: string): string | null {
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
    return 'Vui lòng nhập email hợp lệ.'
  }
  const normalizedPhone = phone.trim()
  if (!normalizedPhone) return null
  const digits = normalizedPhone.replace(/\D/g, '')
  if (!/^\+?[0-9().\-\s]+$/.test(normalizedPhone) || digits.length < 8 || digits.length > 15) {
    return 'Số điện thoại cần có từ 8 đến 15 chữ số.'
  }
  return null
}

export function buildAccountActionRequest(
  apiUrl: string,
  userId: string,
  action: AccountAction,
  temporaryPassword: string,
): AccountActionRequest {
  if (action === 'reset-password') {
    return {
      url: `${apiUrl}/api/users/${userId}/reset-password`,
      method: 'POST',
      body: JSON.stringify({ new_password: temporaryPassword }),
    }
  }
  if (action === 'deactivate') {
    return {
      url: `${apiUrl}/api/users/${userId}`,
      method: 'DELETE',
      body: undefined,
    }
  }
  if (action === 'soft-delete') {
    return {
      url: `${apiUrl}/api/users/${userId}/soft-delete`,
      method: 'POST',
      body: undefined,
    }
  }
  return {
    url: `${apiUrl}/api/users/${userId}`,
    method: 'PUT',
    body: JSON.stringify({ is_active: true }),
  }
}
