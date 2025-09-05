import React from 'react'
import { ScrollView, View, Text, Pressable, RefreshControl } from 'react-native'
import { hapticSelection } from '@/utils/haptics'
import { useQuery } from '@tanstack/react-query'
import { Skeleton } from '@/components/system/Skeleton'
import { fetchHomeData } from '@/services/home'
import { useTheme } from '@/providers/ThemeProvider'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'

type BillingCycle = 'monthly' | 'yearly'

export const PricingScreen: React.FC = () => {
  const { theme } = useTheme()
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['pricingData', t('langKey', { defaultValue: '' })],
    queryFn: () => fetchHomeData((t as any).i18n),
  })
  const packages = (data?.packages || []) as { tier: string; monthly: number; yearly: number; features: string[] }[]
  const [cycle, setCycle] = React.useState<BillingCycle>('monthly')
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.color.background }}
      contentContainerStyle={{ padding: 16 }}
      refreshControl={<RefreshControl refreshing={!!isFetching && !isLoading} onRefresh={() => refetch()} tintColor={theme.color.primary as any} />}
    >
      <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700', marginBottom: 12 }}>{t('pricing.title')}</Text>
      {/* Billing toggle */}
      <View style={{ flexDirection: 'row', alignSelf: 'center', marginBottom: 12, backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}> 
        {(['monthly','yearly'] as BillingCycle[]).map(key => (
          <Pressable key={key} onPress={() => { setCycle(key); hapticSelection() }} style={{ paddingVertical: 8, paddingHorizontal: 16, borderRadius: 12, backgroundColor: cycle === key ? 'rgba(100,78,249,0.15)' : 'transparent' }}>
            <Text style={{ color: cycle === key ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{t(`pricing.billing.${key}`)}</Text>
          </Pressable>
        ))}
      </View>
      {isLoading ? (
        <View>
          <Skeleton height={120} radius={16} />
          <View style={{ height: 12 }} />
          <Skeleton height={120} radius={16} />
          <View style={{ height: 12 }} />
          <Skeleton height={120} radius={16} />
        </View>
      ) : isError ? (
        <View style={{ padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>Failed to load pricing.</Text>
          <Button title="Retry" variant="outline" onPress={() => refetch()} />
        </View>
      ) : (
        packages.map((p, idx) => (
          <View key={idx} style={{ backgroundColor: theme.color.card, borderColor: theme.color.border, borderWidth: 1, borderRadius: theme.radius.xl, padding: theme.spacing.lg, marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '600' }}>{p.tier}</Text>
              <Text style={{ color: theme.color.cardForeground, fontSize: 22, fontWeight: '700' }}>${cycle === 'monthly' ? p.monthly : p.yearly}<Text style={{ color: theme.color.mutedForeground, fontSize: 14 }}> {t('pricing.perMonth')}{cycle === 'yearly' ? ` · ${t('pricing.billing.yearlyNote')}` : ''}</Text></Text>
            </View>
            <View style={{ marginTop: 8, gap: 6 }}>
              {p.features.map((f, i) => (
                <Text key={i} style={{ color: theme.color.mutedForeground }}>• {f}</Text>
              ))}
            </View>
            <View style={{ marginTop: 12, alignItems: 'flex-start' }}>
              <Button title={t('pricing.getStarted') || 'Get started'} variant="outline" />
            </View>
          </View>
        ))
      )}
    </ScrollView>
  )
}
