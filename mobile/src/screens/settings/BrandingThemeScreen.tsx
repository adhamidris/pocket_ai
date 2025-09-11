import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, TextInput, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { ThemeTokens, ThemePreset } from '../../types/settings'
import { ColorSwatch, ContrastBadge, BubblePreview, FontPicker, AvatarShapePicker, LogoUploader } from '../../components/settings'
import { track } from '../../lib/analytics'
import { useNavigation } from '@react-navigation/native'

export interface BrandingThemeScreenProps {
  initial: ThemeTokens
  presets?: ThemePreset[]
  onSave: (tokens: ThemeTokens) => void
}

const defaultPresets: ThemePreset[] = [
  {
    id: 'neutral',
    name: 'Neutral',
    tokens: { primary: '#4F46E5', secondary: '#64748B', bg: '#0B1220', surface: '#111827', text: '#E5E7EB', textMuted: '#9CA3AF', bubbleAgent: '#E5E7EB', bubbleCustomer: '#A7F3D0', bubbleAI: '#BFDBFE' }
  },
  {
    id: 'bold',
    name: 'Bold',
    tokens: { primary: '#DB2777', secondary: '#F59E0B', bg: '#0B0B0B', surface: '#171717', text: '#FAFAFA', textMuted: '#A3A3A3', bubbleAgent: '#FDE68A', bubbleCustomer: '#FCA5A5', bubbleAI: '#93C5FD' }
  },
  {
    id: 'soft',
    name: 'Soft',
    tokens: { primary: '#22C55E', secondary: '#06B6D4', bg: '#0E1A24', surface: '#12202B', text: '#E6F1F5', textMuted: '#9FB3C8', bubbleAgent: '#D1FAE5', bubbleCustomer: '#E0E7FF', bubbleAI: '#CCFBF1' }
  }
]

const RowCard: React.FC<{ title: string }>=({ title, children }) => {
  const { theme } = useTheme()
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>{title}</Text>
      {children}
    </View>
  )
}

const BrandingThemeScreen: React.FC<BrandingThemeScreenProps> = ({ initial, presets = defaultPresets, onSave }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [tokens, setTokens] = React.useState<ThemeTokens>(initial)

  const applyPreset = (p: ThemePreset) => setTokens((t) => ({ ...t, ...p.tokens })) as any
  const setColor = (key: keyof ThemeTokens, val: string) => setTokens((t) => ({ ...t, [key]: val }))
  const debouncedSet = React.useRef<{ t?: any; run: (fn: () => void) => void }>({
    run(fn) { if (this.t) clearTimeout(this.t); this.t = setTimeout(fn, 200) as any }
  }).current
  const setRadius = (val: number) => setTokens((t) => ({ ...t, bubbleRadius: Math.max(0, Math.min(24, Math.round(val))) }))

  const reset = () => setTokens(initial)
  const exportJson = () => {
    // eslint-disable-next-line no-console
    console.log('ThemeTokens JSON', JSON.stringify(tokens, null, 2))
    Alert.alert('Exported', 'Theme JSON printed to console.')
  }

  const save = () => { onSave(tokens); Alert.alert('Saved', 'Theme draft updated.'); try { track('settings.theme.save') } catch {} }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack?.()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Branding & Theming</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={reset} accessibilityRole="button" accessibilityLabel="Reset" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Reset</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={exportJson} accessibilityRole="button" accessibilityLabel="Export JSON" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Export JSON</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save Draft" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save Draft</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24 }}>
          {/* Presets */}
          <RowCard title="Presets">
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {presets.map((p) => (
                <TouchableOpacity key={p.id} onPress={() => applyPreset(p)} accessibilityRole="button" accessibilityLabel={`Preset ${p.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground }}>{p.name}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </RowCard>

          {/* Colors */}
          <RowCard title="Colors">
            <View style={{ gap: 10 }}>
              <ColorSwatch label="Primary" value={tokens.primary} onChange={(v) => debouncedSet.run(() => setColor('primary', v))} />
              <ColorSwatch label="Secondary" value={tokens.secondary} onChange={(v) => debouncedSet.run(() => setColor('secondary', v))} />
              <ColorSwatch label="Background" value={tokens.bg} onChange={(v) => debouncedSet.run(() => setColor('bg', v))} />
              <ColorSwatch label="Surface" value={tokens.surface} onChange={(v) => debouncedSet.run(() => setColor('surface', v))} />
              <ColorSwatch label="Text" value={tokens.text} onChange={(v) => debouncedSet.run(() => setColor('text', v))} />
              <ColorSwatch label="Text Muted" value={tokens.textMuted} onChange={(v) => debouncedSet.run(() => setColor('textMuted', v))} />
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <ContrastBadge fg={tokens.text} bg={tokens.bg} />
                <ContrastBadge fg={tokens.textMuted} bg={tokens.surface} />
              </View>
            </View>
          </RowCard>

          {/* Bubbles */}
          <RowCard title="Bubbles">
            <View style={{ gap: 10 }}>
              <ColorSwatch label="Agent Bubble" value={tokens.bubbleAgent} onChange={(v) => setColor('bubbleAgent', v)} />
              <ColorSwatch label="Customer Bubble" value={tokens.bubbleCustomer} onChange={(v) => setColor('bubbleCustomer', v)} />
              <ColorSwatch label="AI Bubble" value={tokens.bubbleAI} onChange={(v) => setColor('bubbleAI', v)} />
              <AvatarShapePicker value={tokens.avatarShape} onChange={(v) => setTokens((t) => ({ ...t, avatarShape: v }))} />
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Radius</Text>
                <TouchableOpacity onPress={() => setRadius(tokens.bubbleRadius - 2)} accessibilityRole="button" accessibilityLabel="Dec radius" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground }}>-</Text>
                </TouchableOpacity>
                <TextInput value={String(tokens.bubbleRadius)} onChangeText={(v) => setRadius(Number(v) || 0)} keyboardType="numeric" style={{ minWidth: 56, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 6, color: theme.color.cardForeground }} />
                <TouchableOpacity onPress={() => setRadius(tokens.bubbleRadius + 2)} accessibilityRole="button" accessibilityLabel="Inc radius" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground }}>+</Text>
                </TouchableOpacity>
              </View>
            </View>
          </RowCard>

          {/* Typography */}
          <RowCard title="Typography">
            <FontPicker value={tokens.fontFamily} onChange={(v) => setTokens((t) => ({ ...t, fontFamily: v }))} />
            <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Font sizes follow OS scaling settings.</Text>
          </RowCard>

          {/* Logo */}
          <RowCard title="Logo">
            <LogoUploader url={tokens.logoUrl} onPick={(url) => setTokens((t) => ({ ...t, logoUrl: url }))} />
          </RowCard>

          {/* Preview */}
          <RowCard title="Preview">
            <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: tokens.bg }}>
              <View style={{ padding: 12, borderRadius: 10, backgroundColor: tokens.surface, marginBottom: 12 }}>
                <Text style={{ color: tokens.text, fontWeight: '700' }}>Header</Text>
              </View>
              <BubblePreview tokens={tokens} />
              <View style={{ padding: 12, borderRadius: 10, backgroundColor: tokens.surface, marginTop: 12 }}>
                <Text style={{ color: tokens.textMuted }}>Footer</Text>
              </View>
            </View>
          </RowCard>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default BrandingThemeScreen


