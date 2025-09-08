import Header from "@/components/Header";
import Footer from "@/components/Footer";
import MobileAppPromo from "@/components/MobileAppPromo";
import { useI18n } from "@/i18n/I18nProvider";

type IntegrationItem = { name: string; slug: string };

const Integrations = () => {
  const { t } = useI18n();
  const lastUpdated = new Date().toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  const items = t<IntegrationItem[]>("integrations.items");

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
              <span className="text-gradient-hero">{t("integrations.title")}</span>
            </h1>
            <p className="text-muted-foreground text-lg">
              {t("integrations.subtitle")} · {lastUpdated}
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
              <Section id="catalog" title={t("integrations.page.catalog.title")}>
                {(t<string[]>("integrations.page.catalog.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 mt-4">
                  {items.map((it) => (
                    <div key={it.slug} className="group rounded-xl border border-border bg-card hover:bg-accent/40 transition-colors p-4 flex items-center justify-center h-20">
                      <img
                        src={`https://cdn.simpleicons.org/${it.slug}/9CA3AF`}
                        alt={`${it.name} logo`}
                        className="h-9 w-auto opacity-90 group-hover:opacity-100 transition-opacity"
                        loading="lazy"
                        decoding="async"
                      />
                    </div>
                  ))}
                </div>
              </Section>

              <Section id="embed" title={t("integrations.page.embed.title")}>
                {(t<string[]>("integrations.page.embed.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
                <div className="rounded-xl border border-border bg-card p-4 md:p-5">
                  <div className="text-sm text-muted-foreground mb-2">{t("integrations.page.embed.exampleLabel")}</div>
                  <div className="flex items-center gap-2 text-sm">
                    <span className="inline-flex items-center px-2 py-1 rounded-md bg-primary/10 text-primary border border-primary/30">https://yourdomain.com/chat/your-agent</span>
                    <span className="text-muted-foreground">{t("integrations.page.embed.exampleHint")}</span>
                  </div>
                </div>
              </Section>

              <Section id="howto" title={t("integrations.page.howto.title")}>
                <ol className="list-decimal ps-5 space-y-2">
                  {(t<string[]>("integrations.page.howto.bullets") || []).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ol>
              </Section>

              <Section id="webhooks" title={t("integrations.page.webhooks.title")}>
                {(t<string[]>("integrations.page.webhooks.paragraphs") || []).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Section>

              <Section id="support" title={t("integrations.page.support.title")}>
                {(t<string[]>("integrations.page.support.paragraphs") || []).map((p, i) => (
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
                      ["catalog", t("integrations.page.catalog.title")],
                      ["embed", t("integrations.page.embed.title")],
                      ["howto", t("integrations.page.howto.title")],
                      ["webhooks", t("integrations.page.webhooks.title")],
                      ["support", t("integrations.page.support.title")],
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

export default Integrations;


