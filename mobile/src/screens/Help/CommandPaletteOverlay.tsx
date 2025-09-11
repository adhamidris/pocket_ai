import React, { useMemo, useState } from 'react'
import { View, TouchableWithoutFeedback } from 'react-native'
import { CommandPalette, CommandItem } from '../../components/help/CommandPalette'
import { useNavigation } from '@react-navigation/native'
import { track } from '../../lib/analytics'

export const CommandPaletteOverlay: React.FC<{ visible: boolean; onClose: () => void }> = ({ visible, onClose }) => {
  const navigation = useNavigation<any>()

  const items: CommandItem[] = useMemo(() => ([
    { id: 'open-rule', title: 'Open Rule Builder', onPress: () => { try { track('help.command_palette.run', { cmd: 'open-rule' }) } catch {}; navigation.navigate('Main', { screen: 'Automations', params: { screen: 'RuleBuilder' } }) } },
    { id: 'open-portal', title: 'Open Portal Preview', onPress: () => { try { track('help.command_palette.run', { cmd: 'open-portal' }) } catch {}; navigation.navigate('Main', { screen: 'Portal' }) } },
    { id: 'help-channels', title: 'Help: Channels', onPress: () => { try { track('help.command_palette.run', { cmd: 'help-channels' }) } catch {}; navigation.navigate('HelpCenter', { initialTab: 'search', initialTag: 'Channels' }) } },
    { id: 'help-knowledge', title: 'Help: Knowledge', onPress: () => { try { track('help.command_palette.run', { cmd: 'help-knowledge' }) } catch {}; navigation.navigate('HelpCenter', { initialTab: 'search', initialTag: 'Knowledge' }) } },
  ]), [navigation])

  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      <CommandPalette visible={visible} items={items} onClose={() => { try { track('help.command_palette.open') } catch {}; onClose() }} />
    </View>
  )
}

export default CommandPaletteOverlay


