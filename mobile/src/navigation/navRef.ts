import { createNavigationContainerRef } from '@react-navigation/native'

export const navigationRef = createNavigationContainerRef<any>()

export function navigate(name: string, params?: any) {
  if (navigationRef.isReady()) {
    // @ts-ignore
    navigationRef.navigate(name, params)
  }
}

