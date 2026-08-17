import { describe, expect, it } from 'vitest'

import { buildAccountActionRequest, encodeBusinessReason, validateAccountContactInput } from './user-account-actions'

describe('buildAccountActionRequest', () => {
  it('encodes a Vietnamese business reason into a safe request header', () => {
    expect(encodeBusinessReason(' Kiểm thử trực tiếp giao diện ')).toBe(
      'Ki%E1%BB%83m%20th%E1%BB%AD%20tr%E1%BB%B1c%20ti%E1%BA%BFp%20giao%20di%E1%BB%87n',
    )
  })

  it('uses the deactivation endpoint so active sessions are revoked', () => {
    expect(buildAccountActionRequest('http://api.test', 'user:1', 'deactivate', '')).toEqual({
      url: 'http://api.test/api/users/user:1',
      method: 'DELETE',
      body: undefined,
    })
  })

  it('keeps activation and password reset payloads explicit', () => {
    expect(buildAccountActionRequest('http://api.test', 'user:1', 'activate', '')).toEqual({
      url: 'http://api.test/api/users/user:1',
      method: 'PUT',
      body: JSON.stringify({ is_active: true }),
    })
    expect(buildAccountActionRequest('http://api.test', 'user:1', 'reset-password', 'temporary-123')).toEqual({
      url: 'http://api.test/api/users/user:1/reset-password',
      method: 'POST',
      body: JSON.stringify({ new_password: 'temporary-123' }),
    })
  })

  it('uses a distinct soft-delete endpoint so delete is not confused with locking', () => {
    expect(buildAccountActionRequest('http://api.test', 'user:1', 'soft-delete', '')).toEqual({
      url: 'http://api.test/api/users/user:1/soft-delete',
      method: 'POST',
      body: undefined,
    })
  })

  it('validates contact fields before sending an account form', () => {
    expect(validateAccountContactInput('nguoidung@', '0912345678')).toBe('Vui lòng nhập email hợp lệ.')
    expect(validateAccountContactInput('nguoidung@example.test', '09AB123')).toBe('Số điện thoại cần có từ 8 đến 15 chữ số.')
    expect(validateAccountContactInput('nguoidung@example.test', '+84 912 345 678')).toBeNull()
    expect(validateAccountContactInput('nguoidung@example.test', '')).toBeNull()
  })
})
