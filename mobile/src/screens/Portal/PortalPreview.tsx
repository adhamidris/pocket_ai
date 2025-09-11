import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import PortalHeader from '../../components/portal/PortalHeader'
import Composer from '../../components/portal/Composer'
import Bubble from '../../components/portal/Bubble'
import TypingIndicator from '../../components/portal/TypingIndicator'
import SystemNotice from '../../components/portal/SystemNotice'
import QuickRepliesBar from '../../components/portal/QuickRepliesBar'
import ActionSuggestions from '../../components/portal/ActionSuggestions'
import { ChatMessage, ComposerState, MsgPartFile } from '../../types/portal'
import VoiceBar from '../../components/portal/VoiceBar'
import HandoffBar from '../../components/portal/HandoffBar'
import CsatPanel from '../../components/portal/CsatPanel'
import TranscriptForm from '../../components/portal/TranscriptForm'
import { PortalThemeProvider, useThemeTokens } from '../../components/portal/theme'
import OfflineBadge from '../../components/portal/OfflineBadge'
import { EmptyStateGuide } from '../../components/help/EmptyStateGuide'

const maskPii = (text: string): string => {
  // rudimentary masking for emails and phones
  const emailRe = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g
  const phoneRe = /\+?\d[\d\-\s]{6,}\d/g
  return text.replace(emailRe, '***@***').replace(phoneRe, '***')
}

const seedTranscript = (): ChatMessage[] => {
  const now = Date.now()
  return [
    { id: 'm1', at: now - 60000, sender: 'system', parts: [{ kind: 'notice', tone: 'info', text: 'Welcome to Acme Support!' }] as any },
    { id: 'm2', at: now - 50000, sender: 'ai', parts: [{ kind: 'card', title: 'Hi, I am your assistant', subtitle: 'I can help with orders and returns', body: 'Choose an option below or ask a question.' }] as any, meta: { delivered: true } },
    { id: 'm3', at: now - 40000, sender: 'customer', parts: [{ kind: 'text', text: 'Where is my order?' }] as any, meta: { delivered: true, read: true } },
    { id: 'm4', at: now - 30000, sender: 'ai', parts: [
      { kind: 'text', text: 'Please provide your order number or email.' } as any,
      { kind: 'buttons', actions: [{ id: 'check_order', label: 'Check order status', style: 'primary' }] } as any,
    ] as any },
    { id: 'm5', at: now - 20000, sender: 'ai', parts: [{ kind: 'image', url: 'https://placekitten.com/300/200', alt: 'Sample' }] as any },
    { id: 'm6', at: now - 15000, sender: 'ai', parts: [{ kind: 'file', name: 'faq.pdf', sizeKB: 42, mime: 'application/pdf' }] as any },
  ]
}

const PortalPreview: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [messages, setMessages] = React.useState<ChatMessage[]>(seedTranscript())
  const [composer, setComposer] = React.useState<ComposerState>({ text: '', attachments: [] })
  const [typing, setTyping] = React.useState<boolean>(false)
  const [allowVoice, setAllowVoice] = React.useState<boolean>(false)
  const [allowUploads, setAllowUploads] = React.useState<boolean>(true)
  const [prechat, setPrechat] = React.useState<boolean>(false)
  const [consent, setConsent] = React.useState<boolean>(false)
  const [hidePii, setHidePii] = React.useState<boolean>(false)
  const [showQuick, setShowQuick] = React.useState<boolean>(true)
  const [showActions, setShowActions] = React.useState<boolean>(true)
  const [session, setSession] = React.useState<'connecting'|'connected'|'queued'|'offline'|'ended'>('connected')
  const [queue, setQueue] = React.useState<{ position: number; etaMins?: number } | undefined>(undefined)
  const [csatOpen, setCsatOpen] = React.useState(false)
  const [csat, setCsat] = React.useState<{ rating?: 1|2|3|4|5; comment?: string }>({})
  const [csatSubmitted, setCsatSubmitted] = React.useState(false)
  const [transcriptEmail, setTranscriptEmail] = React.useState('')
  const [highContrast, setHighContrast] = React.useState(false)
  const tokens = useThemeTokens({ highContrast })
  const [sentTimestamps, setSentTimestamps] = React.useState<number[]>([])
  const [cooldown, setCooldown] = React.useState<number>(0)
  const [notice, setNotice] = React.useState<string | undefined>(undefined)
  const [offline, setOffline] = React.useState<boolean>(false)
  const prevOffline = React.useRef<boolean>(false)

  const addMessage = (m: ChatMessage) => setMessages((arr) => [...arr, m])

  const onSend = () => {
    const text = composer.text?.trim()
    if (!text) return
    const bad = ['damn','fuck']
    if (bad.some((w) => text.toLowerCase().includes(w))) {
      setNotice('Please avoid profanity.')
      setTimeout(() => setNotice(undefined), 1500)
      return
    }
    const nowCheck = Date.now()
    const windowed = sentTimestamps.filter((t) => nowCheck - t < 10000)
    if (windowed.length >= 5) {
      setCooldown(3)
      const timer = setInterval(() => setCooldown((c) => { if (c <= 1) { clearInterval(timer as any); return 0 } return c - 1 }), 1000)
      return
    }
    setSentTimestamps([...windowed, nowCheck])
    const now = Date.now()
    const parts: any[] = [{ kind: 'text', text }]
    // attach files as uploading bubbles
    for (const f of composer.attachments) {
      parts.push({ kind: 'file', name: f.name, sizeKB: f.sizeKB, mime: f.mime })
    }
    addMessage({ id: `c-${now}`, at: now, sender: 'customer', parts, meta: { delivered: true } })
    try { (require('../../lib/analytics') as any).track('portal.message.send', { sender: 'customer' }) } catch {}
    setComposer((c) => ({ ...c, text: '', attachments: [] }))
    setTyping(true)
    setTimeout(() => {
      const reply: ChatMessage = { id: `a-${Date.now()}`, at: Date.now(), sender: 'ai', parts: [{ kind: 'text', text: 'Here is a helpful answer based on your question.' }] as any, meta: { delivered: true } }
      addMessage(reply)
      try { (require('../../lib/analytics') as any).track('portal.message.send', { sender: 'ai' }) } catch {}
      setTyping(false)
    }, 1200)
  }

  const onAttach = () => {
    if (!allowUploads) return
    const file: MsgPartFile = { kind: 'file', name: `img_${Date.now()}.png`, sizeKB: 128, mime: 'image/png' }
    setComposer((c) => ({ ...c, attachments: [...c.attachments, file] }))
  }

  const renderItem = ({ item }: { item: ChatMessage }) => {
    let mapped = item
    if (hidePii) {
      mapped = {
        ...item,
        parts: item.parts.map((p: any) => p.kind === 'text' ? { ...p, text: maskPii(p.text) } : p)
      }
    }
    return <Bubble msg={mapped} enableTts={allowVoice} />
  }

  const Header: React.FC = () => (
    <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Portal Preview</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <TouchableOpacity onPress={() => setPrechat((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle pre-chat" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: prechat ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: prechat ? theme.color.primary : theme.color.cardForeground }}>Pre‑chat</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setConsent((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle consent" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: consent ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: consent ? theme.color.primary : theme.color.cardForeground }}>Consent</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setAllowVoice((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle voice" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: allowVoice ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: allowVoice ? theme.color.primary : theme.color.cardForeground }}>Voice</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setAllowUploads((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle uploads" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: allowUploads ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: allowUploads ? theme.color.primary : theme.color.cardForeground }}>Uploads</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setHidePii((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle hide PII" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: hidePii ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: hidePii ? theme.color.primary : theme.color.cardForeground }}>Hide PII</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setShowQuick((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle quick replies" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: showQuick ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: showQuick ? theme.color.primary : theme.color.cardForeground }}>Quick</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setShowActions((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle action suggestions" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: showActions ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: showActions ? theme.color.primary : theme.color.cardForeground }}>Actions</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setHighContrast((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle high contrast" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: highContrast ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: highContrast ? theme.color.primary : theme.color.cardForeground }}>High contrast</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle offline" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: offline ? theme.color.primary : theme.color.cardForeground }}>{offline ? 'Offline' : 'Online'}</Text>
          </TouchableOpacity>
        </View>
      </View>
    </View>
  )

  // read prechat
  // @ts-ignore
  const pre = global.__prechat
  // read linkUrl from navigation params (context)
  const routeParams: any = (useNavigation() as any)?.getState?.()?.routes?.slice(-1)[0]?.params || {}
  const linkUrl: string | undefined = routeParams.linkUrl

  React.useEffect(() => { try { (require('../../lib/analytics') as any).track('portal.open') } catch {} }, [])
  React.useEffect(() => {
    if (prevOffline.current && !offline) {
      // went from offline to online
      const now = Date.now()
      addMessage({ id: `sys-recon-${now}`, at: now, sender: 'system', parts: [{ kind: 'notice', tone: 'info', text: 'Reconnecting…' }] as any })
    }
    prevOffline.current = offline
  }, [offline])
  const themePublished = true // demo flag; set false to show guide

  return (
    <PortalThemeProvider value={tokens}>
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <Header />
      {themePublished ? (
        <PortalHeader businessName="Acme Inc." status="online" subtitle={pre?.name ? `Hi, ${pre.name}!` : undefined} />
      ) : (
        <View style={{ paddingHorizontal: 16 }}>
          <EmptyStateGuide title="Publish a theme to see branding" lines={["Preview is locked until a theme is published."]} cta={{ label: 'Open Settings', onPress: () => (require('@react-navigation/native') as any).useNavigation().navigate('Settings') }} />
        </View>
      )}
      {pre?.name ? <SystemNotice text={`Session started for ${pre.name}`} tone="success" /> : null}
      {offline ? <OfflineBadge /> : null}
      {notice ? <SystemNotice text={notice} tone="warn" /> : null}
      {prechat ? (
        <SystemNotice text="Pre‑chat form is enabled (UI‑only)." tone="info" />
      ) : null}
      {consent ? (
        <SystemNotice text="Consent is required before messaging (UI‑only)." tone="warn" />
      ) : null}
      {showQuick ? (
        <QuickRepliesBar replies={["Where's my order?", 'Refund status', 'Return policy']} onSelect={(text) => { try { (require('../../lib/analytics') as any).track('portal.quick_reply') } catch {}; setComposer((c) => ({ ...c, text })); setTimeout(onSend, 50) }} />
      ) : null}
      {showActions ? (
        <ActionSuggestions actions={[{ id: 'track', label: 'Track order' }, { id: 'book', label: 'Book appointment' }]} onSelect={(id) => {
          try { (require('../../lib/analytics') as any).track('portal.action_click') } catch {}
          const now = Date.now()
          const notice: ChatMessage = { id: `s-${now}`, at: now, sender: 'system', parts: [{ kind: 'notice', tone: 'info', text: `Action selected: ${id}` }] as any }
          const card: ChatMessage = { id: `c-${now}`, at: now, sender: 'ai', parts: [{ kind: 'card', title: id === 'track' ? 'Track order' : 'Book appointment', body: 'Follow the steps to proceed.' }] as any }
          setMessages((arr) => [...arr, notice, card])
        }} />
      ) : null}
      <FlatList
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingVertical: 8 }}
        data={messages}
        keyExtractor={(m) => m.id}
        renderItem={renderItem}
        initialNumToRender={20}
        windowSize={12}
        removeClippedSubviews
        getItemLayout={(data, index) => ({ length: 88, offset: 88 * index, index })}
      />
      {typing ? <TypingIndicator /> : null}
      <VoiceBar enabled={allowVoice} onTranscript={(text) => { setComposer((c) => ({ ...c, text })); setTimeout(onSend, 50) }} />
      <HandoffBar
        session={session}
        queue={queue}
        onRequestHuman={() => {
          const now = Date.now()
          setSession('queued')
          setQueue({ position: 3, etaMins: 5 })
          addMessage({ id: `sys-${now}`, at: now, sender: 'system', parts: [{ kind: 'notice', tone: 'info', text: 'Human agent requested. You are now in queue.' }] as any })
          try { (require('../../lib/analytics') as any).track('portal.handoff.request') } catch {}
        }}
        onCancelRequest={() => {
          const now = Date.now()
          setSession('connected')
          setQueue(undefined)
          addMessage({ id: `sys-${now}`, at: now, sender: 'system', parts: [{ kind: 'notice', tone: 'success', text: 'Cancelled. You are chatting with AI.' }] as any })
          try { (require('../../lib/analytics') as any).track('portal.queue.cancel') } catch {}
        }}
        onEndChat={() => {
          setSession('ended')
          setComposer((c) => ({ ...c, disabled: true }))
          setCsatOpen(true)
          try { (require('../../lib/analytics') as any).track('portal.end') } catch {}
          try { (require('react-native') as any).DeviceEventEmitter.emit('portal.tested') } catch {}
        }}
      />
      {csatOpen ? (
        <View style={{ paddingHorizontal: 12, paddingVertical: 8, gap: 12 }}>
          {!csatSubmitted && (
            <CsatPanel
              value={csat}
              onChange={setCsat as any}
              onSubmit={() => {
                setCsatSubmitted(true)
                const now = Date.now()
                addMessage({ id: `sys-${now}`, at: now, sender: 'system', parts: [{ kind: 'notice', tone: 'success', text: 'Thanks for your feedback!' }] as any })
                try { (require('../../lib/analytics') as any).track('portal.csat.submit', { rating: csat.rating }) } catch {}
              }}
            />
          )}
          <TranscriptForm
            email={transcriptEmail}
            onChange={setTranscriptEmail}
            onSend={() => {
              if (!/^\S+@\S+\.\S+$/.test(transcriptEmail)) return
              const now = Date.now()
              addMessage({ id: `sys-${now}`, at: now, sender: 'system', parts: [{ kind: 'notice', tone: 'info', text: `Transcript sent to ${transcriptEmail}` }] as any })
              try { (require('../../lib/analytics') as any).track('portal.transcript.send') } catch {}
            }}
          />
          <TouchableOpacity
            onPress={() => {
              setMessages(seedTranscript())
              setSession('connected')
              setComposer({ text: '', attachments: [], disabled: false })
              setCsatOpen(false)
              setCsat({})
              setCsatSubmitted(false)
              setTranscriptEmail('')
            }}
            accessibilityRole="button"
            accessibilityLabel="Restart chat"
            style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}
          >
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Restart chat</Text>
          </TouchableOpacity>
        </View>
      ) : null}
      <Composer
        state={{ ...composer, disabled: composer.disabled || cooldown > 0 || offline }}
        onChange={setComposer}
        onSend={onSend}
        onAttach={(err) => { if (err) { setNotice(err); setTimeout(() => setNotice(undefined), 1500) } else { onAttach() } }}
        onMic={() => {}}
        allowVoice={allowVoice}
        testID="portal-composer"
      />
      {cooldown > 0 ? <SystemNotice text={`Please wait ${cooldown}s before sending more messages.`} tone="warn" /> : null}
      {offline ? <SystemNotice text="You're offline. Messages can't be sent. Retry when online." tone="warn" /> : null}
    </SafeAreaView>
    </PortalThemeProvider>
  )
}

export default PortalPreview


