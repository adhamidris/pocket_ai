import React from 'react'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { Currency } from '../../types/billing'
import { track } from '../../lib/analytics'

const KEY = 'billing_currency'

let current: Currency = 'USD'
const listeners = new Set<() => void>()

// Rates expressed as units of currency per 1 USD (UI-only, not real-time)
const rates: Record<Currency, number> = {
  USD: 1,
  EUR: 0.92,
  GBP: 0.79,
  EGP: 50,
  AED: 3.67,
  SAR: 3.75,
}

export const getCurrency = (): Currency => current
export const setCurrency = async (c: Currency) => {
  current = c
  try { await AsyncStorage.setItem(KEY, c) } catch {}
  try { track('billing.currency.change', { currency: c }) } catch {}
  listeners.forEach((l) => l())
}

export const initCurrency = async () => {
  try {
    const saved = await AsyncStorage.getItem(KEY)
    if (saved) {
      current = saved as Currency
      listeners.forEach((l) => l())
    }
  } catch {}
}

export const convertAmount = (amountCents: number, from: Currency, to: Currency): number => {
  if (from === to) return Math.round(amountCents)
  const rateFrom = rates[from]
  const rateTo = rates[to]
  if (!rateFrom || !rateTo) return Math.round(amountCents)
  const usdCents = amountCents / rateFrom
  const target = usdCents * rateTo
  return Math.round(target)
}

export const useCurrency = (): [Currency, (c: Currency) => void] => {
  const [, force] = React.useState(0)
  React.useEffect(() => {
    let mounted = true
    initCurrency()
    const listener = () => mounted && force((x) => x + 1)
    listeners.add(listener)
    return () => { mounted = false; listeners.delete(listener) }
  }, [])
  return [current, (c: Currency) => { setCurrency(c) }]
}


