import React from 'react'
import { Modal, TouchableOpacity, ScrollView } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { ConsentState } from '../../types/crm'

export interface RowActionsSheetProps {
  id: string
  visible: boolean
  onClose: () => void
  onToggleVip?: (id: string) => void
  onAddRemoveTag?: (id: string) => void
  onSetConsent?: (id: string, state: ConsentState) => void
  onStartConversation?: (id: string) => void
  onMerge?: (id: string) => void
  onDelete?: (id: string) => void
  testID?: string
}

const ActionRow: React.FC<{ label: string; onPress: () => void }> = ({ label, onPress }) => (
  <TouchableOpacity onPress={onPress} accessibilityLabel={label} accessibilityRole="button" style={{ paddingVertical: 12, minHeight: 44 }} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
    <Text size={14} weight="600" color={tokens.colors.cardForeground}>{label}</Text>
  </TouchableOpacity>
)

const RowActionsSheet: React.FC<RowActionsSheetProps> = ({ id, visible, onClose, onToggleVip, onAddRemoveTag, onSetConsent, onStartConversation, onMerge, onDelete, testID }) => {
  const [confirm, setConfirm] = React.useState(false)

  const wrap = (cb?: () => void) => () => { try { cb && cb() } finally { onClose() } }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Box style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
        <Box p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderTopLeftRadius: 16, borderTopRightRadius: 16 }} testID={testID}>
          <Box row align="center" justify="space-between" mb={12}>
            <Text size={16} weight="600" color={tokens.colors.cardForeground}>Contact Actions</Text>
            <TouchableOpacity onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
              <Text size={14} color={tokens.colors.mutedForeground}>Close</Text>
            </TouchableOpacity>
          </Box>
          <ScrollView style={{ maxHeight: 420 }}>
            <ActionRow label="Toggle VIP" onPress={wrap(() => onToggleVip?.(id))} />
            <ActionRow label="Add/Remove Tag" onPress={wrap(() => onAddRemoveTag?.(id))} />
            <ActionRow label="Set Consent: Granted" onPress={wrap(() => onSetConsent?.(id, 'granted'))} />
            <ActionRow label="Set Consent: Denied" onPress={wrap(() => onSetConsent?.(id, 'denied'))} />
            <ActionRow label="Start Conversation" onPress={wrap(() => onStartConversation?.(id))} />
            <ActionRow label="Merge…" onPress={wrap(() => onMerge?.(id))} />
            <ActionRow label="Delete" onPress={() => setConfirm(true)} />
          </ScrollView>
        </Box>
      </Box>

      {/* Delete confirm */}
      <Modal visible={confirm} transparent animationType="fade" onRequestClose={() => setConfirm(false)}>
        <Box style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <Box p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border, width: '90%' }}>
            <Text size={16} weight="700" color={tokens.colors.cardForeground} style={{ marginBottom: 8 }}>Delete Contact</Text>
            <Text size={13} color={tokens.colors.mutedForeground} style={{ marginBottom: 16 }}>Are you sure you want to delete this contact?</Text>
            <Box row align="center" justify="flex-end" gap={12}>
              <TouchableOpacity onPress={() => setConfirm(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text size={14} color={tokens.colors.mutedForeground}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => { setConfirm(false); onClose(); onDelete?.(id) }} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text size={14} color={tokens.colors.error}>
                  Delete
                </Text>
              </TouchableOpacity>
            </Box>
          </Box>
        </Box>
      </Modal>
    </Modal>
  )
}

export default RowActionsSheet


