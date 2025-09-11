import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import TimezonePicker from '../../components/settings/TimezonePicker'
import LocalePicker from '../../components/settings/LocalePicker'
import { track } from '../../lib/analytics'

export interface LocaleTimezoneValues {
  timezone: string
  locale: string
  dateFormat: 'auto'|'DMY'|'MDY'|'YMD'
  timeFormat: '12h'|'24h'
  rtl: boolean
}

export interface LocaleTimezoneScreenProps {
  initial: LocaleTimezoneValues
  onSave: (v: LocaleTimezoneValues) => void
  onClose?: () => void
}

const formatExample = (d: Date, v: LocaleTimezoneValues): string => {
  const pad = (n: number) => String(n).padStart(2, '0')
  const dd = pad(d.getDate())
  const mm = pad(d.getMonth() + 1)
  const yyyy = String(d.getFullYear())
  const hh24 = pad(d.getHours())
  const hh12n = d.getHours() % 12 || 12
  const hh12 = pad(hh12n)
  const min = pad(d.getMinutes())
  const date = v.dateFormat === 'DMY' ? `${dd}/${mm}/${yyyy}` : v.dateFormat === 'MDY' ? `${mm}/${dd}/${yyyy}` : v.dateFormat === 'YMD' ? `${yyyy}/${mm}/${dd}` : d.toLocaleDateString(v.locale)
  const time = v.timeFormat === '24h' ? `${hh24}:${min}` : `${hh12}:${min} ${d.getHours() >= 12 ? 'PM' : 'AM'}`
  return `${date} ${time} (${v.timezone})`
}

const Row: React.FC<{ label: string }>=({ label, children }) => {
  const { theme } = useTheme()
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>{label}</Text>
      {children}
    </View>
  )
}

const Pill: React.FC<{ active: boolean; onPress: () => void; label: string }>=({ active, onPress, label }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const LocaleTimezoneScreen: React.FC<LocaleTimezoneScreenProps> = ({ initial, onSave, onClose }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [vals, setVals] = React.useState<LocaleTimezoneValues>(initial)

  const save = () => { onSave(vals); try { track('settings.locale.save') } catch {}; onClose && onClose() }

  const example = React.useMemo(() => formatExample(new Date(), vals), [vals])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Locale & Timezone</Text>
            <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24 }}>
          <Row label="Timezone">
            <TimezonePicker value={vals.timezone} onChange={(v) => setVals((s) => ({ ...s, timezone: v }))} />
          </Row>

          <Row label="Locale">
            <LocalePicker value={vals.locale} onChange={(v) => setVals((s) => ({ ...s, locale: v }))} />
          </Row>

          <Row label="Date Format">
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {(['auto','DMY','MDY','YMD'] as const).map((f) => (
                <Pill key={f} label={f} active={vals.dateFormat === f} onPress={() => setVals((s) => ({ ...s, dateFormat: f }))} />
              ))}
            </View>
          </Row>

          <Row label="Time Format">
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {(['12h','24h'] as const).map((f) => (
                <Pill key={f} label={f} active={vals.timeFormat === f} onPress={() => setVals((s) => ({ ...s, timeFormat: f }))} />
              ))}
            </View>
          </Row>

          <Row label="RTL">
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground }}>Enable RTL mirroring</Text>
              <Switch value={vals.rtl} onValueChange={(v) => setVals((s) => ({ ...s, rtl: v }))} accessibilityLabel="RTL toggle" />
            </View>
          </Row>

          {/* Preview */}
          <Row label="Preview">
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>{example}</Text>
            <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
              <View style={{ flexDirection: vals.rtl ? 'row-reverse' : 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View style={{ width: 44, height: 44, borderRadius: vals.rtl ? 8 : 22, backgroundColor: theme.color.muted }} />
                <Text style={{ color: theme.color.cardForeground, flex: 1, textAlign: vals.rtl ? 'right' : 'left', marginHorizontal: 8 }}>Sample text mirrored with RTL</Text>
                <View style={{ width: 44, height: 16, borderRadius: 8, backgroundColor: theme.color.muted }} />
              </View>
            </View>
          </Row>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default LocaleTimezoneScreen


