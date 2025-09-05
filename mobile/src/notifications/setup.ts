import * as Notifications from 'expo-notifications'
import { Platform } from 'react-native'

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
})

export async function initNotifications() {
  // Permissions
  const { status } = await Notifications.getPermissionsAsync()
  if (status !== 'granted') {
    await Notifications.requestPermissionsAsync()
  }
  // Android channel
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'Default',
      importance: Notifications.AndroidImportance.DEFAULT,
      lightColor: '#644EF9',
      enableLights: true,
      showBadge: false,
    })
  }
  // Categories (actions)
  await Notifications.setNotificationCategoryAsync('chat', [
    {
      identifier: 'REPLY',
      buttonTitle: 'Reply',
      options: { opensAppToForeground: true },
      textInput: { submitButtonTitle: 'Send', placeholder: 'Type…' },
    },
    {
      identifier: 'OPEN',
      buttonTitle: 'Open',
      options: { opensAppToForeground: true },
    },
  ])
}

export async function scheduleTestNotification() {
  await Notifications.scheduleNotificationAsync({
    content: {
      title: 'New chat waiting — Nancy replied',
      body: 'Tap to open or reply directly.',
      categoryIdentifier: 'chat',
      data: { route: 'chat', agentId: 'nancy' },
    },
    trigger: null, // immediate
  })
}

export function addNotificationResponseListener(onRoute: (route?: string, extras?: any) => void) {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    const action = response.actionIdentifier
    const data: any = response.notification.request.content.data || {}
    // For REPLY with text input, userText may contain typed reply (iOS and Android where supported)
    const userText: string | undefined = (response as any).userText
    onRoute(data.route, { action, userText, ...data })
  })
}
