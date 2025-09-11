import React from 'react'
import { View, Text, ScrollView, FlatList } from 'react-native'
import { tokens } from '../../ui/tokens'
import { BreakdownRow } from '../../types/analytics'

export interface BreakdownTableProps { rows: BreakdownRow[]; columns: string[]; testID?: string }

const BreakdownTable: React.FC<BreakdownTableProps> = ({ rows, columns, testID }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View>
          <View style={{ flexDirection: 'row', backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
            {columns.map((c, idx) => (
              <View key={idx} style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{c}</Text>
              </View>
            ))}
          </View>
          <FlatList
            data={rows}
            keyExtractor={(r) => r.name}
            renderItem={({ item: r }) => (
              <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                  <Text style={{ color: tokens.colors.cardForeground }}>{r.name}</Text>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                  <Text style={{ color: tokens.colors.cardForeground }}>{r.value}</Text>
                </View>
                {Object.entries(r.extra || {}).map(([k, v], i) => (
                  <View key={`${r.name}-${k}`} style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>{v ?? '—'}</Text>
                  </View>
                ))}
              </View>
            )}
            style={{ maxHeight: 260 }}
            removeClippedSubviews
            initialNumToRender={12}
            windowSize={10}
          />
        </View>
      </ScrollView>
    </View>
  )
}

export default React.memo(BreakdownTable)


