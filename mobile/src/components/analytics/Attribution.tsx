import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'
import { AttributionRow } from '../../types/analytics'
import EntitlementsGate from '../billing/EntitlementsGate'

export interface AttributionProps { testID?: string }

const genRows = (): AttributionRow[] => (
  [
    { source: 'google', medium: 'cpc', campaign: 'brand', volume: 420, conversions: 180, resolutionRate: 92 },
    { source: 'facebook', medium: 'cpm', campaign: 'prospecting', volume: 280, conversions: 90, resolutionRate: 88 },
    { source: 'instagram', medium: 'cpm', campaign: 'retargeting', volume: 180, conversions: 70, resolutionRate: 90 },
    { source: 'email', medium: 'newsletter', campaign: 'oct', volume: 110, conversions: 55, resolutionRate: 94 },
    { source: 'direct', medium: undefined, campaign: undefined, volume: 360, conversions: 190, resolutionRate: 95 },
  ]
)

const Attribution: React.FC<AttributionProps> = ({ testID }) => {
  const navigation = useNavigation<any>()
  const [rows, setRows] = React.useState<AttributionRow[]>(() => genRows())
  const [campaign, setCampaign] = React.useState<string | undefined>(undefined)

  const campaigns = React.useMemo(() => Array.from(new Set(rows.map((r) => r.campaign).filter(Boolean))) as string[], [rows])
  const filtered = React.useMemo(() => rows.filter((r) => !campaign || r.campaign === campaign), [rows, campaign])

  const openUtmBuilder = () => { try { (require('../../lib/analytics') as any).track('analytics.cta', { target: 'utm_builder' }) } catch {}; navigation.navigate('Channels', { screen: 'UtmBuilder' }) }
  const openConversationsForSource = (src: string) => { try { (require('../../lib/analytics') as any).track('analytics.cta', { target: 'open_conversations_source' }) } catch {}; navigation.navigate('Conversations', { screen: 'ConversationsHome', params: { prefill: src } }) }

  return (
    <EntitlementsGate require="analyticsDepth">
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Attribution (UTM & Source)</Text>
      </View>
      <View style={{ padding: 12, gap: 12 }}>
        {/* Filter + CTA */}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, justifyContent: 'space-between' }}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={() => setCampaign(undefined)} accessibilityRole="button" accessibilityLabel="All campaigns" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: campaign ? tokens.colors.border : tokens.colors.primary }}>
                <Text style={{ color: campaign ? tokens.colors.mutedForeground : tokens.colors.primary, fontWeight: '600' }}>All campaigns</Text>
              </TouchableOpacity>
              {campaigns.map((c) => (
                <TouchableOpacity key={c} onPress={() => setCampaign(c)} accessibilityRole="button" accessibilityLabel={`Filter ${c}`} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: campaign === c ? tokens.colors.primary : tokens.colors.border }}>
                  <Text style={{ color: campaign === c ? tokens.colors.primary : tokens.colors.mutedForeground, fontWeight: '600' }}>{c}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </ScrollView>
          <TouchableOpacity onPress={openUtmBuilder} accessibilityRole="button" accessibilityLabel="Open UTM Builder" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: tokens.colors.primary }}>
            <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Open UTM Builder</Text>
          </TouchableOpacity>
        </View>

        {/* Table */}
        <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View>
              <View style={{ flexDirection: 'row', backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
                {['Source', 'Medium', 'Campaign', 'Volume', 'Conversions*', 'Resolution %', 'Actions'].map((c, idx) => (
                  <View key={idx} style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                    <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{c}</Text>
                  </View>
                ))}
              </View>
              <ScrollView style={{ maxHeight: 320 }}>
                {filtered.map((r, idx) => (
                  <View key={idx} style={{ flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                      <Text style={{ color: tokens.colors.cardForeground }}>{r.source}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                      <Text style={{ color: tokens.colors.cardForeground }}>{r.medium || '—'}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 160 }}>
                      <Text style={{ color: tokens.colors.cardForeground }}>{r.campaign || '—'}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                      <Text style={{ color: tokens.colors.cardForeground }}>{r.volume}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                      <Text style={{ color: tokens.colors.cardForeground }}>{r.conversions ?? '—'}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                      <Text style={{ color: tokens.colors.cardForeground }}>{r.resolutionRate ?? '—'}{r.resolutionRate != null ? '%' : ''}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 200 }}>
                      <TouchableOpacity onPress={() => openConversationsForSource(r.source)} accessibilityRole="button" accessibilityLabel={`Open conversations ${r.source}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border }}>
                        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>Open Conversations</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                ))}
              </ScrollView>
            </View>
          </ScrollView>
        </View>
        <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12 }}>* Conversions = resolved conversations (UI‑only)</Text>
      </View>
    </View>
    </EntitlementsGate>
  )
}

export default React.memo(Attribution)

