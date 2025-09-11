import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, Alert, Switch, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import HoursGrid from '../../components/automations/HoursGrid'
import HolidayPicker from '../../components/automations/HolidayPicker'
import { BusinessCalendar, BusinessHoursDay } from '../../types/automations'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import { track } from '../../lib/analytics'

export interface BusinessHoursParams {
  calendar: BusinessCalendar
  pauseOutsideHours?: boolean
  onApply?: (calendar: BusinessCalendar, opts: { syncToSla: boolean }) => void
}

const validateTime = (s: string) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s)
const toMinutes = (s: string) => parseInt(s.slice(0, 2)) * 60 + parseInt(s.slice(3, 5))

const hasOverlaps = (day: BusinessHoursDay) => {
  const ranges = day.ranges
    .filter((r) => validateTime(r.start) && validateTime(r.end) && toMinutes(r.start) < toMinutes(r.end))
    .map((r) => ({ a: toMinutes(r.start), b: toMinutes(r.end) }))
    .sort((x, y) => x.a - y.a)
  for (let i = 1; i < ranges.length; i++) {
    if (ranges[i].a < ranges[i - 1].b) return true
  }
  return false
}

const BusinessHoursScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params: BusinessHoursParams = route.params || {}

  const [tz, setTz] = React.useState(params.calendar?.timezone || 'UTC')
  const [cal, setCal] = React.useState<BusinessCalendar>(params.calendar)
  const [syncToSla, setSyncToSla] = React.useState<boolean>(params.pauseOutsideHours ?? true)
  const [offline, setOffline] = React.useState(false)

  const setClosedToday = (d: number, closed: boolean) => {
    setCal((c) => ({
      ...c,
      days: c.days.map((dd) => dd.day === d ? { ...dd, open: !closed, ranges: closed ? [] : dd.ranges } : dd)
    }))
  }

  const onSave = () => {
    // Validate
    if (!tz.trim()) { Alert.alert('Timezone required'); return }
    const next: BusinessCalendar = { ...cal, timezone: tz.trim() }
    for (const d of next.days) {
      // invalid ranges
      for (const r of d.ranges) {
        if (!validateTime(r.start) || !validateTime(r.end) || toMinutes(r.start) >= toMinutes(r.end)) {
          Alert.alert('Invalid hours', `Check ${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.day]} ranges`)
          return
        }
      }
      if (hasOverlaps(d)) {
        Alert.alert('Overlap detected', `Adjust overlapping ranges on ${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.day]}`)
        return
      }
    }
    try { params.onApply && params.onApply(next, { syncToSla }); track('hours.save', { timezone: next.timezone }) } catch {}
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Business Hours</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {offline && <OfflineBanner visible />}
        {/* Timezone */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Timezone</Text>
          <TextInput value={tz} onChangeText={setTz} placeholder="e.g., UTC or America/New_York" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Timezone" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>

        {/* Closed today toggles */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Closed today</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {cal.days.map((d) => (
              <View key={d.day} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.mutedForeground }}>{['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.day]}</Text>
                <Switch value={!d.open} onValueChange={(v) => setClosedToday(d.day, v)} accessibilityLabel={`Closed ${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.day]}`} />
              </View>
            ))}
          </View>
        </View>

        {/* Hours grid */}
        <View style={{ marginBottom: 16 }}>
          <HoursGrid calendar={cal} onChange={setCal} />
        </View>

        {/* Holidays */}
        <View style={{ marginBottom: 16 }}>
          <HolidayPicker holidays={cal.holidays} onChange={(h) => setCal((c) => ({ ...c, holidays: h }))} />
        </View>

        {/* SLA sync */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Sync pause-outside-hours flag in SLA</Text>
          <Switch value={syncToSla} onValueChange={setSyncToSla} accessibilityLabel="Sync to SLA" />
        </View>

        {/* Security & Privacy */}
        <TouchableOpacity onPress={() => navigation.navigate('CRM', { screen: 'PrivacyCenter' })} accessibilityLabel="Open Security & Privacy Center" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Open Security & Privacy Center</Text>
        </TouchableOpacity>

        {/* Save */}
        <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
          <TouchableOpacity onPress={onSave} accessibilityLabel="Save Hours" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save Hours</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default BusinessHoursScreen


