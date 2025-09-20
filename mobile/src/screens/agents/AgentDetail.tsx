import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity } from 'react-native'
import { Modal } from '../../components/ui/Modal'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { Bot, ClipboardList, Star, Settings, BarChart3, Briefcase, Target, Plus, X, Power, MessageCircle, BookOpen } from 'lucide-react-native'

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
    dataAccess?: { mode?: 'all' | 'select'; selectedCollections?: string[] }
  } | null
  onClose: () => void
  onToggleStatus?: (id: string) => void
  onEdit?: (agentId: string) => void
}

export const AgentDetail: React.FC<AgentDetailProps> = ({ visible, agent, onClose, onToggleStatus, onEdit }) => {
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState<'overview'|'performance'|'access'|'settings'>('overview')

  // Guard must happen before accessing agent fields to avoid hook/order errors
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

  const SectionTitle: React.FC<{ title: string; icon?: React.ReactNode; mb?: number }> = ({ title, icon, mb }) => (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: mb ?? 6 }}>
      {icon}
      <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>{title}</Text>
    </View>
  )

  const Pill: React.FC<{ label: string }> = ({ label }) => (
    <View
      style={{
        backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any),
        borderRadius: 999,
        paddingHorizontal: 10,
        paddingVertical: 6,
        marginRight: 6,
        marginBottom: 6,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.primary as any }} />
        <Text style={{ color: theme.color.primary as any, fontSize: 12, fontWeight: '600' }}>{label}</Text>
      </View>
    </View>
  )
  const SectionDivider: React.FC = () => (
    <View style={{ height: 1, backgroundColor: theme.color.border, marginVertical: 0 }} />
  )
  // Prepared fallback message for sections with no active parameters (not used yet)
  const FALLBACK_MESSAGE = 'No selections yet. Configure to get started.'
  const contentIndent = 24 // align sub-content under titles (icon 16 + gap 8)
  const ACTION_BUTTON_HEIGHT = 44
  const ACTION_BUTTON_RADIUS = 16

  const tabs = [
    { key: 'overview' as const, label: 'Overview', icon: Bot },
    { key: 'performance' as const, label: 'Performance', icon: BarChart3 },
    { key: 'access' as const, label: 'Access', icon: BookOpen },
    { key: 'settings' as const, label: 'Settings', icon: Settings },
  ]

  // KPIs (performance) state
  const commonKpis = [
    'Customer Satisfaction',
    'First Contact Resolution',
    'Response Time',
    'Resolution Rate',
    'Escalation Rate',
    'Sales Conversion',
    'Deflection Rate',
    'Average Handling Time'
  ]
  const [selectedKpis, setSelectedKpis] = useState<string[]>(['Customer Satisfaction', 'First Contact Resolution'])
  const [customKpiText, setCustomKpiText] = useState('')
  
  // Access state (frontend only)
  type DataAccessMode = 'all' | 'select'
  const availableCollections = [
    'Company Vision',
    'Mission Statement',
    'Support SOPs',
    'Product Knowledge Base',
    'Security Policies',
    'HR Handbook',
  ]
  const initialMode = (agent?.dataAccess?.mode as DataAccessMode) || 'all'
  const initialSelected = (agent?.dataAccess?.selectedCollections || []) as string[]
  const [accessMode, setAccessMode] = useState<DataAccessMode>(initialMode)
  const [accessSelected, setAccessSelected] = useState<string[]>(initialSelected)
  const toggleAccessCollection = (c: string) => setAccessSelected(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c])

  if (!agent) return null
  const name = agent.name
  const roleText = agent.role || (agent.roles || []).slice(0, 2).join(' • ')

  const toggleKpi = (k: string) => {
    setSelectedKpis(prev => prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k])
  }
  const addCustomKpi = () => {
    const t = customKpiText.trim()
    if (!t) return
    if (!selectedKpis.includes(t)) setSelectedKpis(prev => [...prev, t])
    setCustomKpiText('')
  }
  const removeCustomKpi = (k: string) => setSelectedKpis(prev => prev.filter(x => x !== k))

  return (
    <Modal visible={visible} onClose={onClose} size="lg" autoHeight={false}>
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
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: theme.radius.sm, paddingHorizontal: 8, paddingVertical: 4 }}>
              <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: agent.status === 'active' ? theme.color.success : theme.color.mutedForeground }} />
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>{agent.status}</Text>
            </View>
            <TouchableOpacity
              onPress={onClose}
              activeOpacity={0.85}
              style={{
                width: 32,
                height: 32,
                borderRadius: 16,
                backgroundColor: theme.color.muted,
                alignItems: 'center',
                justifyContent: 'center'
              }}
              accessibilityLabel={'Close agent details'}
            >
              <X size={16} color={theme.color.mutedForeground as any} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Separator between header and tabs */}
        <View style={{ height: 1, backgroundColor: theme.color.border }} />

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
              <Text style={{ textAlign: 'center', color: activeTab === tab.key ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>{tab.label}</Text>
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
              <View style={{ gap: 6 }}>
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
                <SectionDivider />

                {/* Access summary */}
                <View>
                  <SectionTitle title="Access" icon={<BookOpen size={16} color={theme.color.mutedForeground as any} />} />
                  <View style={{ paddingLeft: contentIndent }}>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6, alignItems: 'center' }}>
                      <Pill label={agent.dataAccess?.mode === 'all' ? 'Access: All files' : `Access: ${(agent.dataAccess?.selectedCollections || []).length} collections`} />
                    </View>
                    {agent.dataAccess?.mode === 'select' && (agent.dataAccess?.selectedCollections?.length || 0) > 0 && (
                      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6 }}>
                        {(agent.dataAccess?.selectedCollections || []).slice(0, 2).map(c => (
                          <Pill key={`ac-${c}`} label={c} />
                        ))}
                        {((agent.dataAccess?.selectedCollections || []).length > 2) && (
                          <Pill label={`+${(agent.dataAccess?.selectedCollections || []).length - 2} more`} />
                        )}
                      </View>
                    )}
                    <TouchableOpacity onPress={() => setActiveTab('access')} activeOpacity={0.85}>
                      <Text style={{ color: theme.color.primary as any, fontSize: 12, fontWeight: '700' }}>Manage Access</Text>
                    </TouchableOpacity>
                  </View>
                </View>
                <SectionDivider />

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
                        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6, marginTop: 2 }}>
                          <MessageCircle size={12} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontStyle: 'italic', flex: 1 }}>
                            “{(toneDescriptions[agent.tone]?.sample) || 'Hello! How can I help you today?'}”
                          </Text>
                        </View>
                      </>
                    )}
                  </View>
                </View>
                <SectionDivider />

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
                        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6, marginTop: 2 }}>
                          <MessageCircle size={12} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontStyle: 'italic', flex: 1 }}>
                            “I’ve reviewed the details carefully and here’s the best path forward.”
                          </Text>
                        </View>
                      </>
                    )}
                  </View>
                </View>
                <SectionDivider />

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
                        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6, marginTop: 2 }}>
                          <MessageCircle size={12} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontStyle: 'italic', flex: 1 }}>
                            “{(escalationDescriptions[agent.escalationRule]?.sample) || 'Passing this to a human specialist.'}”
                          </Text>
                        </View>
                      </>
                    )}
                  </View>
                </View>
                {agent.description ? <SectionDivider /> : null}

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
              <View style={{ gap: 12 }}>
                <SectionTitle title="KPIs" icon={<Target size={16} color={theme.color.mutedForeground as any} />} mb={4} />
                <View>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 13, marginBottom: 12 }}>
                    Select the metrics you want the agent to prioritize during chats.
                  </Text>
                  {/* Common KPI toggles */}
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 8 }}>
                    {commonKpis.map(k => {
                      const selected = selectedKpis.includes(k)
                      return (
                        <TouchableOpacity
                          key={k}
                          onPress={() => toggleKpi(k)}
                          activeOpacity={0.85}
                          style={{
                            paddingHorizontal: 13,
                            paddingVertical: 9,
                            borderRadius: 999,
                            marginRight: 6,
                            marginBottom: 6,
                            backgroundColor: selected ? (theme.dark ? theme.color.secondary : theme.color.accent) : (theme.dark ? theme.color.secondary : theme.color.card)
                          }}
                        >
                          {selected ? (
                            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                              <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.primary as any }} />
                              <Text style={{ color: theme.color.primary as any, fontSize: 12, fontWeight: '600' }}>{k}</Text>
                            </View>
                          ) : (
                            <Text style={{ color: theme.color.mutedForeground as any, fontSize: 12, fontWeight: '600' }}>{k}</Text>
                          )}
                        </TouchableOpacity>
                      )
                    })}
                  </View>

                  {/* Custom KPI input */}
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
                    <View style={{ flex: 1 }}>
                      <Input
                        value={customKpiText}
                        onChangeText={setCustomKpiText}
                        placeholder="Add custom KPI"
                        borderless
                        surface="accent"
                        size="sm"
                        containerStyle={{ marginBottom: 0 }}
                      />
                    </View>
                    <TouchableOpacity onPress={addCustomKpi} activeOpacity={0.8} style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: theme.color.primary as any, alignItems: 'center', justifyContent: 'center' }}>
                      <Plus size={16} color={'#fff'} />
                    </TouchableOpacity>
                  </View>

                  {/* Show custom KPIs with remove */}
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginTop: 6 }}>
                    {selectedKpis.filter(k => !commonKpis.includes(k)).map(k => (
                      <View key={`custom-${k}`} style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 6, marginRight: 6, marginBottom: 6, gap: 6 }}>
                        <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.primary as any }} />
                        <Text style={{ color: theme.color.primary as any, fontSize: 12, fontWeight: '600' }}>{k}</Text>
                        <TouchableOpacity onPress={() => removeCustomKpi(k)} hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}>
                          <X size={14} color={theme.color.mutedForeground as any} />
                        </TouchableOpacity>
                      </View>
                    ))}
                  </View>

                  {/* Helper note */}
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
                    Your selected KPIs will guide response strategies and prioritization. Backend integration to follow.
                  </Text>
                </View>
              </View>
            )}

            {activeTab === 'access' && (
              <View style={{ gap: 12 }}>
                <SectionTitle title="Data Access" icon={<BookOpen size={16} color={theme.color.mutedForeground as any} />} mb={4} />
                <View>
                  {/* Segmented control */}
                  <View style={{ backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6, flexDirection: 'row', marginBottom: 10 }}>
                    {(['all','select'] as DataAccessMode[]).map(mode => (
                      <TouchableOpacity
                        key={`acc-${mode}`}
                        onPress={() => setAccessMode(mode)}
                        activeOpacity={0.85}
                        style={{ flex: 1, paddingVertical: 10, borderRadius: theme.radius.sm, backgroundColor: accessMode === mode ? theme.color.card : 'transparent' }}
                      >
                        <Text style={{ textAlign: 'center', color: accessMode === mode ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>
                          {mode === 'all' ? 'Grant all access' : 'Select collections'}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {accessMode === 'all' && (
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
                      This agent will have access to all current and future files.
                    </Text>
                  )}

                  {accessMode === 'select' && (
                    <View>
                      <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                        {availableCollections.map(col => {
                          const selected = accessSelected.includes(col)
                          return (
                            <TouchableOpacity
                              key={`col-${col}`}
                              onPress={() => toggleAccessCollection(col)}
                              activeOpacity={0.85}
                              style={{
                                marginRight: 8,
                                marginBottom: 8,
                                paddingVertical: 10,
                                paddingHorizontal: 14,
                                borderRadius: theme.radius.md,
                                backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent)
                              }}
                            >
                              <Text style={{ color: selected ? ('#ffffff' as any) : (theme.color.mutedForeground as any), fontWeight: '700', fontSize: 13 }}>
                                {col}
                              </Text>
                            </TouchableOpacity>
                          )
                        })}
                      </View>
                      <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 4 }}>
                        Selected: {accessSelected.length} {accessSelected.length === 1 ? 'collection' : 'collections'}
                      </Text>
                    </View>
                  )}

                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 8 }}>
                    Changes here are UI-only for now and will sync later.
                  </Text>
                </View>
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
          {/* Activate / Deactivate - filled, borderless */}
          <View style={{ flex: 1 }}>
            <TouchableOpacity
              onPress={() => onToggleStatus?.(agent.id)}
              activeOpacity={0.85}
              style={{
                height: ACTION_BUTTON_HEIGHT,
                borderRadius: ACTION_BUTTON_RADIUS,
                backgroundColor: (agent.status === 'active' ? theme.color.error : theme.color.success) as any,
                alignItems: 'center',
                justifyContent: 'center',
                width: '100%'
              }}
              accessibilityLabel={`${agent.status === 'active' ? 'Deactivate' : 'Activate'} agent ${name}`}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Power size={18} color={'#fff'} />
                <Text style={{ color: '#fff', fontSize: 16, fontWeight: '600' }}>
                  {agent.status === 'active' ? 'Deactivate' : 'Activate'}
                </Text>
              </View>
            </TouchableOpacity>
          </View>

          {/* Edit - neutral card-like, borderless */}
          <View style={{ flex: 1 }}>
            <TouchableOpacity
              onPress={() => onEdit?.(agent.id)}
              activeOpacity={0.85}
              style={{
                height: ACTION_BUTTON_HEIGHT,
                borderRadius: ACTION_BUTTON_RADIUS,
                backgroundColor: theme.dark ? (theme.color.secondary as any) : ('hsl(240,6%,92%)' as any),
                alignItems: 'center',
                justifyContent: 'center',
                width: '100%'
              }}
              accessibilityLabel={`Edit agent ${name}`}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Settings size={18} color={theme.color.primary as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>Edit</Text>
              </View>
            </TouchableOpacity>
          </View>
        </View>

        {/* Close - filled primary, borderless */}
        <TouchableOpacity
          onPress={onClose}
          activeOpacity={0.85}
          style={{
            height: ACTION_BUTTON_HEIGHT,
            borderRadius: ACTION_BUTTON_RADIUS,
            backgroundColor: theme.color.primary as any,
            alignItems: 'center',
            justifyContent: 'center',
            width: '100%'
          }}
          accessibilityLabel={'Close agent details'}
        >
          <Text style={{ color: '#fff', fontSize: 16, fontWeight: '600' }}>Close</Text>
        </TouchableOpacity>
      </View>
    </Modal>
  )
}
