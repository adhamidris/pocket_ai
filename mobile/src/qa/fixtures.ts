import { DeviceEventEmitter } from 'react-native'
import { ConversationSummary, Priority, SLAState, Channel } from '../types/conversations'
import { Contact, ConsentState } from '../types/crm'

export type FixtureProfile = 'baseline' | 'highVolume' | 'lowData' | 'edgeCases'

type Fixtures = {
  conversations: ConversationSummary[]
  contacts: Contact[]
}

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown', 'Ava Davis', 'Sophia Wilson', 'Olivia Martin', 'Lucas Lee', 'Mia Clark', 'Amelia Walker']
const tagsPool = ['Billing', 'VIP', 'Technical', 'Order', 'Returns', 'Onboarding', 'Shipping']
const channels: Channel[] = ['whatsapp', 'instagram', 'facebook', 'web', 'email']
const slaStates: SLAState[] = ['ok', 'risk', 'breach']
const consents: ConsentState[] = ['granted', 'denied', 'unknown', 'withdrawn']

const genConversations = (n: number, opts?: { edge?: boolean }): ConversationSummary[] => {
  const items: ConversationSummary[] = []
  for (let i = 0; i < n; i++) {
    const name = opts?.edge ? `${pick(names)} ${'😀'.repeat(i % 3)}‎‏` : pick(names)
    const waiting = rand(0, 120)
    const priority: Priority = waiting > 60 ? 'high' : pick(['low', 'normal', 'vip'])
    const sla: SLAState = waiting > 45 ? pick(['risk', 'breach']) : pick(slaStates)
    items.push({
      id: `c-${i + 1}`,
      customerName: name,
      lastMessageSnippet: pick(['I need help with my order', 'Payment charged twice', 'Where is my package?', 'Account access issue', 'Can I change my address?']) + (opts?.edge ? ' 🚚📦' : ''),
      lastUpdatedTs: Date.now() - rand(0, 1000 * 60 * 60 * 24),
      channel: pick(channels),
      tags: Array.from(new Set([pick(tagsPool), pick(tagsPool)])).slice(0, rand(1, 2)),
      assignedTo: Math.random() > 0.6 ? pick(['Nancy', 'Jack', 'You']) : undefined,
      priority,
      waitingMinutes: waiting,
      sla,
      lowConfidence: Math.random() > 0.75,
    })
  }
  return items
}

const genContacts = (n: number, opts?: { edge?: boolean }): Contact[] => {
  const items: Contact[] = []
  for (let i = 0; i < n; i++) {
    const baseName = `${pick(names)} ${i + 1}`
    const name = opts?.edge ? `${baseName} ‎‏‏‎` : baseName
    const email = Math.random() > 0.4 ? `${name.toLowerCase().replace(/\s+/g, '.').replace(/[^a-z0-9.]/g, '')}@example.com` : undefined
    const phone = Math.random() > 0.6 ? `+1-555-${rand(100, 999)}-${rand(1000, 9999)}` : undefined
    const chs = Array.from(new Set([pick(channels), pick(channels), pick(channels)])).slice(0, rand(1, 3))
    const tags = Array.from(new Set([pick(tagsPool), pick(tagsPool), pick(tagsPool)])).slice(0, rand(1, 3))
    const vip = Math.random() > 0.85
    const last = Date.now() - rand(0, 1000 * 60 * 60 * 24 * 30)
    const ltv = Math.random() > 0.5 ? rand(100, 20000) : undefined
    const consent = pick(consents)
    items.push({ id: `ct-${i + 1}`, name, email, phone, channels: chs, tags, vip, lastInteractionTs: last, lifetimeValue: ltv, consent })
  }
  return items
}

let currentProfile: FixtureProfile = 'baseline'
let cache: Fixtures = {
  conversations: genConversations(25),
  contacts: genContacts(200),
}

export const getProfile = (): FixtureProfile => currentProfile
export const getFixtures = (): Fixtures => cache

export const setProfile = (profile: FixtureProfile) => {
  currentProfile = profile
  switch (profile) {
    case 'baseline':
      cache = { conversations: genConversations(25), contacts: genContacts(200) }
      break
    case 'highVolume':
      cache = { conversations: genConversations(120), contacts: genContacts(2000) }
      break
    case 'lowData':
      cache = { conversations: genConversations(3), contacts: genContacts(10) }
      break
    case 'edgeCases':
      cache = { conversations: genConversations(40, { edge: true }), contacts: genContacts(400, { edge: true }) }
      break
  }
  try { DeviceEventEmitter.emit('qa.fixtures.changed', { profile }) } catch {}
}

// Initialize deterministic-ish randomness for repeatability across reloads
// Note: Using Math.random is acceptable for demo; for stronger determinism, inject a seedable RNG.


