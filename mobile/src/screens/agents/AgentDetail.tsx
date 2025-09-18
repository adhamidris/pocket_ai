import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity } from 'react-native'
import { Modal } from '../../components/ui/Modal'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Bot, ClipboardList, Star, Settings, BarChart3, Briefcase } from 'lucide-react-native'

interface AgentDetailProps {
  visible: boolean
  agent: {
    id: string
    name: string
    description?: string
    status: 'active' | 'inactive'
    role?: string
    roles?: string[]
    conversations?: number
    openCases?: number
    resolvedCases?: number
    satisfaction?: number
    createdAt?: string
  } | null
  onClose: () => void
  onToggleStatus?: (id: string) => void
  onEdit?: (agentId: string) => void
}

export const AgentDetail: React.FC<AgentDetailProps> = ({ visible, agent, onClose, onToggleStatus, onEdit }) => {
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState<'overview'|'performance'|'settings'>('overview')
  if (!agent) return null

  const name = agent.name
  const roleText = agent.role || (agent.roles || []).slice(0, 2).join(' • ')
  // Analytics removed from modal per request

  // --- Overview helpers ---
  const roleDescriptions: Record<string, string> = {
    'Support Agent': 'Handles customer issues and questions efficiently.',
    'Sales Associate': 'Engages leads and highlights value to convert.',
    'Technical Specialist': 'Guides users through technical workflows and fixes.',
    'Billing Assistant': 'Clarifies invoices, refunds, and payment matters.',
  }
  const toneDescriptions: Record<string, { desc: string; sample: string }> = {
    Friendly: { desc: 'Warm, welcoming, and approachable.', sample: 'Hey there! Happy to help with that right away.' },
    Professional: { desc: 'Clear, precise, and respectful.', sample: 'Certainly. I can assist you with that request.' },
    Empathetic: { desc: 'Acknowledges feelings and reassures.', sample: 'I understand how that feels—let me fix this for you.' },
    Concise: { desc: 'Short, direct, and efficient.', sample: 'Got it. Here’s the fastest way to solve it.' },
    Playful: { desc: 'Light, upbeat tone when appropriate.', sample: 'Gotcha! Let’s sort this out in no time.' },
    Formal: { desc: 'Courteous and precise, suitable for official contexts.', sample: 'I would be pleased to assist with your request.' },
  }
  const traitDescriptions: Record<string, string> = {
    Patient: 'Gives users space, explains calmly.',
    Proactive: 'Offers next steps without being asked.',
    'Detail-oriented': 'Careful, thorough, and precise.',
    Persuasive: 'Frames benefits convincingly and ethically.',
    Analytical: 'Breaks problems down logically.',
    Creative: 'Suggests novel, helpful alternatives.',
  }
  const escalationDescriptions: Record<string, { desc: string; sample: string }> = {
    'Never escalate': { desc: 'Agent resolves all cases independently.', sample: 'I can fully resolve this for you here.' },
    'Escalate on negative sentiment': { desc: 'Hands off when frustration is detected.', sample: 'I’m connecting you with a specialist to make this right.' },
    'Escalate on SLA risk': { desc: 'Hands off when time-to-resolution is at risk.', sample: 'I’m escalating this to ensure we meet our timeline.' },
    'Always escalate complex': { desc: 'Transfers complex scenarios to humans.', sample: 'This is complex—looping a human expert in now.' },
  }

  const SectionTitle: React.FC<{ title: string; icon?: React.ReactNode }> = ({ title, icon }) => (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
      {icon}
      <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>{title}</Text>
    </View>
  )

  const Pill: React.FC<{ label: string }> = ({ label }) => (
    <View style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 4, marginRight: 6, marginBottom: 6 }}>
      <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '700' }}>{label}</Text>
    </View>
  )
  const contentIndent = 24 // align sub-content under titles (icon 16 + gap 8)

  const tabs = [
    { key: 'overview' as const, label: 'Overview', icon: Bot },
    { key: 'performance' as const, label: 'Performance', icon: BarChart3 },
    { key: 'settings' as const, label: 'Settings', icon: Settings },
  ]

  return (
    <Modal visible={visible} onClose={onClose} title={name} size="lg" autoHeight={false}>
      <View style={{ gap: 12, flex: 1, minHeight: 0 }}>
        {/* Header: Avatar + name/role + status */}
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, alignItems: 'center', justifyContent: 'center', marginRight: 12 }}>
            <Bot size={22} color={theme.color.primary as any} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700' }} numberOfLines={1}>{name}</Text>
            {roleText ? (
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }} numberOfLines={1}>{roleText}</Text>
            ) : null}
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: theme.radius.sm, paddingHorizontal: 8, paddingVertical: 4 }}>
            <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: agent.status === 'active' ? theme.color.success : theme.color.mutedForeground }} />
            <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>{agent.status}</Text>
          </View>
        </View>

        {/* Tabs bar */}
        <View style={{ flexDirection: 'row', backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6 }}>
          {tabs.map((tab) => (
            <TouchableOpacity
              key={tab.key}
              onPress={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                paddingVertical: 10,
                borderRadius: theme.radius.sm,
                backgroundColor: activeTab === tab.key ? theme.color.card : 'transparent'
              }}
            >
              <View style={{ alignItems: 'center', gap: 4 }}>
                <tab.icon size={18} color={activeTab === tab.key ? (theme.color.primary as any) : (theme.color.mutedForeground as any)} />
                <Text style={{ color: activeTab === tab.key ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>{tab.label}</Text>
              </View>
            </TouchableOpacity>
          ))}
        </View>

        {/* Scrollable content under tabs (does not overlap buttons) */}
        <View style={{ flex: 1, minHeight: 0 }}>
          <ScrollView
            showsVerticalScrollIndicator={false}
            contentContainerStyle={{ paddingBottom: 12, paddingHorizontal: 0 }}
          >
            {activeTab === 'overview' && (
              <View style={{ gap: 12 }}>
                {/* Analytics removed */}

                {/* Role */}
                <View>
                  <SectionTitle title="Role" icon={<Briefcase size={16} color={theme.color.mutedForeground as any} />} />
                  <View style={{ paddingLeft: contentIndent }}>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6 }}>
                      {(agent.roles && agent.roles.length > 0 ? agent.roles : (agent.role ? [agent.role] : [])).map(r => (
                        <Pill key={r} label={r} />
                      ))}
                    </View>
                    {(agent.roles && agent.roles.length > 0 ? agent.roles : (agent.role ? [agent.role] : [])).map(r => (
                      <Text key={`roler-${r}`} style={{ color: theme.color.mutedForeground, fontSize: 13, marginBottom: 2 }}>
                        {roleDescriptions[r] || 'Configured role.'}
                      </Text>
                    ))}
                  </View>
                </View>

                {/* Tone */}
                <View>
                  <SectionTitle title="Tone" icon={<Star size={16} color={theme.color.mutedForeground as any} />} />
                  <View style={{ paddingLeft: contentIndent }}>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6 }}>
                      {agent.tone ? <Pill label={agent.tone} /> : <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>Not configured.</Text>}
                    </View>
                    {agent.tone && (
                      <>
                        <Text style={{ color: theme.color.mutedForeground, fontSize: 13, marginBottom: 2 }}>
                          {(toneDescriptions[agent.tone]?.desc) || 'Configured tone.'}
                        </Text>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontStyle: 'italic' }}>
                          “{(toneDescriptions[agent.tone]?.sample) || 'Hello! How can I help you today?'}”
                        </Text>
                      </>
                    )}
                  </View>
                </View>

                {/* Traits */}
                <View>
                  <SectionTitle title="Traits" icon={<Bot size={16} color={theme.color.mutedForeground as any} />} />
                  <View style={{ paddingLeft: contentIndent }}>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6 }}>
                      {(agent.traits && agent.traits.length > 0)
                        ? agent.traits.map(t => <Pill key={t} label={t} />)
                        : <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>Not configured.</Text>
                      }
                    </View>
                    {agent.traits && agent.traits.length > 0 && (
                      <>
                        <Text style={{ color: theme.color.mutedForeground, fontSize: 13, marginBottom: 2 }}>
                          {agent.traits.map(t => traitDescriptions[t] || 'Configured trait.').join(' ')}
                        </Text>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontStyle: 'italic' }}>
                          “I’ve reviewed the details carefully and here’s the best path forward.”
                        </Text>
                      </>
                    )}
                  </View>
                </View>

                {/* Escalation */}
                <View>
                  <SectionTitle title="Escalation" icon={<ClipboardList size={16} color={theme.color.mutedForeground as any} />} />
                  <View style={{ paddingLeft: contentIndent }}>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6 }}>
                      {agent.escalationRule ? <Pill label={agent.escalationRule} /> : <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>Not configured.</Text>}
                    </View>
                    {agent.escalationRule && (
                      <>
                        <Text style={{ color: theme.color.mutedForeground, fontSize: 13, marginBottom: 2 }}>
                          {(escalationDescriptions[agent.escalationRule]?.desc) || 'Configured escalation policy.'}
                        </Text>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontStyle: 'italic' }}>
                          “{(escalationDescriptions[agent.escalationRule]?.sample) || 'Passing this to a human specialist.'}”
                        </Text>
                      </>
                    )}
                  </View>
                </View>

                {/* About */}
                {agent.description ? (
                  <View>
                    <SectionTitle title="About" />
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 14, lineHeight: 20 }}>{agent.description}</Text>
                  </View>
                ) : null}
              </View>
            )}

            {activeTab === 'performance' && (
              <View style={{ gap: 10 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>Performance</Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>
                  Detailed KPIs coming soon. Current overview reflects core metrics.
                </Text>
              </View>
            )}

            {activeTab === 'settings' && (
              <View style={{ gap: 10 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>Configuration</Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>
                  Edit agent settings from the main Agents page.
                </Text>
              </View>
            )}
          </ScrollView>
        </View>

        {/* Actions (sticky at bottom) */}
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <View style={{ flex: 1 }}>
            <Button
              title={agent.status === 'active' ? 'Deactivate' : 'Activate'}
              variant={agent.status === 'active' ? 'danger' : 'default'}
              size="lg"
              fullWidth
              onPress={() => onToggleStatus?.(agent.id)}
            />
          </View>
          <View style={{ flex: 1 }}>
            <Button title="Edit" variant="secondary" size="lg" fullWidth onPress={() => onEdit?.(agent.id)} />
          </View>
        </View>

        <Button title="Close" variant="default" size="lg" fullWidth onPress={onClose} />
      </View>
    </Modal>
  )
}
