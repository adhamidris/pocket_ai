import { useI18n } from "@/i18n/I18nProvider";

const brands: { name: string; slug: string }[] = [
  { name: "Google", slug: "google" },
  { name: "Apple", slug: "apple" },
  { name: "Stripe", slug: "stripe" },
  { name: "Shopify", slug: "shopify" },
  { name: "Netflix", slug: "netflix" },
  { name: "Uber", slug: "uber" },
  { name: "Airbnb", slug: "airbnb" },
  { name: "Slack", slug: "slack" },
  { name: "Spotify", slug: "spotify" },
  { name: "Meta", slug: "meta" },
  { name: "PayPal", slug: "paypal" },
  { name: "Samsung", slug: "samsung" },
  { name: "TikTok", slug: "tiktok" },
];

const TrustedBy = () => {
  const { t } = useI18n();
  return (
    <section className="bg-background py-8 md:py-12">
      <div className="container mx-auto px-6">
        <div className="text-center text-3xl md:text-4xl font-semibold mb-10 md:mb-20 tracking-tight">
          <span className="text-gradient-hero">{t("trustedBy.title")}</span>
        </div>
        <div className="relative">
          <div className="marquee overflow-hidden">
            <div className="marquee-track will-change-transform">
              {[...brands, ...brands].map((b, i) => (
                <div
                  key={`${b.slug}-${i}`}
                  className="mx-12 h-14 flex items-center"
                >
                  <img
                    src={`https://cdn.simpleicons.org/${b.slug}/9CA3AF`}
                    alt={`${b.name} logo`}
                    className="h-9 md:h-12 w-auto opacity-90"
                    loading="lazy"
                    decoding="async"
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default TrustedBy;

