import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import * as Localization from 'expo-localization'
import { helpStrings } from '../../content/help/strings'

export interface Rect { x: number; y: number; width: number; height: number }

export const Coachmark: React.FC<{
  visible: boolean
  anchorFrame: Rect
  title: string
  body: string
  stepIndex?: number
  stepCount?: number
  onNext?: () => void
  onSkip?: () => void
}> = ({ visible, anchorFrame, title, body, stepIndex, stepCount, onNext, onSkip }) => {
  const { theme } = useTheme()
  if (!visible) return null

  const top = Math.max(16, anchorFrame.y + anchorFrame.height + 8)
  const left = Math.max(16, Math.min(anchorFrame.x, 16))

  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, top: 0, right: 0, bottom: 0 }}>
      {/* Bubble */}
      <View
        style={{
          position: 'absolute',
          left,
          top,
          maxWidth: 340,
          backgroundColor: theme.color.card,
          borderRadius: theme.radius.lg,
          padding: 16,
          borderWidth: 1,
          borderColor: theme.color.border,
          ...(theme.shadow.md as any)
        }}
        accessibilityLabel={title}
      >
        <Text style={{ color: theme.color.cardForeground, fontWeight: '600', fontSize: 16, marginBottom: 8 }}>{title}</Text>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 14, marginBottom: 12 }}>{body}</Text>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text accessibilityLiveRegion="polite" style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
            {(() => { const lang = Localization.getLocales()[0]?.languageCode === 'ar' ? 'ar' : 'en'; return (stepIndex !== undefined && stepCount !== undefined) ? helpStrings[lang].stepXofY(stepIndex + 1, stepCount) : '' })()}
          </Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {onSkip && (
              <TouchableOpacity onPress={onSkip} accessibilityRole="button" accessibilityLabel="Skip coachmark" style={{ paddingHorizontal: 12, paddingVertical: 10, minWidth: 44, minHeight: 44, justifyContent: 'center' }}>
                <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>{(() => { const lang = Localization.getLocales()[0]?.languageCode === 'ar' ? 'ar' : 'en'; return helpStrings[lang].skip })()}</Text>
              </TouchableOpacity>
            )}
            {onNext && (
              <TouchableOpacity onPress={onNext} accessibilityRole="button" accessibilityLabel="Next coachmark" style={{ paddingHorizontal: 12, paddingVertical: 10, backgroundColor: theme.color.primary, borderRadius: 12, minWidth: 44, minHeight: 44, justifyContent: 'center' }}>
                <Text style={{ color: '#fff', fontWeight: '600' }}>{(() => { const lang = Localization.getLocales()[0]?.languageCode === 'ar' ? 'ar' : 'en'; return helpStrings[lang].next })()}</Text>
              </TouchableOpacity>
            )}
          </View>
        </View>
      </View>

      {/* Arrow */}
      <View
        style={{
          position: 'absolute',
          left: left + 24,
          top: top - 6,
          width: 12,
          height: 12,
          backgroundColor: theme.color.card,
          transform: [{ rotate: '45deg' }],
          borderLeftWidth: 1,
          borderTopWidth: 1,
          borderColor: theme.color.border
        }}
      />
    </View>
  )
}


