import React from 'react'
import { Modal, ScrollView, TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { RefreshCw, X } from 'lucide-react-native'

export interface SyncCenterSheetProps {
  visible: boolean
  onClose: () => void
  lastSyncAt?: string
  queuedCount?: number
  onRetryAll?: () => void
  testID?: string
}

const SyncCenterSheet: React.FC<SyncCenterSheetProps> = ({ visible, onClose, lastSyncAt, queuedCount = 0, onRetryAll, testID }) => {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Box style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
        <Box p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderTopLeftRadius: 16, borderTopRightRadius: 16 }} testID={testID}>
          <Box row align="center" justify="space-between" mb={12}>
            <Text size={16} weight="600" color={tokens.colors.cardForeground}>Sync Center</Text>
            <TouchableOpacity onPress={onClose} accessibilityLabel="Close Sync Center" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
              <X size={20} color={tokens.colors.mutedForeground as any} />
            </TouchableOpacity>
          </Box>
          <ScrollView style={{ maxHeight: 420 }}>
            <Box mb={12}>
              <Text size={13} color={tokens.colors.mutedForeground}>Last sync</Text>
              <Text size={14} color={tokens.colors.cardForeground}>
                {lastSyncAt || 'Just now'}
              </Text>
            </Box>
            <Box mb={12}>
              <Text size={13} color={tokens.colors.mutedForeground}>Queued actions</Text>
              <Text size={14} color={tokens.colors.cardForeground}>{queuedCount}</Text>
            </Box>
            <TouchableOpacity
              onPress={onRetryAll}
              accessibilityLabel="Retry all"
              accessibilityRole="button"
              style={{
                paddingHorizontal: 12,
                paddingVertical: 10,
                borderRadius: 12,
                borderWidth: 1,
                borderColor: tokens.colors.border,
                backgroundColor: tokens.colors.card,
                flexDirection: 'row',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <RefreshCw size={16} color={tokens.colors.primary as any} />
              <Text size={14} weight="600" color={tokens.colors.primary}>Retry All</Text>
            </TouchableOpacity>
          </ScrollView>
        </Box>
      </Box>
    </Modal>
  )
}

export default SyncCenterSheet


