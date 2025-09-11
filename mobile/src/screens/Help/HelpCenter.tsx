import React, { useEffect, useMemo, useState } from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'
import { SearchBar } from '../../components/help/SearchBar'
import { SearchResults } from '../../components/help/SearchResults'
import { ChecklistCard } from '../../components/help/ChecklistCard'
import { ReleaseNotes } from '../../components/help/ReleaseNotes'
import { Article, Checklist, Tour } from '../../types/help'
import { defaultTours } from '../../content/help/tours'
import { releaseNotes } from '../../content/help/releaseNotes'
import AsyncStorage from '@react-native-async-storage/async-storage'
import * as Network from 'expo-network'

type TabKey = 'search' | 'quickstart' | 'tours' | 'whatsnew'

const demoArticles: Article[] = [
  { id: 'a1', kind: 'howto', title: 'Connect your first channel', bodyMd: '**Step 1**: Open Channels.\n\n**Step 2**: Select WhatsApp.\n\n**Done!**', tags: ['Channels'], updatedAt: Date.now() - 86400000 },
  { id: 'a2', kind: 'guide', title: 'Training your Knowledge base', bodyMd: 'Upload FAQs and link your docs.', tags: ['Knowledge'], updatedAt: Date.now() - 7200000 },
  { id: 'a3', kind: 'faq', title: 'Automations basics', bodyMd: 'Rules, triggers, and actions.', tags: ['Automations'], updatedAt: Date.now() - 3600000 },
]

const demoChecklist: Checklist = {
  id: 'quickstart',
  title: 'Quickstart Checklist',
  progress: 0,
  steps: [
    { id: 'c1', title: 'Connect a channel', done: false, cta: { label: 'Open Channels', route: 'Channels' } },
    { id: 'c2', title: 'Publish chat link', done: false, cta: { label: 'Copy link', route: 'Channels' } },
    { id: 'c3', title: 'Train FAQs', done: false, cta: { label: 'Open Knowledge', route: 'Knowledge' } },
    { id: 'c4', title: 'Set business hours', done: false, cta: { label: 'Set hours', route: 'Automations' } },
    { id: 'c5', title: 'Test portal', done: false, cta: { label: 'Open Portal', route: 'Portal' } },
    { id: 'c6', title: 'Create a rule', done: false, cta: { label: 'New Rule', route: 'Automations' } },
  ]
}

const demoTours: Tour[] = defaultTours

const demoNotes = releaseNotes

const Pill: React.FC<{ active?: boolean; label: string; onPress: () => void }>=({ active, label, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border, backgroundColor: active ? theme.color.secondary : 'transparent' }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.cardForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const HelpCenter: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const [tab, setTab] = useState<TabKey>(route.params?.initialTab ?? 'search')
  const [query, setQuery] = useState(route.params?.initialQuery ?? '')
  const [tag, setTag] = useState<string | null>(route.params?.initialTag ?? null)
  const [checklist, setChecklist] = useState<Checklist>(demoChecklist)
  const [offline, setOffline] = useState<boolean>(false)

  useEffect(() => { track('help.center.view') }, [])
  useEffect(() => {
    const h = setTimeout(() => { if (query?.trim()) track('help.search', { q: query.trim() }) }, 400)
    return () => clearTimeout(h)
  }, [query])
  useEffect(() => {
    // Prefer event listener if available; otherwise poll periodically
    const anyNetwork: any = Network as any
    if (anyNetwork?.addNetworkStateListener) {
      const sub = anyNetwork.addNetworkStateListener((state: any) => setOffline(!state?.isConnected))
      return () => {
        try { sub?.remove?.() } catch {}
      }
    } else {
      let mounted = true
      const check = async () => {
        try {
          const s = await Network.getNetworkStateAsync()
          if (mounted) setOffline(!(s?.isConnected))
        } catch {}
      }
      check()
      const id = setInterval(check, 3000)
      return () => { mounted = false; clearInterval(id) }
    }
  }, [])

  const filtered = useMemo(() => {
    return demoArticles.filter(a => {
      const q = query.trim().toLowerCase()
      const qOk = !q || a.title.toLowerCase().includes(q) || a.bodyMd.toLowerCase().includes(q)
      const tagOk = !tag || a.tags.includes(tag)
      return qOk && tagOk
    })
  }, [query, tag])

  const openTourSurface = (surface: Tour['surface']) => {
    const map: Record<Tour['surface'], string> = {
      dashboard: 'Dashboard',
      conversations: 'Conversations',
      knowledge: 'Knowledge',
      channels: 'Channels',
      automations: 'Automations',
      analytics: 'Analytics',
      settings: 'Settings',
      portal: 'Portal',
      actions: 'Actions',
    }
    const screen = map[surface] || 'Dashboard'
    navigation.navigate('Main', { screen })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Help & Docs</Text>
            <View style={{ width: 64 }} />
          </View>
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
            <Pill label="Search" active={tab==='search'} onPress={() => setTab('search')} />
            <Pill label="Quickstart" active={tab==='quickstart'} onPress={() => setTab('quickstart')} />
            <Pill label="Tours" active={tab==='tours'} onPress={() => setTab('tours')} />
            <Pill label="What’s New" active={tab==='whatsnew'} onPress={() => setTab('whatsnew')} />
          </View>
        </View>

        {/* Body */}
        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          {tab === 'search' && (
            <View>
              {offline && (
                <View style={{ alignSelf: 'flex-start', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, marginBottom: 8 }}>
                  <Text style={{ color: theme.color.mutedForeground }}>Cached</Text>
                </View>
              )}
              <SearchBar value={query} onChange={setQuery} />
              <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
                {['Channels','Knowledge','Automations'].map(t => (
                  <Pill key={t} label={t} active={tag===t} onPress={() => setTag(tag===t? null : t)} />
                ))}
              </View>
              <SearchResults results={filtered} onSelect={(a) => { /* open article viewer route later */ }} />
            </View>
          )}

          {tab === 'quickstart' && (
            <ChecklistCard
              checklist={checklist}
              onToggleStep={(id) => setChecklist(prev => ({ ...prev, steps: prev.steps.map(s => s.id === id ? { ...s, done: !s.done } : s) }))}
              onCtaPress={(s) => navigation.navigate(s.cta?.route || 'Dashboard')}
            />
          )}

          {tab === 'tours' && (
            <View style={{ gap: 8 }}>
              {demoTours.map(t => (
                <View key={t.id} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <View>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{t.title}</Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{t.completed ? 'Completed' : t.eligible ? 'Ready' : 'Unavailable'}</Text>
                  </View>
                  <TouchableOpacity onPress={() => { try { track('help.tour.start', { id: t.id }) } catch {}; openTourSurface(t.surface); navigation.navigate('TourRunner', { tour: t }) }} accessibilityRole="button" accessibilityLabel={`Start ${t.title}`} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
                    <Text style={{ color: '#fff', fontWeight: '700' }}>{t.completed ? 'Resume' : 'Start'}</Text>
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}

          {tab === 'whatsnew' && (
            <ReleaseNotes notes={demoNotes} unseenVersionId={undefined} />
          )}

          {/* Footer links */}
          <View style={{ flexDirection: 'row', gap: 16, marginTop: 16 }}>
            <TouchableOpacity onPress={() => navigation.navigate('Feedback')} accessibilityRole="button" accessibilityLabel="Feedback" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Feedback</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Main', { screen: 'Settings' })} accessibilityRole="button" accessibilityLabel="Contact" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Contact</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Main', { screen: 'Security' })} accessibilityRole="button" accessibilityLabel="Privacy" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Privacy</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default HelpCenter
