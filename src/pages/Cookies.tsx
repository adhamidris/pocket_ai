import Header from "@/components/Header";
import Footer from "@/components/Footer";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useI18n } from "@/i18n/I18nProvider";

const Cookies = () => {
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
              <span className="text-gradient-hero">{t("legal.cookies.title")}</span>
            </h1>
            <p className="text-muted-foreground text-lg">
              {t("legal.cookies.lastUpdatedLabel")}: {lastUpdated}
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
              <Section id="intro" title={t("legal.cookies.sections.intro.title")}>
                {(t<string[]>("legal.cookies.sections.intro.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="what-are-cookies" title={t("legal.cookies.sections.what.title")}>
                {(t<string[]>("legal.cookies.sections.what.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="types" title={t("legal.cookies.sections.types.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.cookies.sections.types.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="how-we-use" title={t("legal.cookies.sections.how.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.cookies.sections.how.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="third-party" title={t("legal.cookies.sections.thirdParty.title")}>
                {(t<string[]>("legal.cookies.sections.thirdParty.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="manage" title={t("legal.cookies.sections.manage.title")}>
                <ul className="list-disc ps-5 space-y-2">
                  {(t<string[]>("legal.cookies.sections.manage.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </Section>

              <Section id="consent" title={t("legal.cookies.sections.consent.title")}>
                {(t<string[]>("legal.cookies.sections.consent.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="changes" title={t("legal.cookies.sections.changes.title")}>
                {(t<string[]>("legal.cookies.sections.changes.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="contact" title={t("legal.cookies.sections.contact.title")}>
                {(t<string[]>("legal.cookies.sections.contact.paragraphs") || []).map((p, i) => (
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
                      ["intro", t("legal.cookies.sections.intro.title")],
                      ["what-are-cookies", t("legal.cookies.sections.what.title")],
                      ["types", t("legal.cookies.sections.types.title")],
                      ["how-we-use", t("legal.cookies.sections.how.title")],
                      ["third-party", t("legal.cookies.sections.thirdParty.title")],
                      ["manage", t("legal.cookies.sections.manage.title")],
                      ["consent", t("legal.cookies.sections.consent.title")],
                      ["changes", t("legal.cookies.sections.changes.title")],
                      ["contact", t("legal.cookies.sections.contact.title")],
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

export default Cookies;


