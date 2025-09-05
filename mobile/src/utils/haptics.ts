import * as Haptics from 'expo-haptics'

export const hapticLight = () => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
export const hapticSelection = () => Haptics.selectionAsync()
export const hapticSuccess = () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
export const hapticError = () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error)

