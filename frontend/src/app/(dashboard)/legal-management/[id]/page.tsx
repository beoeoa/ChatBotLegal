'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  ExternalLink,
  FileClock,
  History,
  Layers3,
  RefreshCw,
  Scale,
  ShieldCheck,
} from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  legalManagementApi,
  type AvailabilitySection,
  type LegalManagementDetail,
} from '@/lib/api/legal-management'

function dateLabel(value?: string | null) {
  if (!value) return 'Chưa có dữ liệu'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('vi-VN')
}

function AvailabilityNotice({ section }: { section: AvailabilitySection }) {
  if (section.status === 'available') return null
  return (
    <Alert>
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{section.status === 'degraded' ? 'Dữ liệu chưa đầy đủ' : 'Không thể đọc'}</AlertTitle>
      <AlertDescription>{section.message || 'Kho dữ liệu này hiện chưa sẵn sàng.'}</AlertDescription>
    </Alert>
  )
}

function Metadata({ label, value }: { label: string; value?: React.ReactNode }) {
  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 break-words text-sm">{value || 'Chưa có dữ liệu'}</div>
    </div>
  )
}

export default function LegalManagementDetailPage() {
  const params = useParams<{ id: string }>()
  const documentId = useMemo(() => decodeURIComponent(String(params?.id || '')), [params?.id])
  const [detail, setDetail] = useState<LegalManagementDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setDetail(await legalManagementApi.detail(documentId))
    } catch {
      setError('Không thể đọc hồ sơ quản trị của văn bản này.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (documentId) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId])

  const document = detail?.document
  const observed = detail?.observed_at ? new Date(detail.observed_at).toLocaleString('vi-VN') : ''

  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-auto bg-muted/20">
        <div className="mx-auto max-w-[1350px] space-y-6 p-4 pt-16 md:p-8 md:pt-8">
          <header className="space-y-4">
            <Link href="/legal-management" className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
              <ArrowLeft className="mr-2 h-4 w-4" /> Quay lại kho văn bản
            </Link>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">Admin · chỉ đọc</Badge>
                  {document?.validity_sync?.display_label && (
                    <Badge variant={document.current_answer_eligible === false ? 'destructive' : 'default'}>
                      {document.validity_sync.display_label}
                    </Badge>
                  )}
                  {document?.stored_status && <Badge variant="outline">Kho: {document.stored_status}</Badge>}
                  {document?.retrieval_tier && <Badge variant="secondary">{document.retrieval_tier === 'core' ? 'Kho nhanh' : 'Kho mở rộng'}</Badge>}
                </div>
                <h1 className="mt-3 max-w-4xl text-2xl font-semibold tracking-tight md:text-3xl">
                  {document?.document_title || (loading ? 'Đang đọc văn bản…' : 'Chi tiết văn bản')}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">{document?.law_number || `ID ${documentId}`}</p>
                {observed && <p className="mt-1 text-xs text-muted-foreground">Quan sát: {observed}</p>}
              </div>
              <Button variant="outline" onClick={() => void load()} disabled={loading}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> Làm mới
              </Button>
            </div>
          </header>

          {error && (
            <Alert variant="destructive" role="alert">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Không tải được chi tiết</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {document?.current_answer_eligible === false && (
            <Alert variant="destructive" role="status">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{document.validity_sync?.display_label}</AlertTitle>
              <AlertDescription>
                Chỉ dùng để tra cứu lịch sử. Retrieval của chatbot không dùng văn bản này để trả lời pháp luật hiện hành.
              </AlertDescription>
            </Alert>
          )}

          {detail && (
            <Tabs defaultValue="general" className="space-y-4">
              <TabsList className="h-auto w-full justify-start overflow-x-auto bg-background p-1">
                <TabsTrigger value="general"><BookOpen className="mr-2 h-4 w-4" />Thông tin chung</TabsTrigger>
                <TabsTrigger value="validity"><Scale className="mr-2 h-4 w-4" />Hiệu lực & quan hệ</TabsTrigger>
                <TabsTrigger value="vectors"><Layers3 className="mr-2 h-4 w-4" />Chỉ mục & vector</TabsTrigger>
                <TabsTrigger value="faq"><ShieldCheck className="mr-2 h-4 w-4" />FAQ ảnh hưởng</TabsTrigger>
                <TabsTrigger value="versions"><FileClock className="mr-2 h-4 w-4" />Phiên bản</TabsTrigger>
                <TabsTrigger value="audit"><History className="mr-2 h-4 w-4" />Audit</TabsTrigger>
              </TabsList>

              <TabsContent value="general" className="space-y-4">
                <Card>
                  <CardHeader><CardTitle>Metadata nguồn</CardTitle></CardHeader>
                  <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    <Metadata label="Số, ký hiệu" value={document?.law_number} />
                    <Metadata label="Loại văn bản" value={document?.document_type} />
                    <Metadata label="Cơ quan ban hành" value={document?.issuing_agency} />
                    <Metadata label="Ngày ban hành" value={dateLabel(document?.issued_date)} />
                    <Metadata label="Ngày hiệu lực" value={dateLabel(document?.effective_date)} />
                    <Metadata label="Ngày hết hiệu lực" value={dateLabel(document?.expired_date)} />
                    <Metadata label="Phạm vi" value={document?.scope} />
                    <Metadata label="Lĩnh vực" value={document?.field_name || document?.sector} />
                    <Metadata label="Cấu trúc" value={`${detail.structure.article_count} điều · ${detail.structure.chunk_count} chunk`} />
                    <Metadata label="Người ký" value={document?.signer_name} />
                    <Metadata label="Chức danh" value={document?.signer_title} />
                    <Metadata
                      label="Nguồn chính thức"
                      value={document?.source_url ? (
                        <a href={document.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center text-primary hover:underline">
                          Mở nguồn <ExternalLink className="ml-1 h-3.5 w-3.5" />
                        </a>
                      ) : undefined}
                    />
                  </CardContent>
                </Card>
                {document?.quality_flags && document.quality_flags.length > 0 && (
                  <Alert>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>Cảnh báo chất lượng metadata</AlertTitle>
                    <AlertDescription>{document.quality_flags.join(', ')}</AlertDescription>
                  </Alert>
                )}
              </TabsContent>

              <TabsContent value="validity" className="space-y-4">
                <AvailabilityNotice section={detail.validity} />
                <AvailabilityNotice section={detail.relationships} />
                {detail.validity.status === 'available' && (
                  <div className="grid gap-4 lg:grid-cols-3">
                    <SummaryBox label="Quan sát" value={detail.validity.observations?.length || 0} />
                    <SummaryBox label="Sự kiện" value={detail.validity.events?.length || 0} />
                    <SummaryBox label="Quyết định" value={detail.validity.decisions?.length || 0} />
                  </div>
                )}
                {(detail.relationships.items || []).length > 0 && (
                  <Card>
                    <CardHeader><CardTitle>Quan hệ đã lưu</CardTitle></CardHeader>
                    <CardContent className="space-y-2">
                      {(detail.relationships.items || []).map((item, index) => (
                        <pre key={index} className="overflow-x-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(item, null, 2)}</pre>
                      ))}
                    </CardContent>
                  </Card>
                )}
                {detail.validity.replacement_discovery && (
                  <Alert>
                    <Scale className="h-4 w-4" />
                    <AlertTitle>Ứng viên văn bản tác động</AlertTitle>
                    <AlertDescription>Chỉ là ứng viên chờ kiểm duyệt; không phải quan hệ pháp lý đã xác nhận.</AlertDescription>
                  </Alert>
                )}
              </TabsContent>

              <TabsContent value="vectors" className="space-y-4">
                <AvailabilityNotice section={detail.vectors} />
                <Alert>
                  <ShieldCheck className="h-4 w-4" />
                  <AlertTitle>Đối chiếu chỉ đọc</AlertTitle>
                  <AlertDescription>Trang này không chạy embedding, re-index hoặc dọn vector.</AlertDescription>
                </Alert>
                <div className="grid gap-4 md:grid-cols-3">
                  <SummaryBox label="Chunk dự kiến" value={detail.vectors.expected ?? detail.structure.chunk_count} />
                  {Object.entries(detail.vectors.collections || {}).map(([name, counts]) => (
                    <SummaryBox key={name} label={name === 'fast' ? 'Kho nhanh hiện có' : 'Kho mở rộng hiện có'} value={counts.present ?? '—'} hint={`${counts.missing ?? '—'} thiếu`} />
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="faq" className="space-y-4">
                <AvailabilityNotice section={detail.faq_impacts} />
                {detail.faq_impacts.status === 'available' && (detail.faq_impacts.items || []).length === 0 && (
                  <p className="text-sm text-muted-foreground">Không có FAQ bị ảnh hưởng.</p>
                )}
              </TabsContent>

              <TabsContent value="versions" className="space-y-4">
                <AvailabilityNotice section={detail.versions} />
                <Card>
                  <CardHeader><CardTitle>Phiên bản hiện có</CardTitle></CardHeader>
                  <CardContent className="text-sm">{detail.versions.legacy_version || document?.version || 'Chưa có metadata phiên bản'}</CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="audit" className="space-y-4">
                <AvailabilityNotice section={detail.audit} />
                <div className="space-y-3">
                  {(detail.audit.items || []).map((item) => (
                    <Card key={item.id || `${item.action}-${item.created}`}>
                      <CardContent className="p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="font-medium">{item.action || 'Sự kiện quản trị'}</div>
                          <div className="text-xs text-muted-foreground">{dateLabel(item.created)}</div>
                        </div>
                        <div className="mt-1 text-sm text-muted-foreground">{item.actor_role || 'unknown'} · {item.actor_user || 'Không rõ người thực hiện'}</div>
                        {item.reason && <p className="mt-2 text-sm">{item.reason}</p>}
                      </CardContent>
                    </Card>
                  ))}
                  {detail.audit.status === 'available' && (detail.audit.items || []).length === 0 && (
                    <p className="text-sm text-muted-foreground">Chưa có audit gắn trực tiếp với văn bản này.</p>
                  )}
                </div>
              </TabsContent>
            </Tabs>
          )}
        </div>
      </div>
    </AppShell>
  )
}

function SummaryBox({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  )
}
