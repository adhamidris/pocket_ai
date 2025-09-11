import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { SkillTag } from '../../types/agents'

export interface SkillChipProps {
  tag: SkillTag
  onPress?: () => void
  selected?: boolean
  testID?: string
}

const levelText = (lvl?: 1|2|3|4|5) => lvl ? `L${lvl}` : ''

const SkillChip: React.FC<SkillChipProps> = ({ tag, onPress, selected, testID }) => {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={onPress}
      accessibilityLabel={`Skill ${tag.name} ${levelText(tag.level)}`}
      accessibilityRole={onPress ? 'button' : 'text'}
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{ minHeight: 44 }}
    >
      <Box px={12} py={8} radius={12} style={{ borderWidth: 1, borderColor: selected ? tokens.colors.primary : tokens.colors.border, backgroundColor: selected ? tokens.colors.card : 'transparent' }}>
        <Text size={12} weight="600" color={selected ? tokens.colors.primary : tokens.colors.mutedForeground}>
          {tag.name}{tag.level ? ` · ${levelText(tag.level)}` : ''}
        </Text>
      </Box>
    </TouchableOpacity>
  )
}

export default React.memo(SkillChip)



