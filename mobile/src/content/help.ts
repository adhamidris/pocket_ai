export type HelpCategory = 'Getting Started' | 'Agents' | 'Conversations' | 'CRM' | 'Billing' | 'Troubleshooting' | 'Legal'

export type HelpArticle = {
  id: string
  title: string
  category: HelpCategory
  keywords: string[]
  body: string[] // paragraphs
}

export const helpArticles: HelpArticle[] = [
  {
    id: 'getting-started-overview',
    title: 'Getting Started Overview',
    category: 'Getting Started',
    keywords: ['onboarding', 'setup', 'profile', 'business'],
    body: [
      'Create your account, complete your business profile, and upload knowledge sources to get value quickly.',
      'You can skip non‑essential steps and return later from Settings > Profile.',
    ],
  },
  {
    id: 'agent-create',
    title: 'Create and Configure an Agent',
    category: 'Agents',
    keywords: ['agents', 'persona', 'tools', 'knowledge'],
    body: [
      'Go to Agents and tap Create. Choose a persona, tone, and enable tools like Knowledge, CRM, or Integrations.',
      'Attach knowledge sources and test replies before publishing.',
    ],
  },
  {
    id: 'conversations-inbox',
    title: 'Working in Conversations',
    category: 'Conversations',
    keywords: ['cases', 'inbox', 'filters', 'search'],
    body: [
      'Use filters to focus by status, date, or agent. Search supports customer name, phone, and text.',
      'Open a case to view context, AI diagnosis, and recent activity.',
    ],
  },
  {
    id: 'crm-basics',
    title: 'CRM Basics',
    category: 'CRM',
    keywords: ['customers', 'segments', 'insights'],
    body: [
      'The CRM shows customer profiles, tags, and insights. Use Segments to group customers, and open a profile to see last contact and value.',
    ],
  },
  {
    id: 'billing-plans',
    title: 'Plans and Billing',
    category: 'Billing',
    keywords: ['subscription', 'upgrade', 'payment'],
    body: [
      'Manage your plan from Settings > Subscription. Upgrade or change billing details as needed.',
      'Usage bars show current consumption for key resources.',
    ],
  },
  {
    id: 'troubleshooting-accuracy',
    title: 'Improve Answer Accuracy',
    category: 'Troubleshooting',
    keywords: ['answers', 'kb', 'grounding'],
    body: [
      'Ensure your knowledge base is up to date and specific. Add FAQs and examples for common edge cases.',
      'Use clear, concise language and verify citations where applicable.',
    ],
  },
  {
    id: 'troubleshooting-connectivity',
    title: 'Connectivity Issues',
    category: 'Troubleshooting',
    keywords: ['network', 'offline', 'login'],
    body: [
      'If the app appears offline, check your connection and try again. If the issue persists, contact support with diagnostics.',
    ],
  },
  {
    id: 'legal-privacy',
    title: 'Privacy and Terms',
    category: 'Legal',
    keywords: ['privacy', 'terms'],
    body: [
      'You can review our Privacy Policy and Terms under Settings > Privacy & Terms.',
    ],
  },
]

