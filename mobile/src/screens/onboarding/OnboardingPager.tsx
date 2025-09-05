import React, { useCallback, useState } from 'react'
import { View } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { Welcome } from './Welcome'
import { SimpleFlow } from './SimpleFlow'
import { ChatPreview } from './ChatPreview'

export const OnboardingPager: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
  const [page, setPage] = useState<number>(0)
  const onSkip = useCallback(async () => {
    await AsyncStorage.setItem('onboardingCompleted', '1')
    onComplete()
  }, [onComplete])
  const onNextWelcome = () => setPage(1)
  const onNextFlow = () => setPage(2)
  const onStart = onSkip

  return (
    <View style={{ flex: 1 }}>
      {page === 0 && <Welcome onNext={onNextWelcome} onSkip={onSkip} />}
      {page === 1 && <SimpleFlow onNext={onNextFlow} />}
      {page === 2 && <ChatPreview onStart={onStart} />}
    </View>
  )
}
