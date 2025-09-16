const en = {
  // Navigation
  nav: {
    dashboard: "Home",
    conversations: "Cases", 
    crm: "Customers",
    agents: "Agents",
    settings: "Settings"
  },

  // Common
  common: {
    continue: "Continue",
    skip: "Skip",
    getStarted: "Get Started",
    back: "Back",
    next: "Next",
    done: "Done",
    cancel: "Cancel",
    save: "Save",
    edit: "Edit",
    delete: "Delete",
    loading: "Loading...",
    error: "Error",
    retry: "Retry",
    refresh: "Refresh",
    or: "OR",
    upload: "Upload"
  },

  // Onboarding
  onboarding: {
    welcome: {
      title: "AI-Powered\nCustomer Service",
      subtitle: "Deliver instant, intelligent support 24/7 with our AI-powered platform",
      ctaStart: "Start onboarding",
      ctaSkipLogin: "Skip to login"
    },
    howItWorks: {
      title: "It is simple!",
      steps: [
        {
          title: "Register",
          bullets: [
            "Set up business profile",
            "Upload SOP/Knowledge Base",
            "Or let AI generate it"
          ]
        },
        {
          title: "Create your AI Agent", 
          bullets: [
            "Configure persona and tools",
            "Get your portal link"
          ]
        },
        {
          title: "Paste, paste, paste",
          bullets: [
            "Paste across web, socials, WhatsApp",
            "Sit back and monitor"
          ]
        }
      ]
      ,
      carousel: {
        titles: [
          "Register",
          "Set up your business profile",
          "Upload your knowledge base"
        ],
        demo: {
          register: [
            "Creating account…",
            "Typing name and email…",
            "Submitting…",
            "Account created"
          ],
          profile: [
            "Opening profile setup…",
            "Filling company name, industry, address…",
            "Saving…",
            "Profile saved"
          ],
          upload: [
            "Starting upload…",
            "Uploading files…",
            "Processing…",
            "Upload complete"
          ]
        }
      }
    },
    chatPreview: {
      title: "See it in action",
      subtitle: "Experience a live conversation"
    },
    skipLogin: {
      tagline: "Your clients, served better."
    },
    shareAnywhere: {
      message: "Hello there! I'm your AI-Powered assistant, thank you for creating me. I'm ready to serve your clients anywhere you'll place me.\nJust pin my link anywhere!",
      cta: "See me in action"
    },
    
  },
  auth: {
    email: "Email",
    password: "Password",
    confirm: "Confirm",
    login: "Login",
    continueApple: "Continue with Apple",
    continueGoogle: "Continue with Google",
    fullName: "Full name"
  },
  profile: {
    companyName: "Company name",
    industry: "Industry",
    address: "Address"
  },
  languages: {
    english: "English",
    arabic: "Arabic",
    spanish: "Spanish",
    french: "French"
  },

  // Dashboard
  dashboard: {
    title: "Home",
    welcome: "Welcome back",
    stats: {
      activeConversations: "Active Conversations",
      todayMessages: "Messages Today",
      responseTime: "Avg Response Time",
      satisfaction: "Satisfaction Rate"
    },
    recentActivity: "Recent Activity",
    quickActions: "Quick Actions",
    noActivity: "No recent activity"
  },

  // Conversations
  conversations: {
    title: "Cases",
    active: "Active", 
    archived: "Archived",
    all: "All",
    noConversations: "No conversations yet",
    startConversation: "Start conversation",
    searchPlaceholder: "Search cases...",
    filters: {
      agent: "Agent",
      status: "Status", 
      date: "Date"
    }
  },

  // CRM
  crm: {
    title: "Customers",
    customers: "Customers",
    segments: "Segments",
    insights: "Insights",
    noCustomers: "No customers yet",
    searchPlaceholder: "Search customers...",
    addCustomer: "Add Customer",
    customerProfile: "Customer Profile",
    activity: "Activity",
    notes: "Notes",
    tags: "Tags"
  },

  // Agents
  agents: {
    title: "Agents",
    active: "Active",
    inactive: "Inactive",
    createAgent: "Create Agent",
    editAgent: "Edit Agent",
    deleteAgent: "Delete Agent",
    noAgents: "No agents created yet",
    configuration: "Configuration",
    persona: "Persona",
    tools: "Tools",
    knowledgeBase: "Knowledge Base"
  },

  // Settings
  settings: {
    title: "Settings",
    sections: {
      account: "Account",
      subscription: "Subscription", 
      integrations: "Integrations",
      notifications: "Notifications",
      support: "Support"
    },
    account: {
      profile: "Profile",
      security: "Security",
      preferences: "Preferences"
    },
    subscription: {
      currentPlan: "Current Plan",
      usage: "Usage",
      billing: "Billing",
      upgrade: "Upgrade Plan"
    }
  },

  // Demo conversation scenarios
  demo: {
    online: "Online",
    inputPlaceholder: "This is a demo - try the real widget below! →",
    scenarios: [
      {
        agentName: "Nancy",
        jobTitle: "E-Commerce Support Agent",
        conversation: [
          { id: 1, text: "Hi! I need help with my order #12345", isBot: false, delay: 1000 },
          { id: 2, text: "Hello, I’m Nancy. I’d be glad to help. I’ll check that order now.", isBot: true, delay: 1400 },
          { id: 3, text: "Thanks for waiting. Your order shipped yesterday and should arrive tomorrow by 3 PM. Tracking: TR123456789.", isBot: true, delay: 1600 },
          { id: 4, text: "Perfect. Can I change the delivery address?", isBot: false, delay: 1200 },
          { id: 5, text: "Certainly. What is the new address?", isBot: true, delay: 1200 },
          { id: 6, text: "123 New Street, Los Angeles, CA 90210", isBot: false, delay: 1300 },
          { id: 7, text: "All set. I’ve updated the address. Anything else I can assist with today?", isBot: true, delay: 1400 },
          { id: 8, text: "No, that’s all. Thank you, Nancy.", isBot: false, delay: 1100 },
          { id: 9, text: "You’re welcome. Happy to help.", isBot: true, delay: 1400 }
        ]
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
          { id: 7, text: "Anytime. If anything else comes up, I’m here to help.", isBot: true, delay: 1400 }
        ]
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
          { id: 10, text: "You’re welcome. I’m here if you need anything else.", isBot: true, delay: 1400 }
        ]
      }
    ]
  }
}

export default en
