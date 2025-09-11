import React from 'react'
import AsyncStorage from '@react-native-async-storage/async-storage'

export type VoicePrefs = {
  enabled: boolean
  pushToTalk: boolean
  tapToToggle: boolean
  autoplayTts: boolean
}

const KEY = 'settings.voice.v1'
const defaults: VoicePrefs = { enabled: false, pushToTalk: true, tapToToggle: false, autoplayTts: false }

export const useVoiceSettings = () => {
  const [prefs, setPrefs] = React.useState<VoicePrefs>(defaults)
  const [loaded, setLoaded] = React.useState(false)

  React.useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(KEY)
        if (raw) setPrefs({ ...defaults, ...JSON.parse(raw) })
      } catch {}
      setLoaded(true)
    })()
  }, [])

  const update = async (next: Partial<VoicePrefs>) => {
    const merged = { ...prefs, ...next }
    setPrefs(merged)
    try { await AsyncStorage.setItem(KEY, JSON.stringify(merged)) } catch {}
  }

  return { prefs, loaded, update }
}

export default useVoiceSettings


