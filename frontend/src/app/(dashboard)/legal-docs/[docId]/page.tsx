import { redirect } from 'next/navigation'

type LegacyViewerProps = {
  params: Promise<{ docId: string }>
  searchParams: Promise<Record<string, string | string[] | undefined>>
}

export default async function LegacyLegalDocViewerPage({ params, searchParams }: LegacyViewerProps) {
  const [{ docId }, queryValues] = await Promise.all([params, searchParams])
  const query = new URLSearchParams()
  for (const key of ['article', 'clause', 'point']) {
    const raw = queryValues[key]
    const value = Array.isArray(raw) ? raw[0] : raw
    if (value) query.set(key, value)
  }
  const suffix = query.toString() ? `?${query.toString()}` : ''
  redirect(`/legal-documents/${encodeURIComponent(docId)}${suffix}`)
}
