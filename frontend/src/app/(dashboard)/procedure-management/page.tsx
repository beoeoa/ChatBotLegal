'use client'

import { ClipboardCheck, ExternalLink, MessageCircleQuestion } from 'lucide-react'
import Link from 'next/link'

import { AppShell } from '@/components/layout/AppShell'
import { FormGovernancePanel } from '@/components/legal-import/FormGovernancePanel'
import { Button } from '@/components/ui/button'

export default function ProcedureManagementPage() {
  return (
    <AppShell>
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 sm:p-6 md:pb-16">
        <div className="mx-auto w-full max-w-7xl space-y-6">
          <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h1 className="flex items-center gap-3 text-xl font-bold md:text-2xl">
                <ClipboardCheck className="h-8 w-8 text-primary" />
                Thủ tục và biểu mẫu
              </h1>
              <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
                Duyệt, xác nhận và phát hành biểu mẫu đúng thủ tục. Văn bản pháp luật được tiếp nhận ở một luồng riêng.
              </p>
            </div>
            <nav aria-label="Liên kết nghiệp vụ liên quan" className="flex flex-wrap gap-2">
              <Button asChild variant="outline">
                <Link href="/procedures">
                  <ExternalLink className="mr-2 h-4 w-4" /> Kho thủ tục công khai
                </Link>
              </Button>
              <Button asChild variant="outline">
                <Link href="/faq-management">
                  <MessageCircleQuestion className="mr-2 h-4 w-4" /> Quản lý thủ tục hành chính
                </Link>
              </Button>
            </nav>
          </header>
          <FormGovernancePanel />
        </div>
      </main>
    </AppShell>
  )
}
