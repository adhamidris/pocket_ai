import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const ApprovalBadge: React.FC<{ require: boolean; role?: 'owner'|'admin'|'supervisor' }>
  = ({ require, role }) => {
  const { theme } = useTheme()
  if (!require) return null
  return (
    <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.secondary }} accessibilityLabel={`Approval required${role ? ' by ' + role : ''}`} accessibilityRole="text">
      <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Approval: {role || 'required'}</Text>
    </View>
  )
}

export default ApprovalBadge


