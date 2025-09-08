import Header from "@/components/Header";
import Footer from "@/components/Footer";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useI18n } from "@/i18n/I18nProvider";

const Privacy = () => {
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
              <span className="text-gradient-hero">{t("legal.privacy.title")}</span>
            </h1>
            <p className="text-muted-foreground text-lg">
              {t("legal.privacy.lastUpdatedLabel")}:
              {" "}{lastUpdated}
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
              <Section id="overview" title={t("legal.privacy.sections.overview.title")}>
                {(t<string[]>("legal.privacy.sections.overview.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="collection" title={t("legal.privacy.sections.collection.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.privacy.sections.collection.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="use" title={t("legal.privacy.sections.use.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.privacy.sections.use.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="ai" title={t("legal.privacy.sections.ai.title")}>
                {(t<string[]>("legal.privacy.sections.ai.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="sharing" title={t("legal.privacy.sections.sharing.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.privacy.sections.sharing.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="intl" title={t("legal.privacy.sections.intl.title")}>
                {(t<string[]>("legal.privacy.sections.intl.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="retention" title={t("legal.privacy.sections.retention.title")}>
                {(t<string[]>("legal.privacy.sections.retention.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="rights" title={t("legal.privacy.sections.rights.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.privacy.sections.rights.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
                {(t<string[]>("legal.privacy.sections.rights.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="security" title={t("legal.privacy.sections.security.title")}>
                {(t<string[]>("legal.privacy.sections.security.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="children" title={t("legal.privacy.sections.children.title")}>
                {(t<string[]>("legal.privacy.sections.children.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="changes" title={t("legal.privacy.sections.changes.title")}>
                {(t<string[]>("legal.privacy.sections.changes.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="contact" title={t("legal.privacy.sections.contact.title")}>
                {(t<string[]>("legal.privacy.sections.contact.paragraphs") || []).map((p, i) => (
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
                      ["overview", t("legal.privacy.sections.overview.title")],
                      ["collection", t("legal.privacy.sections.collection.title")],
                      ["use", t("legal.privacy.sections.use.title")],
                      ["ai", t("legal.privacy.sections.ai.title")],
                      ["sharing", t("legal.privacy.sections.sharing.title")],
                      ["intl", t("legal.privacy.sections.intl.title")],
                      ["retention", t("legal.privacy.sections.retention.title")],
                      ["rights", t("legal.privacy.sections.rights.title")],
                      ["security", t("legal.privacy.sections.security.title")],
                      ["children", t("legal.privacy.sections.children.title")],
                      ["changes", t("legal.privacy.sections.changes.title")],
                      ["contact", t("legal.privacy.sections.contact.title")],
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

export default Privacy;


