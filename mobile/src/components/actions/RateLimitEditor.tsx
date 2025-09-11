import React from 'react'
import { View, Text, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const RateLimitEditor: React.FC<{ value?: { count: number; perSeconds: number }; onChange: (v: { count: number; perSeconds: number }) => void }>
  = ({ value, onChange }) => {
  const { theme } = useTheme()
  const [count, setCount] = React.useState<string>(String(value?.count ?? 3))
  const [secs, setSecs] = React.useState<string>(String(value?.perSeconds ?? 60))
  React.useEffect(() => { onChange({ count: Number(count) || 0, perSeconds: Number(secs) || 0 }) }, [count, secs])
  return (
    <View style={{ gap: 8 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Rate limit</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
          <TextInput value={count} onChangeText={setCount} keyboardType="numeric" placeholder="Count" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
        </View>
        <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
          <TextInput value={secs} onChangeText={setSecs} keyboardType="numeric" placeholder="Per seconds" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
        </View>
      </View>
    </View>
  )
}

export default RateLimitEditor


