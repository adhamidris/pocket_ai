import React from 'react'
import { View } from 'react-native'
import { CoverageStat } from '../../types/knowledge'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface CoverageTileProps { stat: CoverageStat; testID?: string }

const CoverageTile: React.FC<CoverageTileProps> = ({ stat, testID }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, backgroundColor: tokens.colors.card }}>
      <Text size={12} color={tokens.colors.mutedForeground}>Coverage</Text>
      <Text size={18} weight="700" color={tokens.colors.cardForeground}>{stat.coveragePct}%</Text>
      <Text size={12} color={tokens.colors.mutedForeground}>{stat.topics} topics • {stat.faqs} FAQs • {stat.gaps} gaps</Text>
    </View>
  )
}

export default React.memo(CoverageTile)


