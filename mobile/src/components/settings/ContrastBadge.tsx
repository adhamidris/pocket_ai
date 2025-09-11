import React from 'react'
import { View, Text } from 'react-native'

export interface ContrastBadgeProps { fg: string; bg: string; testID?: string }

const hexToRgb = (hex: string): { r: number; g: number; b: number } => {
  const h = hex.replace('#', '')
  const bigint = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16)
  return { r: (bigint >> 16) & 255, g: (bigint >> 8) & 255, b: bigint & 255 }
}

const luminance = (hex: string): number => {
  const { r, g, b } = hexToRgb(hex)
  const a = [r, g, b].map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]
}

const ContrastBadge: React.FC<ContrastBadgeProps> = ({ fg, bg, testID }) => {
  const L1 = luminance(fg) + 0.05
  const L2 = luminance(bg) + 0.05
  const ratio = L1 > L2 ? L1 / L2 : L2 / L1
  const pass = ratio >= 4.5
  return (
    <View testID={testID} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: pass ? '#16a34a' : '#ef4444', backgroundColor: (pass ? '#16a34a' : '#ef4444') + '22' }}>
      <Text style={{ color: pass ? '#16a34a' : '#ef4444', fontWeight: '700' }}>{pass ? 'Pass' : 'Fail'} ({ratio.toFixed(2)}:1)</Text>
    </View>
  )
}

export default React.memo(ContrastBadge)


