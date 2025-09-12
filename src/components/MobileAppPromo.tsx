import { useI18n } from "@/i18n/I18nProvider";
import { Smartphone } from "lucide-react";

const MobileAppPromo: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const { t } = useI18n();

  return (
    <section className={`bg-background ${compact ? "pt-6 md:pt-8 pb-8 md:pb-10" : "pt-10 md:pt-12 pb-14 md:pb-16"}`}>
      <div className="container mx-auto px-6">
        <div className={`relative overflow-hidden rounded-2xl ${compact ? "p-4 md:p-5" : "p-6 md:p-8"} shadow-premium bg-gradient-hero text-white`}>
          {/* subtle animated glow accents */}
          <div className={`pointer-events-none absolute -top-10 -left-10 ${compact ? "w-48 h-48" : "w-64 h-64"} rounded-full bg-white/10 blur-3xl animate-pulse`} aria-hidden />
          <div className={`pointer-events-none absolute -bottom-12 -right-12 ${compact ? "w-56 h-56" : "w-72 h-72"} rounded-full bg-white/10 blur-3xl animate-pulse`} style={{ animationDelay: '1.2s' }} aria-hidden />

          <div className="relative flex flex-col md:flex-row items-center justify-between gap-6">
            <h3 className={`${compact ? "text-xl md:text-2xl" : "text-2xl md:text-3xl"} font-semibold text-center md:text-start inline-flex items-center gap-2`}>
              <Smartphone className="w-6 h-6 text-white" aria-hidden />
              <span>{t("mobileApp.title")}</span>
            </h3>
            <div className="flex gap-4 justify-center md:justify-end">
              <a href="#" className="group inline-flex items-center gap-3.5 rounded-xl border border-white/20 bg-white/10 px-6 py-3.5 shadow-sm hover:bg-white/15 transition-all duration-200 hover:translate-y-[-1px]" aria-label={t("hero.stats.googlePlay")}>
                <svg width="30" height="30" viewBox="0 0 512 512" aria-hidden="true">
                  <path fill="currentColor" d="M325.3 234.3 90.7 28.6C79 19 64 24.7 64 39.3v433.4c0 14.7 15 20.3 26.7 10.7l234.6-205.7c9.3-8.1 9.3-23.1 0-30.4z"/>
                  <linearGradient id="g2-app" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#34a853"/>
                    <stop offset="100%" stopColor="#4285f4"/>
                  </linearGradient>
                  <path fill="url(#g2-app)" d="M421.9 213.8 360.4 178 325.3 234.3c9.3 8.1 9.3 23.1 0 30.4l35.1 56.3 61.5-35.8c18.5-10.8 18.5-38.5 0-49.4z"/>
                </svg>
                <div className="leading-tight">
                  <div className="text-[17px] font-semibold text-white">{t("hero.stats.googlePlay")}</div>
                </div>
              </a>
              <a href="#" className="group inline-flex items-center gap-3.5 rounded-xl border border-white/20 bg-white/10 px-6 py-3.5 shadow-sm hover:bg-white/15 transition-all duration-200 hover:translate-y-[-1px]" aria-label={t("hero.stats.appStore")}>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M16.365 1.43c0 1.14-.47 2.25-1.2 3.05-.76.83-2.01 1.47-3.12 1.39-.13-1.13.38-2.27 1.14-3.06.79-.85 2.12-1.46 3.18-1.38zm3.54 16.3c-.61 1.36-.9 1.95-1.68 3.15-1.09 1.67-2.63 3.75-4.53 3.75-1.7 0-2.14-1.1-4.46-1.1-2.34 0-2.83 1.1-4.53 1.1-1.92 0-3.36-1.8-4.45-3.46C-.02 17.9-.4 14.49 1.3 12.23c1.1-1.54 2.86-2.51 4.85-2.55 1.9-.04 3.69 1.28 4.46 1.28.77 0 2.54-1.58 4.3-1.35 1.47.17 2.85.76 3.88 1.73-3.52 1.93-2.95 6.97.1 7.49z"/>
                </svg>
                <div className="leading-tight">
                  <div className="text-[17px] font-semibold text-white">{t("hero.stats.appStore")}</div>
                </div>
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default MobileAppPromo;
