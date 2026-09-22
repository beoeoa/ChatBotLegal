import { describe, expect, it } from 'vitest'

import { formatApiError, getApiErrorCode, getApiErrorMessage } from './error-handler'

describe('error-handler', () => {
  it('translates a lifecycle code nested in a JSON response string', () => {
    const error = {
      response: {
        status: 409,
        data: '{"detail":"document_not_effective_for_current_search"}',
      },
    }

    expect(formatApiError(error)).toBe(
      'Văn bản chưa đến ngày có hiệu lực nên chưa thể dùng cho tra cứu hiện hành.',
    )
    expect(getApiErrorCode(error)).toBe('document_not_effective_for_current_search')
  })

  it('does not expose unknown English, JSON, HTTP, or stack details', () => {
    expect(formatApiError(new Error('An internal provider exploded'))).toBe(
      'Không thể hoàn tất thao tác. Vui lòng thử lại.',
    )
    expect(formatApiError('{"detail":"some_new_internal_code"}')).toBe(
      'Không thể hoàn tất thao tác. Vui lòng thử lại.',
    )
    expect(formatApiError({ response: { status: 500, data: { detail: 'SQL connection failed' } } }))
      .toBe('Hệ thống gặp sự cố khi xử lý yêu cầu. Dữ liệu của bạn chưa bị thay đổi.')
  })

  it('keeps safe Vietnamese guidance from the backend', () => {
    expect(formatApiError({ response: { data: { detail: 'Vui lòng chọn ít nhất một văn bản.' } } }))
      .toBe('Vui lòng chọn ít nhất một văn bản.')
  })

  it('explains a stale FAQ publication in Vietnamese', () => {
    expect(formatApiError({ response: { status: 409, data: { detail: 'FAQ_ACTIVE_RELEASE_CHANGED' } } }))
      .toMatch(/Danh mục công khai vừa thay đổi/)
  })

  it('explains a compensated replacement failure without exposing its machine code', () => {
    const error = {
      response: {
        status: 502,
        data: { detail: { code: 'replacement_activation_failed', stage: 'retrieval_precheck' } },
      },
    }

    expect(formatApiError(error)).toMatch(/Bản cũ vẫn được bảo vệ/)
    expect(formatApiError(error)).not.toContain('replacement_activation_failed')
  })

  it('uses Vietnamese messages for network, timeout and common HTTP failures', () => {
    expect(formatApiError({ code: 'ERR_NETWORK', message: 'Network Error' })).toMatch(/Không thể kết nối/)
    expect(formatApiError({ code: 'ECONNABORTED', message: 'timeout of 1000ms exceeded' })).toMatch(/quá nhiều thời gian/)
    expect(formatApiError({ response: { status: 403, data: {} } })).toBe('Bạn không có quyền thực hiện thao tác này.')
  })

  it('preserves existing translated error keys', () => {
    const t = (key: string) => ({
      'apiErrors.invalidPassword': 'Mật khẩu không đúng.',
      'apiErrors.genericError': 'Đã có lỗi xảy ra.',
    }[key] || key)

    expect(getApiErrorMessage('Invalid password', t)).toBe('Mật khẩu không đúng.')
  })
})
