export interface FaqItem {
  id: string
  question: string
  answer: string
  steps?: string[]
  form_ids?: string[]
  domain?: string
  ward_scope?: string | null
  review_status?: string
  created_at?: string
  updated_at?: string
  approved_by?: string | null
}

export interface FaqListResponse {
  total: number
  items: FaqItem[]
}
