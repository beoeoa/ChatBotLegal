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

      <div className="grid gap-2 md:grid-cols-2">
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
