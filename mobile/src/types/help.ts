export type ArticleKind = 'guide'|'howto'|'faq'|'troubleshoot'|'release'
export interface Article { id: string; kind: ArticleKind; title: string; bodyMd: string; tags: string[]; updatedAt: number; relatedIds?: string[] }

export interface ChecklistStep { id: string; title: string; done: boolean; cta?: { label: string; route: string; params?: Record<string, any> } }
export interface Checklist { id: string; title: string; steps: ChecklistStep[]; progress: number }

export interface TourStep { id: string; anchorTestId: string; title: string; body: string; placement: 'top'|'bottom'|'left'|'right'|'auto' }
export interface Tour { id: string; title: string; steps: TourStep[]; completed: boolean; eligible: boolean; surface: 'dashboard'|'conversations'|'knowledge'|'channels'|'automations'|'analytics'|'settings'|'portal'|'actions' }

export interface Nudge { id: string; text: string; route?: string; testId?: string; dismissible: boolean; priority: 'low'|'med'|'high' }

export interface ReleaseNote { id: string; version: string; dateIso: string; highlights: { title: string; body: string; route?: string }[] }

export interface FeedbackItem { id: string; text: string; contact?: string; context?: string; rating?: 1|2|3|4|5; createdAt: number }

export interface Survey { id: string; name: string; trigger: 'time'|'screen'|'action'; question: string; scale?: 'nps'|'csat'|'likert'; options?: string[]; dismissible: boolean; cooldownDays: number }

export interface HelpState { seenVersions: string[]; completedTours: string[]; dismissedNudges: string[]; checklists: Checklist[] }


