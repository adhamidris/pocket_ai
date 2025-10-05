import { useMemo, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useI18n } from "@/i18n/I18nProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import Header from "@/components/Header";
import MobileAppPromo from "@/components/MobileAppPromo";
import Footer from "@/components/Footer";
import { useIsMobile } from "@/hooks/use-mobile";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, Paperclip } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem } from "@/components/ui/command";
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ApiError } from "@/services/http";
import { useIdempotency } from "@/hooks/useIdempotency";
import {
  attachUploadLinks,
  completeRegistration,
  configureAgent,
  startRegistration,
  upsertBusiness,
} from "@/services/registerApi";
import type { SessionProgress } from "@/services/registerApi";
import { clearToken, getStoredToken, loginWithPassword, storeToken } from "@/services/auth";

const CAPTCHA_FALLBACK = (import.meta.env.VITE_CAPTCHA_TOKEN as string | undefined) || undefined;

const Register = () => {
  const { t, dir, lang } = useI18n();
  const isMobile = useIsMobile();
  const [step, setStep] = useState<'form' | 'business' | 'agent' | 'uploads'>("form");
  const idempotencyKeyFor = useIdempotency();
  const [registrationId, setRegistrationId] = useState<string | null>(null);
  const [businessId, setBusinessId] = useState<string | null>(null);
  const [, setSessionProgress] = useState<SessionProgress | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(() => getStoredToken());
  const [submittingStep, setSubmittingStep] = useState<'form' | 'business' | 'agent' | 'uploads' | null>(null);

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
          password: z
            .string()
            .min(8, t("auth.register.errors.passwordMin")),
          confirmPassword: z
            .string()
            .min(8, t("auth.register.errors.passwordMin")),
        })
        .refine((vals) => vals.password === vals.confirmPassword, {
          path: ["confirmPassword"],
          message: t("auth.register.errors.passwordsMismatch"),
        }),
    [t]
  );

  type RegisterFormValues = z.infer<typeof schema> & {
    businessName: string;
    industry: string;
    specifyIndustry: string;
    lineOfBusiness: string[];
    lineOfBusinessCustom: string[];
    country: string;
    website: string;
    agentName: string;
    agentTitle: string;
    agentTone: string;
    agentTraits: string[];
    agentEscalation: string;
    uploadsVision: string[];
    uploadsMission: string[];
    uploadsCatalog: string[];
    uploadsFaqs: string[];
    uploadsKb: string[];
    uploadsSops: string[];
    uploadsTc: string[];
    uploadsVisionUrl: string;
    uploadsMissionUrl: string;
    uploadsCatalogUrl: string;
    uploadsFaqsUrl: string;
    uploadsKbUrl: string;
    uploadsSopsUrl: string;
    uploadsTcUrl: string;
  };

  const form = useForm<RegisterFormValues>({
    defaultValues: {
      firstName: "",
      email: "",
      // phoneCountry: "+1",
      // phone: "",
      password: "",
      confirmPassword: "",
      // Business step defaults
      businessName: "",
      industry: "",
      specifyIndustry: "",
      lineOfBusiness: [],
      lineOfBusinessCustom: [],
      country: "",
      website: "",
      agentName: "",
      agentTitle: "",
      agentTone: "",
      agentTraits: [],
      agentEscalation: "",
      uploadsVision: [],
      uploadsMission: [],
      uploadsCatalog: [],
      uploadsFaqs: [],
      uploadsKb: [],
      uploadsSops: [],
      uploadsTc: [],
      uploadsVisionUrl: "",
      uploadsMissionUrl: "",
      uploadsCatalogUrl: "",
      uploadsFaqsUrl: "",
      uploadsKbUrl: "",
      uploadsSopsUrl: "",
      uploadsTcUrl: "",
    },
    mode: "onTouched",
    resolver: zodResolver(schema),
  });

  useEffect(() => {
    if (accessToken) {
      storeToken(accessToken);
    } else {
      clearToken();
    }
  }, [accessToken]);

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

  // Role-step typing effect
  // Business-step typing effect
  const businessSub = t("auth.register.businessSub") as string;
  const [typedBusiness, setTypedBusiness] = useState("");
  const bizIntervalRef = useRef<number | null>(null);
  const bizTimeoutRef = useRef<number | null>(null);
  const agentSub = 'Set up your agent basics';
  const [typedAgent, setTypedAgent] = useState("");
  const agentIntervalRef = useRef<number | null>(null);
  const agentTimeoutRef = useRef<number | null>(null);
  const [lobOpen, setLobOpen] = useState(false);
  const [industryOpen, setIndustryOpen] = useState(false);
  const [showIndustryCustom, setShowIndustryCustom] = useState(false);
  const [lobCustomInput, setLobCustomInput] = useState("");
  const [lobQuery, setLobQuery] = useState("");
  // Uploads step selection (web)
  const [selectedUploadTypes, setSelectedUploadTypes] = useState<string[]>(['vision','faqs','catalog']);

  useEffect(() => {
    if (step !== 'business') return () => {};
    const start = () => {
      let i = 0;
      if (bizIntervalRef.current) window.clearInterval(bizIntervalRef.current);
      if (bizTimeoutRef.current) window.clearTimeout(bizTimeoutRef.current);
      bizIntervalRef.current = window.setInterval(() => {
        i += 1;
        setTypedBusiness(businessSub.slice(0, i));
        if (i >= businessSub.length) {
          if (bizIntervalRef.current) window.clearInterval(bizIntervalRef.current);
          bizTimeoutRef.current = window.setTimeout(start, 1400);
        }
      }, 80);
    };
    start();
    return () => {
      if (bizIntervalRef.current) window.clearInterval(bizIntervalRef.current);
      if (bizTimeoutRef.current) window.clearTimeout(bizTimeoutRef.current);
    };
  }, [step, businessSub]);

  // Agent-step typing effect
  useEffect(() => {
    if (step !== 'agent') return () => {};
    const start = () => {
      let i = 0;
      if (agentIntervalRef.current) window.clearInterval(agentIntervalRef.current);
      if (agentTimeoutRef.current) window.clearTimeout(agentTimeoutRef.current);
      agentIntervalRef.current = window.setInterval(() => {
        i += 1;
        setTypedAgent(agentSub.slice(0, i));
        if (i >= agentSub.length) {
          if (agentIntervalRef.current) window.clearInterval(agentIntervalRef.current);
          agentTimeoutRef.current = window.setTimeout(start, 1400);
        }
      }, 80);
    };
    start();
    return () => {
      if (agentIntervalRef.current) window.clearInterval(agentIntervalRef.current);
      if (agentTimeoutRef.current) window.clearTimeout(agentTimeoutRef.current);
    };
  }, [step]);

  // Helper to map broader industry buckets to LoB presets
  const mapIndustryToLoBKey = (v?: string) => {
    if (!v) return 'Other';
    const s = v.toLowerCase();
    // Keep existing categories
    if (s.includes('e‑commerce') || s.includes('e-commerce') || s.includes('retail')) return 'E‑commerce';
    if (s.includes('saas') || s.includes('software')) return 'SaaS';
    if (s.includes('financial')) return 'Finance';
    if (s.includes('health')) return 'Healthcare';
    if (s.includes('education')) return 'Education';
    if (s.includes('hospitality') || s.includes('travel')) return 'Hospitality';
    // New detailed categories
    if (s.includes('manufactur')) return 'Manufacturing';
    if (s.includes('logistics') || s.includes('transport')) return 'Logistics';
    if (s.includes('real estate')) return 'Real Estate';
    if (s.includes('media') || s.includes('entertainment')) return 'Media & Entertainment';
    if (s.includes('telecom')) return 'Telecommunications';
    if (s.includes('energy') || s.includes('utilit')) return 'Energy & Utilities';
    if (s.includes('nonprofit') || s.includes('ngo')) return 'Nonprofit & NGOs';
    if (s.includes('professional')) return 'Professional Services';
    if (s.includes('consumer')) return 'Consumer Services';
    return 'Other';
  };
  const isOther = false;

  // Countries for business profile (with flags)
  const bizCountries = [
    { value: 'United States', flag: '🇺🇸' },
    { value: 'United Kingdom', flag: '🇬🇧' },
    { value: 'United Arab Emirates', flag: '🇦🇪' },
    { value: 'Saudi Arabia', flag: '🇸🇦' },
    { value: 'Egypt', flag: '🇪🇬' },
    { value: 'France', flag: '🇫🇷' },
    { value: 'Germany', flag: '🇩🇪' },
    { value: 'Spain', flag: '🇪🇸' },
    { value: 'Italy', flag: '🇮🇹' },
    { value: 'Other', flag: '🌐' },
  ] as const;

  // Auto-detect country for Business step (timezone first, then locale)
  useEffect(() => {
    if (step !== 'business') return;
    try {
      const current = (form.getValues as any)("country");
      if (current) return;

      const regionToCountry: Record<string, string> = {
        US: 'United States',
        GB: 'United Kingdom',
        AE: 'United Arab Emirates',
        SA: 'Saudi Arabia',
        EG: 'Egypt',
        FR: 'France',
        DE: 'Germany',
        ES: 'Spain',
        IT: 'Italy',
      };

      let region: string | undefined;
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const tzToRegion: Record<string, string> = {
        'Europe/London': 'GB',
        'Europe/Paris': 'FR',
        'Europe/Berlin': 'DE',
        'Europe/Madrid': 'ES',
        'Europe/Rome': 'IT',
        'America/New_York': 'US',
        'America/Chicago': 'US',
        'America/Denver': 'US',
        'America/Los_Angeles': 'US',
        'Asia/Riyadh': 'SA',
        'Asia/Dubai': 'AE',
        'Africa/Cairo': 'EG',
      };
      if (tz && tzToRegion[tz]) region = tzToRegion[tz];

      if (!region) {
        const langs = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]).filter(Boolean) as string[];
        for (const l of langs) {
          try {
            // @ts-ignore Intl.Locale may be missing in some environments
            const loc = new (Intl as any).Locale(l);
            if (loc && loc.region) { region = String(loc.region).toUpperCase(); break; }
          } catch {
            const m = l && l.match(/[-_](\w{2})/);
            if (m && m[1]) { region = m[1].toUpperCase(); break; }
          }
        }
        if (!region && langs[0] && /^ar/i.test(langs[0])) region = 'EG';
      }

      const country = region ? regionToCountry[region] : undefined;
      if (country) (form.setValue as any)("country", country, { shouldDirty: false, shouldTouch: false });
    } catch {
      // ignore
    }
  }, [step, form]);

  // Options for industry niches per industry (match mobile)
  const lobOptionsByIndustry: Record<string, string[]> = {
    'E‑commerce': [
      'Apparel',
      'Electronics',
      'Beauty & Personal Care',
      'Home & Kitchen',
      'Sports & Outdoors',
      'Groceries',
      'Digital Goods',
      'Handmade & Crafts',
      'Automotive Accessories',
    ],
    'SaaS': [
      'CRM',
      'Marketing Automation',
      'Analytics',
      'Project Management',
      'Customer Support',
      'Developer Tools',
      'Productivity',
      'Security',
      'Billing/Subscriptions',
      'Auth/Identity',
      'Observability',
      'Data Platform',
    ],
    'Finance': [
      'Banking',
      'Lending',
      'Payments',
      'Wealth Management',
      'Insurance',
      'Accounting',
      'Crypto/Blockchain',
      'Trading Platforms',
    ],
    'Healthcare': [
      'Clinics',
      'Telemedicine',
      'Pharmacy',
      'Diagnostics',
      'Medical Devices',
      'Wellness',
      'Electronic Health Records',
    ],
    'Education': [
      'K‑12',
      'Higher Education',
      'EdTech Platform',
      'Corporate Training',
      'Test Prep',
      'Language Learning',
      'Tutoring & Coaching',
    ],
    'Hospitality': [
      'Hotels',
      'Restaurants',
      'Catering',
      'Travel & Tours',
      'Venues & Events',
      'Short‑Term Rentals',
    ],
    'Manufacturing': [
      'OEM Production',
      'Contract Manufacturing',
      'CNC Machining',
      'Injection Molding',
      '3D Printing',
      'PCB Assembly',
      'Quality Assurance',
      'Procurement & Supply',
      'Packaging',
      'Maintenance (MRO)',
    ],
    'Logistics': [
      'Freight Forwarding',
      'Last‑Mile Delivery',
      'Warehousing & Fulfillment',
      'Cold Chain',
      'Customs Brokerage',
      'Fleet Management',
      'Courier',
      'LTL/FTL Trucking',
      'Air Cargo',
      'Ocean Freight',
    ],
    'Real Estate': [
      'Residential Sales',
      'Commercial Leasing',
      'Property Management',
      'Valuation & Appraisal',
      'Real Estate Development',
      'Facility Management',
      'Co‑working',
      'Mortgage Brokerage',
      'Title & Escrow',
      'Short‑Term Rentals',
    ],
    'Media & Entertainment': [
      'Streaming Subscriptions',
      'OTT Platform',
      'Content Production',
      'Post‑Production',
      'Music Publishing',
      'Game Development',
      'Live Events',
      'Digital Advertising',
      'Influencer Campaigns',
      'Licensing & Syndication',
    ],
    'Telecommunications': [
      'Mobile Voice',
      'Fixed Broadband',
      'VoIP',
      'IoT Connectivity',
      'Cloud PBX',
      'SIP Trunking',
      'Managed Networks',
      '5G Solutions',
      'Fiber to the Home',
      'Data Center Colocation',
    ],
    'Energy & Utilities': [
      'Electricity Supply',
      'Natural Gas Supply',
      'Renewable Generation',
      'Solar Installation',
      'Energy Storage',
      'Smart Metering',
      'Demand Response',
      'Energy Trading',
      'EV Charging',
      'Utility Billing',
    ],
    'Nonprofit & NGOs': [
      'Fundraising',
      'Grant Management',
      'Program Delivery',
      'Volunteer Management',
      'Advocacy & Outreach',
      'Education Programs',
      'Healthcare Missions',
      'Disaster Relief',
      'Community Development',
      'Monitoring & Evaluation',
    ],
    'Professional Services': [
      'Consulting',
      'Legal Advisory',
      'Tax & Audit',
      'Accounting',
      'Architecture',
      'Engineering',
      'Design & Creative',
      'Recruitment',
      'IT Consulting',
      'Managed IT',
    ],
    'Consumer Services': [
      'Home Cleaning',
      'Appliance Repair',
      'Beauty & Wellness',
      'Fitness & Training',
      'Tutoring',
      'Pet Care',
      'Event Planning',
      'Photography',
      'Home Renovation',
      'Moving & Storage',
    ],
    'Other': [
      'Consulting',
      'Custom Development',
      'Training & Enablement',
      'Support & Success',
    ],
  };

  const captchaToken = CAPTCHA_FALLBACK;

  const stringifyDetails = (details: Record<string, unknown> | null | undefined) => {
    if (!details) return "";
    const entries = Object.entries(details);
    if (!entries.length) return "";
    return entries
      .slice(0, 3)
      .map(([key, value]) => `${key}: ${String(value)}`)
      .join(", ");
  };

  const sanitizeList = (list: string[]) => list.map((item) => item.trim()).filter((item) => item.length > 0);

  const sanitizeOptional = (value: string | null | undefined) => {
    if (!value) return undefined;
    const trimmed = value.trim();
    return trimmed.length ? trimmed : undefined;
  };

  const showError = (error: unknown, fallback: string) => {
    let description = fallback;
    if (error instanceof ApiError) {
      const detail = stringifyDetails(error.details);
      description = detail ? `${error.message} (${detail})` : error.message;
    } else if (error instanceof Error) {
      description = error.message;
    }
    toast({
      title: fallback,
      description,
      variant: "destructive",
    });
    console.error(fallback, error);
  };

  const ensureAuthToken = async (): Promise<string | null> => {
    if (accessToken) return accessToken;
    const email = (form.getValues("email") || "").trim();
    const password = form.getValues("password") || "";
    try {
      const tokenResult = await loginWithPassword(email, password);
      if (tokenResult) {
        setAccessToken(tokenResult);
        return tokenResult;
      }
    } catch (error) {
      throw error;
    }
    return null;
  };

  const requireAuthToken = async () => {
    try {
      const tokenResult = await ensureAuthToken();
      if (!tokenResult) {
        const message = "Authentication required to continue registration";
        showError(new Error(message), message);
        throw new Error(message);
      }
      return tokenResult;
    } catch (error) {
      showError(error, "Authentication required to continue registration");
      throw error;
    }
  };

  const onSubmit = async (values: z.infer<typeof schema>) => {
    const payload = {
      firstName: values.firstName.trim(),
      email: values.email.trim(),
      password: values.password || undefined,
    };
    const idemKey = idempotencyKeyFor("form", payload);
    setSubmittingStep("form");
    try {
      const response = await startRegistration(payload, {
        idempotencyKey: idemKey,
        captchaToken,
      });
      const startResponse =
        (response as { registrationId?: string | null; session?: SessionProgress | null } | null) ?? null;
      const nextRegistrationId = startResponse?.registrationId?.trim() ?? "";
      if (!nextRegistrationId) {
        throw new Error("Registration response missing registrationId");
      }
      setRegistrationId(nextRegistrationId);
      setSessionProgress(startResponse?.session ?? null);
      toast({
        title: t("auth.register.success.title"),
        description: t("auth.register.businessSub"),
      });
      try {
        const tokenResult = await loginWithPassword(payload.email, values.password);
        if (tokenResult) {
          setAccessToken(tokenResult);
        }
      } catch (error) {
        console.warn("Login after registration failed", error);
      }
      setStep("business");
    } catch (error) {
      showError(error, "Could not start registration");
    } finally {
      setSubmittingStep(null);
    }
  };

  const onBusinessSubmit = async (values: RegisterFormValues) => {
    if (!registrationId) {
      showError(new Error("Registration session missing"), "Registration session not found");
      return;
    }
    const payload = {
      businessName: values.businessName.trim(),
      industry: values.industry,
      specifyIndustry: sanitizeOptional(values.specifyIndustry),
      lineOfBusiness: sanitizeList(values.lineOfBusiness),
      lineOfBusinessCustom: sanitizeList(values.lineOfBusinessCustom),
      country: sanitizeOptional(values.country),
      website: sanitizeOptional(values.website),
    };
    let tokenValue: string;
    try {
      tokenValue = await requireAuthToken();
    } catch {
      return;
    }
    const idemKey = idempotencyKeyFor("business", payload);
    setSubmittingStep("business");
    try {
      const response = await upsertBusiness(registrationId, payload, {
        idempotencyKey: idemKey,
        token: tokenValue,
      });
      const businessResponse =
        (response as {
          business?: { id?: string | null } | null;
          session?: SessionProgress | null;
        } | null) ?? null;
      const nextBusinessId = businessResponse?.business?.id?.trim() ?? "";
      if (!nextBusinessId) {
        throw new Error("Business response missing id");
      }
      setBusinessId(nextBusinessId);
      setSessionProgress(businessResponse?.session ?? null);
      toast({
        title: "Business profile saved",
        description: "Next, configure your AI agent.",
      });
      setStep("agent");
    } catch (error) {
      showError(error, "Could not save business profile");
    } finally {
      setSubmittingStep(null);
    }
  };

  const onAgentSubmit = async (values: RegisterFormValues) => {
    if (!businessId) {
      showError(new Error("Business missing"), "Business not attached to session");
      return;
    }
    const payload = {
      agentName: sanitizeOptional(values.agentName),
      agentTitle: sanitizeOptional(values.agentTitle),
      agentTone: sanitizeOptional(values.agentTone),
      agentTraits: sanitizeList(values.agentTraits),
      agentEscalation: sanitizeOptional(values.agentEscalation),
    };
    let tokenValue: string;
    try {
      tokenValue = await requireAuthToken();
    } catch {
      return;
    }
    const idemKey = idempotencyKeyFor("agent", payload);
    setSubmittingStep("agent");
    try {
      const response = await configureAgent(businessId, payload, {
        idempotencyKey: idemKey,
        token: tokenValue,
      });
      if (!response) {
        throw new Error("Agent configuration response missing");
      }
      const agentResponse =
        (response as { session?: SessionProgress | null } | null) ?? null;
      setSessionProgress(agentResponse?.session ?? null);
      toast({
        title: "Agent configuration saved",
        description: "Add knowledge sources to finish up.",
      });
      setStep("uploads");
    } catch (error) {
      showError(error, "Could not configure agent");
    } finally {
      setSubmittingStep(null);
    }
  };

  const onUploadsSubmit = async (values: RegisterFormValues) => {
    if (!businessId || !registrationId) {
      showError(new Error("Registration incomplete"), "Missing session context");
      return;
    }
    const linkCatalog: Record<string, string[]> = {
      vision: sanitizeList(values.uploadsVision),
      mission: sanitizeList(values.uploadsMission),
      catalog: sanitizeList(values.uploadsCatalog),
      faqs: sanitizeList(values.uploadsFaqs),
      kb: sanitizeList(values.uploadsKb),
      sops: sanitizeList(values.uploadsSops),
      tc: sanitizeList(values.uploadsTc),
    };
    const links: Record<string, string[]> = {};
    Object.entries(linkCatalog).forEach(([key, arr]) => {
      if (arr.length) links[key] = Array.from(new Set(arr));
    });

    let tokenValue: string;
    try {
      tokenValue = await requireAuthToken();
    } catch {
      return;
    }

    setSubmittingStep("uploads");
    try {
      if (Object.keys(links).length) {
        const payload = {
          links,
          language: lang && lang.length <= 16 ? lang : undefined,
        };
        const idemKey = idempotencyKeyFor("uploads", payload);
        const response = await attachUploadLinks(businessId, payload, {
          idempotencyKey: idemKey,
          token: tokenValue,
        });
        if (!response) {
          throw new Error("Attach links response missing");
        }
        const uploadResponse =
          (response as {
            created?: Record<string, number | undefined>;
            duplicates?: number;
            session?: SessionProgress | null;
          }) ?? {};
        const createdRecord = uploadResponse.created ?? {};
        const createdTotal = Object.values(createdRecord).reduce(
          (sum, count) => sum + (typeof count === "number" ? count : 0),
          0
        );
        const duplicates =
          typeof uploadResponse.duplicates === "number" ? uploadResponse.duplicates : 0;
        setSessionProgress(uploadResponse.session ?? null);
        toast({
          title: "Knowledge attached",
          description: `Added ${createdTotal} new sources${duplicates ? `, ${duplicates} duplicate(s) skipped` : ""}.`,
        });
      }

      const completion = await completeRegistration(registrationId, { token: tokenValue });
      if (!completion) {
        throw new Error("Registration completion response missing");
      }
      const completionResponse =
        (completion as { session?: SessionProgress | null } | null) ?? null;
      setSessionProgress(completionResponse?.session ?? null);
      toast({
        title: "Registration complete",
        description: "You're all set! Redirecting shortly...",
      });
    } catch (error) {
      showError(error, "Could not finalize registration");
    } finally {
      setSubmittingStep(null);
    }
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
            <div className="rounded-xl border border-border bg-card shadow-premium overflow-hidden">
          {/* Header */}
          <div className="bg-secondary p-3 md:p-4 rounded-t-xl register-header">
            {step === 'form' ? (
              <>
                <h1 className="text-2xl md:text-3xl font-bold text-secondary-foreground text-center register-header-title">
                  {t("auth.register.titlePrefix")} {" "}
                  <span className="text-gradient-hero">{t("auth.register.titleHighlight")}</span>
                </h1>
                <p className="mt-1 text-center text-sm md:text-base streaming-text opacity-80 font-medium tracking-wide">
                  {typed}
                </p>
              </>
            ) : step === 'business' ? (
              <>
                <h1 className="text-2xl md:text-3xl font-bold text-secondary-foreground text-center register-header-title">
                  {t("auth.register.businessTitlePrefix")} {" "}
                  <span className="text-gradient-hero">{t("auth.register.businessTitleHighlight")}</span>
                </h1>
                <p className="mt-1 text-center text-sm md:text-base streaming-text opacity-80 font-medium tracking-wide">
                  {typedBusiness}
                </p>
              </>
            ) : step === 'agent' ? (
              <>
                <h1 className="text-2xl md:text-3xl font-bold text-secondary-foreground text-center register-header-title">
                  Agent {" "}
                  <span className="text-gradient-hero">Setup</span>
                </h1>
                <p className="mt-1 text-center text-sm md:text-base streaming-text opacity-80 font-medium tracking-wide">
                  {typedAgent}
                </p>
              </>
            ) : (
              <>
                <h1 className="text-2xl md:text-3xl font-bold text-secondary-foreground text-center register-header-title">
                  Knowledge {" "}
                  <span className="text-gradient-hero">Uploads</span>
                </h1>
                <p className="mt-1 text-center text-sm md:text-base streaming-text opacity-80 font-medium tracking-wide">
                  Add links to your key docs so your agent gets smart fast
                </p>
              </>
            )}
          </div>

          {/* Body */}
          <div className="p-3 md:p-4">
            <div className="relative min-h-[20rem]">
            <div className={`${step === 'form' ? 'animate-panel-in' : 'hidden'}`}>
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

                <Button
                  type="submit"
                  className="w-full bg-gradient-primary text-white hover:opacity-90"
                  disabled={submittingStep === 'form'}
                >
                  {submittingStep === 'form' ? 'Creating...' : t("auth.register.createAccount")}
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
                          <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36 16.8 36 11 30.2 11 23S16.8 10 24 10c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 12 2 2 12 2 24s10 22 22 22 22-10 22-22c0-1.3-.1-2.5-.4-3.5z" />
                          <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.4 16.3 18.8 14 24 14c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 15.3 2 7.8 7.1 4.2 14.1l2.1.6z" />
                          <path fill="#4CAF50" d="M24 46c6 0 11.5-2.2 15.6-5.8l-7.2-5.9C30.7 35.7 27.6 37 24 37c-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46z" />
                          <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-1.3 3.8-4.8 6.5-9.3 6.5-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46c12 0 22-10 22-22 0-1.3-.1-2.5-.4-3.5z" />
                        </svg>
                      </span>
                      <span>{t("auth.register.continueGoogle")}</span>
                    </span>
                  </Button>
                </div>
              </form>
            </Form>
            </div>

            {/* Step 2 (Business): Business profile setup */}
            <div className={`${step === 'business' ? 'animate-panel-in' : 'hidden'}`}>
              <Form {...form}>
                <form
                  className="space-y-4"
                  noValidate
                  dir={dir}
                  onSubmit={form.handleSubmit(onBusinessSubmit)}
                >
                  <FormField
                    control={form.control}
                    name={"businessName" as any}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Business name</FormLabel>
                        <FormControl>
                          <Input className="bg-input border-0" placeholder="Acme Inc." {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <FormField
                      control={form.control}
                      name={"industry" as any}
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Industry</FormLabel>
                          <Popover open={industryOpen} onOpenChange={setIndustryOpen}>
                            <PopoverTrigger asChild>
                              <button type="button" className="w-full h-10 px-3 rounded-md bg-input border-0 text-left text-sm text-foreground/90 flex items-center justify-between">
                                <span className={field.value ? '' : 'text-muted-foreground'}>
                                  {field.value || 'Search industry'}
                                </span>
                                <ChevronDown className="w-4 h-4 text-muted-foreground" />
                              </button>
                            </PopoverTrigger>
                            <PopoverContent className="p-0 w-[var(--radix-popover-trigger-width)]">
                              <Command>
                                <CommandInput placeholder="Search industry..." />
                                <CommandEmpty>No industry found.</CommandEmpty>
                                <CommandGroup>
                                  {[
                                    'E‑commerce & Retail',
                                    'SaaS & Software',
                                    'Financial Services',
                                    'Healthcare & Life Sciences',
                                    'Education',
                                    'Hospitality & Travel',
                                    'Manufacturing',
                                    'Logistics & Transportation',
                                    'Real Estate',
                                    'Media & Entertainment',
                                    'Telecommunications',
                                    'Energy & Utilities',
                                    'Nonprofit & NGOs',
                                    'Professional Services',
                                    'Consumer Services',
                                  ].map(opt => (
                                    <CommandItem key={opt} value={opt} onSelect={(val)=>{
                                      (form.setValue as any)('industry', val, { shouldDirty: true, shouldTouch: true });
                                      (form.setValue as any)('lineOfBusiness', [], { shouldDirty: false, shouldTouch: false });
                                      setIndustryOpen(false);
                                    }}>
                                      {opt}
                                    </CommandItem>
                                  ))}
                                </CommandGroup>
                              </Command>
                            </PopoverContent>
                          </Popover>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    {isOther ? (
                      <FormField key="right-specifyIndustry"
                        control={form.control}
                        name={"specifyIndustry" as any}
                        render={({ field }) => (
                          <FormItem className="fade-in">
                            <FormLabel>Specify Industry</FormLabel>
                            <FormControl>
                              <Input className="bg-input border-0" placeholder="Describe your industry" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    ) : (
                      <>
                      <FormField key="right-lineOfBusiness"
                        control={form.control}
                        name={"lineOfBusiness" as any}
                        render={({ field }) => {
                          const selected: string[] = Array.isArray(field.value) ? field.value : [];
                          const custom: string[] = Array.isArray((form.watch as any)("lineOfBusinessCustom")) ? (form.watch as any)("lineOfBusinessCustom") : [];
                          const allSel = Array.from(new Set([...(selected||[]), ...(custom||[])]));
                          const industry = (form.watch as any)("industry") as string | undefined;
                          const key = mapIndustryToLoBKey(industry);
                          const baseOptions = lobOptionsByIndustry[key] || lobOptionsByIndustry['Other'];
                          const options = baseOptions;
                          const maxVisible = 2; // reserve space for +N reliably
                          const visible = allSel.slice(0, maxVisible);
                          const remaining = Math.max(0, allSel.length - visible.length);
                          return (
                              <FormItem className="fade-in">
                                <FormLabel>Industry niches</FormLabel>
                                <DropdownMenu open={!!industry && lobOpen} onOpenChange={(open)=> industry ? setLobOpen(open) : setLobOpen(false)}>
                                  <DropdownMenuTrigger asChild>
                                    <button type="button" disabled={!industry} className={`w-full min-h-10 px-2 py-1.5 rounded-md bg-input border-0 text-left text-sm text-foreground/90 flex items-center justify-between ${!industry ? 'opacity-60 cursor-not-allowed' : ''}`}>
                                      {allSel.length === 0 ? (
                                        <>
                                          <span className="text-muted-foreground flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{industry ? 'Select niches' : 'Select industry first'}</span>
                                          <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0 ms-1" />
                                        </>
                                      ) : (
                                        <>
                                          <span className="flex items-center gap-1.5 overflow-hidden flex-1">
                                            {visible.map((opt) => (
                                              <Badge key={opt} variant="secondary" className="shrink-0">
                                                {opt}
                                              </Badge>
                                            ))}
                                          </span>
                                          <span className="shrink-0 inline-flex items-center gap-1.5">
                                            {remaining > 0 && (
                                              <Badge variant="outline" className="shrink-0">+{remaining}</Badge>
                                            )}
                                            <ChevronDown className="w-4 h-4 text-muted-foreground" />
                                          </span>
                                        </>
                                      )}
                                    </button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="start" className="min-w-[18rem]">
                                    {industry ? (
                                      <>
                                        <div className="px-2 py-1.5">
                                          <Input value={lobQuery} onChange={(e)=>setLobQuery(e.target.value)} placeholder="Search niches" className="h-8 bg-input" />
                                        </div>
                                        {options.filter(o=> !lobQuery.trim() || o.toLowerCase().includes(lobQuery.toLowerCase())).map((opt) => {
                                          const checked = selected.includes(opt);
                                          return (
                                            <DropdownMenuCheckboxItem
                                              key={opt}
                                              checked={checked}
                                              onSelect={(e) => e.preventDefault()}
                                              onCheckedChange={(ck) => {
                                                const curr = new Set(selected);
                                                if (ck) curr.add(opt); else curr.delete(opt);
                                                (form.setValue as any)("lineOfBusiness", Array.from(curr), { shouldDirty: true, shouldTouch: true });
                                              }}
                                            >
                                              {opt}
                                            </DropdownMenuCheckboxItem>
                                          );
                                        })}
                                        <DropdownMenuSeparator />
                                        <div className="px-2 py-1.5">
                                          <div className="flex items-center gap-2">
                                            <Input value={lobCustomInput} onChange={(e)=>setLobCustomInput(e.target.value)} placeholder="Add custom" className="h-8 bg-input" />
                                            <Button type="button" size="sm" onClick={()=>{
                                              const v = lobCustomInput.trim();
                                              if(!v) return;
                                              const prev = Array.isArray((form.getValues as any)("lineOfBusinessCustom")) ? (form.getValues as any)("lineOfBusinessCustom") : [];
                                              if (!prev.includes(v) && !selected.includes(v)) {
                                                (form.setValue as any)("lineOfBusinessCustom", [...prev, v], { shouldDirty: true, shouldTouch: true });
                                              }
                                              setLobCustomInput("");
                                            }}>Add</Button>
                                          </div>
                                        </div>
                                        <div className="px-2 py-1.5">
                                          <Button type="button" size="sm" className="w-full" onClick={() => setLobOpen(false)}>Done</Button>
                                        </div>
                                      </>
                                    ) : (
                                      <div className="px-3 py-2 text-sm text-muted-foreground">Select industry first</div>
                                    )}
                                  </DropdownMenuContent>
                                </DropdownMenu>
                                <FormMessage />
                              </FormItem>
                            );
                          }}
                        />
                      </>
                    )}
                  </div>

                  {isOther ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 fade-in">
                      <FormField
                        control={form.control}
                        name={"lineOfBusiness" as any}
                        render={({ field }) => {
                          const selected: string[] = Array.isArray(field.value) ? field.value : [];
                          const industry = (form.watch as any)("industry") as string | undefined;
                          const options = industry && lobOptionsByIndustry[industry] ? lobOptionsByIndustry[industry] : lobOptionsByIndustry['Other'];
                          const maxVisible = 2;
                          const visible = selected.slice(0, maxVisible);
                          const remaining = Math.max(0, selected.length - visible.length);
                          return (
                              <FormItem className="fade-in">
                                <FormLabel>Industry niches</FormLabel>
                                <DropdownMenu open={!!industry && lobOpen} onOpenChange={(open)=> industry ? setLobOpen(open) : setLobOpen(false)}>
                                  <DropdownMenuTrigger asChild>
                                    <button type="button" disabled={!industry} className={`w-full min-h-10 px-2 py-1.5 rounded-md bg-input border-0 text-left text-sm text-foreground/90 flex items-center justify-between ${!industry ? 'opacity-60 cursor-not-allowed' : ''}`}>
                                      {selected.length === 0 ? (
                                        <>
                                          <span className="text-muted-foreground flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{industry ? 'Select niches' : 'Select industry first'}</span>
                                          <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0 ms-1" />
                                        </>
                                      ) : (
                                        <>
                                          <span className="flex items-center gap-1.5 overflow-hidden flex-1">
                                            {visible.map((opt) => (
                                              <Badge key={opt} variant="secondary" className="shrink-0">
                                                {opt}
                                              </Badge>
                                            ))}
                                          </span>
                                          <span className="shrink-0 inline-flex items-center gap-1.5">
                                            {remaining > 0 && (
                                              <Badge variant="outline" className="shrink-0">+{remaining}</Badge>
                                            )}
                                            <ChevronDown className="w-4 h-4 text-muted-foreground" />
                                          </span>
                                        </>
                                      )}
                                    </button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="start" className="min-w-[18rem]">
                                    {industry ? (
                                      <>
                                        {options.map((opt) => {
                                          const checked = selected.includes(opt);
                                          return (
                                            <DropdownMenuCheckboxItem
                                              key={opt}
                                              checked={checked}
                                              onSelect={(e) => e.preventDefault()}
                                              onCheckedChange={(ck) => {
                                                const curr = new Set(selected);
                                                if (ck) curr.add(opt); else curr.delete(opt);
                                                (form.setValue as any)("lineOfBusiness", Array.from(curr), { shouldDirty: true, shouldTouch: true });
                                              }}
                                            >
                                              {opt}
                                            </DropdownMenuCheckboxItem>
                                          );
                                        })}
                                        <DropdownMenuSeparator />
                                        <div className="px-2 py-1.5">
                                          <Button type="button" size="sm" className="w-full" onClick={() => setLobOpen(false)}>Done</Button>
                                        </div>
                                      </>
                                    ) : (
                                      <div className="px-3 py-2 text-sm text-muted-foreground">Select industry first</div>
                                    )}
                                  </DropdownMenuContent>
                                </DropdownMenu>
                                <FormMessage />
                              </FormItem>
                            );
                          }}
                        />
                        
                      </div>
                    ) : (
                    <div className="grid grid-cols-1 gap-4 fade-in">
                      <FormField
                        control={form.control}
                        name={"country" as any}
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Country</FormLabel>
                            <Select onValueChange={field.onChange} value={field.value}>
                              <FormControl>
                                <SelectTrigger className="bg-input border-0">
                                  <SelectValue placeholder="Select country" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {bizCountries.map((c) => (
                                  <SelectItem key={c.value} value={c.value}>
                                    <span className="inline-flex items-center gap-2">
                                      <span className="text-base leading-none">{c.flag}</span>
                                      <span>{c.value}</span>
                                    </span>
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                    )}

                  

                  {/* Website moved to end (optional) */}
                  <FormField
                    control={form.control}
                    name={"website" as any}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Website <span className="text-muted-foreground">(optional)</span></FormLabel>
                        <FormControl>
                          <Input className="bg-input border-0" type="url" placeholder="https://example.com" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <div className="flex items-center justify-between gap-3 pt-2">
                    <Button type="button" variant="ghost" onClick={() => setStep('form')}>Back</Button>
                    <Button
                      type="submit"
                      className="bg-gradient-primary text-white hover:opacity-90"
                      disabled={submittingStep === 'business'}
                    >
                      {submittingStep === 'business' ? 'Saving...' : 'Next'}
                    </Button>
                  </div>
                </form>
              </Form>
            </div>

            {/* Step 4 (Agent): Agent setup */}
            <div className={`${step === 'agent' ? 'animate-panel-in' : 'hidden'}`}>
              <Form {...form}>
                <form
                  className="space-y-4"
                  noValidate
                  dir={dir}
                  onSubmit={form.handleSubmit(onAgentSubmit)}
                >
                  <FormField
                    control={form.control}
                    name={'agentName' as any}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Agent name</FormLabel>
                        <FormControl>
                          <Input className="bg-input border-0" placeholder="e.g., Nancy" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <FormField
                      control={form.control}
                      name={'agentTitle' as any}
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Role</FormLabel>
                          <Select onValueChange={field.onChange} value={field.value}>
                            <FormControl>
                              <SelectTrigger className="bg-input border-0 ring-0 focus:ring-0 focus-visible:ring-0 data-[state=open]:ring-0">
                                <SelectValue placeholder="Select a role" />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {['Customer Support Agent','Support Specialist','Customer Success Representative','Sales Support Agent','Front Desk Representative','Account Manager','Helpdesk Agent'].map(opt => (
                                <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name={'agentTone' as any}
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Tone</FormLabel>
                          <Select onValueChange={field.onChange} value={field.value}>
                            <FormControl>
                              <SelectTrigger className="bg-input border-0 ring-0 focus:ring-0 focus-visible:ring-0 data-[state=open]:ring-0">
                                <SelectValue placeholder="Select tone" />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {['Friendly','Professional','Empathetic','Concise','Cheerful','Calm'].map(opt => (
                                <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>

                  <FormField
                    control={form.control}
                    name={'agentTraits' as any}
                    render={({ field }) => {
                      const value: string[] = Array.isArray(field.value) ? field.value : [];
                      const toggle = (opt: string) => {
                        const set = new Set(value);
                        if (set.has(opt)) set.delete(opt); else set.add(opt);
                        (form.setValue as any)('agentTraits', Array.from(set), { shouldDirty: true, shouldTouch: true });
                      };
                      const traits = ['Helpful','Proactive','Patient','Detail‑oriented','Persuasive','Resourceful'];
                      return (
                        <FormItem>
                          <FormLabel>Traits</FormLabel>
                          <div className="grid place-items-center w-full">
                            <div className="inline-flex flex-wrap gap-2 justify-start text-left">
                              {traits.map(opt => {
                                const active = value.includes(opt);
                                return (
                                  <button type="button" key={opt} onClick={() => toggle(opt)} className={`px-3 h-8 rounded-md text-sm border ${active ? 'bg-primary text-primary-foreground border-primary' : 'bg-input text-foreground/90 border-border'} hover:opacity-90`}>
                                    {opt}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                          <FormMessage />
                        </FormItem>
                      );
                    }}
                  />

                  <FormField
                    control={form.control}
                    name={'agentEscalation' as any}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Escalation rule</FormLabel>
                        <Select onValueChange={field.onChange} value={field.value}>
                          <FormControl>
                            <SelectTrigger className="bg-input border-0 ring-0 focus:ring-0 focus-visible:ring-0 data-[state=open]:ring-0">
                              <SelectValue placeholder="Choose escalation rule" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {[
                              'Escalate if user asks for human',
                              'Escalate after 2 failed answers',
                              'Escalate on billing/security topics',
                              'Never escalate automatically',
                            ].map(opt => (
                              <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <div className="flex itemscenter justify-between gap-3 pt-2">
                    <Button type="button" variant="ghost" onClick={() => setStep('business')}>Back</Button>
                    <Button
                      type="submit"
                      className="bg-gradient-primary text-white hover:opacity-90"
                      disabled={submittingStep === 'agent'}
                    >
                      {submittingStep === 'agent' ? 'Saving...' : 'Next'}
                    </Button>
                  </div>
                </form>
              </Form>
            </div>

            {/* Step 5 (Uploads): Knowledge uploads */}
            <div className={`${step === 'uploads' ? 'animate-panel-in' : 'hidden'}`}>
              <Form {...form}>
                <form
                  className="space-y-4"
                  noValidate
                  dir={dir}
                  onSubmit={form.handleSubmit(onUploadsSubmit)}
                >
                  {(() => {
                    const catalogEntity = 'Products & Services';
                    const configs = [
                      { id: 'vision', label: 'Vision', fv: 'uploadsVision' as const, fi: 'uploadsVisionUrl' as const, placeholder: 'https://your-site.com/vision' },
                      { id: 'mission', label: 'Mission', fv: 'uploadsMission' as const, fi: 'uploadsMissionUrl' as const, placeholder: 'https://your-site.com/mission' },
                      { id: 'catalog', label: `${catalogEntity} Catalog`, fv: 'uploadsCatalog' as const, fi: 'uploadsCatalogUrl' as const, placeholder: 'https://your-site.com/catalog' },
                      { id: 'faqs', label: 'FAQs', fv: 'uploadsFaqs' as const, fi: 'uploadsFaqsUrl' as const, placeholder: 'https://your-site.com/faqs' },
                      { id: 'kb', label: 'Knowledge Base', fv: 'uploadsKb' as const, fi: 'uploadsKbUrl' as const, placeholder: 'https://help.your-site.com' },
                      { id: 'sops', label: 'SOPs', fv: 'uploadsSops' as const, fi: 'uploadsSopsUrl' as const, placeholder: 'https://drive.google.com/...' },
                      { id: 'tc', label: 'T&C', fv: 'uploadsTc' as const, fi: 'uploadsTcUrl' as const, placeholder: 'https://your-site.com/terms' },
                    ] as const;
                    const activeConfigs = configs.filter((c) => selectedUploadTypes.includes(c.id));

                    return (
                      <>
                        <div className="rounded-lg border border-border bg-secondary/40 p-3 md:p-4">
                          <div className="text-sm font-medium text-foreground">Which materials do you want to attach?</div>
                          <ToggleGroup
                            type="multiple"
                            value={selectedUploadTypes}
                            onValueChange={(values) => {
                              const next = Array.isArray(values) ? values : [];
                              const map = new Map(configs.map((c) => [c.id, c]));
                              setSelectedUploadTypes((prev) => {
                                const removed = prev.filter((id) => !next.includes(id));
                                removed.forEach((id) => {
                                  const cfg = map.get(id);
                                  if (!cfg) return;
                                  (form.setValue as any)(cfg.fv, [], { shouldDirty: true, shouldTouch: true });
                                  (form.setValue as any)(cfg.fi, '', { shouldDirty: true, shouldTouch: true });
                                });
                                return next;
                              });
                            }}
                            className="mt-3 flex flex-wrap gap-2"
                          >
                            {configs.map((cfg) => (
                              <ToggleGroupItem
                                key={cfg.id}
                                value={cfg.id}
                                className="h-8 rounded-md border border-border bg-input px-3 text-sm font-medium text-foreground/90 transition hover:opacity-90 data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
                              >
                                {cfg.label}
                              </ToggleGroupItem>
                            ))}
                          </ToggleGroup>
                        </div>

                        <div className="mt-3 space-y-3">
                          {activeConfigs.length === 0 ? (
                            <div className="rounded-lg border border-dashed border-border bg-background/40 px-6 py-8 text-center text-sm text-muted-foreground">
                              Select at least one material above to start adding links.
                            </div>
                          ) : (
                            activeConfigs.map((cfg) => {
                                const links: string[] = Array.isArray((form.watch as any)(cfg.fv)) ? (form.watch as any)(cfg.fv) : [];
                                const pending = String((form.watch as any)(cfg.fi) || '');

                                const attach = () => {
                                  const url = String((form.getValues as any)(cfg.fi) || '').trim();
                                  if (!url) return;
                                  const list: string[] = Array.isArray((form.getValues as any)(cfg.fv)) ? (form.getValues as any)(cfg.fv) : [];
                                  if (!list.includes(url)) {
                                    (form.setValue as any)(cfg.fv, [...list, url], { shouldDirty: true, shouldTouch: true });
                                  }
                                  (form.setValue as any)(cfg.fi, '', { shouldDirty: true, shouldTouch: true });
                                };

                                return (
                                  <div key={cfg.id} className="space-y-3 rounded-lg border border-border bg-secondary/50 p-3 md:p-4">
                                    <div className="flex items-center justify-between gap-2">
                                      <div className="font-medium text-sm text-foreground">{cfg.label}</div>
                                      {links.length > 0 && (
                                        <div className="text-xs text-muted-foreground">{links.length} added</div>
                                      )}
                                    </div>
                                    <div className="flex flex-col gap-2 md:flex-row">
                                      <Input
                                        className="bg-input border-0 md:flex-1"
                                        placeholder={cfg.placeholder}
                                        value={pending}
                                        onChange={(e) => (form.setValue as any)(cfg.fi, e.target.value, { shouldDirty: true })}
                                        onKeyDown={(e) => {
                                          if (e.key === 'Enter') {
                                            e.preventDefault();
                                            attach();
                                          }
                                        }}
                                      />
                                      <Button
                                        type="button"
                                        variant="secondary"
                                        size="sm"
                                        className="flex w-full items-center justify-center gap-2 md:w-auto"
                                        onClick={attach}
                                      >
                                        <Paperclip className="h-4 w-4" />
                                        <span className="text-xs font-medium md:text-sm">Attach</span>
                                      </Button>
                                    </div>
                                    {links.length > 0 && (
                                      <div className="flex flex-wrap gap-2">
                                        {links.map((u) => (
                                          <span key={u} className="inline-flex items-center gap-2 rounded-md border border-border bg-input px-2.5 py-1 text-xs">
                                            <span className="max-w-[16rem] truncate">{u}</span>
                                            <button
                                              type="button"
                                              className="text-muted-foreground transition hover:text-foreground"
                                              onClick={() => {
                                                const list: string[] = Array.isArray((form.getValues as any)(cfg.fv)) ? (form.getValues as any)(cfg.fv) : [];
                                                (form.setValue as any)(cfg.fv, list.filter((x) => x !== u), { shouldDirty: true, shouldTouch: true });
                                              }}
                                            >
                                              ×
                                            </button>
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                );
                              })
                          )}
                        </div>
                      </>
                    );
                  })()}

                  <div className="flex items-center justify-between gap-3 pt-2">
                    <Button type="button" variant="ghost" onClick={() => setStep('agent')}>Back</Button>
                    <Button
                      type="submit"
                      className="bg-gradient-primary text-white hover:opacity-90"
                      disabled={submittingStep === 'uploads'}
                    >
                      {submittingStep === 'uploads' ? 'Finishing...' : 'Finish'}
                    </Button>
                  </div>
                </form>
              </Form>
            </div>
            </div>
            
            <div className="mt-6 text-center text-sm">
              {step === 'form' && (
                <div className="text-foreground/80">
                  {t("auth.register.haveAccount")} {" "}
                  <Link to="/" className="text-primary hover:underline">
                    {t("auth.register.login")}
                  </Link>
                </div>
              )}
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
