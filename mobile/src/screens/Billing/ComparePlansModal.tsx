import React from 'react'
import { Modal, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Plan } from '../../types/billing'
import FeatureList from '../../components/billing/FeatureList'

export interface ComparePlansModalProps { visible: boolean; onClose: () => void; plans: Plan[] }

const ComparePlansModal: React.FC<ComparePlansModalProps> = ({ visible, onClose, plans }) => {
  const { theme } = useTheme()
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center', alignItems: 'center', padding: 16 }}>
        <View style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, maxHeight: '80%', width: '100%' }}>
          <View style={{ padding: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700' }}>Compare Plans</Text>
            <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel="Close compare" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Close</Text>
            </TouchableOpacity>
          </View>
          <ScrollView contentContainerStyle={{ padding: 12 }}>
            {plans.map((p) => (
              <View key={p.id} style={{ marginBottom: 12 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>{p.name}</Text>
                <FeatureList features={p.features} />
              </View>
            ))}
          </ScrollView>
        </View>
      </View>
    </Modal>
  )
}

export default ComparePlansModal


