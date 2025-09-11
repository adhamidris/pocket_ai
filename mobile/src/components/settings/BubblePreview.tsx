import React from 'react'
import { View, Text } from 'react-native'
import { ThemeTokens } from '../../types/settings'

export interface BubblePreviewProps { tokens: ThemeTokens; testID?: string }

const Bubble: React.FC<{ text: string; color: string; align: 'left'|'right'; radius: number }> = ({ text, color, align, radius }) => (
  <View style={{ alignSelf: align === 'left' ? 'flex-start' : 'flex-end', backgroundColor: color, paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius, marginVertical: 4, maxWidth: '80%' }}>
    <Text style={{ color: '#000' }}>{text}</Text>
  </View>
)

const BubblePreview: React.FC<BubblePreviewProps> = ({ tokens, testID }) => {
  return (
    <View testID={testID} accessibilityRole="image" accessibilityLabel="Chat bubble preview" style={{ padding: 12, borderWidth: 1, borderColor: '#e5e7eb', borderRadius: 12 }}>
      <Bubble text="Hi! How can I help?" color={tokens.bubbleAI} align="left" radius={tokens.bubbleRadius} />
      <Bubble text="I need help with my order" color={tokens.bubbleCustomer} align="right" radius={tokens.bubbleRadius} />
      <Bubble text="Sure, let's check that." color={tokens.bubbleAgent} align="left" radius={tokens.bubbleRadius} />
    </View>
  )
}

export default React.memo(BubblePreview)


