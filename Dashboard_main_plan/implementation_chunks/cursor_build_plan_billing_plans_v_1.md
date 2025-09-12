# Cursor Build Plan — Billing & Plans (Subscriptions, Usage, Entitlements) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first (no backend), local demo state

> Build **Billing & Plans**: plan matrix, checkout/upgrade flows, payment methods, invoices, taxes, usage meters, dunning, coupons, entitlements gating, and cross‑app upsells. Paste each *Prompt Block* into Cursor, complete it, run, verify with *Acceptance Checks*, then proceed.

---

## Assumptions
- Settings module exists (we’ll link Billing there). Analytics util `track()` and Offline/Sync Center stubs exist.
- Entitlement checks will gate features across modules (Dashboard/Knowledge/Automations/Analytics/Channels).

## Paths & Conventions
- **Screens:** `mobile/src/screens/Billing/*`
- **Components:** `mobile/src/components/billing/*`
- **Types:** `mobile/src/types/billing.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix `bill-` (e.g., `bill-home`, `bill-upgrade`, `bill-card-add`, `bill-invoice-row-<id>`)
- Spacing 4/8/12/16/24; touch ≥ 44×44dp; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Models for plans, prices, subscription, payment methods, invoices, usage, coupons, entitlements.

### Prompt Block
Create `mobile/src/types/billing.ts`:
```ts
export type Currency = 'USD'|'EUR'|'GBP'|'EGP'|'AED'|'SAR';
export type Interval = 'month'|'year';
export type PlanTier = 'Free'|'Starter'|'Pro'|'Scale'|'Enterprise';

export interface Price { id:string; currency:Currency; unitAmount:number; interval:Interval; trialDays?:number }
export interface FeatureLimit { key:string; value:number|'unlimited'; note?:string }
export interface Entitlement { key:string; enabled:boolean; limit?:number|'unlimited' }

export interface Plan {
  id:string; tier:PlanTier; name:string; blurb:string;
  prices: Price[];
  features: FeatureLimit[];          // human‑readable
  entitlements: Entitlement[];       // machine‑readable gates
  recommended?: boolean;
}

export type PaymentBrand = 'visa'|'mastercard'|'amex'|'mada'|'paypal'|'applepay'|'googlepay';
export interface PaymentMethod { id:string; brand:PaymentBrand; last4?:string; expMonth?:number; expYear?:number; isDefault:boolean }

export type SubStatus = 'trialing'|'active'|'past_due'|'canceled'|'incomplete'|'incomplete_expired'|'paused';
export interface Subscription { id:string; planId:string; status:SubStatus; currentPeriodStart:number; currentPeriodEnd:number; cancelAtPeriodEnd?:boolean; quantity:number; currency:Currency; unitAmount:number; proration?:boolean; couponId?:string }

export interface Invoice { id:string; number:string; amountDue:number; currency:Currency; created:number; dueDate?:number; status:'paid'|'open'|'void'|'uncollectible'; pdfUrl?:string }

export interface UsageMeter { key:'messages'|'mtu'|'uploadsMB'|'knowledgeSources'|'automations'|'agents'; periodStart:number; periodEnd:number; used:number; limit:number|'unlimited' }

export interface Coupon { id:string; code:string; percentOff?:number; amountOffCents?:number; currency?:Currency; duration:'once'|'repeating'|'forever'; maxRedemptions?:number; expiresAt?:number }

export interface TaxInfo { country:string; vatId?:string; invoiceName?:string; address1?:string; city?:string; zip?:string }

export interface DunningState { cardExpiring:boolean; lastChargeFailed?:boolean; lastFailureMessage?:string; retryAt?:number; graceUntil?:number }

export interface BillingPortalState {
  plans: Plan[];
  sub?: Subscription;
  paymentMethods: PaymentMethod[];
  invoices: Invoice[];
  usage: UsageMeter[];
  coupons: Coupon[];
  tax?: TaxInfo;
  dunning?: DunningState;
  currency: Currency;
}
```

### Deliverables
- `types/billing.ts`

### Acceptance Checks
- Types compile; no circular deps.

---

## STEP 2 — Primitive Components
**Goal:** Reusable tiles for plan cards, price selector, usage bars, payment methods, invoices.

### Prompt Block
In `mobile/src/components/billing/`, create:
- `PlanCard.tsx` (`plan:Plan; selected?:boolean; onSelect:()=>void`) — shows name, blurb, price toggle (M/Y), top features, Recommended badge.
- `PriceToggle.tsx` (`value:Interval; onChange:(v:Interval)=>void`)
- `FeatureList.tsx` (`features:FeatureLimit[]`)
- `UsageBar.tsx` (`meter:UsageMeter`) — progress bar with limit label and %.
- `PaymentMethodRow.tsx` (`pm:PaymentMethod; onSetDefault:()=>void; onRemove:()=>void`)
- `InvoiceRow.tsx` (`inv:Invoice; onOpen:()=>void`)
- `CouponInput.tsx` (`onApply:(code:string)=>void; onRemove:()=>void; applied?:Coupon`)
- `TaxForm.tsx` (`value:TaxInfo; onChange:(v:TaxInfo)=>void`)
- `DunningBanner.tsx` (`state?:DunningState; onUpdateCard:()=>void`)
- `ListSkeleton.tsx`, `EmptyState.tsx`

### Deliverables
- 11 primitives exported

### Acceptance Checks
- Render primitives with demo props; touch ≥44dp; a11y labels present.

---

## STEP 3 — Billing Home
**Goal:** Overview with plan, usage, invoices, and actions.

### Prompt Block
Create `mobile/src/screens/Billing/BillingHome.tsx`:
- Header `Billing & Plans` with overflow: **Download invoices**, **Billing contacts**, **Help** (stubs).
- Sections:
  - **Current Plan**: card with plan name, status badge (trial/active/past due), next renewal date, **Manage Plan** CTA.
  - **Usage**: grid of `UsageBar`s for key meters.
  - **Payment Method**: default card row + **Manage Payment Methods** CTA.
  - **Invoices**: last 5 `InvoiceRow`s + **View All**.
  - **Dunning**: `DunningBanner` if applicable.
- Pull‑to‑refresh reshuffles demo state; `track('billing.view')` on mount.
Register as `Billing` in navigator and link from Settings home.

### Deliverables
- `BillingHome.tsx`
- Navigator update

### Acceptance Checks
- Home renders summaries; CTAs navigate to editors.

---

## STEP 4 — Plan Matrix & Upgrade Flow
**Goal:** Compare plans and start checkout.

### Prompt Block
Create `PlanMatrix.tsx`:
- `PriceToggle` for Monthly/Yearly; list `PlanCard`s for tiers (Free/Starter/Pro/Scale) with entitlements highlights (AI deflection, SLA, analytics depth, channels, knowledge size).
- **Compare table** button → opens `ComparePlansModal` with a simple matrix of `FeatureList`s.
- Selecting a plan → `CheckoutScreen` with selected plan/price.

Create `CheckoutScreen.tsx`:
- Shows plan summary, price (with interval), **quantity** (seats) stepper, coupon input, tax preview (UI only), total.
- Buttons: **Start trial** (if available), **Subscribe**.
- On complete: update local `Subscription` and `Entitlements`, add success toast.

### Deliverables
- `PlanMatrix.tsx`, `CheckoutScreen.tsx`, `ComparePlansModal.tsx`

### Acceptance Checks
- Switching interval updates prices; selecting plan flows to checkout; subscribing updates BillingHome.

---

## STEP 5 — Manage Plan (Upgrade/Downgrade, Cancel, Proration)
**Goal:** Change plan and proration messaging (UI‑only).

### Prompt Block
Create `ManagePlanScreen.tsx`:
- Shows current plan details, **Change Plan** (opens `PlanMatrix` in modal mode), **Cancel at period end** toggle, **Resume** if canceled.
- Proration note (UI text): shows sample credit/charge.
- **Pause subscription** toggle (UI only) with grace period explanation.

### Deliverables
- `ManagePlanScreen.tsx`

### Acceptance Checks
- Toggling cancel/ pause updates local sub; opening matrix updates plan.

---

## STEP 6 — Payment Methods
**Goal:** Add/remove/set default; card brand visuals.

### Prompt Block
Create `PaymentMethodsScreen.tsx`:
- List of `PaymentMethodRow`s; **Add method** opens sheet with fake card entry (brand + last4 + exp).
- Actions: **Set default**, **Remove** (confirm).
- Apple Pay/Google Pay toggles (UI only), PayPal connect stub.

### Deliverables
- `PaymentMethodsScreen.tsx`

### Acceptance Checks
- Adding/removing/default updates list; dunning banner clears when default added.

---

## STEP 7 — Invoices
**Goal:** Full invoices list and details.

### Prompt Block
Create `InvoicesScreen.tsx`:
- Filters: **Status**, **Date range**, **Amount**.
- Virtualized list of `InvoiceRow`s; tap opens **InvoiceDetail** (dummy with line items & **Open PDF** stub).
- **Download all** button logs event.

### Deliverables
- `InvoicesScreen.tsx`, `InvoiceDetail.tsx`

### Acceptance Checks
- Filters change list; detail opens; open PDF shows toast.

---

## STEP 8 — Taxes & Billing Profile
**Goal:** Collect VAT/tax information and invoice fields.

### Prompt Block
Create `TaxBillingProfile.tsx`:
- `TaxForm` for country, VAT ID, invoice name/address.
- Save updates local `TaxInfo` and shows preview on invoices.
- Regional hints (e.g., EU VAT, MENA tax ID) as UI text.

### Deliverables
- `TaxBillingProfile.tsx`

### Acceptance Checks
- Saving updates invoice preview fields; validation for basic formats.

---

## STEP 9 — Coupons & Promotions
**Goal:** Apply/remove coupons and show effects.

### Prompt Block
Add a `CouponsScreen.tsx`:
- `CouponInput` to apply code; show applied coupon chip with description.
- If `percentOff`, compute new total (UI only); if `amountOff`, show currency‑aware deduction.
- Remove coupon restores price.

### Deliverables
- `CouponsScreen.tsx`

### Acceptance Checks
- Applying/removing updates totals in checkout and manage screens.

---

## STEP 10 — Usage & Limits
**Goal:** Detail screens for meters and hard/soft caps.

### Prompt Block
Create `UsageLimitsScreen.tsx`:
- List of `UsageBar`s with notes for each meter.
- Soft cap banner (warn at 80%, 100%); **Request limit increase** button (stub).
- Hard cap behavior note (UI only) and **Auto‑upgrade** toggle.

### Deliverables
- `UsageLimitsScreen.tsx`

### Acceptance Checks
- Bars reflect demo state; toggles persist locally.

---

## STEP 11 — Dunning & Grace
**Goal:** Retry flow, grace period, past due states.

### Prompt Block
Create `DunningScreen.tsx`:
- `DunningBanner` at top; timeline of charge attempts; **Retry now** button (simulated result success/fail); **Update card** CTA navigates to Payment Methods.
- **Grace period** countdown (UI only); **Contact support** button (stub).

### Deliverables
- `DunningScreen.tsx`

### Acceptance Checks
- Retry toggles lastChargeFailed and clears on success; banners reflect state.

---

## STEP 12 — Entitlements & Gating (Global)
**Goal:** Enforce plan capabilities across app (UI‑only).

### Prompt Block
Create `EntitlementsGate.tsx` (in `components/billing/`):
- Props: `{ require: string; limitKey?: string; children }` — if not entitled, render **UpsellInline** with plan suggestion and **Upgrade** CTA → `PlanMatrix`.
- Provide a tiny `useEntitlements()` hook to read current entitlements from local Billing state.

Sweep across modules and wrap premium surfaces:
- **Knowledge**: TrainingCenter, Versions, RedactionRules.
- **Automations**: Simulator, SLA editor.
- **Analytics**: Cohorts, Attribution, Saved Reports.
- **Channels**: Short link & Verification logs.
- **Agents**: Allowlist editor.

### Deliverables
- `EntitlementsGate.tsx` + usage in premium surfaces

### Acceptance Checks
- When on Free/Starter, premium sections show UpsellInline; after upgrade, sections render.

---

## STEP 13 — Cross‑App Upsells
**Goal:** Contextual upgrades from blocked actions.

### Prompt Block
Create `UpsellInline.tsx` (reusable) and add CTAs:
- From **Dashboard** when clicking a premium KPI → open `PlanMatrix` pre‑selected to the lowest plan that unlocks it.
- From **Knowledge FailureLog** when clicking **Attach to source** (if gate) → upsell.
- From **Automations RuleBuilder** when selecting **Deflect** (if gate) → upsell.

### Deliverables
- `UpsellInline.tsx` + hooks

### Acceptance Checks
- CTAs navigate with the correct plan highlighted.

---

## STEP 14 — Price Localization & Currency
**Goal:** Switch currency for display.

### Prompt Block
Create `CurrencySelector.tsx` (in Settings or Billing header):
- Choose currency (USD/EUR/GBP/EGP/AED/SAR).
- Prices across PlanMatrix/Checkout reflect selected currency using a simple conversion table (UI only).

### Deliverables
- `CurrencySelector.tsx` + wiring into PlanMatrix/Checkout

### Acceptance Checks
- Changing currency updates visible prices consistently.

---

## STEP 15 — Offline & Sync Stubs
**Goal:** Prepare edits for offline‑first.

### Prompt Block
- Reuse **OfflineBanner** on Billing screens.
- Queue local mutations: subscribe/change plan, add/remove card, apply coupon, save tax profile; show small clock near changed row; clear after timeout.
- Add **Sync Center** button in Billing header.

### Deliverables
- Queued visuals; header button

### Acceptance Checks
- Offline shows banner; queued icons appear then clear.

---

## STEP 16 — Accessibility & Performance
**Goal:** WCAG basics and smooth lists.

### Prompt Block
- Add `accessibilityLabel` to all controls; ensure ≥44dp touch; verify contrast for price/usage bars.
- Virtualize invoices list; memoize `PlanCard`, `UsageBar`.

### Deliverables
- A11y & perf tweaks

### Acceptance Checks
- Lists scroll smoothly; screen readers announce plan/price, usage %, and invoice details.

---

## STEP 17 — Analytics Stubs
**Goal:** Instrument billing funnel and events.

### Prompt Block
Using `lib/analytics.ts` instrument:
- `track('billing.view')`, `track('billing.plan_matrix.view')`, `track('billing.checkout.view')`
- `track('billing.checkout.apply_coupon', { code })`
- `track('billing.subscribe', { planId, interval, seats })`
- `track('billing.change_plan', { from, to })`
- `track('billing.cancel_toggle', { value })`, `track('billing.pause_toggle', { value })`
- `track('billing.card.add')`, `track('billing.card.default')`, `track('billing.card.remove')`
- `track('billing.invoice.open')`, `track('billing.invoices.download_all')`
- `track('billing.tax.save')`, `track('billing.currency.change', { currency })`
- `track('billing.dunning.retry', { success })`
- `track('upsell.view')`, `track('upsell.cta', { from, planId })`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events during interactions in dev.

---

## STEP 18 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow for Billing & Plans.

### Prompt Block
- Verify: Home summaries, Plan matrix & checkout, Manage plan, Payment methods, Invoices, Taxes, Coupons, Usage & Limits, Dunning, Entitlements gating & upsells, Currency selector, Offline/queue visuals, A11y/Perf, Analytics.
- Create `mobile/docs/KNOWN_GAPS_Billing.md` listing deferred items (real processor integration, proration math, strong customer auth/3DS, tax engine, invoice PDF generation, seat provisioning, self‑serve refunds, overage billing, revenue recognition, multi‑entity billing, promo/credits wallet).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.

---

## Paste‑Ready Micro Prompts
- "Define billing types (prices, plans, subscription, invoices, usage, coupons, tax, dunning)."
- "Build PlanCard, PriceToggle, FeatureList, UsageBar, PaymentMethodRow, InvoiceRow, CouponInput, TaxForm, DunningBanner primitives."
- "Create BillingHome and register in navigator; show plan, usage, payment, invoices, dunning."
- "Create PlanMatrix + ComparePlansModal and CheckoutScreen with coupon and tax preview."
- "Create ManagePlanScreen for upgrade/downgrade/cancel/pause with proration note."
- "Create PaymentMethodsScreen with add/remove/default and wallet toggles."
- "Create InvoicesScreen and InvoiceDetail with PDF stub."
- "Create TaxBillingProfile for VAT/tax info and invoice fields."
- "Create CouponsScreen to apply/remove promo codes."
- "Create UsageLimitsScreen with soft/hard caps, auto‑upgrade toggle."
- "Create DunningScreen with retry timeline and update‑card flow."
- "Implement EntitlementsGate and apply to premium surfaces across modules; add UpsellInline CTAs."
- "Add CurrencySelector and update PlanMatrix/Checkout prices accordingly."
- "Add OfflineBanner reuse and queue edits; add Sync Center in header."
- "Add accessibility labels, ≥44dp touch, memoized lists/cards."
- "Instrument billing funnel analytics; create KNOWN_GAPS_Billing.md."

---

## Done‑Definition (Billing & Plans v0)
- BillingHome reflects demo subscription, usage meters, invoices, and payment method; dunning banner shows when applicable.
- PlanMatrix/Checkout enable upgrade with interval/currency, coupon, and trial; ManagePlan supports cancel/pause.
- Payment methods and invoices screens work; Taxes and Coupons screens affect totals; Usage/Limits and Dunning flows are visible.
- Entitlements gate premium features with clean upsells; currency switch works; offline visuals and analytics stubs are present.

