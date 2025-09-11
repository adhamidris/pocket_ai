import React from 'react'
import { DeviceEventEmitter } from 'react-native'

export type PrivacyModes = { anonymizeAnalytics: boolean; hideContactPII: boolean; strictLogging?: boolean }

const DEFAULT: PrivacyModes = { anonymizeAnalytics: false, hideContactPII: false, strictLogging: false }

export const usePrivacyModes = () => {
  const [modes, setModes] = React.useState<PrivacyModes>(DEFAULT)

  React.useEffect(() => {
    const sub = DeviceEventEmitter.addListener('privacy.modes', (vals: PrivacyModes) => {
      setModes((prev) => ({ ...prev, ...(vals || {}) }))
    })
    return () => sub.remove()
  }, [])

  return modes
}

export default usePrivacyModes


