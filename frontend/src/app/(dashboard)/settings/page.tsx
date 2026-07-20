'use client'

import { useEffect, useState } from 'react'
import { AppShell } from '@/components/layout/AppShell'
import { SettingsForm } from './components/SettingsForm'
import { useSettings } from '@/lib/hooks/use-settings'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getConfig, getDeploymentModeOverride, setDeploymentModeOverride } from '@/lib/config'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export default function SettingsPage() {
  const { t } = useTranslation()
  const { refetch } = useSettings()
  const [apiUrl, setApiUrl] = useState('')
  const [apiUrlSource, setApiUrlSource] = useState<'runtime' | 'environment' | 'fallback' | 'override' | ''>('')
  const [deploymentMode, setDeploymentMode] = useState<'auto' | 'local' | 'custom'>('auto')
  const [customUrl, setCustomUrl] = useState('')
  const [dbStatus, setDbStatus] = useState<'online' | 'offline' | ''>('')

  useEffect(() => {
    const load = async () => {
      try {
        const cfg = await getConfig()
        setApiUrl(cfg.apiUrl)
        setApiUrlSource(cfg.apiUrlSource)
        setDeploymentMode(getDeploymentModeOverride())
        setDbStatus(cfg.dbStatus || '')
        if (typeof window !== 'undefined') {
          setCustomUrl(localStorage.getItem('api-url-override') || '')
        }
      } catch {
        setApiUrl('')
      }
    }
    void load()
  }, [])

  const applyMode = () => {
    setDeploymentModeOverride(deploymentMode, customUrl)
    void refetch()
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <div className="max-w-4xl space-y-6">
            <div className="flex items-center gap-4 mb-6">
              <h1 className="text-2xl font-bold">{t('navigation.settings')}</h1>
              <Button variant="outline" size="sm" onClick={() => refetch()}>
                <RefreshCw className="h-4 w-4" />
              </Button>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Chế độ triển khai</CardTitle>
                <CardDescription>
                  Chọn chế độ điều khiển API cho bảng điều khiển. Tự động giữ nguyên cơ chế chạy hiện tại.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Chế độ</Label>
                    <Select value={deploymentMode} onValueChange={(v) => setDeploymentMode(v as 'auto' | 'local' | 'custom')}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">Tự động</SelectItem>
                        <SelectItem value="local">Cục bộ</SelectItem>
                        <SelectItem value="custom">Tùy chỉnh</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>URL API hiện tại</Label>
                    <Input value={apiUrl || '?'} readOnly />
                  </div>
                  <div className="space-y-2">
                    <Label>Nguồn URL</Label>
                    <Input value={apiUrlSource || '?'} readOnly />
                  </div>
                  <div className="space-y-2">
                    <Label>Cơ sở dữ liệu</Label>
                    <Input value={dbStatus || 'không rõ'} readOnly />
                  </div>
                </div>

                {deploymentMode === 'custom' && (
                  <div className="space-y-2">
                    <Label>URL API tùy chỉnh</Label>
                    <Input
                      value={customUrl}
                      onChange={(e) => setCustomUrl(e.target.value)}
                      placeholder="http://localhost:5055"
                    />
                  </div>
                )}

                <div className="flex justify-end">
                  <Button type="button" onClick={applyMode}>Áp dụng</Button>
                </div>
              </CardContent>
            </Card>

            <SettingsForm />
          </div>
        </div>
      </div>
    </AppShell>
  )
}


