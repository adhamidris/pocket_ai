import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, I18nManager } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import i18n from '../../i18n'

const pseudo = (s: string) => {
  const map: Record<string, string> = { a: 'á', e: 'ë', i: 'ï', o: 'õ', u: 'û', A: 'Â', E: 'Ë', I: 'Ï', O: 'Ö', U: 'Û' }
  return '⟦ ' + s.split('').map(ch => map[ch] || ch).join('') + ' ⟧⟦⟦'
}

export const I18nAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [locale, setLocale] = React.useState<string>(i18n.language)
  const [usePseudo, setUsePseudo] = React.useState<boolean>(false)
  const [rtl, setRtl] = React.useState<boolean>(I18nManager.isRTL)
  const [now, setNow] = React.useState<Date>(new Date())

  const setLang = async (lng: string) => {
    await i18n.changeLanguage(lng)
    setLocale(lng)
    setUsePseudo(false)
  }

  const toggleRTL = async () => {
    const next = !rtl
    setRtl(next)
    try { I18nManager.allowRTL(next); I18nManager.forceRTL(next) } catch {}
  }

  const t = (key: string): string => {
    const txt = i18n.t(key) as string
    return usePseudo ? pseudo(txt) : txt
  }

  const formatted = new Intl.DateTimeFormat(locale === 'pseudo' ? 'en' : locale, { dateStyle: 'full', timeStyle: 'short' }).format(now)

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>I18n & RTL Audit</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <Chip label="EN" active={locale === 'en'} onPress={() => setLang('en')} />
          <Chip label="AR" active={locale === 'ar'} onPress={() => setLang('ar')} />
          <Chip label="FR" active={locale === 'fr'} onPress={() => setLang('fr')} />
          <Chip label="Pseudo" active={usePseudo} onPress={() => setUsePseudo(v => !v)} />
          <Chip label={rtl ? 'RTL' : 'LTR'} active={rtl} onPress={toggleRTL} />
          <Chip label="Now" active={false} onPress={() => setNow(new Date())} />
        </View>
      </View>
      <View style={{ paddingHorizontal: 16, gap: 16 }}>
        <Card title="Nav Labels">
          <Text style={{ color: theme.color.cardForeground }}>{t('nav.dashboard')} · {t('nav.conversations')} · {t('nav.crm')} · {t('nav.agents')} · {t('nav.settings')}</Text>
        </Card>
        <Card title="Common Actions">
          <Text style={{ color: theme.color.cardForeground }}>{t('common.continue')} · {t('common.save')} · {t('common.cancel')} · {t('common.edit')} · {t('common.delete')}</Text>
        </Card>
        <Card title="Date/Time Format">
          <Text style={{ color: theme.color.cardForeground }}>{formatted}</Text>
        </Card>
        <Card title="Long Text (clipping check)">
          <Text style={{ color: theme.color.cardForeground }} numberOfLines={0}>{t('onboarding.welcome.subtitle')}</Text>
        </Card>
      </View>
    </SafeAreaView>
  )
}

const Chip: React.FC<{ label: string; active: boolean; onPress: () => void }> = ({ label, active, onPress }) => (
  <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: active ? '#0ea5e9' : '#ccc', backgroundColor: active ? '#0ea5e933' : 'transparent' }}>
    <Text style={{ color: active ? '#0369a1' : '#333', fontWeight: '700' }}>{label}</Text>
  </TouchableOpacity>
)

const Card: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => {
  const { theme } = useTheme()
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 8 }}>{title}</Text>
      {children}
    </View>
  )
}

export default I18nAudit


