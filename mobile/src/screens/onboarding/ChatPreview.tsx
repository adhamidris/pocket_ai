import React, { useMemo } from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { hapticLight } from '@/utils/haptics'

export const ChatPreview: React.FC<{ onStart?: () => void }> = ({ onStart }) => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const scenario = useMemo(() => (t<any>('demo.scenarios', { returnObjects: true }) as any[])[0], [t])
  const messages = scenario?.conversation?.slice(0, 3) || []
  return (
    <View style={{ flex: 1, padding: 24, alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: '100%', borderRadius: 16, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card, padding: 12 }}>
        {messages.map((m: any, idx: number) => (
          <View key={idx} style={{ alignItems: m.isBot ? 'flex-start' : 'flex-end', marginVertical: 6 }}>
            <View style={{ maxWidth: '80%', padding: 10, borderRadius: 16, backgroundColor: m.isBot ? theme.color.card : theme.color.primary }}>
              <Text style={{ color: m.isBot ? theme.color.cardForeground : '#fff' }}>{m.text}</Text>
            </View>
          </View>
        ))}
      </View>
      {onStart ? (
        <View style={{ marginTop: 24 }}>
          <Button title={t('common.getStarted') || 'Get started'} onPress={() => { hapticLight(); onStart() }} />
        </View>
      ) : null}
    </View>
  )
}
