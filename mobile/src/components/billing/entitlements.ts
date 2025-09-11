import React from 'react'

export interface EntitlementEntry { enabled: boolean; limit?: number | 'unlimited' }
export type EntitlementsMap = Record<string, EntitlementEntry>

let current: EntitlementsMap = {}
const listeners = new Set<() => void>()

export const setEntitlements = (next: EntitlementsMap) => {
  current = next
  listeners.forEach((l) => l())
}

export const getEntitlements = (): EntitlementsMap => current

export const useEntitlements = (): EntitlementsMap => {
  const subscribe = React.useCallback((onStoreChange: () => void) => {
    listeners.add(onStoreChange)
    return () => listeners.delete(onStoreChange)
  }, [])
  // Using simple custom sync since RN may not have useSyncExternalStore polyfill
  const [, force] = React.useState(0)
  React.useEffect(() => {
    const unsub = subscribe(() => force((x) => x + 1))
    return unsub
  }, [subscribe])
  return current
}


