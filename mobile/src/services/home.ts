export type HomeData = {
  features: string[]
  testimonials: { quote: string; author: string; role: string }[]
  packages: { tier: string; monthly: number; yearly: number; features: string[] }[]
}

export async function fetchHomeData(i18n: any): Promise<HomeData> {
  // Simulate network latency
  await new Promise(r => setTimeout(r, 800))
  // Map from i18n to a response-like object
  const features = i18n.t('featuresSnap.items', { returnObjects: true }) as string[]
  const testimonials = i18n.t('testimonials.items', { returnObjects: true }) as any[]
  const packages = i18n.t('pricing.packages', { returnObjects: true }) as any[]
  return { features, testimonials, packages }
}

