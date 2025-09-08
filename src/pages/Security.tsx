import Header from "@/components/Header";
import Footer from "@/components/Footer";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useI18n } from "@/i18n/I18nProvider";

const Security = () => {
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
              <span className="text-gradient-hero">{t("legal.security.title")}</span>
            </h1>
            <p className="text-muted-foreground text-lg">{t("legal.security.lastUpdatedLabel")}: {lastUpdated}</p>
          </div>
        </div>
      </div>

      {/* Content */}
      <main>
        <div className="container mx-auto px-6 pb-10 md:pb-16">
          <div className="grid lg:grid-cols-12 gap-8">
            {/* Content */}
            <div className="lg:col-span-8 space-y-6 md:space-y-8">
              <Section id="overview" title={t("legal.security.sections.overview.title")}>
                {(t<string[]>("legal.security.sections.overview.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="encryption" title={t("legal.security.sections.encryption.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.security.sections.encryption.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="access" title={t("legal.security.sections.access.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.security.sections.access.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="data" title={t("legal.security.sections.data.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.security.sections.data.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="network" title={t("legal.security.sections.network.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.security.sections.network.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="appsec" title={t("legal.security.sections.appsec.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.security.sections.appsec.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="incident" title={t("legal.security.sections.incident.title")}>
                {(t<string[]>("legal.security.sections.incident.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="compliance" title={t("legal.security.sections.compliance.title")}>
                {(t<string[]>("legal.security.sections.compliance.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="customer" title={t("legal.security.sections.customer.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.security.sections.customer.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="contact" title={t("legal.security.sections.contact.title")}>
                {(t<string[]>("legal.security.sections.contact.paragraphs") || []).map((p, i) => (
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
                      ["overview", t("legal.security.sections.overview.title")],
                      ["encryption", t("legal.security.sections.encryption.title")],
                      ["access", t("legal.security.sections.access.title")],
                      ["data", t("legal.security.sections.data.title")],
                      ["network", t("legal.security.sections.network.title")],
                      ["appsec", t("legal.security.sections.appsec.title")],
                      ["incident", t("legal.security.sections.incident.title")],
                      ["compliance", t("legal.security.sections.compliance.title")],
                      ["customer", t("legal.security.sections.customer.title")],
                      ["contact", t("legal.security.sections.contact.title")],
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

export default Security;


