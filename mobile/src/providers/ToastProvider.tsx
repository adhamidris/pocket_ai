import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { Animated, Easing, Text, View } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'
import { hapticError, hapticSuccess, hapticLight } from '@/utils/haptics'

type ToastType = 'success' | 'error' | 'info'
type ToastMsg = { id: number; text: string; type: ToastType }

type ToastCtx = {
  show: (text: string, type?: ToastType) => void
}

const Ctx = createContext<ToastCtx | null>(null)

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { theme } = useTheme()
  const [toast, setToast] = useState<ToastMsg | null>(null)
  const anim = useRef(new Animated.Value(0)).current
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const idRef = useRef(1)

  const hide = useCallback(() => {
    Animated.timing(anim, { toValue: 0, duration: 200, easing: Easing.out(Easing.ease), useNativeDriver: true }).start(() => setToast(null))
  }, [anim])

  const show = useCallback((text: string, type: ToastType = 'info') => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    const id = idRef.current++
    setToast({ id, text, type })
    anim.setValue(0)
    Animated.timing(anim, { toValue: 1, duration: 220, easing: Easing.out(Easing.ease), useNativeDriver: true }).start()
    timer.current = setTimeout(hide, 2600)
    // Haptics mapping
    if (type === 'success') hapticSuccess()
    else if (type === 'error') hapticError()
    else hapticLight()
  }, [anim, hide])

  const value = useMemo<ToastCtx>(() => ({ show }), [show])

  const translateY = anim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] })
  const opacity = anim

  const bg = toast?.type === 'success' ? 'rgba(16,185,129,0.12)'
    : toast?.type === 'error' ? 'rgba(239,68,68,0.12)'
    : 'rgba(255,255,255,0.08)'
  const border = toast?.type === 'success' ? 'rgba(16,185,129,0.5)'
    : toast?.type === 'error' ? 'rgba(239,68,68,0.5)'
    : theme.color.border
  const textColor = theme.color.cardForeground

  return (
    <Ctx.Provider value={value}>
      {children}
      {toast && (
        <Animated.View pointerEvents="none" style={{ position: 'absolute', left: 12, right: 12, bottom: 28, opacity, transform: [{ translateY }], zIndex: 10000 }}>
          <View pointerEvents="none" style={{ backgroundColor: bg, borderColor: border, borderWidth: 1, borderRadius: 12, paddingVertical: 12, paddingHorizontal: 14 }}>
            <Text style={{ color: textColor }}>{toast.text}</Text>
          </View>
        </Animated.View>
      )}
    </Ctx.Provider>
  )
}

export const useToast = () => {
  const c = useContext(Ctx)
  if (!c) throw new Error('useToast must be used within ToastProvider')
  return c
}
