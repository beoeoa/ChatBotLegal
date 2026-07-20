'use client'

import React, { useEffect, useState } from 'react'
import i18n, { ensureLanguageResource } from '@/lib/i18n'
import { LanguageLoadingOverlay } from '@/components/common/LanguageLoadingOverlay'

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    void ensureLanguageResource(i18n.language).then((language) => {
      if (i18n.language !== language) void i18n.changeLanguage(language)
    })
  }, [])

  // Avoid hydration mismatch by waiting for mount
  if (!mounted) {
    return <div style={{ visibility: 'hidden' }}>{children}</div>
  }

  return (
    <>
      <LanguageLoadingOverlay />
      {children}
    </>
  )
}
