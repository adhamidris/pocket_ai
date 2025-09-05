import React from 'react'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import './i18n'
import { ThemeProvider } from '@/providers/ThemeProvider'
import { ReactQueryProvider } from '@/providers/QueryProvider'
import { ToastProvider } from '@/providers/ToastProvider'
import { RootNav } from '@/navigation'

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ThemeProvider>
        <ReactQueryProvider>
          <ToastProvider>
            <RootNav />
          </ToastProvider>
        </ReactQueryProvider>
      </ThemeProvider>
    </GestureHandlerRootView>
  )
}
