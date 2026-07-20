import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

# Create FAQAccordion component
component = '''"""
FAQ Accordion Component - Hiển thị câu hỏi thường gặp dạng collapsible
"""

import { useState } from "react"
import { ChevronDown, ChevronUp, HelpCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { FaqItem } from "@/lib/types/faq"

interface FAQAccordionProps {
  faqs: FaqItem[]
}

export function FAQAccordion({ faqs }: FAQAccordionProps) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  if (!faqs || faqs.length === 0) return null

  return (
    <div className="space-y-3 mt-4">
      <div className="flex items-center gap-2 text-primary">
        <HelpCircle className="h-5 w-5" />
        <h3 className="text-base font-semibold">Câu hỏi thường gặp</h3>
        <Badge variant="secondary" className="ml-auto text-xs">
          {faqs.length} mục
        </Badge>
      </div>

      <div className="space-y-2">
        {faqs.map((faq, idx) => (
          <div
            key={faq.id}
            className="rounded-lg border bg-card transition-all hover:border-primary/30"
          >
            <button
              onClick={() => setOpenIndex(openIndex === idx ? null : idx)}
              className="w-full flex items-start justify-between p-3 text-left gap-2"
              aria-expanded={openIndex === idx}
            >
              <div className="flex-1 min-w-0">
                <p className="font-medium text-sm leading-relaxed">{faq.question}</p>
                {faq.domain && (
                  <Badge variant="outline" className="mt-1 text-[10px]">
                    {faq.domain}
                  </Badge>
                )}
              </div>
              {openIndex === idx ? (
                <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
            </button>

            {openIndex === idx && (
              <div className="px-3 pb-3 pt-0 space-y-2 border-t border-muted/50">
                <div className="pt-2 text-sm text-muted-foreground whitespace-pre-line leading-relaxed">
                  {faq.answer}
                </div>

                {faq.steps && faq.steps.length > 0 && (
                  <div className="bg-muted/30 rounded-md p-2">
                    <p className="text-xs font-semibold mb-1">Các bước thực hiện:</p>
                    <ol className="list-decimal list-inside text-xs space-y-1 text-muted-foreground">
                      {faq.steps.map((step, sIdx) => (
                        <li key={sIdx}>{step}</li>
                      ))}
                    </ol>
                  </div>
                )}

                {faq.form_ids && faq.form_ids.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    <span className="text-xs text-muted-foreground">Biểu mẫu liên quan:</span>
                    {faq.form_ids.map((fid) => (
                      <Badge key={fid} variant="secondary" className="text-[10px]">
                        {fid}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
'''

Path("frontend/src/components/search/FAQAccordion.tsx").write_text(component, encoding="utf-8", newline="\n")
print(f"Created FAQAccordion.tsx ({Path('frontend/src/components/search/FAQAccordion.tsx').stat().st_size} bytes)")

# Create FAQ types
types_code = '''"""
FAQ Types for frontend
"""

export interface FaqItem {
  id: string
  question: string
  answer: string
  steps: string[]
  form_ids: string[]
  domain: string
  ward_scope?: string | null
  review_status: string
  created_at: string
  updated_at: string
  approved_by?: string | null
}

export interface FaqListResponse {
  total: number
  items: FaqItem[]
}
'''

Path("frontend/src/lib/types/faq.ts").write_text(types_code, encoding="utf-8", newline="\n")
print(f"Created faq.ts types ({Path('frontend/src/lib/types/faq.ts').stat().st_size} bytes)")

# Create FAQ API hook
hook_code = '''"""
FAQ API Hook - Fetch và cache FAQ data
"""

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
      const res = await apiClient.get(`/api/faq/?${qs.toString()}`)
      return res.data as FaqListResponse
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useCreateFaq() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (data: Partial<FaqItem>) => {
      const res = await apiClient.post("/api/faq/", data)
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
      const res = await apiClient.post("/api/faq/seed")
      return res.data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FAQ_QUERY_KEY })
    },
  })
}
'''

Path("frontend/src/lib/hooks/use-faq.ts").write_text(hook_code, encoding="utf-8", newline="\n")
print(f"Created use-faq.ts hook ({Path('frontend/src/lib/hooks/use-faq.ts').stat().st_size} bytes)")
