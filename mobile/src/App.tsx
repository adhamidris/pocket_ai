import React from 'react'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './providers/ThemeProvider'
import { RootNavigator } from './navigation/RootNavigator'
import './i18n'
import { FixtureSwitcher } from './qa/FixtureSwitcher'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
})

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <RootNavigator />
          <FixtureSwitcher />
        </ThemeProvider>
      </QueryClientProvider>
    </GestureHandlerRootView>
  )
}
