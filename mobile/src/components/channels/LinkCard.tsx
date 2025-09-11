import React from 'react'
import { View, TouchableOpacity, Switch } from 'react-native'
import { PublishLink } from '../../types/channels'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import VerifyBadge from './VerifyBadge'
import EntitlementsGate from '../billing/EntitlementsGate'

export interface LinkCardProps {
  link: PublishLink
  onCopy: () => void
  onRotate: () => void
  onOpen: () => void
  testID?: string
  themeName?: string
  themeColor?: string
  shortPreferred?: boolean
  onToggleShort?: (v: boolean) => void
  queued?: boolean
}

const LinkCard: React.FC<LinkCardProps> = ({ link, onCopy, onRotate, onOpen, testID, themeName, themeColor, shortPreferred, onToggleShort, queued }) => {
  return (
    <EntitlementsGate require="channelsShortLink">
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 16, backgroundColor: tokens.colors.card, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <Text size={14} weight="700" color={tokens.colors.cardForeground} numberOfLines={1}>
          {link.url}
        </Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <VerifyBadge state={link.verify.state} message={link.verify.message} />
          {queued && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
        </View>
      </View>
      {/* Short link toggle */}
      {!!link.shortUrl && (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <Text size={12} color={tokens.colors.mutedForeground}>Use short link</Text>
          <Switch value={!!shortPreferred} onValueChange={(v) => onToggleShort?.(v)} accessibilityLabel="Toggle short link" />
        </View>
      )}
      {(themeName || themeColor) && (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          {themeName && (
            <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border, backgroundColor: tokens.colors.card }}>
              <Text size={11} color={tokens.colors.mutedForeground}>Theme: {themeName}</Text>
            </View>
          )}
          {themeColor && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <View style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: themeColor, borderWidth: 1, borderColor: tokens.colors.border }} />
              <Text size={11} color={tokens.colors.mutedForeground}>{themeColor}</Text>
            </View>
          )}
        </View>
      )}
      {link.shortUrl && shortPreferred && (
        <Text size={12} color={tokens.colors.mutedForeground} numberOfLines={1}>
          {link.shortUrl}
        </Text>
      )}
      <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
        <TouchableOpacity onPress={onCopy} accessibilityLabel="Copy link" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
          <Text size={13} weight="600" color={tokens.colors.cardForeground}>Copy</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onOpen} accessibilityLabel="Open link" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
          <Text size={13} weight="600" color={tokens.colors.cardForeground}>Open</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onRotate} accessibilityLabel="Rotate link" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
          <Text size={13} weight="600" color={tokens.colors.error}>Rotate</Text>
        </TouchableOpacity>
      </View>
    </View>
    </EntitlementsGate>
  )
}

export default React.memo(LinkCard)



