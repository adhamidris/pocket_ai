export type AnchorRect = { x: number; y: number; width: number; height: number }

type Listener = () => void

const anchors = new Map<string, AnchorRect>()
const listeners = new Set<Listener>()

export const setAnchor = (testID: string, rect: AnchorRect) => {
  anchors.set(testID, rect)
  listeners.forEach(l => l())
}

export const getAnchor = (testID: string): AnchorRect | undefined => anchors.get(testID)

export const subscribeAnchors = (listener: Listener) => {
  listeners.add(listener)
  return () => listeners.delete(listener)
}


