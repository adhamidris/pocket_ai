import React from 'react'
import { Modal, TouchableOpacity, ScrollView, View, Text } from 'react-native'
import Box from '../../ui/Box'
import { tokens } from '../../ui/tokens'
import { AnyAgent, AgentStatus, SkillTag } from '../../types/agents'

export interface AgentRowActionsSheetProps {
  id: string
  item: AnyAgent
  visible: boolean
  onClose: () => void
  onChangeStatus?: (id: string) => void
  onAddRemoveSkill?: (id: string, skill: string) => void
  onAdjustCapacity?: (id: string, delta: number) => void
  onToggleAllowlist?: (id: string, key: string) => void
  testID?: string
}

const statuses: AgentStatus[] = ['online','away','dnd','offline']

const RowBtn: React.FC<{ label: string; onPress: () => void }> = ({ label, onPress }) => (
  <TouchableOpacity onPress={onPress} accessibilityLabel={label} accessibilityRole="button" style={{ paddingVertical: 12, minHeight: 44 }}>
    <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>{label}</Text>
  </TouchableOpacity>
)

const Pill: React.FC<{ text: string }> = ({ text }) => (
  <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border, backgroundColor: tokens.colors.card, marginRight: 6, marginBottom: 6 }}>
    <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12 }}>{text}</Text>
  </View>
)

const AgentRowActionsSheet: React.FC<AgentRowActionsSheetProps> = ({ id, item, visible, onClose, onChangeStatus, onAddRemoveSkill, onAdjustCapacity, onToggleAllowlist, testID }) => {
  const rotateStatus = () => onChangeStatus?.(id)

  const toggleSkill = (skill: string) => onAddRemoveSkill?.(id, skill)

  const decCap = () => onAdjustCapacity?.(id, -1)
  const incCap = () => onAdjustCapacity?.(id, +1)

  const toggleAllow = (key: string) => onToggleAllowlist?.(id, key)

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Box style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
        <Box p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderTopLeftRadius: 16, borderTopRightRadius: 16 }} testID={testID}>
          <Box row align="center" justify="space-between" mb={12}>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', fontSize: 16 }}>Agent Actions</Text>
            <TouchableOpacity onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
              <Text style={{ color: tokens.colors.mutedForeground }}>Close</Text>
            </TouchableOpacity>
          </Box>
          <ScrollView style={{ maxHeight: 440 }}>
            <RowBtn label={`Change Status (current: ${(item as any).status}; cycle: ${statuses.join(' → ')})`} onPress={() => { rotateStatus(); onClose() }} />

            {/* Skills */}
            <Text style={{ color: tokens.colors.mutedForeground, marginTop: 8, marginBottom: 6 }}>Skills</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
              {(item.skills || []).map((s: SkillTag, idx: number) => (
                <TouchableOpacity key={`${String(s.name)}-${idx}`} onPress={() => toggleSkill(String(s.name))} accessibilityLabel={`Toggle skill ${String(s.name)}${s.level ? ` level ${s.level}` : ''}`} accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ marginRight: 6, marginBottom: 6 }}>
                  <Pill text={`${String(s.name)}${s.level ? ` · L${s.level}` : ''}`} />
                </TouchableOpacity>
              ))}
              <TouchableOpacity onPress={() => toggleSkill('support')} accessibilityLabel="Add skill support" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ marginRight: 6, marginBottom: 6 }}>
                <Pill text={'+ support'} />
              </TouchableOpacity>
            </View>

            {/* Capacity (human) */}
            {item.kind === 'human' && (
              <View style={{ marginTop: 12 }}>
                <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Adjust Capacity</Text>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <TouchableOpacity onPress={decCap} accessibilityLabel="Decrease capacity" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8 }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>-1</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={incCap} accessibilityLabel="Increase capacity" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8 }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>+1</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}

            {/* Allowlist quick toggles (AI) */}
            {item.kind === 'ai' && (
              <View style={{ marginTop: 12 }}>
                <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Allowlist</Text>
                {((item as any).allowlist || []).map((a: any) => (
                  <TouchableOpacity key={a.key} onPress={() => toggleAllow(a.key)} accessibilityLabel={`Toggle allowlist ${a.label}`} accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 10 }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>{a.label}</Text>
                    <Text style={{ color: a.enabled ? tokens.colors.success : tokens.colors.mutedForeground, fontWeight: '700' }}>{a.enabled ? 'ON' : 'OFF'}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            )}
          </ScrollView>
        </Box>
      </Box>
    </Modal>
  )
}

export default AgentRowActionsSheet


