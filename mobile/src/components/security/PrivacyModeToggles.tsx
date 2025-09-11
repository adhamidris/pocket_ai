import React from 'react'
import { View, Text, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { PrivacyMode } from '../../types/security'

export interface PrivacyModeTogglesProps { value: PrivacyMode; onChange: (v: PrivacyMode) => void; testID?: string }

const Row: React.FC<{ label: string; value: boolean; onChange: (v: boolean) => void }>=({ label, value, onChange }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 8 }}>
      <Text style={{ color: theme.color.cardForeground }}>{label}</Text>
      <Switch value={value} onValueChange={onChange} accessibilityLabel={label} />
    </View>
  )
}

const PrivacyModeToggles: React.FC<PrivacyModeTogglesProps> = ({ value, onChange, testID }) => {
  return (
    <View testID={testID}>
      <Row label="Anonymize analytics" value={value.anonymizeAnalytics} onChange={(v) => onChange({ ...value, anonymizeAnalytics: v })} />
      <Row label="Hide contact PII" value={value.hideContactPII} onChange={(v) => onChange({ ...value, hideContactPII: v })} />
      <Row label="Strict logging" value={value.strictLogging} onChange={(v) => onChange({ ...value, strictLogging: v })} />
    </View>
  )
}

export default React.memo(PrivacyModeToggles)


