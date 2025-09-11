import React from 'react'
import { View, Text, Image, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { usePortalTokens } from './theme'
import { ChatMessage, MsgPart } from '../../types/portal'

export interface BubbleProps { msg: ChatMessage; onAction?: (id: string) => void; enableTts?: boolean; testID?: string }

const PartRenderer: React.FC<{ part: MsgPart; onAction?: (id: string) => void }> = ({ part, onAction }) => {
  const { theme } = useTheme()
  switch (part.kind) {
    case 'text':
      return <Text style={{ color: theme.color.cardForeground, lineHeight: 20 }}>{part.text}</Text>
    case 'image':
      const [loaded, setLoaded] = React.useState(false)
      return (
        <View style={{ width: 200, height: 120 }}>
          {!loaded && <View style={{ position: 'absolute', left: 0, top: 0, right: 0, bottom: 0, backgroundColor: theme.color.muted, borderRadius: 8 }} />}
          <Image source={{ uri: part.url }} onLoad={() => setLoaded(true)} style={{ width: 200, height: 120, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }} accessibilityLabel={part.alt || 'image'} />
        </View>
      )
    case 'file':
      return (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <View style={{ width: 18, height: 18, borderRadius: 4, backgroundColor: theme.color.muted }} />
          <Text style={{ color: theme.color.cardForeground }}>{part.name} ({part.sizeKB}kb)</Text>
        </View>
      )
    case 'buttons':
      return (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {(part.actions || []).map((a) => (
            <TouchableOpacity key={a.id} onPress={() => onAction && onAction(a.id)} accessibilityRole="button" accessibilityLabel={a.label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, backgroundColor: a.style === 'primary' ? theme.color.primary : theme.color.card }}>
              <Text style={{ color: a.style === 'primary' ? theme.color.primaryForeground : theme.color.cardForeground, fontWeight: '700' }}>{a.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
      )
    case 'card':
      return (
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, padding: 10 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{part.title}</Text>
          {part.subtitle ? <Text style={{ color: theme.color.mutedForeground }}>{part.subtitle}</Text> : null}
          {part.body ? <Text style={{ color: theme.color.cardForeground, marginTop: 4 }}>{part.body}</Text> : null}
          {part.mediaUrl ? <Image source={{ uri: part.mediaUrl }} style={{ width: 220, height: 120, borderRadius: 8, marginTop: 8 }} /> : null}
          {!!part.actions?.length && (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              {part.actions.map((a) => (
                <TouchableOpacity key={a.id} onPress={() => onAction && onAction(a.id)} accessibilityRole="button" accessibilityLabel={a.label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{a.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          )}
        </View>
      )
    case 'list':
      return (
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, overflow: 'hidden' }}>
          <ScrollView style={{ maxHeight: 200 }}>
            {part.items.map((it) => (
              <View key={it.id} style={{ padding: 10, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{it.title}</Text>
                {it.subtitle ? <Text style={{ color: theme.color.mutedForeground }}>{it.subtitle}</Text> : null}
                {it.rightLabel ? <Text style={{ color: theme.color.mutedForeground, alignSelf: 'flex-end' }}>{it.rightLabel}</Text> : null}
              </View>
            ))}
          </ScrollView>
        </View>
      )
    case 'divider':
      return (
        <View style={{ alignItems: 'center', marginVertical: 4 }}>
          <View style={{ height: 1, backgroundColor: theme.color.border, alignSelf: 'stretch' }} />
          {part.label ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 2 }}>{part.label}</Text> : null}
        </View>
      )
    case 'notice':
      return <Text style={{ color: theme.color.mutedForeground }}>{part.text}</Text>
    default:
      return null
  }
}

const Bubble: React.FC<BubbleProps> = ({ msg, onAction, enableTts, testID }) => {
  const { theme } = useTheme()
  const pt = usePortalTokens()
  const isSelf = msg.sender === 'customer'
  const isSystem = msg.sender === 'system'
  const [playing, setPlaying] = React.useState(false)
  const toggleTts = () => { if (!enableTts || isSelf) return; try { (require('../../lib/analytics') as any).track('portal.voice.tts') } catch {}; setPlaying((p) => !p); setTimeout(() => setPlaying(false), 1200) }
  return (
    <View testID={testID} style={{ paddingHorizontal: 12, paddingVertical: 6, alignItems: isSelf ? 'flex-end' : 'flex-start' }}>
      <View style={{ maxWidth: '86%', backgroundColor: isSystem ? 'transparent' : (isSelf ? theme.color.primary : pt.colors.card), borderRadius: pt.bubbleRadius, padding: 10, borderWidth: 1, borderColor: pt.colors.border }} accessibilityLabel={`Message from ${msg.sender}` }>
        <View style={{ gap: 8 }}>
          {msg.parts.map((p, idx) => (
            <PartRenderer key={idx} part={p} onAction={onAction} />
          ))}
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', marginTop: 6 }}>
          <Text style={{ color: isSelf ? theme.color.primaryForeground : theme.color.mutedForeground, fontSize: 10 }}>{new Date(msg.at).toLocaleTimeString()}</Text>
          {msg.meta?.delivered ? <Text style={{ color: isSelf ? theme.color.primaryForeground : theme.color.mutedForeground, fontSize: 10, marginLeft: 6 }}>✓</Text> : null}
          {msg.meta?.read ? <Text style={{ color: isSelf ? theme.color.primaryForeground : theme.color.mutedForeground, fontSize: 10, marginLeft: 2 }}>✓</Text> : null}
          {enableTts && !isSelf ? (
            <TouchableOpacity onPress={toggleTts} accessibilityRole="button" accessibilityLabel="Play TTS" style={{ marginLeft: 8, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 10 }}>{playing ? 'Stop' : 'Play'}</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      </View>
    </View>
  )
}

export default React.memo(Bubble)


