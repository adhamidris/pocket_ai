import React, { useEffect } from 'react'
import { AppState, AppStateStatus } from 'react-native'
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query'

const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnReconnect: true,
      refetchOnWindowFocus: true,
    },
  },
})

export const ReactQueryProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  useEffect(() => {
    const handleAppStateChange = (state: AppStateStatus) => {
      focusManager.setFocused(state === 'active')
    }
    const sub = AppState.addEventListener('change', handleAppStateChange)
    return () => sub.remove()
  }, [])

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

