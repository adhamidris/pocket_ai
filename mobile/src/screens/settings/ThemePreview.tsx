import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'

const ThemePreview: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const themeId: string = route.params?.themeId || 'default'
  const onApply: undefined | ((v: { name?: string; color?: string }) => void) = route.params?.onApply

  const [name, setName] = React.useState('Default Theme')
  const [color, setColor] = React.useState('#3B82F6')

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Theme Preview ({themeId})</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ padding: 16, gap: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: theme.color.mutedForeground, width: 80 }}>Name</Text>
          <TextInput value={name} onChangeText={setName} placeholder="Theme name" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: theme.color.mutedForeground, width: 80 }}>Primary</Text>
          <TextInput value={color} onChangeText={setColor} placeholder="#3B82F6" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
          <View style={{ width: 20, height: 20, borderRadius: 10, backgroundColor: color, borderWidth: 1, borderColor: theme.color.border }} />
        </View>
        <TouchableOpacity onPress={() => { onApply && onApply({ name, color }); navigation.goBack() }} accessibilityLabel="Apply" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Apply</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  )
}

export default ThemePreview


