import { describe, expect, it } from 'vitest'

const source = await import('./ProcedureManagementView?raw').then((module) => module.default)

describe('procedure management workflow', () => {
  it('keeps one sourced list with edit and delete actions', () => {
    expect(source).toContain("apiClient.put(`/procedures/admin/records/")
    expect(source).toContain("apiClient.delete(`/procedures/admin/records/")
    expect(source).toContain('Chỉ hiển thị dữ liệu thực tế đã được lưu')
    expect(source).toContain("review_status: value.review_status || 'candidate_pending_review'")
    expect(source).toContain("review_status: 'approved'")
    expect(source).not.toContain('Release Gate')
  })
})
