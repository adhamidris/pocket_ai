import { Platform, Linking, Alert } from 'react-native'

export async function rateApp(): Promise<void> {
  let StoreReview: any = null
  try {
    // Dynamically require to avoid hard dependency in dev
    StoreReview = require('expo-store-review')
  } catch {}

  try {
    if (StoreReview?.isAvailableAsync && (await StoreReview.isAvailableAsync())) {
      if (typeof StoreReview.requestReview === 'function') {
        await StoreReview.requestReview()
        return
      }
    }
  } catch {
    // fall through to listing
  }

  // Fallback: open the public store listing if configured
  let iosAppId: string | undefined
  let androidPackage: string | undefined
  try {
    const appConfig = require('../../app.json')
    iosAppId = appConfig?.expo?.ios?.appStoreId
    androidPackage = appConfig?.expo?.android?.package
  } catch {}

  const iosUrl = iosAppId ? `https://apps.apple.com/app/id${iosAppId}?action=write-review` : undefined
  const playUrl = androidPackage ? `market://details?id=${androidPackage}` : undefined
  const playHttpsUrl = androidPackage ? `https://play.google.com/store/apps/details?id=${androidPackage}` : undefined

  const url = Platform.OS === 'ios' ? iosUrl : Platform.OS === 'android' ? playUrl ?? playHttpsUrl : undefined

  if (url) {
    try {
      const supported = await Linking.canOpenURL(url)
      if (supported) {
        await Linking.openURL(url)
        return
      }
      // Android fallback to https if market:// fails
      if (Platform.OS === 'android' && playHttpsUrl) {
        await Linking.openURL(playHttpsUrl)
        return
      }
    } catch {}
  }

  Alert.alert(
    'Rate App',
    'Ratings are not available in this build. Configure App Store/Play Store IDs to enable store listing links.',
    [{ text: 'OK' }]
  )
}

