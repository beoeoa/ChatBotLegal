import type { UserRole } from '@/lib/stores/auth-store'

export type ChatViewerIdentity = {
  gender?: string | null
  fullName?: string | null
  department?: string | null
  jobTitle?: string | null
  username?: string | null
}

function clean(value?: string | null): string {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function folded(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('vi-VN')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function personLabel(identity?: ChatViewerIdentity | null): string {
  const fullName = clean(identity?.fullName)
  if (!fullName) return ''

  const normalized = folded(fullName)
  const placeholder = (
    /(?:^|\s)(?:test|demo)(?:\s|$)/.test(normalized)
    || /^(?:nguoi dan|citizen|user|can bo|officer|admin|quan tri vien)(?:\s*\d+)?$/.test(normalized)
  )
  return placeholder ? '' : fullName
}

function officerAddressee(identity?: ChatViewerIdentity | null): string {
  const jobTitle = clean(identity?.jobTitle) || 'cán bộ'
  const rawName = personLabel(identity)
  const genericName = /^(?:cán bộ|chuyên viên|nhân viên|officer|quản trị viên)(?:\s|$)/i.test(rawName)
  const titleAlreadyContainsName = Boolean(rawName) && folded(jobTitle).includes(folded(rawName))
  const name = genericName || titleAlreadyContainsName ? '' : rawName
  const base = [jobTitle, name].filter(Boolean).join(' ')
  const department = clean(identity?.department) || inferOfficerDepartment(identity?.username)
  const suffix = department && !folded(base).includes(folded(department))
    ? ` thuộc ${department}`
    : ''
  return `${base}${suffix}`
}

export function inferOfficerDepartment(username?: string | null): string {
  const value = clean(username).toLocaleLowerCase('vi-VN')
  if (value.includes('hotich')) return 'Tư pháp - Hộ tịch'
  if (value.includes('daidai') || value.includes('datdai')) return 'Địa chính - Đất đai'
  if (value.includes('ansinh')) return 'An sinh xã hội'
  if (value.includes('cutru')) return 'Cư trú - An ninh'
  if (value.includes('khieunai')) return 'Khiếu nại - Tố cáo'
  return ''
}

export function answerSalutation(
  role: UserRole,
  identity?: ChatViewerIdentity | null,
): string {
  return identity?.gender === 'male' ? 'Thưa anh,' : identity?.gender === 'female' ? 'Thưa chị,' : 'Thưa anh/chị,'
}

export function registeredAnswer(content: string, identity?: ChatViewerIdentity | null): string {
  if (identity?.gender !== 'male' && identity?.gender !== 'female') return content
  // Only replace a leading greeting, never names/quotations in the answer body.
  return content.replace(/^(\s*)(?:kính\s+)?(?:thưa|xin chào|chào)\s+[^,\n.!?]{1,120}[,\n.!?]\s*/iu,
    (_match, whitespace) => `${whitespace}${identity.gender === 'male' ? 'Thưa anh,' : 'Thưa chị,'} `)
}

export function welcomeMessage(
  role: UserRole,
  identity?: ChatViewerIdentity | null,
): { title: string; invitation: string } {
  const name = personLabel(identity)
  const department = clean(identity?.department) || (role === 'officer' ? inferOfficerDepartment(identity?.username) : '')
  const jobTitle = clean(identity?.jobTitle)

  if (role === 'officer') {
    return {
      title: `Kính chào ${officerAddressee(identity)}.`,
      invitation: 'Tôi sẵn sàng hỗ trợ tra cứu nghiệp vụ pháp luật trong lĩnh vực được phân công. Mời anh/chị nhập câu hỏi cần xử lý.',
    }
  }

  if (role === 'admin') {
    const addressee = [jobTitle || 'quản trị viên', name].filter(Boolean).join(' ')
    return {
      title: `Kính chào ${addressee}${department ? ` thuộc ${department}` : ''}.`,
      invitation: 'Tôi sẵn sàng hỗ trợ tra cứu và kiểm tra nội dung pháp luật. Mời anh/chị nhập câu hỏi.',
    }
  }

  return {
    title: `Xin chào anh/chị${name ? ` ${name}` : ''}.`,
    invitation: 'Tôi có thể hỗ trợ tra cứu quy định, thủ tục và biểu mẫu pháp luật. Mời anh/chị nhập câu hỏi để bắt đầu.',
  }
}

export function chatStarterQuestions(
  role: UserRole,
  identity?: ChatViewerIdentity | null,
): string[] {
  if (role === 'admin') return []
  if (role === 'citizen') {
    return [
      'Tôi thuê nhà tại Hải Phòng thì đăng ký tạm trú cần giấy tờ gì?',
      'Đăng ký lại khai sinh cần chuẩn bị hồ sơ và nộp ở đâu?',
      'Điều kiện hưởng trợ cấp hưu trí xã hội hiện nay là gì?',
      'Khiếu nại lần đầu quyết định của Chủ tịch UBND phường gửi đến ai?',
    ]
  }

  const department = folded(
    clean(identity?.department) || inferOfficerDepartment(identity?.username),
  )
  if (department.includes('ho tich')) {
    return [
      'Hồ sơ đăng ký lại khai sinh cần kiểm tra những nội dung nào?',
      'Thẩm quyền đăng ký lại khai sinh được xác định ra sao?',
      'Thời hạn giải quyết hồ sơ đăng ký lại khai sinh là bao lâu?',
      'Trường hợp hồ sơ hộ tịch chưa đầy đủ cần hướng dẫn thế nào?',
    ]
  }
  if (department.includes('dat dai') || department.includes('dia chinh')) {
    return [
      'Hồ sơ tách thửa cần kiểm tra điều kiện và giấy tờ nào?',
      'UBND phường xác nhận nội dung gì trong hồ sơ đất đai?',
      'Hồ sơ tặng cho quyền sử dụng đất cần chuyển đến cơ quan nào?',
      'Căn cứ và thời hạn xử lý hồ sơ tách thửa được xác định ra sao?',
    ]
  }
  if (department.includes('an sinh')) {
    return [
      'Hồ sơ trợ cấp hưu trí xã hội cần kiểm tra những gì?',
      'Điều kiện hưởng trợ cấp hưu trí xã hội được xác định ra sao?',
      'Cơ quan nào giải quyết hồ sơ trợ cấp hưu trí xã hội?',
      'Thời hạn và bước xử lý hồ sơ trợ cấp là bao lâu?',
    ]
  }
  if (department.includes('cu tru') || department.includes('an ninh')) {
    return [
      'Hồ sơ đăng ký tạm trú cần kiểm tra giấy tờ chỗ ở nào?',
      'Chủ sở hữu không trực tiếp đi cùng thì tiếp nhận hồ sơ đăng ký tạm trú ra sao?',
      'Thẩm quyền và thời hạn giải quyết đăng ký tạm trú là gì?',
      'Trường hợp dữ liệu cư trú đã có sẵn cần yêu cầu giấy tờ nào?',
    ]
  }
  if (department.includes('khieu nai') || department.includes('to cao')) {
    return [
      'Đơn khiếu nại lần đầu thuộc thẩm quyền của ai?',
      'Cán bộ tiếp nhận cần chuyển đơn khiếu nại đến đâu?',
      'Làm thế nào phân loại nội dung khiếu nại và tố cáo trong cùng đơn?',
      'Thời hạn xử lý và thông báo việc chuyển đơn được xác định ra sao?',
    ]
  }
  return [
    'Hồ sơ này cần kiểm tra những thành phần nào?',
    'Thẩm quyền giải quyết trường hợp này được xác định ra sao?',
    'Thời hạn xử lý và bước tiếp theo là gì?',
  ]
}

export function answerAlreadyHasSalutation(answer?: string | null): boolean {
  const firstLine = clean(answer).split('\n', 1)[0].replace(/^#+\s*/, '')
  return /^(?:kính\s+thưa|thưa\s+|xin\s+chào|kính\s+chào|chào\s+|(?:anh|chị)\s+[\p{L}\p{N}][^,\n.!?]{0,80},|dạ,\s*(?:anh|chị)\s+[\p{L}\p{N}]+)/iu.test(firstLine)
}
