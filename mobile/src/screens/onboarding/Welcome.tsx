import React from 'react'
import { View, Text } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '@/providers/ThemeProvider'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { hapticLight } from '@/utils/haptics'

export const Welcome: React.FC<{ onNext: () => void; onSkip: () => void }> = ({ onNext, onSkip }) => {
  const { theme } = useTheme()
  const { t } = useTranslation()
  return (
    <LinearGradient pointerEvents="box-none" colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ flex: 1 }}>
      <View style={{ flex: 1, padding: 24, alignItems: 'center', justifyContent: 'center' }}>
        <Text style={{ color: '#fff', fontSize: 36, fontWeight: '700', textAlign: 'center' }}>{t('hero.title')}</Text>
        <Text style={{ color: 'rgba(255,255,255,0.9)', marginTop: 8, textAlign: 'center' }}>{t('hero.subtitle')}</Text>
        <View style={{ marginTop: 24, width: '100%', gap: 12 }}>
          <Button title="Continue" variant="hero" size="lg" onPress={() => { hapticLight(); onNext() }} />
          <Button title="Skip" variant="outline" onPress={() => { hapticLight(); onSkip() }} />
        </View>
      </View>
    </LinearGradient>
  )
}
