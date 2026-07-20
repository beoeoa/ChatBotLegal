import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorKey } from '@/lib/utils/error-handler'
import { searchApi } from '@/lib/api/search'
import type { SearchRequest, SearchResponse } from '@/lib/types/search'

/**
 * Search mutation with request cancellation, stale-result protection and a
 * small debounce helper. Search questions/content are never sent to telemetry.
 */
export function useSearch() {
  const { t } = useTranslation()
  const abortRef = useRef<AbortController | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const requestIdRef = useRef(0)
  const [data, setData] = useState<SearchResponse | undefined>()
  const [error, setError] = useState<Error | null>(null)
  const [isPending, setIsPending] = useState(false)

  const mutate = useCallback(async (params: SearchRequest) => {
    if ((params.query || '').trim().length < 2) {
      const emptyResponse: SearchResponse = {
        results: [],
        total_count: 0,
        search_type: 'legal'
      }
      setData(emptyResponse)
      setIsPending(false)
      setError(null)
      return emptyResponse
    }

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const requestId = ++requestIdRef.current
    setIsPending(true)
    setError(null)
    try {
      const response = await searchApi.search(params, controller.signal)
      if (requestId !== requestIdRef.current) return response
      const processedResults = response.results
        .map(result => ({ ...result, final_score: result.relevance ?? result.similarity ?? result.score ?? 0 }))
        .sort((a, b) => b.final_score - a.final_score)
      const processed = { ...response, results: processedResults }
      setData(processed)
      return processed
    } catch (caught) {
      if (controller.signal.aborted) return undefined
      const normalized = caught instanceof Error ? caught : new Error('Kh?ng t?m th?y k?t qu?.')
      if (requestId === requestIdRef.current) {
        setError(normalized)
        toast.error(t('apiErrors.searchFailed'), { description: t(getApiErrorKey(normalized.message)) })
      }
      return undefined
    } finally {
      if (requestId === requestIdRef.current) setIsPending(false)
    }
  }, [t])

  const mutateDebounced = useCallback((params: SearchRequest, delayMs = 300) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => { void mutate(params) }, Math.max(150, delayMs))
  }, [mutate])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    if (debounceRef.current) clearTimeout(debounceRef.current)
    setIsPending(false)
  }, [])

  useEffect(() => () => {
    abortRef.current?.abort()
    if (debounceRef.current) clearTimeout(debounceRef.current)
  }, [])

  return { data, error, isPending, mutate, mutateDebounced, cancel }
}
