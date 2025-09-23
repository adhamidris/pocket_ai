import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity, Linking } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Shield, FileText, Mail } from 'lucide-react-native'

type Section = { id: string; title: string; paragraphs?: string[]; bullets?: string[] }

const PRIVACY: { title: string; sections: Section[] } = {
  title: 'Privacy Policy',
  sections: [
    {
      id: 'overview',
      title: '1. Overview',
      paragraphs: [
        "Pocket AI Support ('we', 'us') provides AI‑powered customer service tools. This Privacy Policy explains how we collect, use, share, and protect your information when you use our website, products, and services.",
      ],
    },
    {
      id: 'collection',
      title: '2. Information We Collect',
      bullets: [
        'Account & Profile: name, email, company, preferences',
        'Usage: app interactions, diagnostics, device data',
        'Customer Data: content you upload or connect (e.g., KB files, chat)',
        'Integrations: data exchanged with connected tools per your configuration',
      ],
    },
    {
      id: 'use',
      title: '3. How We Use Information',
      bullets: [
        'Provide, secure, and improve the Services',
        'Personalize experiences and deliver support',
        'Analyze usage to enhance reliability and performance',
        'Comply with legal obligations and enforce terms',
      ],
    },
    {
      id: 'sharing',
      title: '4. How We Share',
      bullets: [
        'Service providers under contract and confidentiality',
        'Third‑party integrations you connect (per your instructions)',
        'Legal and safety: when required by law or to protect rights',
      ],
    },
    {
      id: 'intl',
      title: '5. International Data Transfers',
      paragraphs: [
        'Where applicable, we use appropriate safeguards (e.g., SCCs) for cross‑border data transfers.',
      ],
    },
    {
      id: 'retention',
      title: '6. Data Retention',
      paragraphs: [
        'We retain information for as long as needed to provide the Services, comply with law, and resolve disputes. You can request deletion subject to legal and operational requirements.',
      ],
    },
    {
      id: 'rights',
      title: "7. Your Rights",
      bullets: [
        'Access, correct, export, or delete your information',
        'Object or restrict certain processing',
        'Withdraw consent where applicable',
      ],
    },
    {
      id: 'security',
      title: '8. Security',
      paragraphs: [
        'We use administrative, technical, and organizational measures to protect information. No method is 100% secure; report issues to privacy@pocket.ai.',
      ],
    },
    {
      id: 'children',
      title: "9. Children’s Privacy",
      paragraphs: [
        'The Services are not directed to children. Do not submit children’s data without proper authorization.',
      ],
    },
    {
      id: 'changes',
      title: '10. Changes to this Policy',
      paragraphs: [
        'We may update this Policy. Material changes will be communicated via the app or email. Continued use constitutes acceptance.',
      ],
    },
    {
      id: 'contact',
      title: '11. Contact',
      paragraphs: [
        'For privacy inquiries or requests, contact privacy@pocket.ai.',
      ],
    },
  ],
}

const TERMS: { title: string; sections: Section[] } = {
  title: 'Terms of Service',
  sections: [
    {
      id: 'overview',
      title: '1. Overview',
      paragraphs: [
        'These Terms govern your use of the Services. By accessing or using the Services, you agree to these Terms. If you do not agree, do not use the Services.',
      ],
    },
    {
      id: 'eligibility',
      title: '2. Eligibility & Acceptable Use',
      bullets: [
        'You must have authority to bind your organization',
        'Use the Services only for lawful purposes and within usage limits',
        'Do not misuse the Services or violate rights/laws',
      ],
    },
    {
      id: 'data',
      title: '3. Customer Data & Privacy',
      paragraphs: [
        'You retain ownership of Customer Data. We process it to provide and improve the Services per our Privacy Policy. You are responsible for necessary consents and rights.',
      ],
    },
    {
      id: 'billing',
      title: '4. Subscriptions & Billing',
      paragraphs: [
        'Plans may be billed monthly/annually. Fees are non‑refundable except as required by law or stated otherwise.',
      ],
    },
    {
      id: 'integrations',
      title: '5. Integrations',
      paragraphs: [
        'When you connect third‑party tools (e.g., CRM, messaging), you authorize us to exchange necessary data with those services subject to their terms.',
      ],
    },
    {
      id: 'termination',
      title: '6. Suspension & Termination',
      paragraphs: [
        'We may suspend or terminate access for violations of these Terms or to protect the Services.',
      ],
    },
    {
      id: 'liability',
      title: '7. Disclaimers & Liability',
      paragraphs: [
        'The Services are provided “as is”. To the extent permitted by law, we disclaim warranties and limit liability as outlined in full Terms on the web app.',
      ],
    },
    {
      id: 'indemnity',
      title: '8. Indemnity',
      paragraphs: [
        'You will defend and indemnify the company for claims arising from your use of the Services or violation of these Terms.',
      ],
    },
    {
      id: 'law',
      title: '9. Governing Law',
      paragraphs: [
        'These Terms are governed by the laws of the organizing jurisdiction, without regard to conflict of laws.',
      ],
    },
    {
      id: 'changes',
      title: '10. Changes to these Terms',
      paragraphs: [
        'We may update these Terms. Material changes will be communicated via the app or email. Continued use constitutes acceptance.',
      ],
    },
    {
      id: 'contact',
      title: '11. Contact',
      paragraphs: [
        'Questions about these Terms? Contact legal@pocket.ai.',
      ],
    },
  ],
}

export const PrivacySection: React.FC = () => {
  const { theme } = useTheme()
  const [tab, setTab] = useState<'privacy' | 'terms'>('privacy')
  const data = tab === 'privacy' ? PRIVACY : TERMS

  const openEmail = (email: string) => Linking.openURL(`mailto:${email}`).catch(() => {})

  return (
    <View>
      {/* Toggle */}
      <View style={{ flexDirection: 'row', marginBottom: 12 }}>
        <TouchableOpacity
          onPress={() => setTab('privacy')}
          style={{
            flex: 1,
            paddingVertical: 10,
            borderRadius: theme.radius.md,
            backgroundColor: tab === 'privacy' ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
            alignItems: 'center'
          }}
        >
          <Text style={{ color: tab === 'privacy' ? '#fff' : (theme.color.mutedForeground as any), fontWeight: '700' }}>Privacy</Text>
        </TouchableOpacity>
        <View style={{ width: 8 }} />
        <TouchableOpacity
          onPress={() => setTab('terms')}
          style={{
            flex: 1,
            paddingVertical: 10,
            borderRadius: theme.radius.md,
            backgroundColor: tab === 'terms' ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
            alignItems: 'center'
          }}
        >
          <Text style={{ color: tab === 'terms' ? '#fff' : (theme.color.mutedForeground as any), fontWeight: '700' }}>Terms</Text>
        </TouchableOpacity>
      </View>

      <Card variant="flat" style={{ backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          {tab === 'privacy' ? (
            <Shield size={16} color={theme.color.primary as any} />
          ) : (
            <FileText size={16} color={theme.color.primary as any} />
          )}
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700' }}>{data.title}</Text>
        </View>
        <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ paddingBottom: 4 }}>
          {data.sections.map((s) => (
            <View key={s.id} style={{ marginBottom: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>{s.title}</Text>
              {(s.paragraphs || []).map((p, i) => (
                <Text key={`${s.id}-p-${i}`} style={{ color: theme.color.mutedForeground, fontSize: 13, lineHeight: 20, marginBottom: 6 }}>{p}</Text>
              ))}
              {(s.bullets || []).map((b, i) => (
                <View key={`${s.id}-b-${i}`} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8, marginBottom: 4 }}>
                  <Text style={{ color: theme.color.primary, fontSize: 12, marginTop: 2 }}>•</Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 13, lineHeight: 20, flex: 1 }}>{b}</Text>
                </View>
              ))}
            </View>
          ))}

          {/* Contact shortcuts */}
          <View style={{ flexDirection: 'row', gap: 12, marginTop: 4 }}>
            <TouchableOpacity onPress={() => openEmail('privacy@pocket.ai')} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Mail size={14} color={theme.color.primary as any} />
              <Text style={{ color: theme.color.primary, fontSize: 13, fontWeight: '700' }}>privacy@pocket.ai</Text>
            </TouchableOpacity>
            {tab === 'terms' && (
              <TouchableOpacity onPress={() => openEmail('legal@pocket.ai')} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Mail size={14} color={theme.color.primary as any} />
                <Text style={{ color: theme.color.primary, fontSize: 13, fontWeight: '700' }}>legal@pocket.ai</Text>
              </TouchableOpacity>
            )}
          </View>
        </ScrollView>
      </Card>
    </View>
  )
}

