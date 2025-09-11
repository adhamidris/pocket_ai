export type CondKey = 'intent'|'channel'|'tag'|'vip'|'priority'|'timeOfDay'|'dayOfWeek'|'withinHours'|'messageContains'|'unassigned'|'waitingMinutes'|'csatEnabled'
export type Op = 'is'|'in'|'contains'|'gte'|'lte'|'eq'|'neq'|'true'|'false'
export interface Condition { key: CondKey; op: Op; value?: any }

export type ActionKey = 'autoReply'|'routeToAgent'|'routeToSkill'|'setPriority'|'addTag'|'escalate'|'deflect'|'close'|'requestCSAT'|'scheduleCallback'
export interface Action { key: ActionKey; params?: Record<string,any> }

export interface Rule {
  id: string
  name: string
  when: Condition[]
  then: Action[]
  enabled: boolean
  order: number
}

export interface BusinessHoursDay { day: 0|1|2|3|4|5|6; open: boolean; ranges: { start: string; end: string }[] }
export interface Holiday { id: string; date: string; name: string }
export interface BusinessCalendar { timezone: string; days: BusinessHoursDay[]; holidays: Holiday[] }

export type Priority = 'low'|'normal'|'high'|'vip'
export type Channel = 'whatsapp'|'instagram'|'facebook'|'web'|'email'
export interface SlaTarget { priority: Priority; channel?: Channel; frtP50Sec: number; frtP90Sec: number; resolutionHrs?: number }
export interface SlaPolicy { id: string; name: string; targets: SlaTarget[]; pauseOutsideHours: boolean }

export interface AutoResponder { id: string; name: string; active: boolean; message: string; channels: Channel[]; onlyOutsideHours?: boolean; intentFilter?: string[] }

export interface SimulationInput { when: Condition[]; nowIso: string }
export interface SimulationResult { matchedRuleIds: string[]; actions: Action[]; slaAtRisk?: boolean; notes?: string[] }


