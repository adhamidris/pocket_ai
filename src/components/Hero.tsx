import { Button } from "@/components/ui/button";
import { ArrowRight, Play } from "lucide-react";
import DemoChatWidget from "./DemoChatWidget";
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";

function AnimatedCounter({
  target,
  durationMs = 1200,
  suffix = "",
  prefix = "",
  formatter,
  className = "",
}: {
  target: number;
  durationMs?: number;
  suffix?: string;
  prefix?: string;
  formatter?: (value: number) => string;
  className?: string;
}) {
  const [value, setValue] = useState(0);
  const [hasStarted, setHasStarted] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const stepCount = 60;
  const stepDuration = Math.max(16, Math.floor(durationMs / stepCount));
  const increment = useMemo(() => target / stepCount, [target]);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !hasStarted) {
            setHasStarted(true);
          }
        });
      },
      { threshold: 0.3 }
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasStarted]);

  useEffect(() => {
    if (!hasStarted) return;
    let current = 0;
    const interval = setInterval(() => {
      current += increment;
      if (current >= target) {
        current = target;
        clearInterval(interval);
      }
      setValue(current);
    }, stepDuration);
    return () => clearInterval(interval);
  }, [hasStarted, increment, stepDuration, target]);

  const display = formatter ? formatter(value) : Math.round(value).toLocaleString();

  return (
    <div ref={ref} className={className}>
      {prefix}
      {display}
      {suffix}
    </div>
  );
}

const Hero = () => {
  const { t, dir } = useI18n();
  return (
    <section className="relative min-h-[80vh] md:min-h-[85vh] flex items-center justify-center overflow-hidden bg-background">
      
      {/* Animated background elements */}
      <div className="absolute inset-0 opacity-20">
        <div className="absolute top-20 left-10 w-72 h-72 bg-primary/20 rounded-full blur-3xl animate-none md:animate-pulse" />
        <div className="absolute bottom-20 right-10 w-96 h-96 bg-primary-light/15 rounded-full blur-3xl animate-none md:animate-pulse" style={{ animationDelay: '1s' }} />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-gradient-hero opacity-10 rounded-full blur-3xl animate-none md:animate-pulse" style={{ animationDelay: '2s' }} />
      </div>

      <div className="container mx-auto px-6 pt-24 pb-12 md:pt-28 md:pb-16 relative z-10">
        <div className="grid lg:grid-cols-2 gap-12 items-center">
          {/* Left Content */}
          <div className="text-center lg:text-start animate-fade-in">
            
            <h1 className="text-5xl lg:text-7xl font-bold text-foreground leading-tight mb-6">
              {t("hero.titlePrefix")} {" "}
              <span className="text-gradient-hero">{t("hero.titleHighlight")}</span>
            </h1>
            
            <p className="text-xl text-neutral-500 mb-8 max-w-lg mx-auto lg:mx-0 leading-relaxed">
              {t("hero.subtitle")}
            </p>
            
            <div className="flex flex-col sm:flex-row gap-4 justify-center lg:justify-start">
              <Button 
                size="lg" 
                className="bg-gradient-primary text-white hover:opacity-90 transition-all transform hover:scale-105 shadow-lg group"
              >
                {t("hero.ctaTrial")}
                <ArrowRight className={`ms-2 h-4 w-4 group-hover:translate-x-1 transition-transform ${dir === 'rtl' ? 'rtl-rotate-180' : ''}`} />
              </Button>
              
              <Button 
                size="lg" 
                variant="ghost" 
                className="border border-neutral-200 hover:border-primary hover:text-primary transition-all group"
              >
                <Play className="me-2 h-4 w-4" />
                {t("hero.ctaDemo")}
              </Button>
            </div>

            
            {/* Stats */}
            <div className="flex flex-col sm:flex-row gap-8 mt-8 pt-6 border-t border-neutral-200">
              <div className="text-center lg:text-start">
                <AnimatedCounter className="text-3xl font-bold text-foreground" target={90} suffix="%" formatter={(v) => Math.round(v).toString()} />
                <div className="text-neutral-500 text-sm">{t("hero.stats.fasterResponse")}</div>
              </div>
              <div className="text-center lg:text-start">
                <div className="text-3xl font-bold text-foreground flex items-baseline justify-center lg:justify-start gap-1">
                  <AnimatedCounter target={24} className="leading-none" formatter={(v) => Math.round(v).toString()} />
                  <span>/7</span>
                </div>
                <div className="text-neutral-500 text-sm">{t("hero.stats.aiSupport")}</div>
              </div>
              <div className="text-center lg:text-start">
                <AnimatedCounter
                  className="text-3xl font-bold text-foreground"
                  target={10000}
                  formatter={(v) => {
                    const n = Math.round(v);
                    return n >= 1000 ? `${Math.round(n / 1000)}k+` : `${n}`;
                  }}
                />
                <div className="text-neutral-500 text-sm">{t("hero.stats.happyCustomers")}</div>
              </div>
            </div>

            {/* Mobile apps under stats */}
            <div className="mt-6 text-center lg:text-start">
              <div className="flex gap-5 justify-center lg:justify-start">
                <a href="#" className="inline-flex items-center gap-3.5 rounded-xl border border-border bg-card px-6 py-3.5 shadow-sm hover:bg-accent/40 transition-colors" aria-label={t("hero.stats.googlePlay")}>
                  <svg width="30" height="30" viewBox="0 0 512 512" aria-hidden="true">
                    <path fill="currentColor" d="M325.3 234.3 90.7 28.6C79 19 64 24.7 64 39.3v433.4c0 14.7 15 20.3 26.7 10.7l234.6-205.7c9.3-8.1 9.3-23.1 0-30.4z"/>
                    <linearGradient id="g2" x1="0" y1="0" x2="1" y2="1">
                      <stop offset="0%" stop-color="#34a853"/>
                      <stop offset="100%" stop-color="#4285f4"/>
                    </linearGradient>
                    <path fill="url(#g2)" d="M421.9 213.8 360.4 178 325.3 234.3c9.3 8.1 9.3 23.1 0 30.4l35.1 56.3 61.5-35.8c18.5-10.8 18.5-38.5 0-49.4z"/>
                  </svg>
                  <div className="leading-tight">
                    <div className="text-[17px] font-semibold text-foreground">{t("hero.stats.googlePlay")}</div>
                  </div>
                </a>
                <a href="#" className="inline-flex items-center gap-3.5 rounded-xl border border-border bg-card px-6 py-3.5 shadow-sm hover:bg-accent/40 transition-colors" aria-label={t("hero.stats.appStore")}>
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M16.365 1.43c0 1.14-.47 2.25-1.2 3.05-.76.83-2.01 1.47-3.12 1.39-.13-1.13.38-2.27 1.14-3.06.79-.85 2.12-1.46 3.18-1.38zm3.54 16.3c-.61 1.36-.9 1.95-1.68 3.15-1.09 1.67-2.63 3.75-4.53 3.75-1.7 0-2.14-1.1-4.46-1.1-2.34 0-2.83 1.1-4.53 1.1-1.92 0-3.36-1.8-4.45-3.46C-.02 17.9-.4 14.49 1.3 12.23c1.1-1.54 2.86-2.51 4.85-2.55 1.9-.04 3.69 1.28 4.46 1.28.77 0 2.54-1.58 4.3-1.35 1.47.17 2.85.76 3.88 1.73-3.52 1.93-2.95 6.97.1 7.49z"/>
                  </svg>
                  <div className="leading-tight">
                    <div className="text-[17px] font-semibold text-foreground">{t("hero.stats.appStore")}</div>
                  </div>
                </a>
              </div>
            </div>
          </div>

          {/* Right Content - Interactive Demo */}
          <div className="relative animate-slide-up" style={{ animationDelay: '0.3s' }}>
            <div className="relative">
              {/* Browser mockup */}
              <div className="bg-card rounded-2xl shadow-premium p-6 border border-border">
                <div className="flex items-center gap-2 mb-4 pb-4 border-b border-border">
                  <div className="flex gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-400" />
                    <div className="w-3 h-3 rounded-full bg-yellow-400" />
                    <div className="w-3 h-3 rounded-full bg-green-400" />
                  </div>
                  <div className="flex-1 bg-input rounded-lg px-4 py-1 text-sm text-muted-foreground text-center">
                    {t("hero.stats.browserBar")}
                  </div>
                </div>
                
                
                {/* Live Demo Chat */}
                <div className="relative">
                  <DemoChatWidget />
                </div>
              </div>
              
              {/* Floating elements */}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default Hero;
