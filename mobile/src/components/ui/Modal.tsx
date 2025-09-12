import React from 'react'
import { 
  Modal as RNModal, 
  View, 
  Text, 
  TouchableOpacity, 
  Pressable,
  Dimensions 
} from 'react-native'
import { X } from 'lucide-react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface ModalProps {
  visible: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
  size?: 'sm' | 'md' | 'lg'
}

export const Modal: React.FC<ModalProps> = ({ 
  visible, 
  onClose, 
  title, 
  children,
  size = 'md'
}) => {
  const { theme } = useTheme()
  const { width, height } = Dimensions.get('window')

  const getModalWidth = () => {
    switch (size) {
      case 'sm': return Math.min(320, width - 32)
      case 'lg': return Math.min(720, width - 24)
      default: return Math.min(420, width - 28)
    }
  }

  return (
    <RNModal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <Pressable 
        style={{
          flex: 1,
          backgroundColor: 'rgba(0,0,0,0.5)',
          justifyContent: 'center',
          alignItems: 'center',
          padding: 8
        }}
        onPress={onClose}
      >
        <Pressable 
          style={{
            backgroundColor: theme.color.card,
            borderRadius: theme.radius.xl,
            width: getModalWidth(),
            maxHeight: height * 0.9,
            borderWidth: 0,
            borderColor: 'transparent',
            shadowColor: '#000',
            shadowOpacity: 0.25,
            shadowRadius: 20,
            shadowOffset: { width: 0, height: 10 },
            elevation: 10,
            overflow: 'hidden'
          }}
          onPress={(e) => e.stopPropagation()}
        >
          {/* Header */}
          {title && (
            <View style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: 20,
              borderBottomWidth: 1,
              borderBottomColor: theme.color.border
            }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 18,
                fontWeight: '600',
                flex: 1
              }}>
                {title}
              </Text>
              <TouchableOpacity
                onPress={onClose}
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 16,
                  backgroundColor: theme.color.muted,
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <X size={16} color={theme.color.mutedForeground} />
              </TouchableOpacity>
            </View>
          )}

          {/* Content */}
          <View style={{ padding: 16 }}>
            {children}
          </View>
        </Pressable>
      </Pressable>
    </RNModal>
  )
}
