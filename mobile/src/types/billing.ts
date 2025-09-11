export type Currency = 'USD' | 'EUR' | 'GBP' | 'EGP' | 'AED' | 'SAR'
export type Interval = 'month' | 'year'
export type PlanTier = 'Free' | 'Starter' | 'Pro' | 'Scale' | 'Enterprise'

export interface Price { id: string; currency: Currency; unitAmount: number; interval: Interval; trialDays?: number }
export interface FeatureLimit { key: string; value: number | 'unlimited'; note?: string }
export interface Entitlement { key: string; enabled: boolean; limit?: number | 'unlimited' }

export interface Plan {
  id: string
  tier: PlanTier
  name: string
  blurb: string
  prices: Price[]
  features: FeatureLimit[]
  entitlements: Entitlement[]
  recommended?: boolean
}

export type PaymentBrand = 'visa' | 'mastercard' | 'amex' | 'mada' | 'paypal' | 'applepay' | 'googlepay'
export interface PaymentMethod { id: string; brand: PaymentBrand; last4?: string; expMonth?: number; expYear?: number; isDefault: boolean }

export type SubStatus = 'trialing' | 'active' | 'past_due' | 'canceled' | 'incomplete' | 'incomplete_expired' | 'paused'
export interface Subscription {
  id: string
  planId: string
  status: SubStatus
  currentPeriodStart: number
  currentPeriodEnd: number
  cancelAtPeriodEnd?: boolean
  quantity: number
  currency: Currency
  unitAmount: number
  proration?: boolean
  couponId?: string
}

export interface Invoice { id: string; number: string; amountDue: number; currency: Currency; created: number; dueDate?: number; status: 'paid' | 'open' | 'void' | 'uncollectible'; pdfUrl?: string }

export interface UsageMeter { key: 'messages' | 'mtu' | 'uploadsMB' | 'knowledgeSources' | 'automations' | 'agents'; periodStart: number; periodEnd: number; used: number; limit: number | 'unlimited' }

export interface Coupon { id: string; code: string; percentOff?: number; amountOffCents?: number; currency?: Currency; duration: 'once' | 'repeating' | 'forever'; maxRedemptions?: number; expiresAt?: number }

export interface TaxInfo { country: string; vatId?: string; invoiceName?: string; address1?: string; city?: string; zip?: string }

export interface DunningState { cardExpiring: boolean; lastChargeFailed?: boolean; lastFailureMessage?: string; retryAt?: number; graceUntil?: number }

export interface BillingPortalState {
  plans: Plan[]
  sub?: Subscription
  paymentMethods: PaymentMethod[]
  invoices: Invoice[]
  usage: UsageMeter[]
  coupons: Coupon[]
  tax?: TaxInfo
  dunning?: DunningState
  currency: Currency
}


