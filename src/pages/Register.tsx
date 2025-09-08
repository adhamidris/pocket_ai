import { useMemo, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useI18n } from "@/i18n/I18nProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import Header from "@/components/Header";
import MobileAppPromo from "@/components/MobileAppPromo";
import Footer from "@/components/Footer";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { HelpCircle } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Link } from "react-router-dom";
import { toast } from "@/components/ui/sonner";

const Register = () => {
  const { t, dir, lang } = useI18n();

  /* Phone field temporarily disabled
  const countries = [
    { id: "eg", dial: "+20", abbr: "EG", flag: "🇪🇬", example: "0100 000 0000" },
    { id: "ae", dial: "+971", abbr: "UAE", flag: "🇦🇪", example: "050 000 0000" },
    { id: "sa", dial: "+966", abbr: "KSA", flag: "🇸🇦", example: "050 000 0000" },
    { id: "us", dial: "+1", abbr: "US", flag: "🇺🇸", example: "(555) 000-0000" },
    { id: "gb", dial: "+44", abbr: "UK", flag: "🇬🇧", example: "07123 456789" },
    { id: "fr", dial: "+33", abbr: "FR", flag: "🇫🇷", example: "06 12 34 56 78" },
    { id: "de", dial: "+49", abbr: "DE", flag: "🇩🇪", example: "0151 2345678" },
    { id: "es", dial: "+34", abbr: "ES", flag: "🇪🇸", example: "612 34 56 78" },
    { id: "it", dial: "+39", abbr: "IT", flag: "🇮🇹", example: "345 678 9012" },
  ] as const;
  */

  // Local part only (no leading +; country code selected separately)
  /* Phone field temporarily disabled
  const phoneRegex = useMemo(() => /^[0-9\s\-()]{7,20}$/, []);
  */

  const schema = useMemo(
    () =>
      z
        .object({
          firstName: z
            .string()
            .min(2, t("auth.register.errors.firstNameRequired")),
          email: z
            .string()
            .email(t("auth.register.errors.emailInvalid")),
          // phoneCountry: z.string(),
          // phone: z
          //   .string()
          //   .optional()
          //   .refine((v) => !v || phoneRegex.test(v), {
          //     message: t("auth.register.errors.phoneInvalid"),
          //   }),
          password: z
            .string()
            .min(8, t("auth.register.errors.passwordMin")),
          confirmPassword: z
            .string()
            .min(8, t("auth.register.errors.passwordMin")),
          role: z.enum(["business", "entrepreneur", "employee"], {
            required_error: t("auth.register.errors.roleRequired"),
          }),
        })
        .refine((vals) => vals.password === vals.confirmPassword, {
          path: ["confirmPassword"],
          message: t("auth.register.errors.passwordsMismatch"),
        }),
    [t /*, phoneRegex*/]
  );

  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      firstName: "",
      email: "",
      // phoneCountry: "+1",
      // phone: "",
      password: "",
      confirmPassword: "",
      role: "business",
    },
    mode: "onTouched",
  });

  /* Phone field temporarily disabled
  // Auto-detect visitor region and preselect country code (timezone-first, then language)
  useEffect(() => {
    try {
      const current = form.getValues("phoneCountry");
      if (current && current !== "+1") return;
      // detection logic...
    } catch {}
  }, []);
  */

  /* Phone field temporarily disabled
  const selectedDial = form.watch("phoneCountry");
  const selectedCountry = useMemo(
    () => countries.find((c) => c.dial === selectedDial) ?? countries.find(c => c.dial === "+1")!,
    [countries, selectedDial]
  );
  */

  // Mobile-like typing effect for subheader (looping)
  const fullSub = t("auth.register.welcomeSub") as string;
  const [typed, setTyped] = useState("");
  const intervalRef = useRef<number | null>(null);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const start = () => {
      let i = 0;
      if (intervalRef.current) window.clearInterval(intervalRef.current);
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
      intervalRef.current = window.setInterval(() => {
        i += 1;
        setTyped(fullSub.slice(0, i));
        if (i >= fullSub.length) {
          if (intervalRef.current) window.clearInterval(intervalRef.current);
          timeoutRef.current = window.setTimeout(start, 1400);
        }
      }, 80);
    };
    start();
    return () => {
      if (intervalRef.current) window.clearInterval(intervalRef.current);
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    };
  }, [fullSub]);

  const onSubmit = (values: z.infer<typeof schema>) => {
    // Simulated submit; integrate API here later.
    toast.success(t("auth.register.success.title"), {
      description: t("auth.register.success.description", {
        name: values.firstName,
      }) as unknown as string,
    });
    form.reset();
  };

  // Ensure entering Register lands at the top (avoid showing lower sections first)
  useEffect(() => {
    const prev = (window.history as any).scrollRestoration;
    try { (window.history as any).scrollRestoration = 'manual'; } catch {}
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    return () => { try { (window.history as any).scrollRestoration = prev || 'auto'; } catch {} };
  }, []);

  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main>
        <section className="relative min-h-[60vh] flex items-center justify-center px-4 pt-4 pb-6 md:px-6 md:pt-6 md:pb-8 overflow-hidden">
          {/* Floating Orbs (desktop-sized) */}
          <div className="pointer-events-none absolute inset-0 opacity-30 register-orbs">
            <div
              className="absolute top-10 left-8 w-[420px] h-[420px] bg-gradient-hero rounded-full blur-3xl animate-none md:animate-pulse"
              style={{ animationDelay: '0s' }}
            />
            <div
              className="absolute bottom-12 right-8 w-[520px] h-[520px] bg-gradient-primary rounded-full blur-3xl animate-none md:animate-pulse"
              style={{ animationDelay: '1.2s' }}
            />
          </div>
          <div className="relative z-10 w-full max-w-lg md:max-w-xl">
            {/* Welcome block above the form header */}
            <div className="mb-2 text-center register-welcome">
              <p className="mt-1 text-base md:text-lg text-muted-foreground/70 font-medium tracking-wide">{typed}</p>
            </div>
            <div className="rounded-xl border border-border bg-card shadow-premium overflow-hidden">
          {/* Header */}
          <div className="bg-secondary p-3 md:p-4 rounded-t-xl register-header">
            <h1 className="text-2xl md:text-3xl font-bold text-secondary-foreground text-center register-header-title">
              {t("auth.register.titlePrefix")} {" "}
              <span className="text-gradient-hero">{t("auth.register.titleHighlight")}</span>
            </h1>
          </div>

          {/* Form */}
          <div className="p-3 md:p-4">
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-4"
                noValidate
                dir={dir}
              >
                <FormField
                  control={form.control}
                  name="firstName"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("auth.register.firstName")}</FormLabel>
                      <FormControl>
                        <Input
                          className="bg-input border-0"
                          placeholder={t("auth.register.placeholders.firstName")}
                          autoComplete="given-name"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("auth.register.email")}</FormLabel>
                      <FormControl>
                        <Input
                          className="bg-input border-0"
                          type="email"
                          placeholder={t("auth.register.placeholders.email")}
                          autoComplete="email"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Phone (optional) — temporarily disabled
                <div>
                  <div className="text-sm font-medium text-foreground">
                    {t("auth.register.phone")} {" "}
                    <span className="text-muted-foreground">({t("auth.register.optional")})</span>
                  </div>
                  <div className="mt-2 flex gap-2">
                    <div className="shrink-0">
                      <FormField
                        control={form.control}
                        name="phoneCountry"
                        render={({ field }) => (
                          <FormItem>
                            <Select onValueChange={field.onChange} value={field.value}>
                              <FormControl>
                                <SelectTrigger className="w-auto bg-input border-0">
                                  <SelectValue placeholder="+1" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {countries.map((c) => (
                                  <SelectItem key={c.id} value={c.dial}>
                                    <span className="inline-flex items-center gap-2">
                                      <span className="text-base leading-none">{c.flag}</span>
                                      <span className="font-medium">{c.abbr}</span>
                                      <span className="text-muted-foreground">{c.dial}</span>
                                    </span>
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </FormItem>
                        )}
                      />
                    </div>
                    <div className="flex-1">
                      <FormField
                        control={form.control}
                        name="phone"
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <Input
                                className="bg-input border-0"
                                type="tel"
                                placeholder={selectedCountry.example}
                                autoComplete="tel"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  </div>
                </div>
                */}

                <FormField
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("auth.register.password")}</FormLabel>
                      <FormControl>
                        <Input
                          className="bg-input border-0"
                          type="password"
                          placeholder={t("auth.register.placeholders.password")}
                          autoComplete="new-password"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="confirmPassword"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("auth.register.confirmPassword")}</FormLabel>
                      <FormControl>
                        <Input
                          className="bg-input border-0"
                          type="password"
                          placeholder={t("auth.register.placeholders.confirmPassword")}
                          autoComplete="new-password"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Registering as a — temporarily disabled */}
                {/*
                <FormField control={form.control} name="role" render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center gap-4 w-full">
                      <FormLabel className="whitespace-nowrap shrink-0">{t("auth.register.registeringAs")}</FormLabel>
                      <div className="flex-1 flex justify-center">
                        <RadioGroup className="grid grid-cols-3 gap-4 place-items-center w-full max-w-md" value={field.value} onValueChange={field.onChange}>
                          <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                            <RadioGroupItem value="business" />
                            <span className="text-sm">{t("auth.register.rolesShort.business")}</span>
                          </label>
                          <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                            <RadioGroupItem value="entrepreneur" />
                            <span className="text-sm">{t("auth.register.rolesShort.entrepreneur")}</span>
                          </label>
                          <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                            <RadioGroupItem value="employee" />
                            <span className="text-sm">{t("auth.register.rolesShort.employee")}</span>
                          </label>
                        </RadioGroup>
                      </div>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button type="button" aria-label="Role help" className="shrink-0 p-1.5 rounded-full border border-border bg-secondary/60 text-foreground hover:bg-secondary">
                            <HelpCircle className="w-4 h-4" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side={dir === 'rtl' ? 'left' : 'right'} align="end" sideOffset={8} avoidCollisions={false} className="max-w-sm bg-black text-white border-border/50">
                          <div className="text-xs space-y-2">
                            <div>
                              <div className="font-semibold">{t("auth.register.roles.business.title")}</div>
                              <div className="text-white/80">{t("auth.register.roles.business.desc")}</div>
                            </div>
                            <div>
                              <div className="font-semibold">{t("auth.register.roles.entrepreneur.title")}</div>
                              <div className="text-white/80">{t("auth.register.roles.entrepreneur.desc")}</div>
                            </div>
                            <div>
                              <div className="font-semibold">{t("auth.register.roles.employee.title")}</div>
                              <div className="text-white/80">{t("auth.register.roles.employee.desc")}</div>
                            </div>
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                    <FormMessage />
                  </FormItem>
                )} />
                */}

                <Button type="submit" className="w-full bg-gradient-primary text-white hover:opacity-90">
                  {t("auth.register.createAccount")}
                </Button>

                {/* Divider */}
                <div className="my-3">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-border" />
                    <span className="text-xs text-muted-foreground">OR</span>
                    <div className="h-px flex-1 bg-border" />
                  </div>
                </div>

                {/* Continue with Google */}
                <div className="mt-3">
                  <Button type="button" variant="secondary" className="w-full">
                    <span className="inline-flex items-center gap-2">
                      <span className="w-6 h-6 rounded-full inline-flex items-center justify-center">
                        <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden>
                          <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36 16.8 36 11 30.2 11 23S16.8 10 24 10c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 12 2 2 12 2 24s10 22 22 22 22-10 22-22c0-1.3-.1-2.5-.4-3.5z"/>
                          <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.4 16.3 18.8 14 24 14c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 15.3 2 7.8 7.1 4.2 14.1l2.1.6z"/>
                          <path fill="#4CAF50" d="M24 46c6 0 11.5-2.2 15.6-5.8l-7.2-5.9C30.7 35.7 27.6 37 24 37c-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46z"/>
                          <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-1.3 3.8-4.8 6.5-9.3 6.5-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46c12 0 22-10 22-22 0-1.3-.1-2.5-.4-3.5z"/>
                        </svg>
                      </span>
                      <span>{t("auth.register.continueGoogle")}</span>
                    </span>
                  </Button>
                </div>
              </form>
            </Form>

            <div className="mt-6 text-center text-sm text-foreground/80">
              {t("auth.register.haveAccount")} {" "}
              <Link to="/" className="text-primary hover:underline">
                {t("auth.register.login")}
              </Link>
            </div>
          </div>
            </div>
          </div>
        </section>
        <MobileAppPromo compact />
      </main>
      <Footer />
    </div>
  );
};

export default Register;
