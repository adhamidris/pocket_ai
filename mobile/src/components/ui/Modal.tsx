import React, { useEffect, useRef } from 'react'
import { 
  Modal as RNModal, 
  View, 
  Text, 
  TouchableOpacity, 
  Pressable,
  Dimensions,
  Animated,
  StyleSheet
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
  const { width: screenWidth, height: screenHeight } = Dimensions.get('window')
  const overlayOpacity = useRef(new Animated.Value(0)).current

  // Try to use expo-blur if available, else fall back to dim overlay
  let BlurView: any = null
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    BlurView = require('expo-blur').BlurView
  } catch {}

  useEffect(() => {
    if (visible) {
      Animated.timing(overlayOpacity, {
        toValue: 1,
        duration: 200,
        useNativeDriver: true,
      }).start()
    } else {
      Animated.timing(overlayOpacity, {
        toValue: 0,
        duration: 150,
        useNativeDriver: true,
      }).start()
    }
  }, [visible, overlayOpacity])

  const getModalWidth = () => {
    switch (size) {
      case 'sm': return Math.min(320, screenWidth - 32)
      case 'lg': return Math.min(720, screenWidth - 24)
      default: return Math.min(420, screenWidth - 28)
    }
  }

  const getModalHeight = () => {
    switch (size) {
      case 'sm': return Math.min(420, Math.floor(screenHeight * 0.85))
      case 'lg': return Math.min(720, Math.floor(screenHeight * 0.9))
      default: return Math.min(560, Math.floor(screenHeight * 0.88))
    }
  }

  return (
    <RNModal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={{ flex: 1 }}>
        {/* Blurred/dim backdrop with clickable overlay behind content */}
        <Animated.View style={[StyleSheet.absoluteFillObject as any, { opacity: overlayOpacity }]}> 
          {BlurView ? (
            <BlurView
              tint={theme.dark ? 'dark' : 'light'}
              intensity={50}
              style={StyleSheet.absoluteFillObject}
            />
          ) : (
            <View style={[StyleSheet.absoluteFillObject as any, { backgroundColor: 'rgba(0,0,0,0.45)' }]} />
          )}
          {/* Add a subtle dark veil to improve contrast */}
          <View pointerEvents="none" style={[StyleSheet.absoluteFillObject as any, { backgroundColor: theme.dark ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.15)' }]} />
          {/* Overlay pressable to close modal when tapping outside content */}
          <TouchableOpacity onPress={onClose} style={[StyleSheet.absoluteFillObject as any]} />
        </Animated.View>

        {/* Centered modal content */}
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', padding: 8 }}>
          <View 
            style={{
              backgroundColor: theme.color.card,
              borderRadius: theme.radius.xl,
              width: getModalWidth(),
              height: getModalHeight(),
              borderWidth: 0,
              borderColor: 'transparent',
              shadowColor: '#000',
              shadowOpacity: 0.25,
              shadowRadius: 20,
              shadowOffset: { width: 0, height: 10 },
              elevation: 10,
              overflow: 'hidden'
            }}
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
          <View style={{ flex: 1, padding: 16 }}>
            {children}
          </View>
          </View>
        </View>
      </View>
    </RNModal>
  )
}
