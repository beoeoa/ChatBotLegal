"use client"

import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Archive, BookOpen, ClipboardCheck, FileWarning, Gavel, Loader2, MessageSquare, RefreshCw, ShieldCheck, Users } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/lib/api/client'
import { useAuthStore } from '@/lib/stores/auth-store'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'

interface Overview {
  users?: { total?: number; officer?: number; online?: number }
  live_support?: Record<string, number>
  legal_cases?: { total?: number; open?: number }
  knowledge?: { candidate_total?: number; forms_total?: number; forms_without_file?: number; faq_total?: number; faq_approved?: number }
  quality?: { insufficient_evidence?: number }
}

interface AdminUser { id: string; username: string; email?: string; role: string; allowed_domains?: string[]; ward_scope?: string; is_active: boolean; must_change_password?: boolean }
interface SupportItem { id: string; domain?: string; status?: string; age_minutes?: number; sla_overdue?: boolean }
interface LegalCase { id: string; status?: string; owner_user_id?: string; assigned_officer_id?: string }
interface Candidate { id: string; law_number?: string; title?: string; status?: string; review_status?: string; raw_metadata?: { ocr_status?: string } }
interface KnowledgeData { candidates?: Candidate[] }
interface QualityData { insufficient?: unknown[]; feedback?: unknown[] }
interface AuditItem { id: string; action: string; entity_type?: string; entity_id?: string; created?: string; actor_role?: string; details?: { reason?: string } }

function apiErrorDetail(error: unknown, fallback: string): string {
  if (typeof error !== 'object' || error === null || !('response' in error)) return fallback
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : fallback
}

function Counter({ label, value, icon: Icon }: { label: string; value: number | undefined; icon: typeof Users }) {
  return <Card><CardContent className="flex items-center gap-3 p-4"><div className="rounded-md bg-primary/10 p-2 text-primary"><Icon className="h-5 w-5" /></div><div><p className="text-xs text-muted-foreground">{label}</p><p className="text-2xl font-semibold">{value ?? 0}</p></div></CardContent></Card>
}

export default function AdminControlPage() {
  const role = useAuthStore((state) => state.role)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [support, setSupport] = useState<SupportItem[]>([])
  const [cases, setCases] = useState<LegalCase[]>([])
  const [knowledge, setKnowledge] = useState<KnowledgeData | null>(null)
  const [quality, setQuality] = useState<QualityData | null>(null)
  const [audit, setAudit] = useState<AuditItem[]>([])
  const [loading, setLoading] = useState(true)
  const [reasonOpen, setReasonOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState<null | { label: string; run: (reason: string) => Promise<void> }>(null)

  const load = useCallback(async () => {
    if (role !== 'admin') return
    setLoading(true)
    try {
      const [o, u, s, c, k, q, a] = await Promise.all([
        apiClient.get<Overview>('/admin/control/overview'),
        apiClient.get<AdminUser[]>('/admin/control/users'),
        apiClient.get<SupportItem[]>('/admin/control/live-support'),
        apiClient.get<LegalCase[]>('/admin/control/legal-cases'),
        apiClient.get<KnowledgeData>('/admin/control/knowledge'),
        apiClient.get<QualityData>('/admin/control/quality'),
        apiClient.get<AuditItem[]>('/admin/control/audit?limit=100'),
      ])
      setOverview(o.data); setUsers(u.data); setSupport(s.data); setCases(c.data); setKnowledge(k.data); setQuality(q.data); setAudit(a.data)
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, 'Không tải được dữ liệu quản trị.'))
    } finally { setLoading(false) }
  }, [role])

  useEffect(() => { void load() }, [load])

  const askReason = (label: string, run: (value: string) => Promise<void>) => {
    setPending({ label, run }); setReason(''); setReasonOpen(true)
  }
  const confirmReason = async () => {
    if (!pending || reason.trim().length < 3) { toast.error('Nhập lý do nghiệp vụ tối thiểu 3 ký tự.'); return }
    try { await pending.run(reason.trim()); setReasonOpen(false); setPending(null); await load() } catch (error: unknown) { toast.error(apiErrorDetail(error, 'Không thực hiện được thao tác.')) }
  }

  if (role !== 'admin') return <main className="p-8"><Card><CardContent className="flex items-center gap-3 p-6 text-destructive"><AlertCircle />Chỉ Admin có quyền mở Trung tâm quản trị.</CardContent></Card></main>

  return <main className="mx-auto max-w-7xl space-y-6 p-5 md:p-8">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-bold">Trung tâm quản trị</h1><p className="text-sm text-muted-foreground">Quản lý người dùng, hỗ trợ trực tuyến, kho tri thức, chất lượng và nhật ký kiểm toán.</p></div><Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Làm mới</Button></header>
    <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Counter label="Người dùng" value={overview?.users?.total} icon={Users} />
      <Counter label="Phiên hỗ trợ đang chờ" value={overview?.live_support?.waiting} icon={MessageSquare} />
      <Counter label="SLA quá hạn" value={overview?.live_support?.sla_overdue} icon={FileWarning} />
      <Counter label="Câu thiếu căn cứ" value={overview?.quality?.insufficient_evidence} icon={ClipboardCheck} />
    </section>
    <Tabs defaultValue="users" className="space-y-4"><TabsList className="flex h-auto flex-wrap justify-start gap-1"><TabsTrigger value="users">User & Role</TabsTrigger><TabsTrigger value="support">Live Support</TabsTrigger><TabsTrigger value="cases">Legal Cases</TabsTrigger><TabsTrigger value="knowledge">Knowledge</TabsTrigger><TabsTrigger value="quality">Quality</TabsTrigger><TabsTrigger value="audit">Audit</TabsTrigger></TabsList>
      <TabsContent value="users"><Card><CardHeader><CardTitle className="flex items-center gap-2"><Users className="h-5 w-5" />Người dùng và vai trò</CardTitle></CardHeader><CardContent className="overflow-x-auto"><table className="w-full text-sm"><thead className="border-b text-left text-muted-foreground"><tr><th className="p-2">Tài khoản</th><th>Vai trò</th><th>Lĩnh vực</th><th>Phường/xã</th><th>Trạng thái</th><th /></tr></thead><tbody>{users.map(user => <tr key={user.id} className="border-b"><td className="p-2"><b>{user.username}</b><div className="text-xs text-muted-foreground">{user.email}</div></td><td><Badge variant="outline">{user.role}</Badge></td><td>{(user.allowed_domains || []).join(', ') || 'Chưa gán'}</td><td>{user.ward_scope || 'Chưa gán'}</td><td>{user.is_active ? 'Hoạt động' : 'Đã khóa'}{user.must_change_password ? ' • Phải đổi mật khẩu' : ''}</td><td><Button size="sm" variant="outline" onClick={() => askReason(user.is_active ? 'Khóa tài khoản' : 'Mở khóa tài khoản', async value => apiClient.put(`/users/${user.id}`, { is_active: !user.is_active }, { headers: { 'X-Business-Reason': value } }))}>{user.is_active ? 'Khóa' : 'Mở khóa'}</Button></td></tr>)}</tbody></table></CardContent></Card></TabsContent>
      <TabsContent value="support"><Card><CardHeader><CardTitle className="flex items-center gap-2"><MessageSquare className="h-5 w-5" />Hỗ trợ trực tuyến • Hàng đợi và SLA</CardTitle></CardHeader><CardContent className="space-y-3">{support.length === 0 && <p className="text-sm text-muted-foreground">Không có phiên hỗ trợ.</p>}{support.map(item => <div className="flex flex-wrap items-center justify-between gap-3 rounded border p-3" key={item.id}><div><b>{item.id}</b><p className="text-xs text-muted-foreground">{item.domain} • {item.status} • {item.age_minutes ?? '?'} phút</p></div><div className="flex gap-2">{item.sla_overdue && <Badge variant="destructive">Quá SLA</Badge>}<Button size="sm" variant="outline" onClick={() => askReason('Xem chi tiết phiên hỗ trợ', async value => { const r = await apiClient.get(`/admin/control/live-support/${item.id}?reason=${encodeURIComponent(value)}`); toast.success(`Đã ghi nhật ký việc xem ${r.data.id}`) })}>Xem có kiểm toán</Button></div></div>)}</CardContent></Card></TabsContent>
      <TabsContent value="cases"><Card><CardHeader><CardTitle className="flex items-center gap-2"><Gavel className="h-5 w-5" />Hồ sơ pháp lý</CardTitle></CardHeader><CardContent className="space-y-3"><p className="text-sm text-muted-foreground">Metadata được hiển thị theo chủ sở hữu và trạng thái. Mở nội dung hồ sơ luôn yêu cầu lý do nghiệp vụ và ghi nhật ký kiểm toán.</p>{cases.length === 0 && <p className="text-sm text-muted-foreground">Chưa có hồ sơ pháp lý.</p>}{cases.map(item => <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded border p-3"><div><b>{item.id}</b><p className="text-xs text-muted-foreground">{item.status} • Chủ sở hữu: {item.owner_user_id || 'Cần admin xử lý'} • Cán bộ: {item.assigned_officer_id || 'Chưa gán'}</p></div><Button size="sm" variant="outline" onClick={() => askReason('Xem nội dung hồ sơ pháp lý', async value => { const r = await apiClient.get(`/admin/control/legal-cases/${item.id}?reason=${encodeURIComponent(value)}`); toast.success(`Đã ghi nhật ký việc xem hồ sơ ${r.data.id || item.id}`) })}>Mở có kiểm toán</Button></div>)}</CardContent></Card></TabsContent>
      <TabsContent value="knowledge"><Card><CardHeader><CardTitle className="flex items-center gap-2"><BookOpen className="h-5 w-5" />Kho tri thức</CardTitle></CardHeader><CardContent className="space-y-3"><div className="grid gap-3 md:grid-cols-3"><Counter label="Văn bản ứng viên" value={overview?.knowledge?.candidate_total} icon={Archive}/><Counter label="FAQ đã duyệt" value={overview?.knowledge?.faq_approved} icon={ClipboardCheck}/><Counter label="Biểu mẫu thiếu tệp" value={overview?.knowledge?.forms_without_file} icon={FileWarning}/></div>{(knowledge?.candidates || []).slice(0, 10).map((candidate) => <div key={candidate.id} className="flex flex-wrap items-center justify-between gap-3 rounded border p-3"><div><b>{candidate.law_number || candidate.title || candidate.id}</b><p className="text-xs text-muted-foreground">{candidate.status || candidate.review_status || 'pending'} • OCR: {candidate.raw_metadata?.ocr_status || 'chưa có'}</p></div><div className="flex gap-2"><Button size="sm" onClick={() => askReason('Duyệt văn bản ứng viên', async value => apiClient.post(`/admin/control/knowledge/candidates/${candidate.id}/review`, { decision: 'approved', reason: value, review_note: value }))}>Duyệt</Button><Button size="sm" variant="destructive" onClick={() => askReason('Từ chối văn bản ứng viên', async value => apiClient.post(`/admin/control/knowledge/candidates/${candidate.id}/review`, { decision: 'rejected', reason: value, review_note: value }))}>Từ chối</Button></div></div>)}</CardContent></Card></TabsContent>
      <TabsContent value="quality"><Card><CardHeader><CardTitle className="flex items-center gap-2"><ClipboardCheck className="h-5 w-5" />Chất lượng</CardTitle></CardHeader><CardContent className="space-y-3"><p className="text-sm text-muted-foreground">Theo dõi câu thiếu căn cứ, lỗi trích dẫn, biểu mẫu, OCR và đánh giá của người dân.</p><div className="grid gap-3 md:grid-cols-2"><Card><CardContent className="p-4"><b>Câu thiếu căn cứ</b><p className="text-2xl">{quality?.insufficient?.length ?? 0}</p></CardContent></Card><Card><CardContent className="p-4"><b>Phản hồi hỗ trợ</b><p className="text-2xl">{quality?.feedback?.length ?? 0}</p></CardContent></Card></div></CardContent></Card></TabsContent>
      <TabsContent value="audit"><Card><CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5" />Nhật ký kiểm toán chỉ đọc</CardTitle></CardHeader><CardContent><p className="mb-3 text-sm text-muted-foreground">Nhật ký không có API sửa hoặc xóa. Có thể lọc theo người thực hiện, tài nguyên, hành động, ngày và lý do qua API `/admin/control/audit`.</p><div className="max-h-[480px] space-y-2 overflow-y-auto">{audit.map(item => <div key={item.id} className="rounded border p-3 text-sm"><b>{item.action}</b><span className="mx-2 text-muted-foreground">{item.entity_type}:{item.entity_id}</span><p className="text-xs text-muted-foreground">{item.created} • {item.actor_role} • Lý do: {item.details?.reason || 'Không có'}</p></div>)}</div></CardContent></Card></TabsContent>
    </Tabs>
    <Dialog open={reasonOpen} onOpenChange={setReasonOpen}><DialogContent><DialogHeader><DialogTitle>{pending?.label || 'Xác nhận thao tác'}</DialogTitle><DialogDescription>Nhập lý do nghiệp vụ. Hệ thống sẽ ghi nhật ký kiểm toán trước khi thực hiện hoặc mở dữ liệu.</DialogDescription></DialogHeader><Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Ví dụ: Rà soát hồ sơ theo yêu cầu của Trưởng bộ phận" /><div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setReasonOpen(false)}>Hủy</Button><Button onClick={() => void confirmReason()}>Xác nhận và ghi nhật ký</Button></div></DialogContent></Dialog>
  </main>
}
