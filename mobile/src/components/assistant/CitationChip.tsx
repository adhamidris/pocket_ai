import React, { useState } from 'react'
import { TouchableOpacity, View, Text, Modal, ScrollView } from 'react-native'
import { Citation } from '../../types/assistant'
const mask = (s?: string) => s ? s.replace(/([\w.+-]+)@([\w.-]+)/g, '••••@••••').replace(/(\+?\d[\d\s-]{7,}\d)/g, '+•••-•••-••••') : s
import { useTheme } from '../../providers/ThemeProvider'
import { Badge } from '../ui/Badge'

export const CitationChip: React.FC<{ citations: Citation[]; hidePII?: boolean }>
  = ({ citations, hidePII }) => {
  const { theme } = useTheme()
  const [open, setOpen] = useState(false)

  const label = citations.length === 1 ? '1 source' : `${citations.length} sources`

  return (
    <>
      <TouchableOpacity onPress={() => setOpen(true)} accessibilityLabel="Open citations">
        <Badge variant="secondary" size="sm">{label}</Badge>
      </TouchableOpacity>
      <Modal visible={open} transparent animationType="slide" onRequestClose={() => setOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl, maxHeight: '70%', padding: 16 }}>
            <View style={{ height: 4, width: 40, borderRadius: 2, backgroundColor: theme.color.muted, alignSelf: 'center', marginBottom: 12 }} />
            <Text style={{ color: theme.color.mutedForeground, fontWeight: '600', marginBottom: 8 }}>Sources</Text>
            <ScrollView>
              {citations.map(c => (
                <View key={c.id} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
                  <Text style={{ color: theme.color.foreground, fontWeight: '600' }}>{c.label}</Text>
                  <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>
                    {hidePII ? (mask(c.url) ?? mask(c.refId) ?? c.kind) : (c.url ?? c.refId ?? c.kind)}
                  </Text>
                </View>
              ))}
            </ScrollView>
            <TouchableOpacity onPress={() => setOpen(false)} style={{ alignSelf: 'stretch', padding: 12, alignItems: 'center' }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Close</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </>
  )
}

export default CitationChip


