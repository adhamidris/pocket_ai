const en = {
  dashboard: {
    title: "Dashboard",
    urgent: {
      label: "Urgent",
      noUrgent: "No urgent conversations right now",
      review: "Review",
    },
    stats: {
      todayMessages: "Today Messages",
      responseTime: "Avg Response Time",
      satisfaction: "Satisfaction",
      activeConversations: "Active Conversations",
    },
    quickActions: {
      conversations: {
        title: "Conversations",
        subtitle: "2 active, 1 waiting",
      },
      agents: {
        title: "Agents",
        subtitle: "Configure AI assistants",
      },
      customers: {
        title: "Customers",
        subtitle: "4 customers, 1 VIP",
      },
    },
    recentActivity: "Recent Activity",
    viewAll: "View all",
    emptyFeed: "No activity to show yet.",
  },
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
      tellUsMore: "Tell us more about you",
      tellUsMorePrefix: "Tell us more",
      tellUsMoreHighlight: "about you",
      registeringAsIm: "I'm registering as a",
      businessTitlePrefix: "Business Profile",
      businessTitleHighlight: "Setup",
      businessSub: "Help us understand your business",
      subtitle: "Start your 14-day free trial",
      welcomePrefix: "Welcome to",
      welcomeBrand: "Pocket",
      welcomeSub: "Your clients, served better.",
      roleSub: "Just a few more steps and you're ready…",
      firstName: "First name",
      email: "Email",
      phone: "Phone number",
      password: "Password",
      confirmPassword: "Confirm password",
      optional: "optional",
      createAccount: "Create Account",
      haveAccount: "Already have an account?",
      login: "Log in",
      registeringAs: "Registering as a",
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
    page: {
      catalog: {
        title: "1. Catalog",
        paragraphs: [
          "Connect your stack in minutes. Pick an integration to enable native hand‑offs, syncs, and automation with your existing tools."
        ]
      },
      embed: {
        title: "2. Embed your AI assistant anywhere",
        paragraphs: [
          "Your branded assistant is available via a secure link. Share it on your website, socials, email signatures, WhatsApp, or any platform that supports links."
        ],
        exampleLabel: "Example",
        exampleHint: "→ paste anywhere"
      },
      howto: {
        title: "3. How to connect",
        bullets: [
          "Open Integrations from your dashboard.",
          "Choose a provider (e.g., HubSpot, Slack, WhatsApp).",
          "Authenticate and grant the requested permissions.",
          "Configure what data to sync and which actions tools can perform.",
          "Test the flow, then enable it for your agents."
        ]
      },
      webhooks: {
        title: "4. Webhooks & custom workflows",
        paragraphs: [
          "Use webhooks to receive events and trigger your own automations. Map assistant actions to your internal systems without heavy lifting."
        ]
      },
      support: {
        title: "5. Support",
        paragraphs: [
          "Need help with an integration? Our team can guide setup and best practices. Reach us at integrations@pocket.ai."
        ]
      }
    }
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
  legal: {
    onThisPage: "On this page",
    privacy: {
      title: "Privacy Policy",
      lastUpdatedLabel: "Last updated",
      sections: {
        overview: {
          title: "1. Overview",
          paragraphs: [
            "Pocket AI Support (\"we\", \"us\", \"our\") provides AI‑powered customer service tools including multi‑agent chat, a knowledge base with retrieval and citations (RAG), integrations, and a flexible CRM. This Privacy Policy explains how we collect, use, share, and protect your information when you use our website, products, and services (the \"Services\").",
            "By using the Services, you agree to this Policy. If you do not agree, please discontinue use."
          ]
        },
        collection: {
          title: "2. Information We Collect",
          bullets: [
            "Account and Profile: name, email, role, preferences, authentication identifiers.",
            "Business Data: uploaded SOPs, documents, websites and help‑center sources you connect for RAG.",
            "Communications: chat transcripts, feedback, and logs from agent and user interactions.",
            "Usage and Device: product usage metrics, approximate location, device and browser information, cookies.",
            "Integrations: third‑party app identifiers and metadata when you connect services (e.g., HubSpot, Slack, WhatsApp)."
          ]
        },
        use: {
          title: "3. How We Use Information",
          bullets: [
            "Provide, operate, and improve the Services and features.",
            "Configure and personalize AI agents to match your brand.",
            "Power retrieval‑augmented answers with citations from your data.",
            "Monitor quality, performance, security, and abuse prevention.",
            "Support, troubleshoot, and communicate about the Services.",
            "Comply with legal obligations and enforce terms."
          ]
        },
        ai: {
          title: "4. AI Processing and Training",
          paragraphs: [
            "We use leading large language models (LLMs) and tooling to enable agent capabilities. Unless you opt in to data sharing for model improvement, we do not use your private business content to train foundation models. We may use aggregated, de‑identified analytics to improve system reliability and safety.",
            "Retrieval sources and citations are stored to provide grounded responses and auditability. You can invalidate or refresh sources at any time from your account."
          ]
        },
        sharing: {
          title: "5. How We Share Information",
          bullets: [
            "Vendors and Sub‑processors: infrastructure, analytics, and communication providers under contractual safeguards.",
            "Integrations: when you connect third‑party tools, we share the minimal required data per your configuration.",
            "Legal and Safety: to comply with law, enforce terms, or protect rights, safety, and security.",
            "Business Transfers: as part of a merger, acquisition, or asset sale with notice as required by law."
          ]
        },
        intl: {
          title: "6. International Transfers",
          paragraphs: [
            "Your information may be processed in jurisdictions other than your own. We apply appropriate safeguards (such as standard contractual clauses) to protect cross‑border transfers as required by applicable law."
          ]
        },
        retention: {
          title: "7. Data Retention",
          paragraphs: [
            "We retain information for as long as needed to provide the Services, comply with legal obligations, resolve disputes, and enforce our agreements. You may request deletion of certain data from within your account or by contacting us."
          ]
        },
        rights: {
          title: "8. Your Rights and Choices",
          bullets: [
            "Access, correct, or delete certain personal information.",
            "Export data where applicable.",
            "Opt out of marketing communications.",
            "Control cookies via browser settings.",
            "Disable or remove integrations at any time."
          ],
          paragraphs: [
            "Regional rights (e.g., GDPR, CCPA) may apply depending on your location and role. We honor requests in accordance with applicable laws."
          ]
        },
        security: {
          title: "9. Security",
          paragraphs: [
            "We implement industry‑standard security measures, including encryption in transit and at rest, role‑based access controls, and audit logs for enterprise plans. No method of transmission or storage is 100% secure; please use strong credentials and enable SSO where available."
          ]
        },
        children: {
          title: "10. Children’s Privacy",
          paragraphs: [
            "Our Services are not directed to children under 13 (or the age of digital consent in your region). We do not knowingly collect data from children. If you believe a child has provided personal information, contact us to request deletion."
          ]
        },
        changes: {
          title: "11. Changes to this Policy",
          paragraphs: [
            "We may update this Policy from time to time. Material changes will be announced via the website or email. Your continued use of the Services after an update constitutes acceptance of the revised Policy."
          ]
        },
        contact: {
          title: "12. Contact Us",
          paragraphs: [
            "For privacy inquiries, requests, or complaints, contact our team at privacy@pocket.ai."
          ]
        }
      }
    }
    ,
    terms: {
      title: "Terms of Service",
      lastUpdatedLabel: "Last updated",
      sections: {
        intro: {
          title: "1. Introduction",
          paragraphs: [
            "These Terms of Service (\"Terms\") govern your access to and use of Pocket AI Support’s website, products, and services (the \"Services\"). By accessing or using the Services, you agree to be bound by these Terms. If you do not agree, do not use the Services."
          ]
        },
        eligibility: {
          title: "2. Eligibility",
          paragraphs: [
            "You must be at least the age of majority in your jurisdiction and have the authority to bind your organization to these Terms. You represent and warrant that you will use the Services only for lawful purposes."
          ]
        },
        account: {
          title: "3. Account Registration and Security",
          bullets: [
            "Provide accurate and complete information when creating an account.",
            "Maintain the security of your credentials and notify us of any breach.",
            "You are responsible for all activities under your account."
          ]
        },
        use: {
          title: "4. Acceptable Use",
          bullets: [
            "Do not misuse the Services, attempt unauthorized access, or disrupt operations.",
            "Do not upload unlawful, infringing, or harmful content.",
            "Respect usage limits, fair use, and applicable third‑party terms.",
            "Do not use outputs to violate rights, privacy, or applicable law."
          ]
        },
        customerData: {
          title: "5. Customer Data and Privacy",
          paragraphs: [
            "\"Customer Data\" means content you submit to the Services (e.g., SOPs, documents, websites, chat transcripts, and configuration). You retain ownership of Customer Data. We process Customer Data to provide and improve the Services per our Privacy Policy. You are responsible for obtaining all rights and consents required to submit Customer Data."
          ]
        },
        ai: {
          title: "6. AI Outputs and Limitations",
          paragraphs: [
            "AI‑generated outputs may be probabilistic and may contain errors. Use human oversight where material. Outputs are provided ‘as is’ without warranties. Do not rely on outputs for legal, medical, financial, or other professional advice without validation."
          ]
        },
        ip: {
          title: "7. Intellectual Property",
          bullets: [
            "We and our licensors retain all rights, title, and interest in the Services, including software, models, and design elements.",
            "You are granted a limited, non‑exclusive, non‑transferable license to use the Services in accordance with these Terms.",
            "Feedback you provide may be used by us without obligation."
          ]
        },
        billing: {
          title: "8. Billing and Plans",
          paragraphs: [
            "Pricing is described on our website (packages, wage‑based credits, and self‑hosted options). Fees are non‑refundable unless required by law. We may change prices with prior notice. Usage limits and overage policies may apply."
          ]
        },
        integrations: {
          title: "9. Integrations and Third‑Party Services",
          paragraphs: [
            "When you connect third‑party tools (e.g., HubSpot, Slack, WhatsApp), you authorize us to exchange necessary data with those services. Third‑party terms govern your use of their products. We are not responsible for third‑party services."
          ]
        },
        security: {
          title: "10. Security",
          paragraphs: [
            "We implement industry‑standard measures to protect the Services. No system is completely secure. You are responsible for securing your accounts, endpoints, and integration credentials."
          ]
        },
        term: {
          title: "11. Term and Termination",
          bullets: [
            "We may suspend or terminate access for violations of these Terms.",
            "You may stop using the Services at any time.",
            "Upon termination, your right to access the Services ends; certain provisions survive."
          ]
        },
        warranty: {
          title: "12. Disclaimers",
          paragraphs: [
            "THE SERVICES ARE PROVIDED ‘AS IS’ AND ‘AS AVAILABLE’ WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON‑INFRINGEMENT. WE DO NOT WARRANT THAT THE SERVICES WILL BE ERROR‑FREE OR UNINTERRUPTED."
          ]
        },
        liability: {
          title: "13. Limitation of Liability",
          paragraphs: [
            "TO THE MAXIMUM EXTENT PERMITTED BY LAW, NEITHER WE NOR OUR LICENSORS SHALL BE LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS, REVENUE, DATA, OR USE. OUR AGGREGATE LIABILITY WILL NOT EXCEED THE FEES PAID BY YOU FOR THE SERVICES IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM."
          ]
        },
        indemnity: {
          title: "14. Indemnification",
          paragraphs: [
            "You will defend, indemnify, and hold harmless Pocket AI Support and its affiliates from and against claims arising out of your use of the Services or violation of these Terms, including Customer Data you provide and your use of AI outputs."
          ]
        },
        governingLaw: {
          title: "15. Governing Law and Dispute Resolution",
          paragraphs: [
            "These Terms are governed by the laws of the applicable jurisdiction where Pocket AI Support is organized, without regard to conflict of law principles. Disputes will be resolved through good‑faith negotiations; if unresolved, they shall be brought in competent courts of that jurisdiction."
          ]
        },
        changes: {
          title: "16. Changes to these Terms",
          paragraphs: [
            "We may modify these Terms from time to time. Material changes will be communicated via the website or email. Your continued use of the Services after changes become effective constitutes acceptance."
          ]
        },
        contact: {
          title: "17. Contact",
          paragraphs: [
            "Questions about these Terms? Contact our team at legal@pocket.ai."
          ]
        }
      }
    }
    ,
    gdpr: {
      title: "GDPR Compliance",
      lastUpdatedLabel: "Last updated",
      sections: {
        intro: { title: "1. Introduction", paragraphs: [
          "This GDPR Compliance Statement explains how Pocket AI Support processes personal data as a data controller and as a data processor when providing AI‑powered customer service tools to customers in the EEA, UK, and regions with GDPR‑equivalent laws."
        ] },
        roles: { title: "2. Roles and Responsibilities", bullets: [
          "Customer as Controller: you determine purposes and means of processing Customer Data.",
          "Pocket AI Support as Processor: we process Customer Data on your behalf per the DPA.",
          "Pocket AI Support as Controller: for account data, analytics, marketing, and security logs."
        ] },
        data: { title: "3. Personal Data We Process", bullets: [
          "Account data (name, email, preferences, role), authentication identifiers.",
          "Customer Data you submit: SOPs, documents, websites/help centers, chat transcripts.",
          "Product usage telemetry, device/browser info, cookies, and similar technologies.",
          "Integration identifiers and metadata when you connect third‑party services."
        ] },
        lawful: { title: "4. Lawful Bases for Processing (Controller)", bullets: [
          "Contract: to provide and improve the Services you subscribe to.",
          "Legitimate interests: product security, fraud prevention, analytics.",
          "Consent: marketing communications and certain cookies where required.",
          "Legal obligation: compliance and regulatory requirements."
        ] },
        purposes: { title: "5. Purposes of Processing (Processor)", bullets: [
          "Enable AI agents to respond, retrieve, and cite from connected sources.",
          "Operate features you configure (CRM, routing, reports, integrations).",
          "Maintain reliability, quality, and security of the platform.",
          "Provide support, incident response, and service communications."
        ] },
        rights: { title: "6. Data Subject Rights", paragraphs: [
          "We support GDPR rights: access, rectification, erasure, restriction, objection, and portability. For Customer Data, submit requests to your organization (controller). For data we control, contact us directly."
        ] },
        transfers: { title: "7. International Transfers", paragraphs: [
          "For transfers outside the EEA/UK, we apply safeguards such as Standard Contractual Clauses and additional security measures."
        ] },
        retention: { title: "8. Data Retention", paragraphs: [
          "We retain data only as long as necessary to deliver the Services, meet legal obligations, resolve disputes, and enforce agreements. You can request deletion or export of certain data per your plan and configuration."
        ] },
        security: { title: "9. Security Measures", bullets: [
          "Encryption in transit and at rest for eligible plans.",
          "Role‑based access controls, audit logs, and environment isolation.",
          "Vulnerability management and incident response procedures.",
          "Subprocessor reviews and contractual safeguards."
        ] },
        subprocessors: { title: "10. Subprocessors", paragraphs: [
          "We use vetted subprocessors for infrastructure, analytics, and communications. A current list is available upon request and subject to contractual safeguards."
        ] },
        cookies: { title: "11. Cookies and Similar Technologies", paragraphs: [
          "We use necessary cookies to operate the site and, where required, obtain consent for analytics/marketing cookies. Manage preferences via your browser or our tools."
        ] },
        dpo: { title: "12. Contact and DPO", paragraphs: [
          "For privacy inquiries, data subject requests, or DPA questions, contact privacy@pocket.ai. If a Data Protection Officer (DPO) is appointed, their details will be provided upon request."
        ] },
        updates: { title: "13. Updates to this Statement", paragraphs: [
          "We may update this GDPR Statement. Material changes will be announced via the website or email. Continued use after updates constitutes acceptance."
        ] }
      }
    }
    ,
    compliance: {
      title: "Compliance",
      lastUpdatedLabel: "Last updated",
      sections: {
        overview: { title: "1. Overview", paragraphs: [
          "We are committed to compliance with applicable regulations and industry standards for security, privacy, and operational governance. This page summarizes our approach across key domains."
        ] },
        security: { title: "2. Security & Access Controls", bullets: [
          "Encryption in transit and at rest (eligible plans).",
          "Role‑based access control (RBAC) and least‑privilege.",
          "Audit logs, environment segregation, secret management.",
          "Vulnerability scanning, patching, and incident response."
        ] },
        privacy: { title: "3. Privacy & Data Protection", bullets: [
          "GDPR support with DPA; regional data handling on request.",
          "Configurable data retention, deletion, and export.",
          "Optional self‑hosted deployments for greater control.",
          "No model training on private content unless you opt‑in."
        ] },
        regulatory: { title: "4. Regulatory & Framework Alignment", paragraphs: [
          "We align practices with recognized frameworks and regulations. Certifications may vary by plan and deployment. Documentation available upon request."
        ], bullets: [
          "GDPR (EU/UK): rights, SCCs for transfers.",
          "Security frameworks: SOC 2 controls alignment (where applicable).",
          "Privacy by design: minimization, purpose limitation, access controls."
        ] },
        responsibleAI: { title: "5. Responsible AI", bullets: [
          "Human‑in‑the‑loop for sensitive workflows.",
          "Guardrails, retrieval with citations, auditability.",
          "Abuse prevention and rate limiting.",
          "Continuous evaluation of model behavior and safety."
        ] },
        incident: { title: "6. Incident Response & Reporting", paragraphs: [
          "Procedures include detection, containment, investigation, remediation, and customer notification for material incidents."
        ] },
        subprocessors: { title: "7. Vendors & Subprocessors", paragraphs: [
          "We use vetted vendors with data protection agreements and security reviews. A current list is available on request."
        ] },
        requests: { title: "8. Requests & Documentation", paragraphs: [
          "For security questionnaires, DPA/SCCs, or compliance documentation, contact compliance@pocket.ai."
        ] },
        updates: { title: "9. Updates", paragraphs: [
          "We periodically update controls and this page to reflect evolving standards and product changes. Continued use indicates acceptance."
        ] }
      }
    }
    ,
    cookies: {
      title: "Cookie Policy",
      lastUpdatedLabel: "Last updated",
      sections: {
        intro: { title: "1. Introduction", paragraphs: [
          "This Cookie Policy explains how Pocket AI Support uses cookies and similar technologies on our website and Services. Read with our Privacy Policy and Terms."
        ] },
        what: { title: "2. What Are Cookies?", paragraphs: [
          "Cookies are small text files placed on your device to store data read by the site. They help remember preferences and improve security and performance."
        ] },
        types: { title: "3. Types of Cookies We Use", bullets: [
          "Strictly necessary: essential for core functionality.",
          "Preferences: remember choices like language and theme.",
          "Analytics: understand usage to improve performance and UX.",
          "Marketing: deliver relevant content and measure campaigns."
        ] },
        how: { title: "4. How We Use Cookies", bullets: [
          "Maintain session state, security controls, and load balancing.",
          "Store language and theme preferences.",
          "Measure page performance and feature adoption.",
          "Support integrations you opt in to."
        ] },
        thirdParty: { title: "5. Third‑Party Cookies", paragraphs: [
          "Some cookies are set by providers for analytics, embedded content, or integrations you enable. We require appropriate safeguards."
        ] },
        manage: { title: "6. Managing Cookies", bullets: [
          "Adjust your browser settings to accept, refuse, or delete cookies.",
          "Blocking some cookies may impact functionality.",
          "Use on‑site controls where available to update preferences."
        ] },
        consent: { title: "7. Consent", paragraphs: [
          "Where required by law, we request your consent for non‑essential cookies. You may withdraw or modify consent at any time."
        ] },
        changes: { title: "8. Changes to this Policy", paragraphs: [
          "We may update this Cookie Policy. Material changes will be communicated on this page or via email."
        ] },
        contact: { title: "9. Contact", paragraphs: [
          "Questions about cookies? Contact privacy@pocket.ai."
        ] }
      }
    }
    ,
    security: {
      title: "Security",
      lastUpdatedLabel: "Last updated",
      sections: {
        overview: { title: "1. Overview", paragraphs: [
          "Pocket AI Support is built with a security‑first approach combining encryption, rigorous access controls, continuous monitoring, and secure development practices."
        ] },
        encryption: { title: "2. Encryption", bullets: [
          "TLS for data in transit using modern ciphers.",
          "Encryption at rest for eligible plans and environments.",
          "Key management aligned with cloud provider best practices."
        ] },
        access: { title: "3. Access Control & Authentication", bullets: [
          "Least‑privilege, role‑based access control (RBAC).",
          "SSO/SAML/OIDC support on enterprise plans.",
          "Hardened admin access; MFA required for privileged actions."
        ] },
        data: { title: "4. Data Protection & Residency", bullets: [
          "Configurable retention and deletion workflows.",
          "Optional data residency controls for eligible plans.",
          "No training of foundation models on private content unless you opt in."
        ] },
        network: { title: "5. Network & Infrastructure Security", bullets: [
          "Segregated environments with security groups and firewalls.",
          "DDoS protections and rate limiting for public interfaces.",
          "Backups and restoration checks for resilience."
        ] },
        appsec: { title: "6. Application Security", bullets: [
          "Secure SDLC with reviews and dependency scanning.",
          "Vulnerability management and routine patching.",
          "Security testing and hardening of critical components."
        ] },
        incident: { title: "7. Incident Response", paragraphs: [
          "Procedures include detection, triage, containment, remediation, and post‑incident review with customer notification for material incidents."
        ] },
        compliance: { title: "8. Compliance & Audits", paragraphs: [
          "Controls align with industry frameworks (e.g., SOC 2 control families where applicable). Certifications may vary by plan and deployment; documentation available on request."
        ] },
        customer: { title: "9. Customer Responsibilities", bullets: [
          "Configure roles, permissions, and SSO per your policies.",
          "Manage access to connected integrations and data sources.",
          "Use strong credentials and protect endpoint devices."
        ] },
        contact: { title: "10. Contact", paragraphs: [
          "Security questions or questionnaires? Contact security@pocket.ai."
        ] }
      }
    }
  },
};

export default en;
