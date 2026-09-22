"use client"
import { useParams, useSearchParams } from 'next/navigation'
import { AppShell } from '@/components/layout/AppShell'
import { LegalDocumentViewer } from '@/components/legal/LegalDocumentViewer'
export default function LegalDocumentViewerPage() {
  const params = useParams<{id: string}>()
  const search = useSearchParams()
  const docId = decodeURIComponent(String(params?.id || '')).replace(/^legal:/, '')
  return <AppShell><LegalDocumentViewer docId={docId} lawNumber={search.get('law_number') || ''} articleParam={search.get('article') || ''}
    clauseParam={search.get('clause') || ''} pointParam={search.get('point') || ''} /></AppShell>
}
