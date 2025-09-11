import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'
import { FeatureLimit } from '../../types/billing'

export interface FeatureListProps { features: FeatureLimit[]; testID?: string }

const FeatureList: React.FC<FeatureListProps> = ({ features, testID }) => {
  return (
    <View testID={testID} accessibilityLabel="Feature list" style={{ gap: 6 }}>
      {features.map((f, idx) => (
        <View key={`${f.key}-${idx}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <View style={{ width: 6, height: 6, borderRadius: 6, backgroundColor: tokens.colors.primary }} />
          <Text style={{ color: tokens.colors.cardForeground }}>
            {f.key}: {typeof f.value === 'number' ? f.value : 'Unlimited'}{f.note ? ` (${f.note})` : ''}
          </Text>
        </View>
      ))}
    </View>
  )
}

export default React.memo(FeatureList)


