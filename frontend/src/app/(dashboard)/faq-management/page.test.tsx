import { describe, expect, it } from 'vitest'

const pageSource = await import('./page?raw').then((module) => module.default)
const viewSource = await import('./ProcedureManagementView?raw').then((module) => module.default)

describe('procedure management route contract', () => {
  it('uses the procedure management view as the only route surface', () => {
    expect(pageSource).toContain('ProcedureManagementView')
    expect(viewSource).toContain('Quản lý thủ tục hành chính')
    expect(viewSource).toContain('Tổng thủ tục')
    expect(viewSource).toContain('Thêm thủ tục')
    expect(viewSource).toContain('Thành phần hồ sơ')
    expect(viewSource).toContain('Thời hạn')
    expect(viewSource).toContain('Lệ phí')
    expect(viewSource).toContain('Biểu mẫu chính thức liên quan')
    expect(viewSource).toContain('Thêm biểu mẫu')
    expect(viewSource).toContain('!max-w-[900px]')
    expect(viewSource).toContain("'/procedures/admin/records'")
    expect(viewSource).toContain('>Sửa</Button>')
    expect(viewSource).toContain('>Xóa</Button>')
    expect(viewSource).toContain('<ConfirmDialog')
    expect(viewSource).not.toContain('Nạp danh mục Hải Phòng')
    expect(viewSource).not.toContain('Phát hành thủ tục đã xác nhận')
  })
})
