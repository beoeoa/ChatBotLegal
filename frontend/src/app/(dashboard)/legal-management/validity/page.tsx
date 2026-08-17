'use client'

import Link from 'next/link'
import { ArrowLeft, ShieldCheck } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { LegalValiditySyncPanel } from '@/components/legal-import/LegalValiditySyncPanel'
import { Button } from '@/components/ui/button'

export default function LegalValidityManagementPage() {
  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-auto bg-muted/20">
        <div className="mx-auto max-w-[1500px] space-y-6 p-4 pt-16 md:p-8 md:pt-8">
          <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-primary">
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                Kho văn bản pháp luật
              </div>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight">Văn bản cần kiểm tra hiệu lực</h1>
              <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
                Đối chiếu thông tin với nguồn chính thức và ghi nhận kết quả xử lý.
              </p>
            </div>
            <Button variant="outline" asChild>
              <Link href="/legal-management"><ArrowLeft className="mr-2 h-4 w-4" />Quay lại kho văn bản</Link>
            </Button>
          </header>
          <LegalValiditySyncPanel />
        </div>
      </div>
    </AppShell>
  )
}
