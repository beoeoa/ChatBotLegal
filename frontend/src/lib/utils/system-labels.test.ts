import { describe, expect, it } from 'vitest'

import { dataQualitySummary, systemStatusLabel } from './system-labels'

describe('system-labels', () => {
  it('never returns a raw unknown status', () => {
    expect(systemStatusLabel('some_new_backend_state')).toBe('Chưa xác định')
  })

  it('uses plain Vietnamese for retrieval and release states', () => {
    expect(systemStatusLabel('current_retrievable')).toBe('Đang dùng cho tra cứu hiện hành')
    expect(systemStatusLabel('release_candidate')).toBe('Đang kiểm tra trước khi phát hành')
  })

  it('summarizes technical data-quality flags in plain language', () => {
    expect(dataQualitySummary(['zero_chunks', 'fingerprint_mismatch']))
      .toBe('Chưa có nội dung tra cứu; Dữ liệu tra cứu không đồng nhất')
  })
})
