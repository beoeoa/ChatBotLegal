'use client'

import { AppShell } from '@/components/layout/AppShell'
import { SettingsForm } from './components/SettingsForm'
import { SystemSettingsForm } from './components/SystemSettingsForm'
import { ChatMemorySettings } from './components/ChatMemorySettings'
import { useSettings } from '@/lib/hooks/use-settings'
import { Button } from '@/components/ui/button'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function SettingsPage() {
  const { t } = useTranslation()
  const { refetch } = useSettings()

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 md:p-7">
          <div className="mx-auto max-w-5xl space-y-7">
            <div className="flex flex-col gap-4 border-b border-border/70 pb-5 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="mb-1 text-xs font-bold uppercase tracking-[0.18em] text-primary">Quản trị nền tảng</p>
                <h1 className="font-display text-3xl font-bold text-foreground">{t('navigation.settings')}</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Cấu hình cách hệ thống hiển thị, ghi nhớ hội thoại và lựa chọn model. Mỗi thay đổi được áp dụng có kiểm soát.</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => refetch()} className="shrink-0">
                <RefreshCw className="h-4 w-4" />
                Làm mới cấu hình
              </Button>
            </div>

            <SettingsForm />
            <ChatMemorySettings />
            <SystemSettingsForm showOrganizationUnits={false} />
          </div>
        </div>
      </div>
    </AppShell>
  )
}
