import { viVN } from './vi-VN';
import { enUS } from './en-US';

type LocaleValue = string | number | boolean | null | LocaleObject | LocaleValue[]
interface LocaleObject { [key: string]: LocaleValue }

function mergeLocale(base: LocaleObject, locale: LocaleObject): LocaleObject {
  const merged: LocaleObject = { ...base }
  for (const [key, value] of Object.entries(locale)) {
    if (
      value && typeof value === 'object' && !Array.isArray(value) &&
      merged[key] && typeof merged[key] === 'object' && !Array.isArray(merged[key])
    ) {
      merged[key] = mergeLocale(merged[key] as LocaleObject, value as LocaleObject)
    } else {
      merged[key] = value
    }
  }
  return merged
}

const withEnglishFallback = (locale: LocaleObject) =>
  mergeLocale(enUS as LocaleObject, locale) as TranslationKeys

export const resources = {
  // Vietnamese is the product default and is the only locale needed for the
  // first render. Other locale modules are split into on-demand chunks below.
  'vi-VN': { translation: withEnglishFallback(viVN as LocaleObject) },
} as const;

export type TranslationKeys = typeof enUS;

export const legalValidityLabelsVi = {
  status: {
    active: 'Còn hiệu lực',
    not_yet_effective: 'Chưa có hiệu lực',
    expired: 'Hết hiệu lực',
    expired_partial: 'Hết hiệu lực một phần',
    suspended: 'Tạm ngưng hiệu lực',
    suspended_partial: 'Tạm ngưng một phần',
    amended: 'Đã được sửa đổi, bổ sung',
    replaced: 'Đã được thay thế',
    repealed: 'Đã bị bãi bỏ',
    unknown: 'Chưa xác minh được',
  },
  warning: {
    validity_snapshot_stale: 'Dữ liệu xác minh có thể đã cũ',
    validity_snapshot_unavailable: 'Chưa tải được snapshot xác minh hiệu lực',
    partial_scope_unresolved: 'Chưa xác định chính xác phạm vi điều khoản bị ảnh hưởng',
  },
  verifiedAt: 'Xác minh',
  source: 'Nguồn xác minh hiệu lực',
} as const;

export type LanguageCode = 'zh-CN' | 'en-US' | 'zh-TW' | 'pt-BR' | 'ja-JP' | 'it-IT' | 'fr-FR' | 'ru-RU' | 'bn-IN' | 'ca-ES' | 'es-ES' | 'de-DE' | 'pl-PL' | 'vi-VN';

export type Language = {
  code: LanguageCode;
  label: string;
};

export const languages: Language[] = [
  { code: 'vi-VN', label: 'Tiếng Việt' },
  { code: 'en-US', label: 'English' },
  { code: 'ca-ES', label: 'Català' },
  { code: 'zh-CN', label: '简体中文' },
  { code: 'zh-TW', label: '繁體中文' },
  { code: 'pt-BR', label: 'Português' },
  { code: 'ja-JP', label: '日本語' },
  { code: 'it-IT', label: 'Italiano' },
  { code: 'fr-FR', label: 'Français' },
  { code: 'ru-RU', label: 'Русский' },
  { code: 'bn-IN', label: 'বাংলা' },
  { code: 'es-ES', label: 'Español' },
  { code: 'de-DE', label: 'Deutsch' },
  { code: 'pl-PL', label: 'Polski' },
];

const localeLoaders: Record<LanguageCode, () => Promise<unknown>> = {
  'vi-VN': async () => viVN,
  'en-US': async () => (await import('./en-US')).enUS,
  'zh-CN': async () => (await import('./zh-CN')).zhCN,
  'zh-TW': async () => (await import('./zh-TW')).zhTW,
  'pt-BR': async () => (await import('./pt-BR')).ptBR,
  'ja-JP': async () => (await import('./ja-JP')).jaJP,
  'it-IT': async () => (await import('./it-IT')).itIT,
  'fr-FR': async () => (await import('./fr-FR')).frFR,
  'ru-RU': async () => (await import('./ru-RU')).ruRU,
  'bn-IN': async () => (await import('./bn-IN')).bnIN,
  'ca-ES': async () => (await import('./ca-ES')).caES,
  'es-ES': async () => (await import('./es-ES')).esES,
  'de-DE': async () => (await import('./de-DE')).deDE,
  'pl-PL': async () => (await import('./pl-PL')).plPL,
}

export function isLanguageCode(value: string): value is LanguageCode {
  return value in localeLoaders
}

export async function loadLocale(code: LanguageCode): Promise<TranslationKeys> {
  return withEnglishFallback(await localeLoaders[code]() as LocaleObject)
}
