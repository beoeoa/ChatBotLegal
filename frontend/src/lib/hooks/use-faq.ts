import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import type { FaqItem, FaqListResponse } from "@/lib/types/faq"
import { apiClient } from "@/lib/api/client"

const FAQ_QUERY_KEY = ["faq"]

export function useFaqs(params?: { domain?: string; ward_scope?: string }) {
  return useQuery<FaqListResponse>({
    queryKey: [...FAQ_QUERY_KEY, params],
    queryFn: async () => {
      const qs = new URLSearchParams()
      if (params?.domain) qs.set("domain", params.domain)
      if (params?.ward_scope) qs.set("ward_scope", params.ward_scope)
      const res = await apiClient.get(`/faq?${qs.toString()}`)
      return res.data as FaqListResponse
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useCreateFaq() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (data: Partial<FaqItem>) => {
      const res = await apiClient.post("/faq", data)
      return res.data as FaqItem
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FAQ_QUERY_KEY })
    },
  })
}

export function useSeedFaqs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const res = await apiClient.post("/faq/seed")
      return res.data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FAQ_QUERY_KEY })
    },
  })
}
