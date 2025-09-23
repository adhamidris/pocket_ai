import React, { useState } from 'react'
import { View, Text, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { 
  Bell,
  Mail,
  MessageCircle,
  ShieldAlert,
  CreditCard,
  Calendar,
  Hash,
  Bot
} from 'lucide-react-native'

export const NotificationsSection: React.FC = () => {
  const { theme } = useTheme()

  const withAlpha = (c: string, a: number) =>
    c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c

  const [prefs, setPrefs] = useState({
    push: true,
    email: true,
    sms: false,
    convoMentions: true,
    convoAssigned: true,
    agentAlerts: true,
    billingReminders: true,
    planLimits: true,
    reportsDaily: false,
    reportsWeekly: true,
  })

  const Row: React.FC<{ icon: React.ReactNode; title: string; subtitle?: string; value: boolean; onValueChange: (v: boolean) => void }>
    = ({ icon, title, subtitle, value, onValueChange }) => (
    <View style={{
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 8
    }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1 }}>
        {icon}
        <View style={{ flex: 1 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '500' }}>{title}</Text>
          {subtitle && (
            <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{subtitle}</Text>
          )}
        </View>
      </View>
      <Switch
        value={value}
        onValueChange={onValueChange}
        trackColor={{
          false: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any,
          true: withAlpha(theme.color.muted, theme.dark ? 0.35 : 0.2) as any
        }}
        thumbColor={value ? (theme.color.primary as any) : (theme.color.mutedForeground as any)}
      />
    </View>
  )

  return (
    <View>
      {/* General */}
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>General</Text>
        <Row
          icon={<Bell size={16} color={theme.color.primary as any} />}
          title="Push Notifications"
          subtitle="Alerts delivered to your device"
          value={prefs.push}
          onValueChange={(v) => setPrefs(p => ({ ...p, push: v }))}
        />
        <Row
          icon={<Mail size={16} color={theme.color.primary as any} />}
          title="Email Notifications"
          subtitle="Important updates via email"
          value={prefs.email}
          onValueChange={(v) => setPrefs(p => ({ ...p, email: v }))}
        />
        <Row
          icon={<Hash size={16} color={theme.color.primary as any} />}
          title="SMS Notifications"
          subtitle="Carrier rates may apply"
          value={prefs.sms}
          onValueChange={(v) => setPrefs(p => ({ ...p, sms: v }))}
        />
      </Card>

      {/* Conversations */}
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Conversations</Text>
        <Row
          icon={<MessageCircle size={16} color={theme.color.primary as any} />}
          title="Mentions"
          subtitle="When someone @mentions you"
          value={prefs.convoMentions}
          onValueChange={(v) => setPrefs(p => ({ ...p, convoMentions: v }))}
        />
        <Row
          icon={<Bot size={16} color={theme.color.primary as any} />}
          title="Agent Alerts"
          subtitle="When an AI agent requires attention"
          value={prefs.agentAlerts}
          onValueChange={(v) => setPrefs(p => ({ ...p, agentAlerts: v }))}
        />
        <Row
          icon={<MessageCircle size={16} color={theme.color.primary as any} />}
          title="Assigned To Me"
          subtitle="New conversations assigned to you"
          value={prefs.convoAssigned}
          onValueChange={(v) => setPrefs(p => ({ ...p, convoAssigned: v }))}
        />
      </Card>

      {/* Billing & Account */}
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Billing & Account</Text>
        <Row
          icon={<CreditCard size={16} color={theme.color.primary as any} />}
          title="Billing Reminders"
          subtitle="Upcoming charges and payment issues"
          value={prefs.billingReminders}
          onValueChange={(v) => setPrefs(p => ({ ...p, billingReminders: v }))}
        />
        <Row
          icon={<ShieldAlert size={16} color={theme.color.primary as any} />}
          title="Plan Limits"
          subtitle="Usage thresholds and overages"
          value={prefs.planLimits}
          onValueChange={(v) => setPrefs(p => ({ ...p, planLimits: v }))}
        />
      </Card>

      {/* Reports */}
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Reports</Text>
        <Row
          icon={<Calendar size={16} color={theme.color.primary as any} />}
          title="Daily Summary"
          subtitle="Key activity delivered each morning"
          value={prefs.reportsDaily}
          onValueChange={(v) => setPrefs(p => ({ ...p, reportsDaily: v }))}
        />
        <Row
          icon={<Calendar size={16} color={theme.color.primary as any} />}
          title="Weekly Summary"
          subtitle="Digest every Monday"
          value={prefs.reportsWeekly}
          onValueChange={(v) => setPrefs(p => ({ ...p, reportsWeekly: v }))}
        />
      </Card>

      {/* Do Not Disturb removed as requested */}
    </View>
  )
}
