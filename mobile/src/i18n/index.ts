import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import * as Localization from 'expo-localization'
import en from './en'
import ar from './ar'

export const resources = { en: { translation: en }, ar: { translation: ar } } as const

i18n
  .use(initReactI18next)
  .init({
    compatibilityJSON: 'v4',
    resources,
    lng: Localization.getLocales()[0]?.languageCode === 'ar' ? 'ar' : 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false }
  })

export default i18n

