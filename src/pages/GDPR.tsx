import Header from "@/components/Header";
import Footer from "@/components/Footer";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useI18n } from "@/i18n/I18nProvider";

const GDPR = () => {
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
              <span className="text-gradient-hero">{t("legal.gdpr.title")}</span>
            </h1>
            <p className="text-muted-foreground text-lg">
              {t("legal.gdpr.lastUpdatedLabel")}: {lastUpdated}
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
              <Section id="intro" title={t("legal.gdpr.sections.intro.title")}>
                {(t<string[]>("legal.gdpr.sections.intro.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="roles" title={t("legal.gdpr.sections.roles.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.gdpr.sections.roles.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="data" title={t("legal.gdpr.sections.data.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.gdpr.sections.data.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="lawful" title={t("legal.gdpr.sections.lawful.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.gdpr.sections.lawful.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="purposes" title={t("legal.gdpr.sections.purposes.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.gdpr.sections.purposes.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="rights" title={t("legal.gdpr.sections.rights.title")}>
                {(t<string[]>("legal.gdpr.sections.rights.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="transfers" title={t("legal.gdpr.sections.transfers.title")}>
                {(t<string[]>("legal.gdpr.sections.transfers.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="retention" title={t("legal.gdpr.sections.retention.title")}>
                {(t<string[]>("legal.gdpr.sections.retention.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="security" title={t("legal.gdpr.sections.security.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.gdpr.sections.security.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="subprocessors" title={t("legal.gdpr.sections.subprocessors.title")}>
                {(t<string[]>("legal.gdpr.sections.subprocessors.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="cookies" title={t("legal.gdpr.sections.cookies.title")}>
                {(t<string[]>("legal.gdpr.sections.cookies.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="dpo" title={t("legal.gdpr.sections.dpo.title")}>
                {(t<string[]>("legal.gdpr.sections.dpo.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="updates" title={t("legal.gdpr.sections.updates.title")}>
                {(t<string[]>("legal.gdpr.sections.updates.paragraphs") || []).map((p, i) => (
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
                      ["intro", t("legal.gdpr.sections.intro.title")],
                      ["roles", t("legal.gdpr.sections.roles.title")],
                      ["data", t("legal.gdpr.sections.data.title")],
                      ["lawful", t("legal.gdpr.sections.lawful.title")],
                      ["purposes", t("legal.gdpr.sections.purposes.title")],
                      ["rights", t("legal.gdpr.sections.rights.title")],
                      ["transfers", t("legal.gdpr.sections.transfers.title")],
                      ["retention", t("legal.gdpr.sections.retention.title")],
                      ["security", t("legal.gdpr.sections.security.title")],
                      ["subprocessors", t("legal.gdpr.sections.subprocessors.title")],
                      ["cookies", t("legal.gdpr.sections.cookies.title")],
                      ["dpo", t("legal.gdpr.sections.dpo.title")],
                      ["updates", t("legal.gdpr.sections.updates.title")],
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

export default GDPR;


