import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { ActionSpec, SimInput, SimResult, ActionRun, AllowRule } from '../../types/actions'
import RiskBadge from '../../components/actions/RiskBadge'
import ParamEditor from '../../components/actions/ParamEditor'
import SimResultCard from '../../components/actions/SimResultCard'
import { evaluateRule } from '../../lib/actionEvaluator'
import { track } from '../../lib/analytics'

type Params = { spec: ActionSpec; allow?: AllowRule[]; onPropose?: (rule: AllowRule) => void; onRun?: (run: ActionRun) => void }

const ActionDetail: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { spec, allow = [], onPropose, onRun } = (route.params || {}) as Params

  const [params, setParams] = React.useState<Record<string, any>>({})
  const [res, setRes] = React.useState<SimResult | undefined>()
  const [errors, setErrors] = React.useState<string[]>([])

  const validate = (): boolean => {
    const errs: string[] = []
    spec.params.forEach(p => {
      if (p.required && (params[p.key] === undefined || params[p.key] === '')) errs.push(`${p.label} is required`)
      if (p.type === 'number' && params[p.key] != null && isNaN(Number(params[p.key]))) errs.push(`${p.label} must be a number`)
    })
    setErrors(errs)
    return errs.length === 0
  }

  const simulate = () => {
    if (!validate()) return
    const input: SimInput = { actionId: spec.id, params }
    const evalRes = evaluateRule((allow || []).find(r => r.actionId === spec.id), input.params, spec.id)
    const preview = `Would execute ${spec.name} with ${JSON.stringify(params)}`
    const warnings = evalRes.warnings || (spec.riskLevel === 'high' ? ['High risk action'] : undefined)
    setRes({ ok: evalRes.ok, preview, warnings, errors: evalRes.errors, estimatedLatencyMs: 300, affectedRecords: evalRes.ok ? 1 : 0 })
    try { track('actions.detail.simulate', { actionId: spec.id }) } catch {}
  }

  const proposeRule = () => {
    const rule: AllowRule = { actionId: spec.id, requireApproval: spec.riskLevel !== 'low', approverRole: spec.riskLevel === 'high' ? 'owner' : 'admin' }
    onPropose ? onPropose(rule) : Alert.alert('Proposed', JSON.stringify(rule, null, 2))
  }

  const run = () => {
    if (!validate()) return
    const rule = (allow || []).find(r => r.actionId === spec.id)
    const evalRes = evaluateRule(rule, params, spec.id)
    const approved = !!rule && !rule.requireApproval && !evalRes.rateLimited && !evalRes.errors?.length
    const run: ActionRun = { id: `run-${Date.now()}`, actionId: spec.id, requestedBy: 'ai', createdAt: Date.now(), state: evalRes.rateLimited ? 'rate_limited' : (approved ? 'approved' : 'queued'), params, result: { ok: evalRes.ok, preview: res?.preview || `Run ${spec.name}`, warnings: evalRes.warnings, errors: evalRes.errors } }
    onRun ? onRun(run) : Alert.alert('Run created', `${run.id} (${run.state})`)
    try { track('actions.detail.run', { actionId: spec.id }); if (evalRes.rateLimited) track('actions.rule.rate_limit', { actionId: spec.id }) } catch {}
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <View style={{ flex: 1, alignItems: 'center' }}>
              <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>{spec?.name || 'Action'}</Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>v{spec?.version} • {spec?.id}</Text>
            </View>
            <RiskBadge risk={spec?.riskLevel || 'low'} />
          </View>
        </View>

        {/* Summary */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Summary</Text>
          <Text style={{ color: theme.color.mutedForeground }}>{spec?.summary}</Text>
        </View>

        {/* Parameters */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Parameters</Text>
          <ParamEditor spec={spec} value={params} onChange={setParams} />
          {errors.length > 0 && (
            <View style={{ marginTop: 8 }}>
              {errors.map((e, i) => (
                <Text key={i} style={{ color: theme.color.error }}>• {e}</Text>
              ))}
            </View>
          )}
        </View>

        {/* Effects */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Effects</Text>
          <View style={{ gap: 4 }}>
            {(spec?.effects || []).map((fx, i) => (
              <Text key={i} style={{ color: theme.color.mutedForeground }}>• {fx}</Text>
            ))}
          </View>
        </View>

        {/* Safety tips */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Safety tips</Text>
          <Text style={{ color: theme.color.mutedForeground }}>Review guardrails and approvals before enabling high-risk actions. Test with simulator first.</Text>
        </View>

        {/* Actions */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24, flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <TouchableOpacity onPress={simulate} accessibilityLabel="Simulate" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
            <Text style={{ color: '#fff', fontWeight: '700' }}>Simulate</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={proposeRule} accessibilityLabel="Propose Allow Rule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Propose Allow Rule</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={run} accessibilityLabel="Run" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Run (UI-only)</Text>
          </TouchableOpacity>
        </View>

        {/* Sim result */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <SimResultCard res={res} />
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default ActionDetail


