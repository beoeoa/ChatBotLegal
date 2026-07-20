'use client'

import { useEffect, useState } from 'react'
import { Card } from '@/components/ui/card'
import { getConfig } from '@/lib/config'
import { Badge } from '@/components/ui/badge'
import { useTranslation } from '@/lib/hooks/use-translation'

export function SystemInfo() {
  const { t } = useTranslation()
  const [config, setConfig] = useState<{
    version: string
    latestVersion?: string | null
    hasUpdate?: boolean
    apiUrl?: string
    apiUrlSource?: 'runtime' | 'environment' | 'fallback' | 'override'
    deploymentMode?: 'auto' | 'local' | 'custom'
    dbStatus?: 'online' | 'offline'
  } | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const cfg = await getConfig()
        setConfig(cfg)
      } catch (error) {
        console.error('Failed to load config:', error)
      } finally {
        setIsLoading(false)
      }
    }

    loadConfig()
  }, [])

  if (isLoading) {
    return (
      <Card className="p-6">
        <div className="space-y-4">
          <h2 className="text-xl font-semibold">{t('advanced.systemInfo')}</h2>
          <div className="text-sm text-muted-foreground">{t('common.loading')}</div>
        </div>
      </Card>
    )
  }

  return (
    <Card className="p-6">
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">{t('advanced.systemInfo')}</h2>

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-4">
              <span className="text-sm font-medium">URL API</span>
            <div className="flex flex-col items-end gap-1 text-right">
              <Badge variant="outline" className="font-mono text-xs">{config?.apiUrl || t('advanced.kh?ng r?')}</Badge>
              <span className="text-xs text-muted-foreground">{config?.apiUrlSource || t('advanced.kh?ng r?')}</span>
            </div>
          </div>

          <div className="flex items-center justify-between gap-4">
              <span className="text-sm font-medium">Chế độ triển khai</span>
              <Badge variant="outline">{config?.deploymentMode === 'auto' ? 'Tự động' : config?.deploymentMode === 'local' ? 'Cục bộ' : config?.deploymentMode === 'custom' ? 'Tùy chỉnh' : t('advanced.unknown')}</Badge>
          </div>

          <div className="flex items-center justify-between gap-4">
              <span className="text-sm font-medium">Cơ sở dữ liệu</span>
            <Badge variant={config?.dbStatus === 'offline' ? 'destructive' : 'outline'}>
              {config?.dbStatus || t('advanced.kh?ng r?')}
            </Badge>
          </div>

          <div className="flex items-center justify-between gap-4">
            <span className="text-sm font-medium">{t('advanced.currentVersion')}</span>
            <Badge variant="outline">{config?.version || t('advanced.kh?ng r?')}</Badge>
          </div>

          {config?.latestVersion && (
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm font-medium">{t('advanced.latestVersion')}</span>
              <Badge variant="outline">{config.latestVersion}</Badge>
            </div>
          )}

          <div className="flex items-center justify-between gap-4">
            <span className="text-sm font-medium">{t('advanced.status')}</span>
            {config?.hasUpdate ? (
              <Badge variant="destructive">
                {t('advanced.updateAvailable').replace('{version}', config.latestVersion || '')}
              </Badge>
            ) : config?.latestVersion ? (
              <Badge variant="outline" className="text-green-600 border-green-600">
                {t('advanced.upToDate')}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-muted-foreground">
                {t('advanced.kh?ng r?')}
              </Badge>
            )}
          </div>

          {config?.hasUpdate && (
            <div className="pt-2 border-t">
              <a
                href="https://github.com/lfnovo/open-notebook"
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-primary hover:underline inline-flex items-center gap-1"
              >
                {t('advanced.viewOnGithub')}
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                  />
                </svg>
              </a>
            </div>
          )}

          {!config?.latestVersion && config?.version && (
            <div className="pt-2 text-xs text-muted-foreground">
              {t('advanced.updateCheckFailed')}
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}
