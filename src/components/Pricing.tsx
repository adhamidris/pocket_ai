import { CheckCircle2, Wallet, Building2, Users, Zap, Shield, Layers } from "lucide-react";
import { useMemo, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";

type BillingCycle = "monthly" | "yearly";
type SchemaKey = "wage" | "packages" | "self";

const Pricing = () => {
  const { t } = useI18n();
  const [activeSchema, setActiveSchema] = useState<SchemaKey>("packages"); // middle by default
  const [billingCycle, setBillingCycle] = useState<BillingCycle>("monthly");

  const schemaTabs: { key: SchemaKey; label: string }[] = [
    { key: "wage", label: t("pricing.tabs.wage") },
    { key: "packages", label: t("pricing.tabs.packages") },
    { key: "self", label: t("pricing.tabs.self") },
  ];

  const packages = useMemo(() => t<any>("pricing.packages"), [t]);

  const wagePlans = useMemo(() => t<any>("pricing.wagePlans"), [t]);

  // Wage plans now display as a grid like packages (no sub-toggle)

  return (
    <section id="pricing" className="bg-background pt-16 md:pt-20 pb-24 md:pb-28">
      <div className="container mx-auto px-6">
        <div className="text-center mb-12 md:mb-16">
          <div className="text-xs uppercase tracking-widest text-muted-foreground mb-3">{t("pricing.flexible")}</div>
          <h2 className="text-4xl lg:text-5xl font-bold text-foreground">{t("pricing.choose")}<span className="text-gradient-hero">{t("pricing.motion")}</span></h2>
        </div>

        <div className="flex justify-center">
          <div className="inline-flex gap-2 bg-card border border-border rounded-2xl p-1">
            {schemaTabs.map((s) => (
              <button
                key={s.key}
                onClick={() => setActiveSchema(s.key)}
                className={`px-4 py-2 rounded-xl text-sm transition-all border ${
                  activeSchema === s.key
                    ? "bg-primary/15 border-primary text-primary shadow-soft"
                    : "bg-transparent border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Subheader (no big header) */}
        <div className="text-center text-muted-foreground mt-6 mb-10 max-w-3xl mx-auto">
          {activeSchema === "packages" && (
            <p>{t("pricing.subheader.packages")}</p>
          )}
          {activeSchema === "wage" && (
            <p>{t("pricing.subheader.wage")}</p>
          )}
          {activeSchema === "self" && (
            <p>{t("pricing.subheader.self")}</p>
          )}
        </div>

        {activeSchema === "packages" && (
          <div key={`${activeSchema}-${billingCycle}`} className="animate-panel-in transition-all duration-500 ease-out">
            {/* Billing toggle */}
            <div className="flex justify-center mb-8">
              <div className="inline-flex items-center bg-card border border-border rounded-xl p-1">
                <button
                  onClick={() => setBillingCycle("monthly")}
                  className={`px-4 py-2 rounded-lg text-sm border transition-all ${
                    billingCycle === "monthly"
                      ? "bg-primary/15 border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t("pricing.billingCycle.monthly")}
                </button>
                <button
                  onClick={() => setBillingCycle("yearly")}
                  className={`px-4 py-2 rounded-lg text-sm border transition-all ${
                    billingCycle === "yearly"
                      ? "bg-primary/15 border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t("pricing.billingCycle.yearly")}
                </button>
              </div>
            </div>

            {/* Plan cards */}
            <div className="grid md:grid-cols-3 gap-6 animate-fade-in">
              {packages.map((plan) => {
                const price = billingCycle === "monthly" ? plan.monthly : plan.yearly;
                const suffix = billingCycle === "monthly" ? t("pricing.billingCycle.suffixMonthly") : t("pricing.billingCycle.suffixYearly");
                const isPro = plan.tier === "Pro";
                return (
                  <div
                    key={plan.tier}
                    className={`relative overflow-visible rounded-2xl shadow-premium p-6 lg:p-8 flex flex-col transition-all duration-300 hover:-translate-y-1 hover:shadow-soft bg-primary/10 border border-primary/30 text-center md:text-left ${isPro ? 'ring-1 ring-primary/25 hover:ring-primary/40' : ''}`}
                  >
                    {isPro && (
                      <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                        <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full bg-primary text-white shadow-md">
                          {plan.badge}
                        </span>
                      </div>
                    )}
                    <div className="flex flex-col md:flex-row items-center md:items-center justify-center md:justify-between gap-2 mb-3">
                      <div className="text-lg font-semibold text-card-foreground">{plan.tier}</div>
                      {plan.badge && !isPro && (
                        <span className="text-xs px-2 py-1 rounded-md bg-primary/15 text-primary border border-primary/40">{plan.badge}</span>
                      )}
                    </div>
                    <div className="mb-2 text-muted-foreground">{plan.description}</div>
                    <div className="mt-3 mb-4">
                      <span className="text-4xl lg:text-5xl font-bold text-foreground">${price}</span>
                      <span className="ms-2 text-sm text-muted-foreground">{suffix}</span>
                    </div>
                    <div className="mb-4">
                      <span className="inline-flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30">{t("pricing.billingCycle.trial")}</span>
                    </div>
                    <ul className="space-y-3 mb-6 text-sm text-muted-foreground">
                      {plan.features.map((f) => (
                        <li key={f} className="flex items-start gap-2">
                          <CheckCircle2 className="w-4 h-4 text-primary mt-0.5" />
                          <span>{f}</span>
                        </li>
                      ))}
                    </ul>
                    <button className="mt-auto inline-flex items-center justify-center rounded-xl border border-transparent bg-primary text-primary-foreground hover:opacity-90 transition-all px-4 py-2.5 hover:-translate-y-0.5">
                      {t("pricing.billingCycle.getStarted")}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {activeSchema === "wage" && (
          <div key={activeSchema} className="animate-panel-in transition-all duration-500 ease-out">
            <div className="grid md:grid-cols-3 gap-6 animate-fade-in">
              {wagePlans.map((p) => (
                <div key={p.id} className="relative overflow-visible rounded-2xl shadow-premium p-6 lg:p-8 flex flex-col transition-all duration-300 hover:-translate-y-1 hover:shadow-soft bg-primary/10 border border-primary/30 text-center md:text-left">
                  <div className="flex items-center justify-between mb-3">
                    <div className="text-lg font-semibold text-card-foreground">{p.label}</div>
                    <span className="text-xs px-2 py-1 rounded-md bg-primary/15 text-primary border border-primary/40">{t("pricing.billingCycle.flexible")}</span>
                  </div>
                  <div className="flex flex-col md:flex-row items-center gap-3 mb-2 text-card-foreground justify-center md:justify-start">
                    <Wallet className="w-5 h-5" />
                    <div className="font-semibold">{t("pricing.billingCycle.minCredit")}</div>
                  </div>
                  <div className="text-4xl font-bold text-foreground mb-4">${p.minCredit}</div>
                  <div className="text-muted-foreground mb-3">{t("pricing.billingCycle.activates")}</div>
                  <div className="mb-5">
                    <span className="inline-flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30">{t("pricing.billingCycle.trial")}</span>
                  </div>
                  <ul className="space-y-3 mb-6 text-sm text-muted-foreground">
                    {p.bullets.map((b) => (
                      <li key={b} className="flex items-start gap-2">
                        <CheckCircle2 className="w-4 h-4 text-primary mt-0.5" />
                        <span>{b}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-auto flex flex-wrap gap-3 mb-6">
                    <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30"><Users className="w-3.5 h-3.5"/> Agents</span>
                    <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30"><Layers className="w-3.5 h-3.5"/> CRM</span>
                    <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30"><Shield className="w-3.5 h-3.5"/> Security</span>
                    <span className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30"><Zap className="w-3.5 h-3.5"/> Workflows</span>
                  </div>
                  <button className="inline-flex items-center justify-center rounded-xl border border-transparent bg-primary text-primary-foreground hover:opacity-90 transition-all px-4 py-2.5">
                    {t("pricing.billingCycle.addCredit")}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeSchema === "self" && (
          <div key={activeSchema} className="animate-panel-in transition-all duration-500 ease-out">
            {/* Single option toggle for completeness */}
            <div className="flex justify-center mb-8">
              <div className="inline-flex gap-2 bg-card border border-border rounded-2xl p-1">
                <button className="px-4 py-2 rounded-xl text-sm border bg-primary/15 border-primary text-primary">Custom</button>
              </div>
            </div>

            <div className="max-w-3xl mx-auto rounded-2xl shadow-premium p-6 lg:p-8 transition-all duration-300 bg-primary/10 border border-primary/30">
              <div className="flex items-center gap-3 mb-2 text-card-foreground">
                <Building2 className="w-5 h-5" />
                <div className="font-semibold">{t("pricing.self.title")}</div>
              </div>
              <div className="text-muted-foreground mb-5">{t("pricing.subheader.self")}</div>
              <ul className="space-y-3 text-sm text-muted-foreground mb-6">
                {t<string[]>("pricing.self.bullets").map((b) => (
                  <li key={b} className="flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 text-primary mt-0.5" />
                    <span>{b}</span>
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-3">
                <button className="inline-flex items-center justify-center rounded-xl border border-transparent bg-primary text-primary-foreground hover:opacity-90 transition-all px-4 py-2.5 hover:-translate-y-0.5">
                  {t("pricing.self.ctaQuote")}
                </button>
                <button className="inline-flex items-center justify-center rounded-xl border border-primary/30 bg-primary/10 text-primary hover:bg-primary/15 transition-all px-4 py-2.5 hover:-translate-y-0.5">
                  {t("pricing.self.ctaSales")}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
};

export default Pricing;
