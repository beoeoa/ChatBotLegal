import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { isLanguageCode, loadLocale, resources, type LanguageCode } from './locales'

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'vi-VN',
    interpolation: {
      escapeValue: false, // react already safes from xss
    },
    react: {
      useSuspense: false,
    },
    detection: {
      order: ['localStorage'],
      caches: ['localStorage'],
    },
  })

export async function ensureLanguageResource(language: string): Promise<LanguageCode> {
  const code = isLanguageCode(language) ? language : 'vi-VN'
  if (!i18n.hasResourceBundle(code, 'translation')) {
    const translation = await loadLocale(code)
    i18n.addResourceBundle(code, 'translation', translation, true, true)
  }
  return code
}

export default i18n
