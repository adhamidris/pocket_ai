import React from 'react'
import { DeviceEventEmitter } from 'react-native'
import { useNavigation, useNavigationState } from '@react-navigation/native'
import { Survey } from '../../types/help'
import { SurveyModal } from '../../components/help/SurveyModal'
import { track } from '../../lib/analytics'

const STORAGE_KEY = 'help.surveys'

type Stored = { lastShown: Record<string, number>; results: Array<{ id: string; value: number | string; at: number }> }

const loadStore = async (): Promise<Stored> => {
  try {
    const raw = await (require('@react-native-async-storage/async-storage') as any).default.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return { lastShown: {}, results: [] }
}

const saveStore = async (s: Stored) => {
  try { await (require('@react-native-async-storage/async-storage') as any).default.setItem(STORAGE_KEY, JSON.stringify(s)) } catch {}
}

const dayMs = 86400000

const surveys: Survey[] = [
  { id: 'nps-day7', name: 'How likely to recommend us?', trigger: 'time', question: 'Rate 0–10', scale: 'nps', dismissible: true, cooldownDays: 14 },
  { id: 'csat-first-rule', name: 'How easy was creating your first rule?', trigger: 'action', question: 'Rate 1–5', scale: 'csat', dismissible: true, cooldownDays: 90 },
  { id: 'likert-portal', name: 'How useful was the portal test?', trigger: 'action', question: 'Agree or disagree', scale: 'likert', options: ['Strongly disagree','Disagree','Neutral','Agree','Strongly agree'], dismissible: true, cooldownDays: 90 },
]

export const SurveyManager: React.FC = () => {
  const [visible, setVisible] = React.useState(false)
  const [current, setCurrent] = React.useState<Survey | null>(null)
  const nav = useNavigation<any>()

  // Track last shown & results
  const storeRef = React.useRef<Stored>({ lastShown: {}, results: [] })
  React.useEffect(() => { (async () => { storeRef.current = await loadStore() })() }, [])

  // Time-based trigger: show NPS at day 7 if not shown in last cooldown
  React.useEffect(() => {
    const timer = setInterval(async () => {
      const s = storeRef.current
      const last = s.lastShown['nps-day7'] || 0
      const installedAt = Number(globalThis['__installedAt'] || Date.now() - 7 * dayMs)
      const due = Date.now() - installedAt >= 7 * dayMs
      const cooled = Date.now() - last >= (surveys[0].cooldownDays * dayMs)
      if (due && cooled && !visible && !current) {
        setCurrent(surveys[0])
        setVisible(true)
        s.lastShown['nps-day7'] = Date.now()
        await saveStore(s)
      }
    }, 30000)
    return () => clearInterval(timer)
  }, [visible, current])

  // Action-based: after first rule save CSAT
  React.useEffect(() => {
    const sub = DeviceEventEmitter.addListener('rule.saved', async () => {
      const s = storeRef.current
      const last = s.lastShown['csat-first-rule'] || 0
      if (Date.now() - last >= surveys[1].cooldownDays * dayMs) {
        setCurrent(surveys[1])
        setVisible(true)
        s.lastShown['csat-first-rule'] = Date.now()
        await saveStore(s)
      }
    })
    return () => sub.remove()
  }, [])

  // Action-based: after portal test likert
  React.useEffect(() => {
    const sub = DeviceEventEmitter.addListener('portal.tested', async () => {
      const s = storeRef.current
      const last = s.lastShown['likert-portal'] || 0
      if (Date.now() - last >= surveys[2].cooldownDays * dayMs) {
        setCurrent(surveys[2])
        setVisible(true)
        s.lastShown['likert-portal'] = Date.now()
        await saveStore(s)
      }
    })
    return () => sub.remove()
  }, [])

  return current ? (
    <SurveyModal
      visible={visible}
      survey={current}
      onClose={() => setVisible(false)}
      onSubmit={async (value) => {
        const s = storeRef.current
        s.results.push({ id: current.id, value, at: Date.now() })
        await saveStore(s)
        try { track('help.survey.submit', { id: current.id, score: value }) } catch {}
        try { (require('../../hooks/use-toast') as any).toast?.({ title: 'Thanks for your feedback!' }) } catch {}
      }}
    />
  ) : null
}

export default SurveyManager


