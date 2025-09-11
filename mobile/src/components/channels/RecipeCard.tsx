import React from 'react'
import { View, TouchableOpacity } from 'react-native'
import { RecipeItem } from '../../types/channels'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface RecipeCardProps {
  recipe: RecipeItem
  onCopyTemplate: () => void
  testID?: string
}

const RecipeCard: React.FC<RecipeCardProps> = ({ recipe, onCopyTemplate, testID }) => {
  const [open, setOpen] = React.useState(false)
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 16, backgroundColor: tokens.colors.card, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text size={14} weight="700" color={tokens.colors.cardForeground}>{recipe.title}</Text>
        <TouchableOpacity onPress={() => setOpen((v) => !v)} accessibilityLabel={`Toggle steps for ${recipe.title}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8 }}>
          <Text size={12} color={tokens.colors.cardForeground}>{open ? 'Hide' : 'Show'}</Text>
        </TouchableOpacity>
      </View>
      {open && (
        <View style={{ marginTop: 8, gap: 6 }}>
          {recipe.steps.map((s, idx) => (
            <Text key={idx} size={12} color={tokens.colors.mutedForeground}>• {s}</Text>
          ))}
        </View>
      )}
      <TouchableOpacity onPress={onCopyTemplate} accessibilityLabel={`Copy template for ${recipe.title}`} accessibilityRole="button" style={{ marginTop: 10, alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
        <Text size={13} weight="600" color={tokens.colors.primary}>{recipe.ctaLabel}</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(RecipeCard)



