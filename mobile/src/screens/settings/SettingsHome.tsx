import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Switch } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'
import BusinessProfileScreen from './BusinessProfileScreen'
import DeveloperShell from './DeveloperShell'
import AboutLegal from './AboutLegal'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'

type SectionKey = 'home' | 'business' | 'branding' | 'locale' | 'notifications' | 'publish' | 'developer' | 'api' | 'webhooks' | 'about'

const Row: React.FC<{ title: string; subtitle?: string; onPress: () => void }>=({ title, subtitle, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={title} style={{ paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>{title}</Text>
      {subtitle ? <Text style={{ color: theme.color.mutedForeground, marginTop: 2 }}>{subtitle}</Text> : null}
    </TouchableOpacity>
  )
}

const SettingsHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [section, setSection] = React.useState<SectionKey>('home')
  const [themeName, setThemeName] = React.useState<string>('Default')
  const [themeVerified, setThemeVerified] = React.useState<boolean>(false)
  const [locale, setLocale] = React.useState<string>('en-US')
  const [timezone, setTimezone] = React.useState<string>('UTC')
  const [notifyEmail, setNotifyEmail] = React.useState<boolean>(true)
  const [notifyPush, setNotifyPush] = React.useState<boolean>(false)
  const [offline, setOffline] = React.useState<boolean>(false)
  const [syncOpen, setSyncOpen] = React.useState<boolean>(false)

  React.useEffect(() => { track('settings.view') }, [])

  const notificationsSummary = `Email: ${notifyEmail ? 'On' : 'Off'}, Push: ${notifyPush ? 'On' : 'Off'}`
  const localeSummary = `${locale} • ${timezone}`
  const themeSummary = `${themeName}${themeVerified ? ' • Verified' : ''}`

  const Header: React.FC<{ title: string }>=({ title }) => (
    <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
      {offline && (
        <View style={{ marginBottom: 8 }}>
          <OfflineBanner visible={offline} testID="set-offline" />
        </View>
      )}
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700' }}>{title}</Text>
        <TouchableOpacity onPress={() => (navigation as any).navigate('HelpCenter', { initialTab: 'search', initialTag: 'Settings' })} accessibilityRole="button" accessibilityLabel="Help" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground }}>?</Text>
        </TouchableOpacity>
        {section === 'home' ? (
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle offline" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: offline ? theme.color.primary : theme.color.cardForeground }}>{offline ? 'Cached' : 'Online'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityRole="button" accessibilityLabel="Sync Center" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Sync</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => {}} accessibilityRole="button" accessibilityLabel="Account" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Account</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { try { track('settings.developer.open') } catch {}; setSection('developer') }} accessibilityRole="button" accessibilityLabel="Developer" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Developer</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { try { track('settings.help.open') } catch {}; (navigation as any).navigate('HelpCenter') }} accessibilityRole="button" accessibilityLabel="Help & Docs" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Help</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { try { track('settings.about.open') } catch {}; setSection('about') }} accessibilityRole="button" accessibilityLabel="About" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>About</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <TouchableOpacity onPress={() => setSection('home')} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  )

  if (section === 'business') {
    return (
      <BusinessProfileScreen
        initial={{ id: 'bp-1', name: 'Acme Inc.', website: 'https://example.com', country: 'USA', industry: 'E-commerce', description: 'Great products' }}
        onSave={(p) => { /* update local summary */ setThemeName((n) => n); }}
      />
    )
  }

  if (section === 'branding') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="Branding & Theming" />
          <View style={{ paddingHorizontal: 24, gap: 12 }}>
            <Row title="Theme name" subtitle={themeName} onPress={() => setThemeName(themeName === 'Default' ? 'Brand Blue' : 'Default')} />
            <Row title="Verify applied to Channels" subtitle={themeVerified ? 'Verified' : 'Not verified'} onPress={() => setThemeVerified((v) => !v)} />
            <Row title="Primary color" subtitle="#4F46E5" onPress={() => {}} />
            <Row title="Bubble radius" subtitle="12" onPress={() => {}} />
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (section === 'developer') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="Developer" />
          <View style={{ paddingHorizontal: 24, marginTop: 8 }}>
            <DeveloperShell onOpenApi={() => setSection('api')} onOpenWebhooks={() => setSection('webhooks')} />
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (section === 'api') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="API Keys" />
          <View style={{ paddingHorizontal: 24, gap: 12 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Placeholder: Manage API keys here (create, revoke, copy).</Text>
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (section === 'webhooks') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="Webhooks" />
          <View style={{ paddingHorizontal: 24, gap: 12 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Placeholder: Configure webhook endpoints and secret validation.</Text>
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (section === 'about') {
    return (
      <AboutLegal />
    )
  }

  if (section === 'locale') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="Locale & Timezone" />
          <View style={{ paddingHorizontal: 24, gap: 12 }}>
            <Row title="Locale" subtitle={locale} onPress={() => setLocale(locale === 'en-US' ? 'ar' : 'en-US')} />
            <Row title="Timezone" subtitle={timezone} onPress={() => setTimezone(timezone === 'UTC' ? 'UTC+3' : 'UTC')} />
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (section === 'notifications') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="Notifications" />
          <View style={{ paddingHorizontal: 24, gap: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
              <Text style={{ color: theme.color.cardForeground }}>Email</Text>
              <Switch value={notifyEmail} onValueChange={setNotifyEmail} accessibilityLabel="Email notifications" />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
              <Text style={{ color: theme.color.cardForeground }}>Push</Text>
              <Switch value={notifyPush} onValueChange={setNotifyPush} accessibilityLabel="Push notifications" />
            </View>
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (section === 'publish') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView>
          <Header title="Publish Theme" />
          <View style={{ paddingHorizontal: 24, gap: 12 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Publishing will apply theme to Channels surfaces.</Text>
            <TouchableOpacity onPress={() => { setThemeVerified(true) }} accessibilityRole="button" accessibilityLabel="Publish theme" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Publish</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  // Home
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <Header title="Settings" />
        <View style={{ paddingHorizontal: 24 }}>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, overflow: 'hidden' }}>
            <Row title="Business Profile" subtitle="Name, website, industry" onPress={() => setSection('business')} />
            <Row title="Branding & Theming" subtitle={themeSummary} onPress={() => setSection('branding')} />
            <Row title="Locale & Timezone" subtitle={localeSummary} onPress={() => setSection('locale')} />
            <Row title="Notifications" subtitle={notificationsSummary} onPress={() => setSection('notifications')} />
            <Row title="Publish Theme" subtitle={themeVerified ? 'Verified' : 'Draft'} onPress={() => setSection('publish')} />
            <Row title="Billing & Plans" subtitle="Subscription, usage, invoices" onPress={() => navigation.navigate('Billing')} />
          </View>
        </View>
      </ScrollView>
      <SyncCenterSheet visible={syncOpen} onClose={() => setSyncOpen(false)} lastSyncAt={new Date().toLocaleString()} queuedCount={offline ? 4 : 0} onRetryAll={() => setSyncOpen(false)} />
    </SafeAreaView>
  )
}

export default SettingsHome


