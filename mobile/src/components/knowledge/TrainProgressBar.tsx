import React from 'react'
import { View } from 'react-native'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { TrainingJob } from '../../types/knowledge'

export interface TrainProgressBarProps { job?: TrainingJob; testID?: string }

const TrainProgressBar: React.FC<TrainProgressBarProps> = ({ job, testID }) => {
  if (!job) return null
  const pct = Math.max(0, Math.min(100, Math.round(job.progress)))
  return (
    <View testID={testID}>
      <View style={{ height: 8, borderRadius: 4, backgroundColor: tokens.colors.muted }}>
        <View style={{ width: `${pct}%`, height: '100%', backgroundColor: tokens.colors.primary, borderRadius: 4 }} />
      </View>
      <Text size={11} color={tokens.colors.mutedForeground} style={{ marginTop: 4 }}>{job.state.toUpperCase()} • {pct}%</Text>
    </View>
  )
}

export default React.memo(TrainProgressBar)


