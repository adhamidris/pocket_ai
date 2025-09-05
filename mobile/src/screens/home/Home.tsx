import React from 'react'
import { View, Text, ScrollView } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '@/providers/ThemeProvider'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { useNavigation } from '@react-navigation/native'
import { TestimonialsCarousel } from '@/components/TestimonialsCarousel'
import { useQuery } from '@tanstack/react-query'
import { fetchHomeData } from '@/services/home'
import { Skeleton } from '@/components/system/Skeleton'
import { RefreshControl } from 'react-native'
import { useToast } from '@/providers/ToastProvider'

const StepMini: React.FC<{ title: string; bullets: string[] }> = ({ title, bullets }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flex: 1, backgroundColor: theme.color.card, borderColor: theme.color.border, borderWidth: 1, borderRadius: theme.radius.xl, padding: theme.spacing.lg }}>
      <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', textAlign: 'center' }}>{title}</Text>
      <View style={{ marginTop: 8, gap: 6 }}>
        {bullets.slice(0, 2).map((b, i) => (
          <Text key={i} style={{ color: theme.color.mutedForeground, textAlign: 'center' }}>• {b}</Text>
        ))}
      </View>

      {/* Dev: trigger local notification */}
      <View style={{ marginTop: 16, paddingHorizontal: 16 }}>
        <Button title="Send test notification" variant="outline" onPress={async () => {
          const { scheduleTestNotification } = await import('@/notifications/setup')
          await scheduleTestNotification()
          show('Notification scheduled', 'success')
        }} />
      </View>
    </View>
  )
}

export const Home: React.FC = () => {
  const { theme } = useTheme()
  const { t } = useTranslation()
  const nav = useNavigation<any>()
  const steps = t<any>('howItWorks.steps', { returnObjects: true }) as { title: string; bullets: string[] }[]
  const { show } = useToast()
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['homeData', t('langKey', { defaultValue: '' })],
    queryFn: () => fetchHomeData((t as any).i18n),
  })
  const testimonials = data?.testimonials || []
  const packages = (data?.packages || []) as { tier: string; monthly: number; yearly: number; features: string[] }[]
  const features = data?.features || []
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.color.background }}
      contentContainerStyle={{ paddingBottom: 32 }}
      refreshControl={<RefreshControl refreshing={!!isFetching && !isLoading} onRefresh={() => {
        refetch().then((r: any) => {
          if (r?.data) show('Updated', 'success'); else show('Failed to refresh', 'error')
        }).catch(() => show('Failed to refresh', 'error'))
      }} tintColor={theme.color.primary as any} />}
    >
      {/* Hero */}
      <LinearGradient colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ borderRadius: 24, margin: 16 }}>
        <View style={{ padding: 24 }}>
          <Text style={{ color: '#fff', fontSize: 28, fontWeight: '700' }}>{t('hero.title')}</Text>
          <Text style={{ color: 'rgba(255,255,255,0.9)', marginTop: 8 }}>{t('hero.subtitle')}</Text>
          <View style={{ marginTop: 16, flexDirection: 'row', gap: 12 }}>
            <Button title={t('home.ctaStart') || 'Get Started'} variant="hero" onPress={() => show('Let’s go', 'info')} />
            <Button title={t('home.ctaSignIn') || 'Sign In'} variant="outline" onPress={() => show('Sign in tapped', 'info')} />
          </View>
        </View>
      </LinearGradient>

      {/* It is simple snapshot */}
      <View style={{ paddingHorizontal: 16, gap: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '600', marginBottom: 8 }}>It is simple</Text>
        <View style={{ flexDirection: 'row', gap: 12 }}>
          {steps.slice(0, 3).map((s, i) => (
            <StepMini key={i} title={s.title} bullets={s.bullets || []} />
          ))}
        </View>
      </View>

      {/* Features snapshot */}
      <View style={{ marginTop: 24, paddingHorizontal: 16 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '600', marginBottom: 8 }}>{t('featuresSnap.title') || 'Features'}</Text>
        {isLoading ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
            <Skeleton width={'48%'} height={64} radius={theme.radius.xl} />
            <Skeleton width={'48%'} height={64} radius={theme.radius.xl} />
            <Skeleton width={'48%'} height={64} radius={theme.radius.xl} />
            <Skeleton width={'48%'} height={64} radius={theme.radius.xl} />
          </View>
        ) : isError ? (
          <View style={{ padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>Failed to load features.</Text>
            <Button title="Retry" variant="outline" onPress={() => refetch()} />
          </View>
        ) : (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
            {features.slice(0, 4).map((label, idx) => (
              <View key={idx} style={{ flexBasis: '48%', backgroundColor: theme.color.card, borderColor: theme.color.border, borderWidth: 1, borderRadius: theme.radius.xl, padding: 12, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontWeight: '600' }}>{label}</Text>
              </View>
            ))}
          </View>
        )}
      </View>

      {/* Testimonials */}
      <View style={{ marginTop: 24 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '600', marginHorizontal: 16, marginBottom: 8 }}>{t('testimonials.title') || 'Testimonials'}</Text>
        {isLoading ? (
          <View style={{ flexDirection: 'row', paddingHorizontal: 16, gap: 12 }}>
            <Skeleton width={280} height={140} radius={16} />
            <Skeleton width={280} height={140} radius={16} />
          </View>
        ) : isError ? (
          <View style={{ paddingHorizontal: 16 }}>
            <View style={{ padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>Failed to load testimonials.</Text>
              <Button title="Retry" variant="outline" onPress={() => refetch()} />
            </View>
          </View>
        ) : (
          <TestimonialsCarousel items={testimonials} />
        )}
      </View>

      {/* Pricing summary */}
      <View style={{ marginTop: 24, paddingHorizontal: 16 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '600', marginBottom: 8 }}>{t('pricing.title') || 'Pricing'}</Text>
        {isLoading ? (
          <View style={{ gap: 12 }}>
            <Skeleton height={100} radius={16} />
            <Skeleton height={100} radius={16} />
            <Skeleton height={100} radius={16} />
          </View>
        ) : isError ? (
          <View style={{ padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>Failed to load pricing.</Text>
            <Button title="Retry" variant="outline" onPress={() => refetch()} />
          </View>
        ) : (
          <View style={{ gap: 12 }}>
            {packages.map((p, idx) => (
              <View key={idx} style={{ backgroundColor: theme.color.card, borderColor: theme.color.border, borderWidth: 1, borderRadius: theme.radius.xl, padding: theme.spacing.lg }}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '600' }}>{p.tier}</Text>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 22, fontWeight: '700' }}>${p.monthly}<Text style={{ color: theme.color.mutedForeground, fontSize: 14 }}> {t('pricing.perMonth')}</Text></Text>
                </View>
                <View style={{ marginTop: 8, gap: 6 }}>
                  {p.features.slice(0, 3).map((f, i) => (
                    <Text key={i} style={{ color: theme.color.mutedForeground }}>• {f}</Text>
                  ))}
                </View>
                <View style={{ marginTop: 12, alignItems: 'flex-start' }}>
                  <Button title={t('pricing.getStarted') || 'Get started'} variant="outline" />
                </View>
              </View>
            ))}
          </View>
        )}
        <View style={{ marginTop: 12, alignItems: 'flex-end' }}>
          <Button title={t('pricing.seeFull') || 'See full pricing'} variant="link" onPress={() => nav.navigate('Pricing')} />
        </View>
      </View>
    </ScrollView>
  )
}
