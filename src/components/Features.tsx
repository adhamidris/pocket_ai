import { Bot, Zap, Shield, BarChart3, MessageSquare, Clock, Users, Settings, LayoutDashboard, CreditCard, CheckCircle2, Link, Globe, BookOpen, Layers } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";

// Counts up from 0 to target when the component becomes visible in viewport
function AnimatedCounter({
  target,
  durationMs = 1200,
  suffix = "",
  prefix = "",
  formatter,
}: {
  target: number;
  durationMs?: number;
  suffix?: string;
  prefix?: string;
  formatter?: (value: number) => string;
}) {
  const [value, setValue] = useState(0);
  const [hasStarted, setHasStarted] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const stepCount = 60; // frames for the animation
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

  const display = formatter
    ? formatter(value)
    : Math.round(value).toLocaleString();

  return (
    <div ref={ref} className="text-4xl font-bold text-gradient-hero mb-2">
      {prefix}
      {display}
      {suffix}
    </div>
  );
}

function FeatureTabs() {
  const { t } = useI18n();
  const tabs = [
    {
      key: 'setup',
      label: t('features.tabs.setup.label'),
      icon: Settings,
      title: t('features.tabs.setup.title'),
      promo: t('features.tabs.setup.promo'),
      bullets: [
        { icon: CheckCircle2, label: t('features.tabs.setup.bullets.0') },
        { icon: Settings, label: t('features.tabs.setup.bullets.1') },
        { icon: Link, label: t('features.tabs.setup.bullets.2') },
        { icon: Globe, label: t('features.tabs.setup.bullets.3') },
      ],
    },
    {
      key: 'agents',
      label: t('features.tabs.agents.label'),
      icon: Users,
      title: t('features.tabs.agents.title'),
      promo: t('features.tabs.agents.promo'),
      bullets: [
        { icon: CheckCircle2, label: t('features.tabs.agents.bullets.0') },
        { icon: Zap, label: t('features.tabs.agents.bullets.1') },
        { icon: Clock, label: t('features.tabs.agents.bullets.2') },
        { icon: Shield, label: t('features.tabs.agents.bullets.3') },
      ],
    },
    {
      key: 'kb',
      label: t('features.tabs.kb.label'),
      icon: BookOpen,
      title: t('features.tabs.kb.title'),
      promo: t('features.tabs.kb.promo'),
      bullets: [
        { icon: Link, label: t('features.tabs.kb.bullets.0') },
        { icon: CheckCircle2, label: t('features.tabs.kb.bullets.1') },
        { icon: Shield, label: t('features.tabs.kb.bullets.2') },
        { icon: Zap, label: t('features.tabs.kb.bullets.3') },
      ],
    },
    {
      key: 'crm',
      label: t('features.tabs.crm.label'),
      icon: Layers,
      title: t('features.tabs.crm.title'),
      promo: t('features.tabs.crm.promo'),
      bullets: [
        { icon: Layers, label: t('features.tabs.crm.bullets.0') },
        { icon: Users, label: t('features.tabs.crm.bullets.1') },
        { icon: BarChart3, label: t('features.tabs.crm.bullets.2') },
        { icon: Link, label: t('features.tabs.crm.bullets.3') },
      ],
    },
    {
      key: 'integrations',
      label: t('features.tabs.integrations.label'),
      icon: Link,
      title: t('features.tabs.integrations.title'),
      promo: t('features.tabs.integrations.promo'),
      bullets: [],
    },
    {
      key: 'dashboard',
      label: t('features.tabs.ops.label'),
      icon: LayoutDashboard,
      title: t('features.tabs.ops.title'),
      promo: t('features.tabs.ops.promo'),
      bullets: [
        { icon: MessageSquare, label: t('features.tabs.ops.bullets.0') },
        { icon: Users, label: t('features.tabs.ops.bullets.1') },
        { icon: BarChart3, label: t('features.tabs.ops.bullets.2') },
        { icon: Shield, label: t('features.tabs.ops.bullets.3') },
      ],
    },
    {
      key: 'billing',
      label: t('features.tabs.billing.label'),
      icon: CreditCard,
      title: t('features.tabs.billing.title'),
      promo: t('features.tabs.billing.promo'),
      bullets: [
        { icon: CreditCard, label: t('features.tabs.billing.bullets.0') },
        { icon: Zap, label: t('features.tabs.billing.bullets.1') },
        { icon: Clock, label: t('features.tabs.billing.bullets.2') },
        { icon: Shield, label: t('features.tabs.billing.bullets.3') },
      ],
    },
  ];

  const [active, setActive] = useState('setup');
  const [isHovered, setIsHovered] = useState(false);

  useEffect(() => {
    if (isHovered) return;
    const idx = tabs.findIndex((t) => t.key === active);
    const id = setInterval(() => {
      const next = tabs[(idx + 1) % tabs.length].key;
      setActive(next);
    }, 7000);
    return () => clearInterval(id);
  }, [active, isHovered]);

  const current = tabs.find((t) => t.key === active)!;

  return (
    <div className="mt-8" onMouseEnter={() => setIsHovered(true)} onMouseLeave={() => setIsHovered(false)}>
      <div className="grid lg:grid-cols-12 gap-8 items-start">
        <div className="lg:col-span-4">
          <div className="flex lg:flex-col flex-wrap gap-4">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setActive(t.key)}
                className={`group relative inline-flex items-center justify-between lg:justify-start gap-3 rounded-2xl border px-5 py-3 text-base lg:text-lg w-full lg:w-auto transition-all ${
                  active === t.key
                    ? 'bg-primary/15 border-primary text-primary shadow-soft'
                    : 'bg-card border-border hover:bg-accent/40 hover:shadow-soft'
                }`}
              >
                <span className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-primary/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden />
                <span className="inline-flex items-center gap-2.5">
                  <t.icon className="w-5 h-5 transition-transform duration-300 group-hover:scale-110" />
                  <span className="font-semibold tracking-tight">{t.label}</span>
                </span>
                <span className={`hidden lg:inline w-1.5 h-1.5 rounded-full ${active === t.key ? 'bg-primary' : 'bg-muted'}`} />
              </button>
            ))}
          </div>
        </div>

        <div className="lg:col-span-8">
          <div className="relative bg-card rounded-3xl p-10 lg:p-14 border border-border shadow-premium overflow-hidden">
            <div className="pointer-events-none absolute -top-24 -right-24 w-[420px] h-[420px] rounded-full bg-gradient-hero opacity-10 blur-3xl animate-pulse" />

            <div key={current.key} className="animate-panel-in transition-all duration-500 ease-out will-change-transform">
              <div className="inline-flex items-center gap-2 text-xs md:text-sm uppercase tracking-wider text-muted-foreground mb-3">
                <current.icon className="w-4 h-4" />
                {current.label}
              </div>
              <h3 className="text-3xl lg:text-4xl font-semibold text-card-foreground mb-4">
                {current.title}
              </h3>
              <p className="text-muted-foreground text-lg lg:text-xl leading-relaxed mb-8 max-w-2xl">
                {current.promo}
              </p>

              {current.key !== 'integrations' ? (
                <div className="grid sm:grid-cols-2 gap-5 mb-8">
                  {current.bullets.map((b, i) => (
                    <div key={b.label} className={`flex items-start gap-3 bg-background/40 rounded-xl p-5 border border-border/60 transition-all duration-300 ${
                      i % 2 === 0 ? 'hover:translate-x-[2px]' : 'hover:-translate-x-[2px]'
                    }`}>
                      <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
                        <b.icon className="w-5 h-5 text-primary" />
                      </div>
                      <div className="text-card-foreground font-medium leading-relaxed">
                        {b.label}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mb-2">
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
                    { (t<{name:string;slug:string}[]>("integrations.items")).map((it, idx) => (
                      <div key={`${it.slug}-${idx}`} className="flex items-center justify-center h-16 border border-border rounded-xl bg-background/40">
                        <img src={`https://cdn.simpleicons.org/${it.slug}/9CA3AF`} alt={`${it.name} logo`} className="h-9 w-auto opacity-90" loading="lazy" decoding="async" />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Removed unused placeholder block below benefits grid */}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const Features = () => {
  const { t } = useI18n();
  const features = [
    {
      icon: Bot,
      title: "Intelligent AI Assistant",
      description: "Advanced natural language processing that understands context and provides accurate, helpful responses to customer inquiries.",
      color: "text-blue-500",
      bgColor: "bg-blue-50",
    },
    {
      icon: Zap,
      title: "Lightning Fast Responses",
      description: "Instant replies that reduce wait times to zero. Your customers get immediate help, any time of day or night.",
      color: "text-yellow-500",
      bgColor: "bg-yellow-50",
    },
    {
      icon: Shield,
      title: "Enterprise Security",
      description: "Bank-level encryption and security protocols ensure your customer data and conversations remain completely secure.",
      color: "text-green-500",
      bgColor: "bg-green-50",
    },
    {
      icon: BarChart3,
      title: "Advanced Analytics",
      description: "Comprehensive insights into customer interactions, satisfaction rates, and support team performance metrics.",
      color: "text-purple-500",
      bgColor: "bg-purple-50",
    },
    {
      icon: MessageSquare,
      title: "Seamless Handoffs",
      description: "Smart routing to human agents when needed, with full conversation context for smooth transitions.",
      color: "text-pink-500",
      bgColor: "bg-pink-50",
    },
    {
      icon: Clock,
      title: "24/7 Availability",
      description: "Never miss a customer inquiry. Your AI assistant works around the clock to provide continuous support.",
      color: "text-indigo-500",
      bgColor: "bg-indigo-50",
    },
  ];

  return (
    <section id="features" className="pt-16 md:pt-20 pb-10 md:pb-28 bg-background relative">
      {/* Background tech decorations */}
      <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden" aria-hidden>
        {/* Top flowing lines */}
        <svg className="absolute -top-6 left-1/2 -translate-x-1/2 w-[1200px] h-20 opacity-20" viewBox="0 0 1200 80" preserveAspectRatio="none">
          <defs>
            <linearGradient id="feat-grad-1" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="hsl(240 100% 70%)"/>
              <stop offset="100%" stopColor="hsl(250 100% 75%)"/>
            </linearGradient>
          </defs>
          <path d="M0,40 C200,10 400,60 600,30 800,55 1000,25 1200,40" fill="none" stroke="url(#feat-grad-1)" strokeWidth="2" strokeLinecap="round" strokeDasharray="6 10">
            <animate attributeName="stroke-dashoffset" from="0" to="-300" dur="10s" repeatCount="indefinite"/>
          </path>
          <path d="M0,60 C250,30 450,70 700,40 900,65 1050,35 1200,55" fill="none" stroke="url(#feat-grad-1)" strokeWidth="1.5" strokeLinecap="round" strokeDasharray="4 12">
            <animate attributeName="stroke-dashoffset" from="0" to="-400" dur="16s" repeatCount="indefinite"/>
          </path>
        </svg>
        {/* Bottom flowing lines */}
        <svg className="absolute -bottom-6 left-1/2 -translate-x-1/2 w-[1200px] h-20 opacity-15" viewBox="0 0 1200 80" preserveAspectRatio="none">
          <defs>
            <linearGradient id="feat-grad-2" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="hsl(250 100% 75%)"/>
              <stop offset="100%" stopColor="hsl(240 100% 70%)"/>
            </linearGradient>
          </defs>
          <path d="M0,20 C220,50 440,10 660,35 880,15 1040,45 1200,25" fill="none" stroke="url(#feat-grad-2)" strokeWidth="2" strokeLinecap="round" strokeDasharray="5 10">
            <animate attributeName="stroke-dashoffset" from="0" to="-320" dur="12s" repeatCount="indefinite"/>
          </path>
        </svg>
        {/* Glowing nodes */}
        <svg className="absolute left-[10%] top-[30%] w-3 h-3" viewBox="0 0 8 8">
          <circle cx="4" cy="4" r="3" fill="hsl(240 100% 70% / 0.35)">
            <animate attributeName="opacity" values="0.2;0.7;0.2" dur="4s" repeatCount="indefinite"/>
          </circle>
        </svg>
        <svg className="absolute right-[12%] top-[55%] w-2.5 h-2.5" viewBox="0 0 8 8">
          <circle cx="4" cy="4" r="3" fill="hsl(250 100% 75% / 0.35)">
            <animate attributeName="opacity" values="0.2;0.8;0.2" dur="5.5s" repeatCount="indefinite"/>
          </circle>
        </svg>
      </div>
      <div className="container mx-auto px-6">
        {/* Section Header */}
        <div className="text-center mb-14 md:mb-24 animate-fade-in">
          <h2 className="text-4xl lg:text-5xl font-bold text-foreground mb-5">
            {t("features.sectionTitlePrefix")} {" "}
            <span className="text-gradient-hero">{t("features.sectionTitleHighlight")}</span>
          </h2>
          <p className="text-xl text-muted-foreground max-w-3xl mx-auto">
            {t("features.sectionSubtitle")}
          </p>
        </div>

        {/* Premium Categorized Features */}
        <FeatureTabs />

        {/* Stats Section removed (moved to Hero) */}
      </div>
    </section>
  );
};

export default Features;
