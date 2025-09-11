import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../providers/ThemeProvider'
import { FixtureProfile, getProfile, setProfile } from './fixtures'

type BtnProps = { label: string; active: boolean; onPress: () => void }
const Btn: React.FC<BtnProps> = ({ label, active, onPress }) => {
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? '#0ea5e9' : '#ccc', backgroundColor: active ? '#0ea5e933' : 'rgba(0,0,0,0.05)' }}>
      <Text style={{ color: active ? '#0369a1' : '#333', fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  )
}

export const FixtureSwitcher: React.FC = () => {
  const { theme } = useTheme()
  const [profile, setLocal] = React.useState<FixtureProfile>(() => getProfile())
  const profiles: Array<{ id: FixtureProfile; label: string }> = [
    { id: 'baseline', label: 'Baseline' },
    { id: 'highVolume', label: 'High Vol' },
    { id: 'lowData', label: 'Low Data' },
    { id: 'edgeCases', label: 'Edge' },
  ]
  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, top: 0, alignItems: 'center' }}>
      <View style={{ marginTop: 6, paddingHorizontal: 8, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginRight: 4 }}>QA Profile:</Text>
        {profiles.map(p => (
          <Btn key={p.id} label={p.label} active={profile === p.id} onPress={() => { setLocal(p.id); setProfile(p.id) }} />
        ))}
      </View>
    </View>
  )
}

export default FixtureSwitcher


