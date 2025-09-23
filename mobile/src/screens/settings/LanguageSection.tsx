import React, { useMemo, useState } from 'react'
import { View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Modal } from '../../components/ui/Modal'
import { Globe, Check, Flag } from 'lucide-react-native'
import * as Localization from 'expo-localization'
import i18n from '../../i18n'

type Lang = { code: string; label: string; supported: boolean }

const countryOptions = [
  { value: 'United States', flag: '🇺🇸' },
  { value: 'United Kingdom', flag: '🇬🇧' },
  { value: 'United Arab Emirates', flag: '🇦🇪' },
  { value: 'Saudi Arabia', flag: '🇸🇦' },
  { value: 'Egypt', flag: '🇪🇬' },
  { value: 'France', flag: '🇫🇷' },
  { value: 'Germany', flag: '🇩🇪' },
  { value: 'Spain', flag: '🇪🇸' },
  { value: 'Italy', flag: '🇮🇹' },
  { value: 'Other', flag: '🌐' },
]

const allLanguages: Lang[] = [
  { code: 'en', label: 'English', supported: true },
  { code: 'ar', label: 'Arabic', supported: true },
  { code: 'fr', label: 'French', supported: false },
  { code: 'de', label: 'German', supported: false },
  { code: 'es', label: 'Spanish', supported: false },
  { code: 'it', label: 'Italian', supported: false },
]

const getRecommended = (country: string): Lang[] => {
  const lower = country.toLowerCase()
  if (['united arab emirates', 'saudi arabia', 'egypt'].some(c => lower.includes(c))) {
    return [
      allLanguages.find(l => l.code === 'ar')!,
      allLanguages.find(l => l.code === 'en')!,
    ]
  }
  if (lower.includes('france')) return [allLanguages.find(l => l.code === 'fr')!, allLanguages.find(l => l.code === 'en')!]
  if (lower.includes('germany')) return [allLanguages.find(l => l.code === 'de')!, allLanguages.find(l => l.code === 'en')!]
  if (lower.includes('spain')) return [allLanguages.find(l => l.code === 'es')!, allLanguages.find(l => l.code === 'en')!]
  if (lower.includes('italy')) return [allLanguages.find(l => l.code === 'it')!, allLanguages.find(l => l.code === 'en')!]
  return [allLanguages.find(l => l.code === 'en')!, allLanguages.find(l => l.code === 'ar')!]
}

export const LanguageSection: React.FC = () => {
  const { theme } = useTheme()
  const defaultCountry = useMemo(() => {
    const locs = (Localization as any).getLocales?.() || []
    const region = (locs[0]?.regionCode || '').toUpperCase()
    switch (region) {
      case 'GB': return 'United Kingdom'
      case 'AE': return 'United Arab Emirates'
      case 'SA': return 'Saudi Arabia'
      case 'EG': return 'Egypt'
      case 'FR': return 'France'
      case 'DE': return 'Germany'
      case 'ES': return 'Spain'
      case 'IT': return 'Italy'
      case 'US': default: return 'United States'
    }
  }, [])
  const [country, setCountry] = useState(defaultCountry)
  const [showCountryModal, setShowCountryModal] = useState(false)

  const recommended = getRecommended(country)
  const currentLang = i18n.language === 'ar' ? 'ar' : 'en'

  const LangRow: React.FC<{ lang: Lang }> = ({ lang }) => (
    <TouchableOpacity
      onPress={() => {
        if (!lang.supported) {
          Alert.alert('Coming soon', `${lang.label} will be available soon.`)
          return
        }
        i18n.changeLanguage(lang.code)
      }}
      style={{
        flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
        paddingVertical: 10, paddingHorizontal: 12,
        borderRadius: theme.radius.md,
        backgroundColor: (currentLang === lang.code) ? (theme.dark ? theme.color.secondary : theme.color.card) : 'transparent'
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <Globe size={16} color={theme.color.primary as any} />
        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>{lang.label}</Text>
        {!lang.supported && (
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>(Coming soon)</Text>
        )}
      </View>
      {currentLang === lang.code && (
        <Check size={16} color={theme.color.primary as any} />
      )}
    </TouchableOpacity>
  )

  return (
    <View>
      {/* Country */}
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Region</Text>
        <TouchableOpacity
          onPress={() => setShowCountryModal(true)}
          style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: theme.radius.md, paddingHorizontal: 12, paddingVertical: 10 }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <Flag size={16} color={theme.color.mutedForeground as any} />
            <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>{country}</Text>
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Change</Text>
        </TouchableOpacity>
      </Card>

      {/* Recommended languages */}
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Recommended</Text>
        <View style={{ gap: 6 }}>
          {recommended.map(l => <LangRow key={`rec-${l.code}`} lang={l} />)}
        </View>
      </Card>

      {/* All languages */}
      <Card variant="flat" style={{ backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>All Languages</Text>
        <View style={{ gap: 6 }}>
          {allLanguages.map(l => <LangRow key={`all-${l.code}`} lang={l} />)}
        </View>
      </Card>

      {/* Country modal */}
      <Modal visible={showCountryModal} onClose={() => setShowCountryModal(false)} title="Select country" size="sm" autoHeight>
        <ScrollView style={{ maxHeight: 360 }} contentContainerStyle={{ paddingVertical: 4 }}>
          {countryOptions.map((opt) => {
            const selected = country === opt.value
            return (
              <TouchableOpacity
                key={opt.value}
                onPress={() => { setCountry(opt.value); setShowCountryModal(false) }}
                style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 10, paddingHorizontal: 12, borderRadius: theme.radius.md, backgroundColor: selected ? (theme.dark ? theme.color.secondary : theme.color.card) : 'transparent', marginBottom: 4 }}
              >
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                  <Text style={{ fontSize: 16 }}>{opt.flag}</Text>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>{opt.value}</Text>
                </View>
                {selected && <Check size={16} color={theme.color.primary as any} />}
              </TouchableOpacity>
            )
          })}
        </ScrollView>
      </Modal>
    </View>
  )
}

