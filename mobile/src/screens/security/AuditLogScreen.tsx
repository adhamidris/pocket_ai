import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, Modal, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { AuditEvent, Risk } from '../../types/security'
import AuditRow from '../../components/security/AuditRow'
import { track } from '../../lib/analytics'

const now = () => Date.now()

const genAudit = (): AuditEvent[] => Array.from({ length: 120 }).map((_, i) => ({
  id: `a-${i}`,
  ts: now() - i * 3600_000,
  actor: (['me','system','ai','agent'] as const)[i % 4],
  action: i % 3 === 0 ? 'export.create' : i % 3 === 1 ? 'policy.update' : 'login'
  ,
  entityType: (['conversation','contact','policy','rule','agent','export','deletion','login','session'] as const)[i % 9],
  entityId: String(1000 + i),
  details: i % 2 ? 'Detail text lorem ipsum' : undefined,
  risk: (['low','medium','high'] as Risk[])[i % 3]
}))

const Chip: React.FC<{ label: string; active?: boolean; onPress: () => void }>=({ label, active, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const AuditLogScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [data, setData] = React.useState<AuditEvent[]>(() => genAudit())
  const [timeWin, setTimeWin] = React.useState<'24h'|'7d'|'30d'|undefined>('24h')
  const [actor, setActor] = React.useState<'me'|'system'|'ai'|'agent'|undefined>()
  const [entity, setEntity] = React.useState<AuditEvent['entityType'] | undefined>()
  const [risk, setRisk] = React.useState<Risk | undefined>()
  const [selected, setSelected] = React.useState<AuditEvent | null>(null)

  const filtered = React.useMemo(() => {
    let arr = data
    if (timeWin) {
      const nowTs = now()
      const win = timeWin === '24h' ? 86400000 : timeWin === '7d' ? 604800000 : 2592000000
      arr = arr.filter((e) => e.ts >= nowTs - win)
    }
    if (actor) arr = arr.filter((e) => e.actor === actor)
    if (entity) arr = arr.filter((e) => e.entityType === entity)
    if (risk) arr = arr.filter((e) => e.risk === risk)
    return arr
  }, [data, timeWin, actor, entity, risk])

  const exportData = (kind: 'json'|'csv') => {
    const payload = filtered
    if (kind === 'json') {
      // eslint-disable-next-line no-console
      console.log('audit.export.json', JSON.stringify(payload.slice(0, 100)))
    } else {
      const csv = ['id,ts,actor,action,entityType,entityId,risk', ...payload.map(e => `${e.id},${e.ts},${e.actor},${e.action},${e.entityType},${e.entityId || ''},${e.risk}`)].join('\n')
      // eslint-disable-next-line no-console
      console.log('audit.export.csv', csv.slice(0, 500))
    }
    try { track('audit.export', { type: kind }) } catch {}
    Alert.alert('Export', `${kind.toUpperCase()} generated (stub).`)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Audit Log</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => exportData('json')} accessibilityRole="button" accessibilityLabel="Export JSON" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Export JSON</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => exportData('csv')} accessibilityRole="button" accessibilityLabel="Export CSV" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Export CSV</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Filters */}
      <View style={{ paddingHorizontal: 24, marginBottom: 8 }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {(['24h','7d','30d'] as const).map((t) => (
            <Chip key={t} label={`Time ${t}`} active={timeWin === t} onPress={() => setTimeWin(timeWin === t ? undefined : t)} />
          ))}
          {(['me','system','ai','agent'] as const).map((a) => (
            <Chip key={a} label={`Actor ${a}`} active={actor === a} onPress={() => setActor(actor === a ? undefined : a)} />
          ))}
          {(['conversation','contact','policy','rule','agent','export','deletion','login','session'] as const).map((en) => (
            <Chip key={en} label={`Entity ${en}`} active={entity === en} onPress={() => setEntity(entity === en ? undefined : en)} />
          ))}
          {(['low','medium','high'] as Risk[]).map((r) => (
            <Chip key={r} label={`Risk ${r}`} active={risk === r} onPress={() => setRisk(risk === r ? undefined : r)} />
          ))}
        </View>
      </View>

      {/* List */}
      <FlatList
        data={filtered}
        keyExtractor={(e) => e.id}
        renderItem={({ item }) => (
          <AuditRow evt={item} onPress={() => setSelected(item)} />
        )}
        initialNumToRender={20}
        maxToRenderPerBatch={20}
        windowSize={11}
        removeClippedSubviews
        contentContainerStyle={{ paddingHorizontal: 24, paddingBottom: 24 }}
      />

      {/* Details sheet */}
      <Modal visible={!!selected} transparent animationType="slide" onRequestClose={() => setSelected(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Event details</Text>
            {selected ? (
              <View>
                <Text style={{ color: theme.color.cardForeground }}>{selected.action}</Text>
                <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>At: {new Date(selected.ts).toLocaleString()}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Actor: {selected.actor}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Entity: {selected.entityType} {selected.entityId || ''}</Text>
                {selected.details ? <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>{selected.details}</Text> : null}
              </View>
            ) : null}
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 12 }}>
              <TouchableOpacity onPress={() => setSelected(null)} accessibilityRole="button" accessibilityLabel="Close" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Close</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default AuditLogScreen


