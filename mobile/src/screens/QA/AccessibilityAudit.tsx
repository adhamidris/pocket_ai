import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TouchableWithoutFeedback, Pressable, TextProps, TouchableOpacityProps, LayoutChangeEvent } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

type Hit = {
  id: string
  type: 'pressable' | 'text'
  label?: string
  hasLabel?: boolean
  x?: number
  y?: number
  w?: number
  h?: number
  color?: string
}

const REGISTRY: { hits: Hit[] } = { hits: [] }
const FLAG_PROP = '__qa_a11y_wrapped__'

const parseHex = (hex?: string): [number, number, number] | null => {
  if (!hex) return null
  const m = /^#([0-9a-f]{6})$/i.exec(hex)
  if (!m) return null
  const v = m[1]
  const r = parseInt(v.slice(0, 2), 16) / 255
  const g = parseInt(v.slice(2, 4), 16) / 255
  const b = parseInt(v.slice(4, 6), 16) / 255
  return [r, g, b]
}

const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4))
const luminance = (rgb: [number, number, number]) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])
const contrastRatio = (fg: string | undefined, bg: string | undefined): number | null => {
  const frgb = parseHex(fg)
  const brgb = parseHex(bg)
  if (!frgb || !brgb) return null
  const L1 = Math.max(luminance(frgb), luminance(brgb))
  const L2 = Math.min(luminance(frgb), luminance(brgb))
  return (L1 + 0.05) / (L2 + 0.05)
}

export const AccessibilityAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [enabled, setEnabled] = React.useState<boolean>(false)
  const [results, setResults] = React.useState<{ missingLabels: Hit[]; smallHit: Hit[]; lowContrast: Hit[]; focusOrderOk: boolean }>({ missingLabels: [], smallHit: [], lowContrast: [], focusOrderOk: true })

  React.useEffect(() => {
    const originalCreateElement = (React as any).createElement
    if (!enabled) return
    REGISTRY.hits = []

    const wrapPressable = (type: any) => (props: any, ...rest: any[]) => {
      if (props && props[FLAG_PROP]) return originalCreateElement(type, props, ...rest)
      const id = props?.testID || `${type?.displayName || type?.name || 'press'}-${Math.random().toString(36).slice(2, 8)}`
      const onLayout = (e: LayoutChangeEvent) => {
        const { x, y, width, height } = e.nativeEvent.layout
        const idx = REGISTRY.hits.findIndex(h => h.id === id)
        const base: Hit = { id, type: 'pressable', label: props?.accessibilityLabel, hasLabel: !!props?.accessibilityLabel, x, y, w: width, h: height }
        if (idx >= 0) REGISTRY.hits[idx] = { ...REGISTRY.hits[idx], ...base }
        else REGISTRY.hits.push(base)
        props?.onLayout && props.onLayout(e)
      }
      const nextProps = { ...props, onLayout, [FLAG_PROP]: true }
      return originalCreateElement(type, nextProps, ...rest)
    }

    const wrapText = (type: any) => (props: TextProps, ...rest: any[]) => {
      if (props && (props as any)[FLAG_PROP]) return originalCreateElement(type, props, ...rest)
      const id = (props as any)?.testID || `text-${Math.random().toString(36).slice(2, 8)}`
      const color = (Array.isArray((props as any).style) ? (props as any).style.find((s: any) => s?.color)?.color : (props as any).style?.color) as string | undefined
      const onLayout = (e: LayoutChangeEvent) => {
        const { x, y, width, height } = e.nativeEvent.layout
        const idx = REGISTRY.hits.findIndex(h => h.id === id)
        const base: Hit = { id, type: 'text', x, y, w: width, h: height, color }
        if (idx >= 0) REGISTRY.hits[idx] = { ...REGISTRY.hits[idx], ...base }
        else REGISTRY.hits.push(base)
        ;(props as any)?.onLayout && (props as any).onLayout(e)
      }
      const nextProps: any = { ...(props as any), onLayout, [FLAG_PROP]: true }
      return originalCreateElement(type, nextProps, ...rest)
    }

    ;(React as any).createElement = (type: any, props: any, ...rest: any[]) => {
      try {
        if (type === TouchableOpacity || type === TouchableWithoutFeedback || type === Pressable) {
          return wrapPressable(type)(props, ...rest)
        }
        if (type === Text) {
          return wrapText(type)(props, ...rest)
        }
        return originalCreateElement(type, props, ...rest)
      } catch (e) {
        return originalCreateElement(type, props, ...rest)
      }
    }

    return () => {
      ;(React as any).createElement = originalCreateElement
    }
  }, [enabled])

  const runChecks = () => {
    const pressables = REGISTRY.hits.filter(h => h.type === 'pressable')
    const texts = REGISTRY.hits.filter(h => h.type === 'text')
    const missingLabels = pressables.filter(h => !h.hasLabel)
    const smallHit = pressables.filter(h => (h.w || 0) < 44 || (h.h || 0) < 44)
    const lowContrast = texts
      .map(h => ({ h, ratio: contrastRatio(h.color, theme.color.background) }))
      .filter(v => (v.ratio || 100) < 4.5)
      .map(v => ({ ...v.h }))
    const ordered = pressables.slice().sort((a, b) => (a.y || 0) - (b.y || 0) || (a.x || 0) - (b.x || 0))
    const focusOrderOk = ordered.every((h, i) => h.id === pressables[i]?.id)
    setResults({ missingLabels, smallHit, lowContrast, focusOrderOk })
  }

  const reset = () => { REGISTRY.hits = []; setResults({ missingLabels: [], smallHit: [], lowContrast: [], focusOrderOk: true }) }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700' }}>Accessibility Audit</Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TouchableOpacity onPress={() => { setEnabled(v => !v); reset() }} accessibilityRole="button" accessibilityLabel="Toggle audit mode" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{enabled ? 'Disable' : 'Enable'}</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={runChecks} accessibilityRole="button" accessibilityLabel="Run checks" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Check</Text>
          </TouchableOpacity>
        </View>
      </View>
      <View style={{ paddingHorizontal: 16, gap: 12 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Row title="Missing labels" count={results.missingLabels.length} color={results.missingLabels.length === 0 ? theme.color.success : theme.color.destructive} themeColor={theme.color} />
          <Row title="Small hit targets (<44dp)" count={results.smallHit.length} color={results.smallHit.length === 0 ? theme.color.success : theme.color.destructive} themeColor={theme.color} />
          <Row title="Low contrast (<4.5:1)" count={results.lowContrast.length} color={results.lowContrast.length === 0 ? theme.color.success : theme.color.warning || '#f59e0b'} themeColor={theme.color} />
          <Row title="Focus order" count={results.focusOrderOk ? 0 : 1} color={results.focusOrderOk ? theme.color.success : theme.color.warning || '#f59e0b'} themeColor={theme.color} suffix={results.focusOrderOk ? 'OK' : 'Check order'} />
        </View>

        {/* A11y Hints */}
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 8 }}>A11y Hints</Text>
          <Hint text="Add accessibilityLabel to all actionable controls (buttons, chips, icons)." />
          <Hint text="Ensure touch targets are at least 44×44dp; add padding if needed." />
          <Hint text="Maintain text contrast ≥ 4.5:1 against backgrounds; adjust theme colors." />
          <Hint text="Logical focus order: top‑to‑bottom, left‑to‑right for groups." />
        </View>
      </View>
    </SafeAreaView>
  )
}

const Row: React.FC<{ title: string; count: number; color: string; themeColor: any; suffix?: string }> = ({ title, count, color, themeColor, suffix }) => (
  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: themeColor.border }}>
    <Text style={{ color: themeColor.cardForeground, fontWeight: '600' }}>{title}</Text>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      {suffix ? <Text style={{ color: themeColor.mutedForeground }}>{suffix}</Text> : null}
      <Text style={{ color, fontWeight: '800' }}>{count}</Text>
    </View>
  </View>
)

const Hint: React.FC<{ text: string }> = ({ text }) => {
  return (
    <View style={{ paddingVertical: 6 }}>
      <Text style={{ color: '#666' }}>{'• '}<Text style={{ color: '#333' }}>{text}</Text></Text>
    </View>
  )
}

export default AccessibilityAudit


