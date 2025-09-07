const en = {
  nav: {
    brand: "Pocket",
    features: "Features",
    pricing: "Pricing",
    about: "About",
    contact: "Contact",
    signIn: "Sign In",
    getStarted: "Get Started",
    register: "Register",
    lightMode: "Light mode",
    toggleLightMode: "Toggle light mode",
    toggleLanguage: "Toggle language",
    language: {
      en: "EN",
      ar: "AR",
    },
  },
  hero: {
    titlePrefix: "AI Powered",
    titleHighlight: "Customer Service",
    subtitle:
      "Deliver instant, intelligent support 24/7 with our AI-powered platform. Reduce response times by 90% and delight your customers.",
    ctaTrial: "Start Free Trial",
    ctaDemo: "Watch Demo",
    stats: {
      fasterResponse: "Faster Response",
      aiSupport: "AI Support",
      happyCustomers: "Happy Customers",
      browserBar: "yourwebsite.com",
      googlePlayGetItOn: "GET IT ON",
      googlePlay: "Google Play",
      appStoreDownloadOnThe: "Download on the",
      appStore: "App Store",
    },
  },
  trustedBy: {
    title: "Trusted by",
  },
  auth: {
    login: {
      title: "Welcome Back to Pocket",
      titlePrefix: "Welcome Back to",
      subtitle: "Login to continue",
      email: "Email",
      password: "Password",
      loginBtn: "Login",
      or: "OR",
      noAccount: "Don't have an account?",
      signUp: "Sign up",
    },
    register: {
      title: "Create your account",
      titlePrefix: "Create your",
      titleHighlight: "Account",
      subtitle: "Start your 14-day free trial",
      welcomePrefix: "Welcome to",
      welcomeBrand: "Pocket",
      welcomeSub: "Your clients, served better.",
      firstName: "First name",
      email: "Email",
      phone: "Phone number",
      password: "Password",
      confirmPassword: "Confirm password",
      optional: "optional",
      createAccount: "Create Account",
      haveAccount: "Already have an account?",
      login: "Log in",
      registeringAs: "Registering as",
      roles: {
        business: {
          title: "Business",
          desc: "to handle basic customer service tasks",
        },
        entrepreneur: {
          title: "Entrepreneur",
          desc: "to handle my clients' day-to-day communications",
        },
        employee: {
          title: "Employee",
          desc: "to handle my frontlining daily tasks",
        },
      },
      rolesShort: {
        business: "Business",
        entrepreneur: "Entrepreneur",
        employee: "Employee",
      },
      placeholders: {
        firstName: "John",
        email: "you@company.com",
        phone: "+1 (555) 000-0000",
        password: "••••••••",
        confirmPassword: "••••••••",
      },
      errors: {
        firstNameRequired: "First name is required",
        emailInvalid: "Please enter a valid email",
        phoneInvalid: "Please enter a valid phone number",
        passwordMin: "Password must be at least 8 characters",
        passwordsMismatch: "Passwords do not match",
        roleRequired: "Please choose how you're registering",
      },
      success: {
        title: "Account created",
        description: "Welcome, {name}! Your account is ready.",
      },
      continueGoogle: "Continue with Google",
    },
  },
  howItWorks: {
    title: "It is simple!",
    steps: [
      {
        title: "Register",
        bullets: [
          "Set up business profile",
          "Upload SOP / Knowledge Base",
          "Or let AI generate it",
        ],
      },
      {
        title: "Create your AI Agent",
        bullets: [
          "Configure persona and tools",
          "Get your portal link",
        ],
      },
      {
        title: "Paste, paste, paste",
        bullets: [
          "Paste across web, socials, WhatsApp",
          "Sit back and monitor",
        ],
      },
    ],
  },
  integrations: {
    title: "Integrations",
    subtitle: "Plug into your tools in minutes",
    items: [
      { name: "HubSpot", slug: "hubspot" },
      { name: "Slack", slug: "slack" },
      { name: "Zapier", slug: "zapier" },
      { name: "WhatsApp", slug: "whatsapp" },
      { name: "Gmail", slug: "gmail" },
      { name: "Shopify", slug: "shopify" },
      { name: "Zendesk", slug: "zendesk" },
      { name: "Intercom", slug: "intercom" },
    ],
  },
  faq: {
    titlePrefix: "Frequently asked",
    titleHighlight: "questions",
    items: [
      {
        q: "How do agents learn our business?",
        a: "Sync your docs and sites or upload files. Retrieval with citations keeps answers grounded and up to date.",
      },
      {
        q: "What happens if the AI doesn’t know?",
        a: "It can request clarification, route to a human, or create a follow‑up with full context—your choice.",
      },
      {
        q: "Can I customize tone and behavior?",
        a: "Yes. Configure personas, guardrails, tools, and workflows per agent, then test in a live sandbox.",
      },
      {
        q: "How does billing work?",
        a: "Choose packages or prepay credit (wage) to activate agents. Switch to yearly for savings.",
      },
      {
        q: "Is my data secure?",
        a: "Bank‑level encryption, roles and audit logs. Optional SSO and data residency controls.",
      },
    ],
  },
  security: {
    title: "Security and compliance",
    bullets: [
      "SSO + role‑based access",
      "Encryption in transit and at rest",
      "Audit logs and event history",
      "Data residency options",
    ],
  },
  mobileApp: {
    title: "Try the mobile app",
  },
  features: {
    sectionTitlePrefix: "Everything You Need for",
    sectionTitleHighlight: "Perfect Support",
    sectionSubtitle:
      "Our comprehensive platform combines cutting-edge AI with intuitive design to deliver exceptional customer service experiences.",
    tabs: {
      setup: {
        label: "Easy Setup",
        title: "From sign‑up to live in minutes",
        promo:
          "Create your account, connect your business data, configure the agent, and share the chat link instantly—everywhere.",
        bullets: [
          "Guided, no‑code onboarding",
          "Business profile & preferences",
          "Instant chat portal link",
          "Omnichannel social & WhatsApp",
        ],
      },
      agents: {
        label: "Multiple Agents",
        title: "Scale with specialized, brand‑aligned agents",
        promo:
          "Spin up dedicated agents for sales, support, onboarding, and more. Each agent carries your tone and executes actions confidently.",
        bullets: [
          "Customizable personas and tone",
          "Actionable workflows and tools",
          "24/7 availability across timezones",
          "Modern, fine‑tuned LLM models",
        ],
      },
      kb: {
        label: "Knowledge Base",
        title: "Instant business intelligence for your agents",
        promo:
          "Upload SOPs and docs, sync help centers and sites. Retrieval‑augmented generation gives agents precise, grounded answers from your materials.",
        bullets: [
          "One‑click uploads & syncs",
          "Automatic chunking & embeddings",
          "Citations & guardrails for trust",
          "Realtime refresh & invalidation",
        ],
      },
      crm: {
        label: "Flexible CRM",
        title: "Build the CRM your workflows deserve",
        promo:
          "Compose a flexible CRM—add or remove tabs, define data parameters to collect, track customer profiles, and manage insights your way.",
        bullets: [
          "Dynamic tabs & fields",
          "Customer profiles & segments",
          "Insight management & exports",
          "Integrations: HubSpot, and more",
        ],
      },
      integrations: {
        label: "Integrations",
        title: "Connect your stack in minutes",
        promo:
          "Plug into tools your team already uses — CRM, support, messaging, and automation platforms.",
      },
      ops: {
        label: "Operations",
        title: "Run operations with clarity and control",
        promo:
          "Governance and operations: queue visibility, live sessions, agent routing, and scheduled reports—everything leaders need to steer performance.",
        bullets: [
          "Live queue & session views",
          "Routing & assignment rules",
          "Scheduled reports & alerts",
          "Roles, audit logs, and SSO",
        ],
      },
      billing: {
        label: "Billing",
        title: "Pay for outcomes—not idle seats",
        promo:
          "Activate agents when you need them. Use wage‑based billing, predictable packages, and smart notifications to stay on budget.",
        bullets: [
          "Wage‑based activation",
          "Usage packages",
          "Credit depletion alerts",
          "Spend caps & schedules",
        ],
      },
    },
    grid: [
      {
        title: "Intelligent AI Assistant",
        description:
          "Advanced natural language processing that understands context and provides accurate, helpful responses to customer inquiries.",
      },
      {
        title: "Lightning Fast Responses",
        description:
          "Instant replies that reduce wait times to zero. Your customers get immediate help, any time of day or night.",
      },
      {
        title: "Enterprise Security",
        description:
          "Bank-level encryption and security protocols ensure your customer data and conversations remain completely secure.",
      },
      {
        title: "Advanced Analytics",
        description:
          "Comprehensive insights into customer interactions, satisfaction rates, and support team performance metrics.",
      },
      {
        title: "Seamless Handoffs",
        description:
          "Smart routing to human agents when needed, with full conversation context for smooth transitions.",
      },
      {
        title: "24/7 Availability",
        description:
          "Never miss a customer inquiry. Your AI assistant works around the clock to provide continuous support.",
      },
    ],
  },
  testimonials: {
    titlePrefix: "Customers who",
    titleHighlight: "build differently",
    subtitle:
      "Real teams, real results. Built with speed, reliability, and brand in mind.",
    items: [
      {
        quote:
          "Response times dropped from minutes to seconds. Our CSAT is up 25% and the team finally sleeps.",
        author: "Maya Patel",
        role: "Head of Support, Flowify",
      },
      {
        quote:
          "We automated order updates and refunds in days. Revenue grew while tickets went down—it's magic.",
        author: "Alex Romero",
        role: "COO, CloudCart",
      },
      {
        quote:
          "Setup was insanely fast. The branded chat feels native—our brand team loves it.",
        author: "Sofia Nguyen",
        role: "VP Marketing, BrightClinic",
      },
      {
        quote:
          "The analytics unlocked patterns we couldn't see before. Smarter ops, happier customers.",
        author: "Daniel Kim",
        role: "Operations Lead, Loop",
      },
      {
        quote:
          "From pilot to production in a week. It just works and keeps getting better.",
        author: "Aisha Al-Farsi",
        role: "Product Manager, Nimbus",
      },
      {
        quote:
          "We scaled support without scaling headcount. The ROI speaks for itself.",
        author: "Jonah Weiss",
        role: "Founder, Parcelio",
      },
    ],
  },
  pricing: {
    flexible: "Flexible pricing",
    choose: "Choose what fits ",
    motion: "your motion",
    subheader: {
      packages: "Simple plans for any stage. Switch billing to see savings with yearly.",
      wage: "Prepay a minimum credit (wage) to activate agents and features, then scale usage.",
      self: "Deploy on your own infrastructure with your database, custom integrations, and add‑ons.",
    },
    tabs: {
      wage: "Wage based",
      packages: "Packages",
      self: "One‑time setup (self‑hosted)",
    },
    billingCycle: {
      monthly: "Monthly",
      yearly: "Yearly",
      suffixMonthly: "/mo",
      suffixYearly: "/mo · billed yearly",
      trial: "7‑day free trial",
      getStarted: "Get started",
      addCredit: "Add credit",
      flexible: "Flexible",
      minCredit: "Minimum credit (wage)",
      activates: "Activates agents and unlocks features. Credit is consumed by usage.",
      badges: {
        usersChoice: "Users’ Choice",
      },
    },
    packages: [
      {
        tier: "Plus",
        description: "For frontliners who talk to customers daily — fits any industry.",
        monthly: 29,
        yearly: 24,
        features: [
          "1 agent",
          "Branded chat portal",
          "Core knowledge base",
          "Inbox + basic analytics",
          "Email transcripts",
        ],
      },
      {
        tier: "Pro",
        badge: "Users’ Choice",
        description: "For SMBs — multiple agents and advanced workflows.",
        monthly: 89,
        yearly: 69,
        features: [
          "Up to 3 agents",
          "Advanced knowledge base + citations",
          "Workflows and tools (actions)",
          "CRM profiles + segments",
          "Reports & scheduled alerts",
        ],
      },
      {
        tier: "Enterprise",
        description: "For large teams — security, scale, and customization.",
        monthly: 249,
        yearly: 199,
        features: [
          "Unlimited agents",
          "SSO, roles & audit logs",
          "Custom routing & priority queues",
          "Integrations (HubSpot, webhooks)",
          "Premium support & SLA",
        ],
      },
    ],
    wagePlans: [
      {
        id: "starter",
        label: "Starter",
        minCredit: 50,
        bullets: [
          "1 agent active",
          "Up to 3k assisted messages",
          "All core features included",
          "Community support",
        ],
      },
      {
        id: "growth",
        label: "Growth",
        minCredit: 200,
        bullets: [
          "Up to 3 agents active",
          "Up to 15k assisted messages",
          "Advanced KB + citations",
          "Priority email support",
        ],
      },
      {
        id: "scale",
        label: "Scale",
        minCredit: 1000,
        bullets: [
          "Unlimited agents active",
          "Up to 100k assisted messages",
          "Full platform + integrations",
          "Premium support & SLA",
        ],
      },
    ],
    self: {
      title: "Self‑hosted deployment",
      bullets: [
        "On‑prem or private cloud",
        "Your database and VPC",
        "Custom integrations & add‑ons",
        "SSO, roles & audit logs",
        "Implementation support",
      ],
      ctaQuote: "Get a quote",
      ctaSales: "Talk to sales",
    },
  },
  footer: {
    brand: "AI Support",
    blurb:
      "Transforming customer service with intelligent AI solutions. Deliver exceptional support experiences that delight your customers and grow your business.",
    categories: {
      Product: ["Features", "Pricing", "API Documentation", "Integrations", "Security"],
      Company: ["About Us", "Careers", "Press", "Blog", "Contact"],
      Resources: ["Help Center", "Community", "Guides", "Status", "Changelog"],
      Legal: ["Privacy Policy", "Terms of Service", "GDPR", "Compliance", "Cookies"],
    },
    bottom: {
      copyright: "© 2024 AI Support. All rights reserved.",
      privacy: "Privacy Policy",
      terms: "Terms of Service",
      cookie: "Cookie Policy",
    },
    social: {
      twitter: "Twitter",
      linkedin: "LinkedIn",
      github: "GitHub",
      email: "Email",
    },
  },
  demo: {
    online: "Online",
    inputPlaceholder: "This is a demo - try the real widget below! →",
    scenarios: [
      {
        agentName: "Nancy",
        jobTitle: "E‑Commerce Support Agent",
        conversation: [
          { id: 1, text: "Hi! I need help with my order #12345", isBot: false, delay: 1000 },
          { id: 2, text: "Hello, I’m Nancy. I’d be glad to help. I’ll check that order now.", isBot: true, delay: 1400 },
          { id: 3, text: "Thanks for waiting. Your order shipped yesterday and should arrive tomorrow by 3 PM. Tracking: TR123456789.", isBot: true, delay: 1600 },
          { id: 4, text: "Perfect. Can I change the delivery address?", isBot: false, delay: 1200 },
          { id: 5, text: "Certainly. What is the new address?", isBot: true, delay: 1200 },
          { id: 6, text: "123 New Street, Los Angeles, CA 90210", isBot: false, delay: 1300 },
          { id: 7, text: "All set. I’ve updated the address. Anything else I can assist with today?", isBot: true, delay: 1400 },
          { id: 8, text: "No, that’s all. Thank you, Nancy.", isBot: false, delay: 1100 },
          { id: 9, text: "You’re welcome. Happy to help.", isBot: true, delay: 1400 },
        ],
      },
      {
        agentName: "Jack",
        jobTitle: "Banking Assistance Agent",
        conversation: [
          { id: 1, text: "Hi. I believe the interest on my credit card was calculated incorrectly.", isBot: false, delay: 1100 },
          { id: 2, text: "Hello, this is Jack. I can clarify. Interest is calculated daily on the carried balance and summed for the billing cycle.", isBot: true, delay: 1600 },
          { id: 3, text: "For example: 8 days at AED 5,000 and 22 days at AED 2,000 produce a blended amount based on each daily balance.", isBot: true, delay: 1700 },
          { id: 4, text: "That helps. Could you send me the detailed breakdown?", isBot: false, delay: 1200 },
          { id: 5, text: "Of course. I’ve sent a statement breakdown to your registered email. Would you like assistance setting up autopay?", isBot: true, delay: 1400 },
          { id: 6, text: "No, that’s fine for now. Thanks, Jack.", isBot: false, delay: 1200 },
          { id: 7, text: "Anytime. If anything else comes up, I’m here to help.", isBot: true, delay: 1400 },
        ],
      },
      {
        agentName: "Suzan",
        jobTitle: "Realtor Agent",
        conversation: [
          { id: 1, text: "Hello Suzan. Are there any units available in Dubai Marina?", isBot: false, delay: 1100 },
          { id: 2, text: "Hello, I'm happy to help. Yes, here’s what’s currently available:", isBot: true, delay: 1400 },
          { id: 3, text: "2BR, 1,320 sqft, Marina view — AED 2.1M.\n3BR, 2,450 sqft, high floor — AED 4.5M.\n1BR, 820 sqft, furnished — AED 1.35M.", isBot: true, delay: 1700 },
          { id: 4, text: "Would you like a sales specialist to contact you?", isBot: true, delay: 1400 },
          { id: 5, text: "Not yet. Could I see some images?", isBot: false, delay: 1300 },
          { id: 6, text: "Certainly. Sharing a few photos:", isBot: true, delay: 1600, images: [
            "https://images.unsplash.com/photo-1505691723518-36a5ac3b2b8f?w=600&q=80&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1523217582562-09d0def993a6?w=600&q=80&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?w=600&q=80&auto=format&fit=crop",
          ] },
          { id: 7, text: "Looks good. Please have someone contact me at +971 50 123 4567. Name: Ahmed.", isBot: false, delay: 1500 },
          { id: 8, text: "Done. I’ve scheduled a call for today at 4:30 PM. Our specialist Sara will contact you shortly.", isBot: true, delay: 1600 },
          { id: 9, text: "Great, thank you.", isBot: false, delay: 1200 },
          { id: 10, text: "You’re welcome. I’m here if you need anything else.", isBot: true, delay: 1400 },
        ],
      },
    ],
  },
  chat: {
    online: "Online",
    headerName: "AI Assistant",
    initial: "Hi! I'm your AI assistant. How can I help you today?",
    demoResponses: [
      "I'd be happy to help you with that! Let me find the information you need.",
      "That's a great question. Here's what I can tell you about our services...",
      "I understand your concern. Let me connect you with the right solution.",
      "Thanks for reaching out! I can definitely assist you with that request.",
    ],
    inputPlaceholder: "Type your message...",
  },
  notFound: {
    title: "404",
    subtitle: "Oops! Page not found",
    home: "Return to Home",
  },
};

export default en;
