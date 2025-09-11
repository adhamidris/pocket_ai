import React, { useEffect, useRef } from 'react'
import { View, findNodeHandle, UIManager } from 'react-native'
import { setAnchor } from '../../lib/helpAnchors'

export const Anchor: React.FC<{ testID: string; children: React.ReactNode }> = ({ testID, children }) => {
  const ref = useRef<View>(null)

  useEffect(() => {
    const node = findNodeHandle(ref.current)
    if (!node) return
    const measure = () => {
      UIManager.measure(node, (_x, _y, width, height, pageX, pageY) => {
        setAnchor(testID, { x: pageX, y: pageY, width, height })
      })
    }
    const id = setInterval(measure, 300)
    measure()
    return () => clearInterval(id)
  }, [testID])

  return (
    <View ref={ref} collapsable={false} testID={testID}>
      {children}
    </View>
  )
}


