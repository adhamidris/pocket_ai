import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'
import StackedBarChart from './StackedBarChart'
import BreakdownTable from './BreakdownTable'
import { BreakdownRow } from '../../types/analytics'
import { SocialChannel } from '../../types/channels'

export interface ChannelBreakdownProps { testID?: string }

const ALL_CHANNELS: SocialChannel[] = ['web', 'whatsapp', 'instagram', 'facebook']

const ChannelBreakdown: React.FC<ChannelBreakdownProps> = ({ testID }) => {
  const navigation = useNavigation<any>()

  const [included, setIncluded] = React.useState<Record<SocialChannel, boolean>>({
    web: true,
    whatsapp: true,
    instagram: true,
    facebook: true,
  })

  const toggle = (ch: SocialChannel) => setIncluded((m) => ({ ...m, [ch]: !m[ch] }))

  const stacks = React.useMemo(() => {
    const channels = ALL_CHANNELS.filter((c) => included[c])
    return channels.map((ch) => ({
      label: ch.toUpperCase(),
      parts: [
        { name: 'Resolved', value: Math.round(40 + Math.random() * 60) },
        { name: 'Open', value: Math.round(10 + Math.random() * 30) },
      ],
    }))
  }, [included.web, included.whatsapp, included.instagram, included.facebook])

  const rows: BreakdownRow[] = React.useMemo(() => {
    const mk = (name: string): BreakdownRow => ({
      name,
      value: Math.round(200 + Math.random() * 600),
      extra: {
        frtP50: Math.round(30 + Math.random() * 40),
        resolutionRate: Math.round(70 + Math.random() * 25),
        csat: Math.round(85 + Math.random() * 10),
        deflection: Math.round(30 + Math.random() * 40),
      },
    })
    return ALL_CHANNELS.filter((c) => included[c]).map((c) => mk(c))
  }, [included.web, included.whatsapp, included.instagram, included.facebook])

  const onOpenRecipe = (channel: SocialChannel) => {
    navigation.navigate('Channels', {
      screen: 'RecipeCenter',
      params: { channel },
    })
  }
  const onOpenConversations = (channel: SocialChannel) => {
    navigation.navigate('Conversations', { screen: 'Conversations', params: { filter: undefined, channel } })
  }

  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Channel Breakdown</Text>
      </View>
      <View style={{ padding: 12, gap: 12 }}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {ALL_CHANNELS.map((ch) => (
              <TouchableOpacity key={ch} onPress={() => toggle(ch)} accessibilityRole="button" accessibilityLabel={`Toggle ${ch}`} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: included[ch] ? tokens.colors.primary : tokens.colors.border }}>
                <Text style={{ color: included[ch] ? tokens.colors.primary : tokens.colors.mutedForeground }}>{included[ch] ? 'Include' : 'Exclude'} {ch}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </ScrollView>

        <StackedBarChart stacks={stacks} testID="an-channel-stacked" />
        <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12 }}>Compare volume by channel. Tap actions to open conversations or recipes.</Text>

        <BreakdownTable
          rows={rows}
          columns={[
            'Channel',
            'Volume',
            'FRT P50 (m)',
            'Resolution %',
            'CSAT',
            'Deflection %',
            'Actions',
          ]}
          testID="an-channel-table"
        />

        <View style={{ gap: 8 }}>
          {ALL_CHANNELS.filter((c) => included[c]).map((ch) => (
            <View key={ch} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: tokens.colors.cardForeground }}>{ch.toUpperCase()}</Text>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <TouchableOpacity onPress={() => onOpenConversations(ch)} accessibilityRole="button" accessibilityLabel={`Open Conversations ${ch}`} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border }}>
                  <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>Open Conversations</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => onOpenRecipe(ch)} accessibilityRole="button" accessibilityLabel={`Open Recipe ${ch}`} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: tokens.colors.primary }}>
                  <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Open Recipe</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))}
        </View>
      </View>
    </View>
  )
}

export default React.memo(ChannelBreakdown)

 

