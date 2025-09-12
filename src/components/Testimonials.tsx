import { Star, Sparkles } from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";

const testimonialsAvatars = [
  "https://i.pravatar.cc/100?img=12",
  "https://i.pravatar.cc/100?img=5",
  "https://i.pravatar.cc/100?img=32",
  "https://i.pravatar.cc/100?img=20",
  "https://i.pravatar.cc/100?img=48",
  "https://i.pravatar.cc/100?img=9",
];

type TItem = { quote: string; author: string; role: string; };
const Card = ({ t }: { t: TItem }) => (
  <div className="bg-card rounded-2xl shadow-premium border border-border p-8 min-w-[320px] md:min-w-[380px]">
    <div className="flex items-center gap-4 mb-6">
      <img src={"https://i.pravatar.cc/100?u=" + encodeURIComponent(t.author)} alt={t.author} className="w-12 h-12 rounded-full object-cover" />
      <div>
        <div className="font-semibold text-card-foreground">{t.author}</div>
        <div className="text-sm text-muted-foreground">{t.role}</div>
      </div>
    </div>
    <div className="text-muted-foreground leading-relaxed">
      “{t.quote}”
    </div>
    <div className="mt-6 flex items-center gap-1 text-primary">
      {Array.from({ length: 5 }).map((_, idx) => (
        <Star key={idx} className="w-4 h-4 fill-current" />
      ))}
    </div>
  </div>
);

const Testimonials = () => {
  const { t } = useI18n();
  const testimonials = t<TItem[]>("testimonials.items");
  return (
    <section className="pt-10 md:pt-20 pb-24 md:pb-28 bg-background">
      <div className="container mx-auto px-6">
        <div className="text-center mb-12 md:mb-20 animate-fade-in">
          <h2 className="text-4xl lg:text-5xl font-bold tracking-tight leading-tight">
            {t("testimonials.titlePrefix")} {" "}
            <span className="text-gradient-hero">{t("testimonials.titleHighlight")}</span>
          </h2>
          <p className="text-muted-foreground mt-5 max-w-2xl mx-auto">
            {t("testimonials.subtitle")}
          </p>
        </div>

        <div className="relative">
          <div className="marquee overflow-hidden">
            <div className="marquee-track will-change-transform">
              {[...testimonials, ...testimonials].map((t, i) => (
                <Card key={`${t.author}-${i}`} t={t} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default Testimonials;
