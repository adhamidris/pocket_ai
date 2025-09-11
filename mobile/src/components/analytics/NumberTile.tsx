import React from 'react'
import { View, Text, TouchableOpacity, Modal } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface NumberTileProps { label: string; value: number | string; delta?: number; helpText?: string; definition?: string; testID?: string }

const NumberTile: React.FC<NumberTileProps> = ({ label, value, delta, helpText, definition, testID }) => {
  const [open, setOpen] = React.useState(false)
  const deltaColor = delta == null ? tokens.colors.mutedForeground : delta >= 0 ? tokens.colors.success : tokens.colors.error
  const deltaSign = delta == null ? '' : delta >= 0 ? '+' : ''
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, backgroundColor: tokens.colors.card, minHeight: 88 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <Text style={{ color: tokens.colors.mutedForeground }}>{label}</Text>
        {definition && (
          <TouchableOpacity onPress={() => setOpen(true)} accessibilityRole="button" accessibilityLabel={`Definition ${label}`} style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border }}>
            <Text style={{ color: tokens.colors.mutedForeground, fontWeight: '700' }}>?</Text>
          </TouchableOpacity>
        )}
      </View>
      <Text style={{ color: tokens.colors.cardForeground, fontSize: 22, fontWeight: '700' }}>{String(value)}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
        {delta != null && (
          <Text style={{ color: deltaColor }}>{deltaSign}{delta}%</Text>
        )}
        {helpText ? <Text style={{ color: tokens.colors.mutedForeground }}>{helpText}</Text> : null}
      </View>
      {!!definition && (
        <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
          <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center', alignItems: 'center' }}>
            <View style={{ backgroundColor: tokens.colors.card, padding: 12, borderRadius: 12, maxWidth: 320, borderWidth: 1, borderColor: tokens.colors.border }}>
              <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 6 }}>{label}</Text>
              <Text style={{ color: tokens.colors.mutedForeground }}>{definition}</Text>
              <TouchableOpacity onPress={() => setOpen(false)} style={{ alignSelf: 'flex-end', marginTop: 8 }}>
                <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Close</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      )}
    </View>
  )
}

export default React.memo(NumberTile)


