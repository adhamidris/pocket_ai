import { AllowRule } from '../types/actions'

type EvalResult = { ok: boolean; warnings?: string[]; errors?: string[]; rateLimited?: boolean }

const rateBuckets: Record<string, number[]> = {}

const isWithinBusinessHours = (rule?: AllowRule): boolean => {
  if (!rule?.timeOfDay || !rule?.weekdays) return true
  try {
    const now = new Date()
    const day = now.getDay()
    if (!rule.weekdays.includes(day)) return false
    const [sh, sm] = (rule.timeOfDay.start || '00:00').split(':').map(Number)
    const [eh, em] = (rule.timeOfDay.end || '23:59').split(':').map(Number)
    const start = new Date(now)
    start.setHours(sh || 0, sm || 0, 0, 0)
    const end = new Date(now)
    end.setHours(eh || 23, em || 59, 59, 999)
    return now >= start && now <= end
  } catch {
    return true
  }
}

const checkRateLimit = (actionId: string, rate?: { count: number; perSeconds: number }): boolean => {
  if (!rate) return false
  const now = Date.now()
  const windowStart = now - rate.perSeconds * 1000
  const arr = (rateBuckets[actionId] ||= [])
  const recent = arr.filter((t) => t >= windowStart)
  const next = [...recent, now]
  rateBuckets[actionId] = next
  return next.length > rate.count
}

export const evaluateRule = (rule: AllowRule | undefined, params: Record<string, any>, actionId: string): EvalResult => {
  const result: EvalResult = { ok: true }
  const warnings: string[] = []
  const errors: string[] = []

  // Business hours
  if (rule?.guard?.businessHoursOnly && !isWithinBusinessHours(rule)) {
    warnings.push('Outside business hours. Schedule for later or adjust business hours in rule.')
  }

  // Guards
  const maxRecords = rule?.guard?.maxRecords
  if (typeof maxRecords === 'number') {
    const val = Number(params?.records || params?.count || 0)
    if (val > maxRecords) errors.push(`Max records exceeded (${val} > ${maxRecords}). Reduce selection.`)
  }
  const maxAmount = rule?.guard?.maxAmount
  if (typeof maxAmount === 'number') {
    const amt = Number(params?.amount || 0)
    if (amt > maxAmount) errors.push(`Max amount exceeded (${amt} > ${maxAmount}). Lower the amount or request approval.`)
  }

  // Rate limit
  const isRateLimited = checkRateLimit(actionId, rule?.rateLimit)
  if (isRateLimited) warnings.push('Rate limit exceeded. Try again later or relax limits in rule.')

  if (warnings.length) result.warnings = warnings
  if (errors.length) { result.errors = errors; result.ok = false }
  if (isRateLimited) { result.rateLimited = true; result.ok = false }
  return result
}

export default evaluateRule


