import React from 'react'
import { Modal, TouchableOpacity, ScrollView } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface RowActionsSheetProps {
  id: string
  visible: boolean
  onClose: () => void
  onAssign?: (id: string) => void
  onAssignToAgent?: (id: string) => void
  onTag?: (id: string) => void
  onSetPriority?: (id: string) => void
  onResolve?: (id: string) => void
  onEscalate?: (id: string) => void
  testID?: string
}

const ActionRow: React.FC<{ label: string; onPress: () => void } > = ({ label, onPress }) => (
  <TouchableOpacity
    onPress={onPress}
    accessibilityLabel={label}
    accessibilityRole="button"
    style={{ paddingVertical: 12 }}
  >
    <Text size={14} weight="600" color={tokens.colors.cardForeground}>{label}</Text>
  </TouchableOpacity>
)

const RowActionsSheet: React.FC<RowActionsSheetProps> = ({ id, visible, onClose, onAssign, onAssignToAgent, onTag, onSetPriority, onResolve, onEscalate, testID }) => {
  const wrap = (cb?: (id: string) => void) => () => { try { cb ? cb(id) : console.log(`[RowAction] ${id}`) } finally { onClose() } }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Box style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
        <Box p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderTopLeftRadius: 16, borderTopRightRadius: 16 }} testID={testID}>
          <Box row align="center" justify="space-between" mb={12}>
            <Text size={16} weight="600" color={tokens.colors.cardForeground}>Actions</Text>
            <TouchableOpacity onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
              <Text size={14} color={tokens.colors.mutedForeground}>Close</Text>
            </TouchableOpacity>
          </Box>
          <ScrollView style={{ maxHeight: 420 }}>
            <ActionRow label="Assign" onPress={wrap(onAssign)} />
            <ActionRow label="Assign to…" onPress={wrap(onAssignToAgent)} />
            <ActionRow label="Tag" onPress={wrap(onTag)} />
            <ActionRow label="Set Priority" onPress={wrap(onSetPriority)} />
            <ActionRow label="Mark Resolved" onPress={wrap(onResolve)} />
            <ActionRow label="Escalate" onPress={wrap(onEscalate)} />
            <ActionRow label="Assign to…" onPress={wrap(onAssign)} />
          </ScrollView>
        </Box>
      </Box>
    </Modal>
  )
}

export default RowActionsSheet


