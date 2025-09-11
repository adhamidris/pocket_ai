# KNOWN GAPS — Billing & Plans (Mobile Demo)

This mobile implementation is UI-first for demo purposes. The following items are intentionally deferred or stubbed:

- Real payment processor integration (Stripe/Adyen/Braintree), PCI, tokenization
- Proration math for mid-cycle plan changes and credits application
- Strong Customer Authentication (3DS/SCA) flows and retries
- Tax engine (VAT/GST/Sales tax), multi-region rules, exemptions, evidence
- Invoice PDF generation, storage, and email delivery
- Seat provisioning/licensing sync with backend (user limits, role mapping)
- Self-serve refunds, credits, and adjustments workflow
- Overage billing (metered per-message/MTU) with thresholds and charges
- Revenue recognition, invoicing schedules, and accounting exports
- Multi-entity/brand billing, consolidated invoicing, cost centers
- Promotions wallet (stackable coupons/credits) and redemption history
- Payment method vaulting, card verification, network tokenization
- Webhooks for payment events (invoice.paid, charge.failed) and dunning automation
- Customer portal deep links and authenticated session management
- Full localization of currency formats and rounding rules per locale

These are candidates for a backend integration phase.

