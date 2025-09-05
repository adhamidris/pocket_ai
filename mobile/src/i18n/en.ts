export default {
  home: {
    ctaStart: 'Get Started',
    ctaSignIn: 'Sign In'
  },
  hero: {
    title: 'It is simple!',
    subtitle: 'Deliver instant, intelligent support 24/7 with our AI-powered platform.'
  },
  howItWorks: {
    steps: [
      { title: 'Register', bullets: ['Set up business profile', 'Upload SOP / Knowledge Base', 'Or let AI generate it'] },
      { title: 'Create your AI Agent', bullets: ['Configure persona and tools', 'Get your portal link'] },
      { title: 'Paste, paste, paste', bullets: ['Paste across web, socials, WhatsApp', 'Sit back & monitor'] },
    ]
  },
  featuresSnap: {
    title: 'Key features',
    items: [
      'Intelligent AI Assistant',
      'Lightning Fast Responses',
      'Enterprise Security',
      'Advanced Analytics'
    ]
  },
  testimonials: {
    title: 'What customers say',
    items: [
      { quote: 'Response times dropped from minutes to seconds. Our CSAT is up 25%.', author: 'Maya Patel', role: 'Head of Support, Flowify' },
      { quote: 'We automated updates and refunds in days. Tickets went down—revenue up.', author: 'Alex Romero', role: 'COO, CloudCart' },
      { quote: 'Setup was insanely fast. The chat feels native to our brand.', author: 'Sofia Nguyen', role: 'VP Marketing, BrightClinic' },
      { quote: 'The analytics unlocked patterns we couldn’t see before.', author: 'Daniel Kim', role: 'Operations Lead, Loop' }
    ]
  },
  pricing: {
    title: 'Simple pricing',
    perMonth: '/mo',
    getStarted: 'Get started',
    seeFull: 'See full pricing',
    billing: { monthly: 'Monthly', yearly: 'Yearly', yearlyNote: 'billed yearly' },
    packages: [
      { tier: 'Plus', monthly: 29, yearly: 24, features: ['1 agent', 'Branded chat portal', 'Core knowledge base'] },
      { tier: 'Pro', monthly: 89, yearly: 69, features: ['Up to 3 agents', 'Advanced KB + citations', 'Workflows & tools'] },
      { tier: 'Enterprise', monthly: 249, yearly: 199, features: ['Unlimited agents', 'SSO & audit logs', 'Custom routing'] }
    ]
  },
  demo: {
    scenarios: [
      {
        agentName: 'Nancy', jobTitle: 'E‑Commerce Support Agent',
        conversation: [
          { id: 1, text: 'Hi! I need help with my order #12345', isBot: false, delay: 1000 },
          { id: 2, text: 'Hello, I’m Nancy. I’d be glad to help. I’ll check that order now.', isBot: true, delay: 1400 },
          { id: 3, text: 'Thanks for waiting. Your order shipped yesterday and should arrive tomorrow by 3 PM.', isBot: true, delay: 1600 }
        ]
      }
    ]
  }
}
