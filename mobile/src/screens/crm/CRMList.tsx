import React from 'react'
import { SafeAreaView, View, TextInput, FlatList, RefreshControl, TouchableOpacity, Text, DeviceEventEmitter } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation, useRoute } from '@react-navigation/native'
import { Contact, ConsentState } from '../../types/crm'
import { Channel } from '../../types/conversations'
import ContactRow from '../../components/crm/ContactRow'
import ListSkeleton from '../../components/crm/ListSkeleton'
import EmptyState from '../../components/crm/EmptyState'
import TagChip from '../../components/crm/TagChip'
import ImportExportSheet from '../../components/crm/ImportExportSheet'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import { track } from '../../lib/analytics'
import { DeviceEventEmitter } from 'react-native'
import { getFixtures } from '../../qa/fixtures'

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const genContacts = (n = 200): Contact[] => {
  const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown', 'Ava Davis', 'Sophia Wilson', 'Olivia Martin', 'Lucas Lee', 'Mia Clark', 'Amelia Walker']
  const tagsPool = ['Enterprise', 'Startup', 'VIP', 'Technical', 'Billing', 'Returns', 'Onboarding']
  const channels: Channel[] = ['whatsapp', 'instagram', 'facebook', 'web', 'email']
  const consents: ConsentState[] = ['granted', 'denied', 'unknown', 'withdrawn']
  const items: Contact[] = []
  for (let i = 0; i < n; i++) {
    const name = `${pick(names)} ${i + 1}`
    const email = Math.random() > 0.4 ? `${name.toLowerCase().replace(/\s+/g, '.')}@example.com` : undefined
    const phone = Math.random() > 0.6 ? `+1-555-${rand(100, 999)}-${rand(1000, 9999)}` : undefined
    const chs = Array.from(new Set([pick(channels), pick(channels), pick(channels)])).slice(0, rand(1, 3))
    const tags = Array.from(new Set([pick(tagsPool), pick(tagsPool), pick(tagsPool)])).slice(0, rand(1, 3))
    const vip = Math.random() > 0.85
    const last = Date.now() - rand(0, 1000 * 60 * 60 * 24 * 30)
    const ltv = Math.random() > 0.5 ? rand(100, 20000) : undefined
    const consent = pick(consents)
    items.push({ id: `ct-${i + 1}`, name, email, phone, channels: chs, tags, vip, lastInteractionTs: last, lifetimeValue: ltv, consent })
  }
  return items
}

type SortKey = 'recent' | 'name' | 'ltv'

export const CRMList: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const [query, setQuery] = React.useState('')
  const [debouncedQuery, setDebouncedQuery] = React.useState('')
  const [active, setActive] = React.useState<{ vip?: string; open?: string; channel?: Channel; tag?: string; consent?: ConsentState }>({})
  const [sort, setSort] = React.useState<SortKey>('recent')
  const [loading, setLoading] = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)
  const [data, setData] = React.useState<Contact[]>(() => getFixtures().contacts)
  const [hidePII, setHidePII] = React.useState<boolean>(false)
  const [highlightId, setHighlightId] = React.useState<string | null>(null)
  const listRef = React.useRef<FlatList<Contact>>(null)
  const [sheetOpen, setSheetOpen] = React.useState(false)
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, { vip?: boolean; tag?: boolean; consent?: boolean }>>({})
  const [debouncedActive, setDebouncedActive] = React.useState<typeof active>(active)
  const ITEM_HEIGHT = 108
  const [syncOpen, setSyncOpen] = React.useState(false)

  // Debounce query
  React.useEffect(() => {
    const h = setTimeout(() => setDebouncedQuery(query.trim().toLowerCase()), 300)
    return () => clearTimeout(h)
  }, [query])

  // Debounce heavy filters
  React.useEffect(() => {
    const h = setTimeout(() => setDebouncedActive(active), 250)
    return () => clearTimeout(h)
  }, [active])

  // Deep link to contactId
  React.useEffect(() => {
    const contactId = route.params?.contactId as string | undefined
    if (!contactId) return
    const index = data.findIndex((c) => c.id === contactId)
    if (index >= 0) {
      setHighlightId(contactId)
      requestAnimationFrame(() => {
        listRef.current?.scrollToIndex({ index, animated: true })
      })
      setTimeout(() => setHighlightId(null), 2000)
    }
  }, [route.params, data])

  React.useEffect(() => {
    track('crm.view')
    setLoading(true)
    const t = setTimeout(() => setLoading(false), 400)
    const sub = DeviceEventEmitter.addListener('qa.fixtures.changed', () => {
      try { setData(getFixtures().contacts) } catch {}
    })
    return () => { clearTimeout(t); sub.remove() }
  }, [])

  React.useEffect(() => {
    const sub = DeviceEventEmitter.addListener('privacy.modes', (vals: any) => setHidePII(!!vals?.hideContactPII))
    return () => sub.remove()
  }, [])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      setData((prev) => [...prev].reverse())
      setRefreshing(false)
    }, 600)
  }

  const filtered = React.useMemo(() => {
    let arr = data
    if (debouncedActive.vip) arr = arr.filter((c) => !!c.vip)
    // Has open conversation (stub: simulate by lastInteraction within 48h)
    if (debouncedActive.open) arr = arr.filter((c) => (c.lastInteractionTs || 0) > Date.now() - 1000 * 60 * 60 * 48)
    if (debouncedActive.channel) arr = arr.filter((c) => c.channels.includes(debouncedActive.channel!))
    if (debouncedActive.tag) arr = arr.filter((c) => c.tags.includes(debouncedActive.tag!))
    if (debouncedActive.consent) arr = arr.filter((c) => c.consent === debouncedActive.consent)
    if (debouncedQuery) {
      const q = debouncedQuery
      arr = arr.filter((c) =>
        c.name.toLowerCase().includes(q) ||
        (c.email || '').toLowerCase().includes(q) ||
        (c.phone || '').toLowerCase().includes(q) ||
        c.tags.some((t) => t.toLowerCase().includes(q))
      )
    }
    if (sort === 'recent') arr = [...arr].sort((a, b) => (b.lastInteractionTs || 0) - (a.lastInteractionTs || 0))
    if (sort === 'name') arr = [...arr].sort((a, b) => a.name.localeCompare(b.name))
    if (sort === 'ltv') arr = [...arr].sort((a, b) => (b.lifetimeValue || 0) - (a.lifetimeValue || 0))
    return arr
  }, [data, debouncedActive, debouncedQuery, sort])

  // Build A–Z index map for fast jumps
  const alpha = React.useMemo(() => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split(''), [])
  const alphaIndex = React.useMemo(() => {
    const map: Record<string, number> = {}
    for (let i = 0; i < filtered.length; i++) {
      const letter = (filtered[i].name[0] || '#').toUpperCase()
      if (!map[letter]) map[letter] = i
    }
    return map
  }, [filtered])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 32, fontWeight: '700' }}>CRM</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => setSheetOpen(true)} accessibilityLabel="Import/Export" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Import/Export</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('CRM', { screen: 'DedupeCenter' })} accessibilityLabel="Dedupe Center" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Dedupe</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('CRM', { screen: 'Segments' })} accessibilityLabel="Segments" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Segments</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle Offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Go Online' : 'Go Offline'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityLabel="Sync Center" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Sync</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Offline banner */}
        {offline && (
          <View style={{ marginBottom: 12 }}>
            <OfflineBanner visible testID="crm-offline" />
          </View>
        )}

        {/* Filter Bar */}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          <TagChip label="VIP" selected={!!active.vip} onPress={() => {
            const next = !active.vip ? '1' : undefined
            setActive((a) => ({ ...a, vip: next }))
            track('crm.filter', { key: 'vip', value: !!next })
          }} testID="crm-filter-vip" />
          <TagChip label="Open" selected={!!active.open} onPress={() => {
            const next = !active.open ? '1' : undefined
            setActive((a) => ({ ...a, open: next }))
            track('crm.filter', { key: 'open', value: !!next })
          }} testID="crm-filter-open" />
          <TagChip label={`Channel: ${active.channel || 'any'}`} onPress={() => {
            const next = active.channel ? undefined : 'whatsapp'
            setActive((a) => ({ ...a, channel: next as any }))
            track('crm.filter', { key: 'channel', value: next || 'any' })
          }} testID="crm-filter-channel" />
          <TagChip label={`Tag: ${active.tag || 'any'}`} onPress={() => {
            const next = active.tag ? undefined : 'VIP'
            setActive((a) => ({ ...a, tag: next }))
            track('crm.filter', { key: 'tag', value: next || 'any' })
          }} testID="crm-filter-tag" />
          <TagChip label={`Consent: ${active.consent || 'any'}`} onPress={() => {
            const next = active.consent ? undefined : 'granted'
            setActive((a) => ({ ...a, consent: next as any }))
            track('crm.filter', { key: 'consent', value: next || 'any' })
          }} testID="crm-filter-consent" />
        </View>

        {/* Sort */}
        <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
          <TagChip label="Recent" selected={sort === 'recent'} onPress={() => { setSort('recent'); track('crm.filter', { key: 'sort', value: 'recent' }) }} testID="crm-sort-recent" />
          <TagChip label="Name A→Z" selected={sort === 'name'} onPress={() => { setSort('name'); track('crm.filter', { key: 'sort', value: 'name' }) }} testID="crm-sort-name" />
          <TagChip label="LTV High→Low" selected={sort === 'ltv'} onPress={() => { setSort('ltv'); track('crm.filter', { key: 'sort', value: 'ltv' }) }} testID="crm-sort-ltv" />
        </View>

        {/* Search */}
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Search name, email, phone, tag"
            placeholderTextColor={theme.color.placeholder}
            style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
          />
        </View>
      </View>

      <View style={{ flex: 1, paddingHorizontal: 24, position: 'relative' }}>
        {loading ? (
          <ListSkeleton rows={12} testID="crm-skeleton" />
        ) : filtered.length === 0 ? (
          <EmptyState message="No contacts match your filters." testID="crm-empty" />
        ) : (
          <FlatList
            ref={listRef}
            testID="crm-list"
            data={filtered}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <View style={{ marginBottom: 12, backgroundColor: item.id === highlightId ? theme.color.accent : 'transparent', borderRadius: 12 }}>
                <ContactRow
                  item={item}
                  onPress={(id) => navigation.navigate('Conversations', { contactId: id })}
                  onToggleVip={(id) => {
                    track('crm.vip', { id, value: !item.vip })
                    if (!offline) return
                    setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), vip: true } }))
                    setTimeout(() => setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), vip: false } })), 1500)
                  }}
                  onAddRemoveTag={(id) => {
                    track('crm.tag', { id, action: 'toggle' })
                    if (!offline) return
                    setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), tag: true } }))
                    setTimeout(() => setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), tag: false } })), 1500)
                  }}
                  onSetConsent={(id) => {
                    if (!offline) return
                    setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), consent: true } }))
                    setTimeout(() => setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), consent: false } })), 1500)
                  }}
                  queuedVip={queued[item.id]?.vip}
                  queuedTag={queued[item.id]?.tag}
                  queuedConsent={queued[item.id]?.consent}
                  hidePII={hidePII}
                />
              </View>
            )}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            initialNumToRender={20}
            maxToRenderPerBatch={20}
            windowSize={12}
            removeClippedSubviews
            getItemLayout={(_, index) => ({ length: ITEM_HEIGHT, offset: ITEM_HEIGHT * index, index })}
            onScrollToIndexFailed={(info) => {
              // Fallback: wait for layout and retry
              setTimeout(() => listRef.current?.scrollToIndex({ index: Math.min(info.index, filtered.length - 1), animated: true }), 50)
            }}
          />
        )}

        {/* A–Z jump sidebar */}
        {filtered.length > 30 && (
          <View style={{ position: 'absolute', right: 0, top: 0, bottom: 0, paddingVertical: 8, justifyContent: 'center' }} pointerEvents="box-none">
            <View style={{ paddingHorizontal: 6, paddingVertical: 4, borderRadius: 12, backgroundColor: theme.color.muted }}>
              {alpha.map((ch) => {
                const enabled = alphaIndex[ch] !== undefined
                return (
                  <TouchableOpacity
                    key={ch}
                    onPress={() => enabled && listRef.current?.scrollToIndex({ index: alphaIndex[ch], animated: true })}
                    disabled={!enabled}
                    accessibilityLabel={`Jump to ${ch}`}
                    accessibilityRole="button"
                    style={{ paddingHorizontal: 4, paddingVertical: 2, opacity: enabled ? 1 : 0.3 }}
                  >
                    <Text style={{ color: theme.color.cardForeground, fontSize: 10, fontWeight: '700' }}>{ch}</Text>
                  </TouchableOpacity>
                )
              })}
            </View>
          </View>
        )}
      </View>
      <ImportExportSheet
        visible={sheetOpen}
        onClose={() => setSheetOpen(false)}
        onApplyImport={(items) => setData((prev) => [...items, ...prev])}
      />
      <SyncCenterSheet
        visible={syncOpen}
        onClose={() => setSyncOpen(false)}
        lastSyncAt={new Date().toLocaleString()}
        queuedCount={Object.values(queued).reduce((acc, v) => acc + (v.vip ? 1 : 0) + (v.tag ? 1 : 0) + (v.consent ? 1 : 0), 0)}
        onRetryAll={() => setSyncOpen(false)}
      />
    </SafeAreaView>
  )
}

export default CRMList


