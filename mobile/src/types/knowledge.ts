export type SourceKind = 'upload' | 'url' | 'note'
export type SourceScope = 'global' | `agent:${string}`
export type SourceStatus = 'idle' | 'training' | 'trained' | 'error'

export interface BaseSource {
  id: string
  kind: SourceKind
  title: string
  enabled: boolean
  scope: SourceScope
  status: SourceStatus
  lastTrainedTs?: number
  estSizeKB?: number
}

export interface UrlSource extends BaseSource {
  kind: 'url'
  url: string
  crawlDepth: number
  allowPaths?: string[]
  blockPaths?: string[]
  respectRobots?: boolean
}

export interface UploadSource extends BaseSource {
  kind: 'upload'
  filename: string
  mime: string
  sizeKB: number
  pages?: number
  hash?: string
}

export interface NoteSource extends BaseSource {
  kind: 'note'
  content: string
  tags?: string[]
}

export type KnowledgeSource = UrlSource | UploadSource | NoteSource

export interface TrainingJob {
  id: string
  sourceId: string
  progress: number
  state: 'queued' | 'running' | 'completed' | 'failed'
  startedAt: number
  finishedAt?: number
  message?: string
}

export interface CoverageStat { topics: number; faqs: number; gaps: number; coveragePct: number }

export interface FailureItem {
  id: string
  question: string
  matchedIntent?: string
  confidence: number
  occurredAt: number
  channel?: string
  conversationId?: string
  suggestedSourceKind?: SourceKind
}

export interface TestCase { id: string; question: string; expectedAnswer?: string; tags?: string[]; lastRunAt?: number; lastPass?: boolean }

export interface RedactionRule { id: string; pattern: string; enabled: boolean; sample?: string; description?: string }

export interface DriftWarning { sourceId: string; kind: 'stale_url' | 'upload_changed' | 'domain_unreachable'; detail?: string; detectedAt: number }


