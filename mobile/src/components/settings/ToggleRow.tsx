import React from 'react'
import { View, Text, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface ToggleRowProps { label: string; value: boolean; onChange: (v: boolean) => void; testID?: string }

const ToggleRow: React.FC<ToggleRowProps> = ({ label, value, onChange, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
      <Text style={{ color: theme.color.cardForeground }}>{label}</Text>
      <Switch value={value} onValueChange={onChange} accessibilityLabel={label} />
    </View>
  )
}

export default React.memo(ToggleRow)


