import React, { useEffect, useMemo, useState } from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, DeviceEventEmitter } from 'react-native'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { Coachmark } from '../../components/help/Coachmark'
import { Spotlight } from '../../components/help/Spotlight'
import { getAnchor, subscribeAnchors } from '../../lib/helpAnchors'
import { Tour, TourStep } from '../../types/help'
import { track } from '../../lib/analytics'

type Params = { tour: Tour }

export const TourRunner: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const tour = route.params?.tour as Tour
  const [index, setIndex] = useState<number>(0)
  const [anchor, setAnchor] = useState<{ x: number; y: number; width: number; height: number } | null>(null)

  const step: TourStep | undefined = tour?.steps[index]
  React.useEffect(() => { if (tour?.id && index === 0) try { track('help.tour.start', { id: tour.id }) } catch {} }, [tour?.id])

  useEffect(() => {
    const refresh = () => {
      if (!step) return
      const rect = getAnchor(step.anchorTestId)
      setAnchor(rect || null)
    }
    const unsub = subscribeAnchors(refresh)
    const id = setInterval(refresh, 300)
    refresh()
    return () => { unsub(); clearInterval(id) }
  }, [step?.anchorTestId])

  if (!tour || !step) {
    return (
      <SafeAreaView style={{ flex: 1 }} />
    )
  }

  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, top: 0, right: 0, bottom: 0 }}>
      {anchor && (
        <>
          <Spotlight visible targetFrame={anchor} onDismiss={() => {}} />
          <Coachmark
            visible
            anchorFrame={anchor}
            title={step.title}
            body={step.body}
            stepIndex={index}
            stepCount={tour.steps.length}
            onSkip={() => { try { track('help.tour.skip', { id: tour.id }) } catch {}; navigation.goBack() }}
            onNext={() => {
              if (index < tour.steps.length - 1) setIndex(i => i + 1)
              else {
                try { track('help.tour.complete', { id: tour.id }) } catch {}
                navigation.goBack()
                setTimeout(() => DeviceEventEmitter.emit('assistant.open', { text: 'Quiz me on what I just learned in the tour.', persona: 'agent' }), 100)
              }
            }}
          />
        </>
      )}
    </View>
  )
}

export default TourRunner


