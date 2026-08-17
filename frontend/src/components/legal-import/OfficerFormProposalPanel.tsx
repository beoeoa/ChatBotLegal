'use client'

import { FormEvent, useCallback, useEffect, useState } from 'react'
import { FileCheck2, RefreshCcw, Send } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { FormReviewCaseV17, legalImportApi } from '@/lib/api/legal-import'

const STATUS_LABELS: Record<string, string> = {
  submitted: 'Đã gửi Admin',
  needs_supplement: 'Cần bổ sung',
  resubmitted: 'Đã gửi lại',
  source_approved: 'Nguồn đã được duyệt',
  legal_enrichment: 'Admin đang hoàn thiện pháp lý',
  ready_for_attestation: 'Chờ xác nhận pháp lý',
  attested: 'Đã xác nhận · chờ phát hành',
  release_candidate: 'Đang kiểm tra phát hành',
  released: 'Đã công khai',
  rejected: 'Đã từ chối',
  withdrawn: 'Đã rút',
}

interface Props {
  domains: string[]
  domainLabels: Record<string, string>
}

export function OfficerFormProposalPanel({ domains, domainLabels }: Props) {
  const [cases, setCases] = useState<FormReviewCaseV17[]>([])
  const [editing, setEditing] = useState<FormReviewCaseV17 | null>(null)
  const [domain, setDomain] = useState(domains[0] || '')
  const [procedureId, setProcedureId] = useState('')
  const [title, setTitle] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [checksum, setChecksum] = useState('')
  const [assetKind, setAssetKind] = useState<'file' | 'eform'>('file')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!domain && domains[0]) setDomain(domains[0])
  }, [domain, domains])

  const load = useCallback(async () => {
    try { setCases(await legalImportApi.myFormGovernanceCases()) }
    catch { /* PostgreSQL workflow may still be intentionally disabled. */ }
  }, [])

  useEffect(() => { void load() }, [load])

  const reset = () => {
    setEditing(null); setProcedureId(''); setTitle(''); setSourceUrl('')
    setChecksum(''); setAssetKind('file'); setNote('')
  }

  const edit = (item: FormReviewCaseV17) => {
    setEditing(item); setDomain(item.domain); setProcedureId(item.procedure_id)
    setTitle(item.title); setSourceUrl(item.current_submission.source_url)
    setChecksum(item.current_submission.source_checksum || '')
    setNote(item.current_submission.note || '')
  }

  const hashFile = async (file?: File) => {
    if (!file) return
    const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
    setChecksum(Array.from(new Uint8Array(digest)).map(value => value.toString(16).padStart(2, '0')).join(''))
    toast.success('Đã tính checksum từ tệp. Tệp không được tự động công khai.')
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!domain || !procedureId.trim() || title.trim().length < 3 || !/^https:\/\//i.test(sourceUrl) || !/^[a-f0-9]{64}$/i.test(checksum)) {
      toast.error('Cần đủ lĩnh vực, mã thủ tục, tên mẫu, URL HTTPS chính thức và checksum SHA-256.')
      return
    }
    setBusy(true)
    const payload = {
      procedure_id: procedureId.trim(), domain, title: title.trim(),
      source_url: sourceUrl.trim(), source_checksum: checksum.toLowerCase(),
      asset_kind: assetKind, note: note.trim(),
    }
    try {
      if (editing) await legalImportApi.supplementFormGovernanceCase(editing.case_id, payload)
      else await legalImportApi.submitFormGovernanceCase(payload)
      toast.success(editing ? 'Đã bổ sung và gửi lại cho Admin.' : 'Đã gửi đề xuất biểu mẫu cho Admin.')
      reset(); await load()
    } catch { toast.error('Không gửi được đề xuất. Hãy kiểm tra đúng lĩnh vực và trạng thái hồ sơ.') }
    finally { setBusy(false) }
  }

  return <Card className="border-primary/20">
    <CardHeader><CardTitle className="flex items-center gap-2"><FileCheck2 className="h-5 w-5" />Đề xuất biểu mẫu chính thức</CardTitle><CardDescription>Biểu mẫu chỉ được công khai sau khi Admin duyệt nguồn, hoàn thiện pháp lý, xác nhận và phát hành.</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <form className="grid gap-3 md:grid-cols-2" onSubmit={submit}>
        <div><Label>Lĩnh vực phụ trách</Label><Select value={domain} onValueChange={setDomain} disabled={Boolean(editing)}><SelectTrigger><SelectValue placeholder="Chọn lĩnh vực" /></SelectTrigger><SelectContent>{domains.map(value => <SelectItem key={value} value={value}>{domainLabels[value] || value}</SelectItem>)}</SelectContent></Select></div>
        <div><Label>Mã thủ tục</Label><Input value={procedureId} onChange={event => setProcedureId(event.target.value)} disabled={Boolean(editing)} placeholder="Mã trên Cổng DVC" /></div>
        <div className="md:col-span-2"><Label>Tên biểu mẫu</Label><Input value={title} onChange={event => setTitle(event.target.value)} /></div>
        <div className="md:col-span-2"><Label>URL nguồn chính thức</Label><Input type="url" value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} placeholder="https://dichvucong.gov.vn/... hoặc https://vbpl.vn/..." /></div>
        <div><Label>Loại biểu mẫu</Label><Select value={assetKind} onValueChange={value => setAssetKind(value as 'file' | 'eform')}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="file">Tệp tải xuống</SelectItem><SelectItem value="eform">Biểu mẫu điện tử</SelectItem></SelectContent></Select></div>
        <div><Label>Tệp đối chiếu để tự tính checksum</Label><Input type="file" onChange={event => void hashFile(event.target.files?.[0])} /></div>
        <div className="md:col-span-2"><Label>Checksum SHA-256</Label><Input value={checksum} onChange={event => setChecksum(event.target.value)} className="font-mono text-xs" /></div>
        <div className="md:col-span-2"><Label>Ghi chú cho Admin</Label><Textarea value={note} onChange={event => setNote(event.target.value)} /></div>
        <div className="flex flex-wrap gap-2 md:col-span-2"><Button type="submit" disabled={busy}><Send className="mr-2 h-4 w-4" />{editing ? 'Gửi lại phần bổ sung' : 'Gửi đề xuất biểu mẫu'}</Button>{editing && <Button type="button" variant="outline" onClick={reset}>Hủy bổ sung</Button>}</div>
      </form>

      <div className="space-y-2"><div className="flex items-center justify-between"><p className="font-medium">Tiến độ đề xuất biểu mẫu</p><Button size="sm" variant="ghost" onClick={() => void load()}><RefreshCcw className="mr-2 h-4 w-4" />Làm mới</Button></div>
        {cases.length === 0 && <p className="text-sm text-muted-foreground">Chưa có đề xuất biểu mẫu trong workflow mới.</p>}
        {cases.map(item => <div key={item.case_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3"><div><p className="font-medium">{item.title}</p><p className="text-xs text-muted-foreground">{item.procedure_id} · lần gửi {item.revision}</p></div><div className="flex items-center gap-2"><Badge variant="outline">{STATUS_LABELS[item.status] || item.status}</Badge>{item.status === 'needs_supplement' && <Button size="sm" onClick={() => edit(item)}>Bổ sung</Button>}</div></div>)}
      </div>
    </CardContent>
  </Card>
}
