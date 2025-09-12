import { useI18n } from "@/i18n/I18nProvider";

const Integrations = () => {
  const { t } = useI18n();
  const items = t<{ name: string; slug: string }[]>("integrations.items");
  return (
    <section id="integrations" className="bg-background pt-16 md:pt-20 pb-20 md:pb-24">
      <div className="container mx-auto px-6">
        <div className="text-center mb-10 md:mb-14">
          <h2 className="text-3xl md:text-4xl font-semibold text-foreground mb-2">
            {t("integrations.title")}
          </h2>
          <p className="text-muted-foreground">
            {t("integrations.subtitle")}
          </p>
        </div>
        <div className="bg-card border border-border rounded-2xl p-6 lg:p-8 shadow-premium">
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-6 md:gap-8">
            {items.map((b, i) => (
              <div key={`${b.slug}-${i}`} className="flex items-center justify-center h-16">
                <img
                  src={`https://cdn.simpleicons.org/${b.slug}/9CA3AF`}
                  alt={`${b.name} logo`}
                  className="h-10 w-auto opacity-90"
                  loading="lazy"
                  decoding="async"
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default Integrations;

