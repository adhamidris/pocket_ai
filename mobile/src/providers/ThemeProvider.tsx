import React, { createContext, useContext, useMemo, useState, useEffect } from 'react'
import { Appearance } from 'react-native'
import { darkTheme, lightTheme, Theme } from '@/theme'

type ThemeCtx = { theme: Theme; toggle: () => void }
const Ctx = createContext<ThemeCtx | null>(null)

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isDark, setIsDark] = useState(Appearance.getColorScheme() !== 'light')

  useEffect(() => {
    const sub = Appearance.addChangeListener(({ colorScheme }) => {
      setIsDark(colorScheme !== 'light')
    })
    return () => sub.remove()
  }, [])

  const value = useMemo<ThemeCtx>(() => ({ theme: isDark ? darkTheme : lightTheme, toggle: () => setIsDark(v => !v) }), [isDark])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useTheme = () => {
  const c = useContext(Ctx)
  if (!c) throw new Error('useTheme must be used within ThemeProvider')
  return c
}

