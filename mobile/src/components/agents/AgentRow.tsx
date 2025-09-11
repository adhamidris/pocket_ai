import React from 'react'
import { TouchableOpacity, Image } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { AnyAgent } from '../../types/agents'
import StatusBadge from './StatusBadge'
import CapacityPill from './CapacityPill'
import SkillChip from './SkillChip'
import AgentRowActionsSheet from './AgentRowActionsSheet'
import { track } from '../../lib/analytics'

export interface AgentRowProps {
  item: AnyAgent
  onPress: (id: string) => void
  onLongPress?: (id: string) => void
  testID?: string
  onQueueStart?: (id: string, type: 'status' | 'skill' | 'capacity' | 'allow') => void
  queuedStatus?: boolean
  queuedSkill?: boolean
  queuedCapacity?: boolean
  queuedAllow?: boolean
}

const initials = (name: string) => name.split(' ').map((n) => n[0]).slice(0, 2).join('').toUpperCase()

const DeflectionPill: React.FC<{ pct?: number }> = ({ pct }) => {
  if (pct == null) return null
  return (
    <Box px={10} py={6} radius={12} style={{ backgroundColor: tokens.colors.muted, borderWidth: 1, borderColor: tokens.colors.border, minHeight: 32, justifyContent: 'center' }} accessibilityLabel={`Deflection target ${pct}%`} accessibilityRole="text">
      <Text size={12} weight="600" color={tokens.colors.mutedForeground}>{`Deflect ${pct}%`}</Text>
    </Box>
  )
}

const AgentRow: React.FC<AgentRowProps> = ({ item, onPress, onLongPress, testID, onQueueStart, queuedStatus, queuedSkill, queuedCapacity, queuedAllow }) => {
  const a11y = `Agent ${item.name}, status ${item.status}`
  const extraSkills = Math.max(0, (item.skills?.length || 0) - 3)
  const [open, setOpen] = React.useState(false)
  const [local, setLocal] = React.useState(item)

  return (
    <TouchableOpacity
      testID={testID}
      onPress={() => onPress(item.id)}
      onLongPress={() => { setOpen(true); onLongPress?.(item.id) }}
      accessibilityLabel={a11y}
      accessibilityRole="button"
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{ paddingVertical: 12, minHeight: 64 }}
    >
      <Box row align="center" gap={12}>
        {/* Avatar */}
        {item.avatarUrl ? (
          <Image source={{ uri: item.avatarUrl }} style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: tokens.colors.muted }} />
        ) : (
          <Box style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: tokens.colors.muted, alignItems: 'center', justifyContent: 'center' }}>
            <Text size={12} weight="700" color={tokens.colors.mutedForeground}>{initials(item.name)}</Text>
          </Box>
        )}

        {/* Main */}
        <Box style={{ flex: 1 }}>
          <Box row align="center" justify="space-between" mb={4}>
            <Box row align="center" gap={8} style={{ flex: 1 }}>
              <Text size={14} weight="700" color={tokens.colors.cardForeground} numberOfLines={1}>{local.name}</Text>
              <StatusBadge status={local.status} />
              {(queuedStatus || queuedAllow) && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
            </Box>
            {local.kind === 'human' ? (
              <Box row align="center" gap={6}>
                <CapacityPill capacity={(local as any).capacity} assignedOpen={(local as any).assignedOpen} />
                {queuedCapacity && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
              </Box>
            ) : (
              <DeflectionPill pct={(local as any).deflectionTarget} />
            )}
          </Box>

          {/* Skills */}
          <Box row wrap gap={6}>
            {(local.skills || []).slice(0, 3).map((s, idx) => (
              <SkillChip key={`${String(s.name)}-${idx}`} tag={s} />
            ))}
            {extraSkills > 0 && (
              <Box px={8} py={4} radius={8} style={{ backgroundColor: tokens.colors.muted }}>
                <Text size={11} color={tokens.colors.mutedForeground}>+{extraSkills}</Text>
              </Box>
            )}
            {queuedSkill && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
          </Box>
        </Box>
      </Box>
      <AgentRowActionsSheet
        id={local.id}
        item={local}
        visible={open}
        onClose={() => setOpen(false)}
        onChangeStatus={() => {
          const order: any[] = ['online','away','dnd','offline']
          const idx = order.indexOf((local as any).status)
          const next = order[(idx + 1) % order.length]
          setLocal((prev: any) => ({ ...prev, status: next }))
          onQueueStart?.(local.id, 'status')
          track('agent.status', { id: local.id, status: next })
        }}
        onAddRemoveSkill={(_, skill) => {
          setLocal((prev: any) => {
            const has = (prev.skills || []).some((s: any) => String(s.name) === String(skill))
            return has
              ? { ...prev, skills: (prev.skills || []).filter((s: any) => String(s.name) !== String(skill)) }
              : { ...prev, skills: [ ...(prev.skills || []), { name: skill, level: 1 } ] }
          })
          onQueueStart?.(local.id, 'skill')
          track('agent.edit', { id: local.id, field: 'skills' })
        }}
        onAdjustCapacity={(_, delta) => {
          if (local.kind !== 'human') return
          setLocal((prev: any) => ({ ...prev, capacity: { ...(prev.capacity || { concurrent: 0 }), concurrent: Math.max(0, (prev.capacity?.concurrent || 0) + delta) } }))
          onQueueStart?.(local.id, 'capacity')
          track('agent.edit', { id: local.id, field: 'capacity' })
        }}
        onToggleAllowlist={(_, key) => {
          if (local.kind !== 'ai') return
          setLocal((prev: any) => ({ ...prev, allowlist: (prev as any).allowlist.map((a: any) => a.key === key ? { ...a, enabled: !a.enabled } : a) }))
          onQueueStart?.(local.id, 'allow')
          const enabled = !!(local as any).allowlist?.find((a: any) => a.key === key)?.enabled
          track('agent.allowlist', { id: local.id, key, enabled: !enabled })
        }}
      />
    </TouchableOpacity>
  )
}

export default React.memo(AgentRow)



