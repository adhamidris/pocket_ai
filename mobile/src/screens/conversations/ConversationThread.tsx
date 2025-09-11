import React from 'react'
import { SafeAreaView, View, FlatList, TouchableOpacity, Text } from 'react-native'
import { useRoute, useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { ConversationDetail, Message } from '../../types/conversations'
import LowConfidenceBanner from '../../components/conversations/LowConfidenceBanner'
import { Modal } from 'react-native'
import ChannelDot from '../../components/conversations/ChannelDot'
import SlaBadge from '../../components/conversations/SlaBadge'
import MessageBubble from '../../components/conversations/MessageBubble'
import MessageMetaRow from '../../components/conversations/MessageMetaRow'
import Composer from '../../components/conversations/Composer'
import { track } from '../../lib/analytics'
import RowActionsSheet from '../../components/conversations/RowActionsSheet'

const getDemoDetail = (id: string): ConversationDetail => {
  const base = {
    id,
    customerName: 'Sarah Johnson',
    lastMessageSnippet: 'I need help with my order',
    lastUpdatedTs: Date.now() - 1000 * 60 * 5,
    channel: 'whatsapp' as const,
    tags: ['Billing', 'Order'],
    assignedTo: 'Nancy',
    priority: 'high' as const,
    waitingMinutes: 32,
    sla: 'risk' as const,
  }
  const now = Date.now()
  const messages: Message[] = [
    { id: 'm1', sender: 'customer', text: 'Hi, I was charged twice for my order.', ts: now - 1000 * 60 * 35 },
    { id: 'm2', sender: 'ai', text: "I'm Nancy, I can help. Let me check your account.", ts: now - 1000 * 60 * 34 },
    { id: 'm3', sender: 'ai', text: 'I see a duplicate charge. I will process a refund immediately.', ts: now - 1000 * 60 * 32 },
    { id: 'm4', sender: 'customer', text: 'Thank you. When will I see the refund?', ts: now - 1000 * 60 * 30 },
  ]
  return { ...base, messages }
}

const ConversationThread: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const id: string = route.params?.id || route.params?.selectedId || 'c-1'

  const [detail, setDetail] = React.useState<ConversationDetail>(() => getDemoDetail(id))
  const [input, setInput] = React.useState('')
  const [sending, setSending] = React.useState(false)
  const [actionsOpen, setActionsOpen] = React.useState(false)
  const [lowConfidence] = React.useState(true)
  const [requiresApproval] = React.useState(true)
  const [confirmOpen, setConfirmOpen] = React.useState(false)

  const sendMessage = () => {
    if (!input.trim()) return
    setSending(true)
    const newMsg: Message = { id: String(Date.now()), sender: 'agent', text: input.trim(), ts: Date.now(), status: 'queued' }
    setDetail((prev) => ({ ...prev, messages: [...prev.messages, newMsg] }))
    setInput('')
    setSending(false)
    track('message.sent', { id, len: newMsg.text.length })
    // mimic queue flush
    setTimeout(() => {
      setDetail((prev) => ({
        ...prev,
        messages: prev.messages.map((m) => (m.id === newMsg.id ? { ...m, status: 'sent' } : m)),
      }))
    }, 1500)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => { track('conversation.action', { action: 'open_actions' }); setActionsOpen(true) }} accessibilityLabel="Actions" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Actions</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { /* open global SyncCenter if wired */ }} accessibilityLabel="Sync Center" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Sync</Text>
            </TouchableOpacity>
          </View>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
          <ChannelDot channel={detail.channel} />
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>{detail.customerName}</Text>
          <SlaBadge state={detail.sla} />
          <Text style={{ marginLeft: 8, color: theme.color.mutedForeground, fontWeight: '600' }}>{detail.priority.toUpperCase()}</Text>
          {detail.assignedTo && (
            <Text style={{ marginLeft: 8, color: theme.color.mutedForeground }}>@{detail.assignedTo}</Text>
          )}
        </View>
      </View>

      {/* Low confidence */}
      <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
        <LowConfidenceBanner visible={lowConfidence} testID="thread-lowconf" />
      </View>

      {/* Thread */}
      <View style={{ flex: 1, paddingHorizontal: 16, paddingTop: 12 }}>
        <FlatList
          data={detail.messages}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => (
            <View style={{ marginBottom: 12 }}>
              <MessageBubble msg={item} />
              <MessageMetaRow msg={item} />
            </View>
          )}
          contentContainerStyle={{ paddingBottom: 12 }}
          onContentSizeChange={() => {}}
          initialNumToRender={14}
          maxToRenderPerBatch={14}
          windowSize={12}
          getItemLayout={(_, index) => ({ length: 64, offset: 64 * index, index })}
        />
      </View>

      {/* Composer */}
      <View style={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: theme.color.border }}>
        <Composer value={input} onChange={setInput} onSend={sendMessage} sending={sending} />
      </View>

      <RowActionsSheet
        id={detail.id}
        visible={actionsOpen}
        onClose={() => setActionsOpen(false)}
        onAssign={(i) => console.log('assign', i)}
        onTag={(i) => console.log('tag', i)}
        onSetPriority={(i) => console.log('priority', i)}
        onResolve={(i) => { track('conversation.action', { action: 'resolve' }); console.log('resolve', i) }}
        onEscalate={(i) => {
          if (requiresApproval) setConfirmOpen(true)
          else { track('conversation.action', { action: 'escalate' }); console.log('escalate', i) }
        }}
      />

      {/* Restricted action gate */}
      <Modal visible={confirmOpen} transparent animationType="fade" onRequestClose={() => setConfirmOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <View style={{ backgroundColor: theme.color.card, borderRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border, width: '90%' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700', marginBottom: 8 }}>Requires Approval</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 16 }}>Escalation requires human approval. Proceed?</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 12 }}>
              <TouchableOpacity onPress={() => setConfirmOpen(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => { setConfirmOpen(false); console.log('escalate approved', id) }} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Confirm</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default ConversationThread


