import Header from "@/components/Header";
import Footer from "@/components/Footer";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useI18n } from "@/i18n/I18nProvider";

const Terms = () => {
  const { t } = useI18n();
  const lastUpdated = new Date().toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  const Section = ({ id, title, children }: { id: string; title: string; children: React.ReactNode }) => (
    <section id={id} className="policy-section bg-card border border-border rounded-2xl shadow-premium p-6 md:p-8 animate-fade-in scroll-mt-28 md:scroll-mt-32">
      <h2 className="text-2xl md:text-3xl font-semibold text-card-foreground mb-4">
        {title}
      </h2>
      <div className="text-muted-foreground leading-relaxed space-y-4">
        {children}
      </div>
    </section>
  );

  return (
    <div className="min-h-screen bg-background">
      <Header />

      {/* Hero / Title */}
      <div className="relative">
        <div className="container mx-auto px-6 pt-20 md:pt-24 pb-10 md:pb-12 relative z-10">
          <div className="max-w-3xl">
            <h1 className="text-4xl md:text-6xl font-bold leading-tight text-foreground mb-4">
              <span className="text-gradient-hero">{t("legal.terms.title")}</span>
            </h1>
            <p className="text-muted-foreground text-lg">
              {t("legal.terms.lastUpdatedLabel")}: {lastUpdated}
            </p>
          </div>
        </div>
      </div>

      {/* Content */}
      <main>
        <div className="container mx-auto px-6 pb-10 md:pb-16">
          <div className="grid lg:grid-cols-12 gap-8">
            {/* Content */}
            <div className="lg:col-span-8 space-y-6 md:space-y-8">
              <Section id="intro" title={t("legal.terms.sections.intro.title")}>
                {(t<string[]>("legal.terms.sections.intro.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="eligibility" title={t("legal.terms.sections.eligibility.title")}>
                {(t<string[]>("legal.terms.sections.eligibility.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="account" title={t("legal.terms.sections.account.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.terms.sections.account.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="use" title={t("legal.terms.sections.use.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.terms.sections.use.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="customer-data" title={t("legal.terms.sections.customerData.title")}>
                {(t<string[]>("legal.terms.sections.customerData.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="ai" title={t("legal.terms.sections.ai.title")}>
                {(t<string[]>("legal.terms.sections.ai.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="ip" title={t("legal.terms.sections.ip.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.terms.sections.ip.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="billing" title={t("legal.terms.sections.billing.title")}>
                {(t<string[]>("legal.terms.sections.billing.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="integrations" title={t("legal.terms.sections.integrations.title")}>
                {(t<string[]>("legal.terms.sections.integrations.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="security" title={t("legal.terms.sections.security.title")}>
                {(t<string[]>("legal.terms.sections.security.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="term" title={t("legal.terms.sections.term.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.terms.sections.term.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="warranty" title={t("legal.terms.sections.warranty.title")}>
                {(t<string[]>("legal.terms.sections.warranty.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="liability" title={t("legal.terms.sections.liability.title")}>
                {(t<string[]>("legal.terms.sections.liability.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="indemnity" title={t("legal.terms.sections.indemnity.title")}>
                {(t<string[]>("legal.terms.sections.indemnity.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="governing-law" title={t("legal.terms.sections.governingLaw.title")}>
                {(t<string[]>("legal.terms.sections.governingLaw.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="changes" title={t("legal.terms.sections.changes.title")}>
                {(t<string[]>("legal.terms.sections.changes.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="contact" title={t("legal.terms.sections.contact.title")}>
                {(t<string[]>("legal.terms.sections.contact.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>
            </div>

            {/* Sticky TOC (desktop) */}
            <aside className="hidden lg:block lg:col-span-4">
              <div className="sticky top-28 space-y-4">
                <div className="bg-card border border-border rounded-2xl shadow-premium p-5">
                  <div className="text-sm uppercase tracking-wider text-muted-foreground mb-3">{t("legal.onThisPage")}</div>
                  <ul className="space-y-2 text-sm">
                    {[
                      ["intro", t("legal.terms.sections.intro.title")],
                      ["eligibility", t("legal.terms.sections.eligibility.title")],
                      ["account", t("legal.terms.sections.account.title")],
                      ["use", t("legal.terms.sections.use.title")],
                      ["customer-data", t("legal.terms.sections.customerData.title")],
                      ["ai", t("legal.terms.sections.ai.title")],
                      ["ip", t("legal.terms.sections.ip.title")],
                      ["billing", t("legal.terms.sections.billing.title")],
                      ["integrations", t("legal.terms.sections.integrations.title")],
                      ["security", t("legal.terms.sections.security.title")],
                      ["term", t("legal.terms.sections.term.title")],
                      ["warranty", t("legal.terms.sections.warranty.title")],
                      ["liability", t("legal.terms.sections.liability.title")],
                      ["indemnity", t("legal.terms.sections.indemnity.title")],
                      ["governing-law", t("legal.terms.sections.governingLaw.title")],
                      ["changes", t("legal.terms.sections.changes.title")],
                      ["contact", t("legal.terms.sections.contact.title")],
                    ].map(([id, label]) => (
                      <li key={id as string}>
                        <a
                          className="text-muted-foreground hover:text-foreground transition-colors"
                          href={`#${id}`}
                        >
                          {label as string}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </aside>
          </div>
        </div>

        {/* Mobile App Promo */}
        <MobileAppPromo />
      </main>

      <Footer />
    </div>
  );
};

export default Terms;


