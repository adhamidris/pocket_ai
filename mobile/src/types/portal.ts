export type Sender = 'customer'|'ai'|'agent'|'system'
export type PartKind = 'text'|'image'|'file'|'card'|'buttons'|'list'|'divider'|'notice'

export interface MsgPartText { kind: 'text'; text: string; markdown?: boolean }
export interface MsgPartImage { kind: 'image'; url: string; alt?: string }
export interface MsgPartFile { kind: 'file'; name: string; sizeKB: number; mime: string; url?: string }
export interface MsgPartButtons { kind: 'buttons'; actions: { id: string; label: string; style?: 'primary'|'secondary'|'link' }[] }
export interface MsgPartCard { kind: 'card'; title: string; subtitle?: string; body?: string; mediaUrl?: string; actions?: MsgPartButtons['actions'] }
export interface MsgPartList { kind: 'list'; items: { id: string; title: string; subtitle?: string; rightLabel?: string }[] }
export interface MsgPartDivider { kind: 'divider'; label?: string }
export interface MsgPartNotice { kind: 'notice'; tone: 'info'|'warn'|'success'|'error'; text: string }
export type MsgPart = MsgPartText|MsgPartImage|MsgPartFile|MsgPartButtons|MsgPartCard|MsgPartList|MsgPartDivider|MsgPartNotice

export interface ChatMessage { id: string; at: number; sender: Sender; parts: MsgPart[]; meta?: { delivered?: boolean; read?: boolean } }

export interface ComposerState { text: string; attachments: MsgPartFile[]; disabled?: boolean; placeholder?: string }

export type SessionState = 'connecting'|'connected'|'queued'|'offline'|'ended'
export interface QueueInfo { position: number; etaMins?: number }

export interface PrechatField { key: string; label: string; type: 'text'|'email'|'phone'|'select'; required?: boolean; options?: string[] }
export interface PrechatForm { title: string; fields: PrechatField[] }

export type ConsentState = 'unknown'|'granted'|'denied'
export interface PortalPrefs { themeId?: string; locale: string; timezone: string; allowUploads: boolean; allowVoice: boolean; showQuickReplies: boolean }

export interface CsatForm { rating?: 1|2|3|4|5; comment?: string }


