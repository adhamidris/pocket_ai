import { Tour } from '../../types/help'

export const defaultTours: Tour[] = [
  {
    id: 'tour-dashboard',
    title: 'Dashboard Tour',
    completed: false,
    eligible: true,
    surface: 'dashboard',
    steps: [
      { id: 's1', anchorTestId: 'kpi-frt', title: 'KPIs', body: 'Track your key performance indicators here.', placement: 'bottom' },
      { id: 's2', anchorTestId: 'quick-actions', title: 'Quick Actions', body: 'Common one-tap actions.', placement: 'top' },
      { id: 's3', anchorTestId: 'industry-tiles', title: 'Industry tiles', body: 'Deep links tailored to your business.', placement: 'top' },
    ]
  },
  { id: 'tour-conversations', title: 'Conversations Tour', completed: false, eligible: true, surface: 'conversations', steps: [ { id: 's1', anchorTestId: 'conv-list', title: 'Inbox', body: 'Your conversations appear here.', placement: 'right' } ] },
  { id: 'tour-knowledge', title: 'Knowledge Tour', completed: false, eligible: true, surface: 'knowledge', steps: [ { id: 's1', anchorTestId: 'knowledge-train', title: 'Training Center', body: 'Train your assistant.', placement: 'bottom' } ] },
  { id: 'tour-channels', title: 'Channels Tour', completed: false, eligible: true, surface: 'channels', steps: [ { id: 's1', anchorTestId: 'channels-home', title: 'Connect Channels', body: 'Add WhatsApp, IG, Web.', placement: 'bottom' } ] },
  { id: 'tour-automations', title: 'Automations Tour', completed: false, eligible: true, surface: 'automations', steps: [ { id: 's1', anchorTestId: 'rules-list', title: 'Rules', body: 'Create automation rules.', placement: 'bottom' } ] },
  { id: 'tour-analytics', title: 'Analytics Tour', completed: false, eligible: true, surface: 'analytics', steps: [ { id: 's1', anchorTestId: 'analytics-overview', title: 'Overview', body: 'Visualize performance.', placement: 'bottom' } ] },
  { id: 'tour-portal', title: 'Portal Tour', completed: false, eligible: true, surface: 'portal', steps: [ { id: 's1', anchorTestId: 'portal-preview', title: 'Preview', body: 'See your hosted portal.', placement: 'bottom' } ] },
]


