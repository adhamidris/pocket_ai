import { useI18n } from "@/i18n/I18nProvider";
import { ShieldCheck, KeyRound, FileSearch, Database } from "lucide-react";

const SecurityStrip = () => {
  const { t } = useI18n();
  const bullets = t<string[]>("security.bullets");
  const icons = [KeyRound, ShieldCheck, FileSearch, Database];
  return (
    <section id="security" className="bg-background pt-10 md:pt-12 pb-14 md:pb-16">
      <div className="container mx-auto px-6">
        <div className="bg-card border border-border rounded-2xl p-5 md:p-6 shadow-soft">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div className="text-2xl font-semibold text-card-foreground">
              {t("security.title")}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
              {bullets.map((b, i) => {
                const Icon = icons[i] ?? ShieldCheck;
                return (
                  <div key={b} className="inline-flex items-center gap-2 rounded-xl border border-border bg-background/40 px-3 py-2">
                    <span className="w-7 h-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                      <Icon className="w-4 h-4" />
                    </span>
                    <span className="text-sm text-muted-foreground">{b}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default SecurityStrip;

