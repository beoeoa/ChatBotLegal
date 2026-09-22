import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  Book,
  Building2,
  Bot,
  ClipboardCheck,
  DatabaseZap,
  HardDriveDownload,
  FileSearch,
  FileText,
  LayoutDashboard,
  LibraryBig,
  MessageCircleQuestion,
  Settings,
  UserCog,
  UserRound,
} from 'lucide-react'

import type { UserRole } from '@/lib/stores/auth-store'

export const ADMIN_LANDING_PATH = '/admin'

export type NavigationItem = {
  id: string
  name: string
  href: string
  icon: LucideIcon
  keywords: string[]
}

export type NavigationGroup = {
  id: string
  title: string
  items: NavigationItem[]
}

export type CreateAction = {
  id: 'source' | 'notebook'
  name: string
  icon: LucideIcon
}

const roleNavigation: Record<Exclude<UserRole, null>, NavigationGroup[]> = {
  citizen: [{
    id: 'citizen-process', title: 'Tra cứu', items: [
      { id: 'ask', name: 'Hỏi đáp pháp luật', href: '/search', icon: MessageCircleQuestion, keywords: ['hỏi đáp', 'ai', 'pháp luật'] },
      { id: 'procedures', name: 'Thủ tục hành chính', href: '/procedures', icon: FileSearch, keywords: ['thủ tục', 'hành chính'] },
      { id: 'support', name: 'Hỗ trợ trực tuyến', href: '/live-support', icon: Bot, keywords: ['hỗ trợ', 'trao đổi'] },
      { id: 'account', name: 'Tài khoản của tôi', href: '/account', icon: UserRound, keywords: ['tài khoản', 'email', 'mật khẩu', 'bảo mật'] },
    ],
  }],
  officer: [
    {
      id: 'officer-overview', title: 'Tổng quan', items: [
        { id: 'officer-dashboard', name: 'Tổng quan nghiệp vụ', href: '/officer-dashboard', icon: LayoutDashboard, keywords: ['dashboard', 'tổng quan', 'nghiệp vụ', 'cán bộ'] },
      ],
    },
    {
      id: 'officer-process', title: 'Nghiệp vụ', items: [
        { id: 'ask', name: 'Hỏi đáp pháp luật', href: '/search', icon: MessageCircleQuestion, keywords: ['hỏi đáp', 'ai', 'pháp luật'] },
        { id: 'sources', name: 'Kho tra cứu công khai', href: '/legal-library', icon: FileText, keywords: ['nguồn', 'tài liệu', 'kho tra cứu'] },
        { id: 'procedures', name: 'Thủ tục hành chính', href: '/procedures', icon: FileSearch, keywords: ['thủ tục', 'hành chính'] },
        { id: 'notebooks', name: 'Hồ sơ pháp lý', href: '/notebooks', icon: Book, keywords: ['hồ sơ', 'ghi chú'] },
        { id: 'support', name: 'Hỗ trợ trực tuyến', href: '/live-support', icon: Bot, keywords: ['hỗ trợ', 'trao đổi'] },
        { id: 'proposals', name: 'Đề xuất văn bản', href: '/officer-proposals', icon: FileSearch, keywords: ['đề xuất', 'văn bản'] },
        { id: 'account', name: 'Tài khoản của tôi', href: '/account', icon: UserRound, keywords: ['tài khoản', 'email', 'mật khẩu', 'bảo mật'] },
      ],
    },
  ],
  admin: [
    { id: 'admin-overview', title: 'Điều hành', items: [
      { id: 'admin-dashboard', name: 'Tổng quan hệ thống', href: '/admin', icon: LayoutDashboard, keywords: ['dashboard', 'tổng quan', 'điều hành', 'trạng thái'] },
      { id: 'admin-activity', name: 'Nhật ký quản trị', href: '/admin/activity', icon: Activity, keywords: ['hoạt động', 'nhật ký', 'kiểm toán', 'audit'] },
      { id: 'admin-backups', name: 'Sao lưu dữ liệu', href: '/admin/backups', icon: HardDriveDownload, keywords: ['sao lưu', 'backup', 'khôi phục', 'kiểm chứng'] },
    ] },
    { id: 'admin-legal-data', title: 'Dữ liệu pháp lý', items: [
      { id: 'legal-management', name: 'Kho văn bản pháp luật', href: '/legal-management', icon: LibraryBig, keywords: ['quản lý', 'kho văn bản', 'hiệu lực', 'chỉ mục'] },
      { id: 'legal-import', name: 'Tiếp nhận & duyệt văn bản', href: '/legal-import', icon: DatabaseZap, keywords: ['nhập', 'duyệt', 'lập chỉ mục'] },
      { id: 'sources', name: 'Kho tra cứu công khai', href: '/legal-library', icon: FileText, keywords: ['nguồn', 'tài liệu', 'kho tra cứu'] },
    ] },
    { id: 'admin-knowledge', title: 'Kho tri thức', items: [
      { id: 'procedures', name: 'Thủ tục hành chính', href: '/procedures', icon: FileSearch, keywords: ['thủ tục', 'tra cứu', 'đã phát hành'] },
      { id: 'procedure-management', name: 'Thủ tục & biểu mẫu', href: '/procedure-management', icon: ClipboardCheck, keywords: ['thủ tục', 'biểu mẫu', 'duyệt', 'phát hành'] },
      { id: 'notebooks', name: 'Hồ sơ pháp lý', href: '/notebooks', icon: Book, keywords: ['hồ sơ', 'ghi chú'] },
      { id: 'faq', name: 'Quản lý thủ tục hành chính', href: '/faq-management', icon: ClipboardCheck, keywords: ['thủ tục', 'hành chính', 'xác nhận', 'phát hành'] },
    ] },
    { id: 'admin-users', title: 'Người dùng', items: [
      { id: 'departments', name: 'Quản lý phòng ban', href: '/departments', icon: Building2, keywords: ['phòng ban', 'đơn vị', 'lĩnh vực'] },
      { id: 'users', name: 'Tài khoản', href: '/users', icon: UserCog, keywords: ['tài khoản', 'người dùng'] },
    ] },
    { id: 'admin-ai-settings', title: 'AI & Cài đặt', items: [
      { id: 'models', name: 'Model và API key', href: '/settings/api-keys', icon: Bot, keywords: ['model', 'api key', 'ai'] },
      { id: 'settings', name: 'Cài đặt', href: '/settings', icon: Settings, keywords: ['cấu hình', 'cài đặt'] },
      { id: 'account', name: 'Tài khoản của tôi', href: '/account', icon: UserRound, keywords: ['tài khoản', 'email', 'mật khẩu', 'bảo mật'] },
    ] },
  ],
}

const adminCreateActions: CreateAction[] = [
  { id: 'source', name: 'Nguồn tài liệu', icon: FileText },
  { id: 'notebook', name: 'Hồ sơ pháp lý', icon: Book },
]

const officerCreateActions: CreateAction[] = [
  { id: 'notebook', name: 'Hồ sơ pháp lý', icon: Book },
]

export function navigationForRole(role: UserRole | null): NavigationGroup[] {
  return role ? roleNavigation[role] : []
}

export function createActionsForRole(role: UserRole | null): CreateAction[] {
  if (role === 'admin') return adminCreateActions
  if (role === 'officer') return officerCreateActions
  return []
}

export function allowedPathsForRole(role: UserRole): string[] {
  return navigationForRole(role).flatMap((group) => group.items.map((item) => item.href.split('?')[0]))
}
