import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import KnowledgeHome from '../screens/Knowledge/KnowledgeHome'
import AddUrlSource from '../screens/Knowledge/AddUrlSource'
import AddUploadSource from '../screens/Knowledge/AddUploadSource'
import NoteEditor from '../screens/Knowledge/NoteEditor'
import TrainingCenter from '../screens/Knowledge/TrainingCenter'
import CoverageHealth from '../screens/Knowledge/CoverageHealth'
import FailureLog from '../screens/Knowledge/FailureLog'
import TestHarness from '../screens/Knowledge/TestHarness'
import SourcePriority from '../screens/Knowledge/SourcePriority'
import RedactionRules from '../screens/Knowledge/RedactionRules'
import VersionsScreen from '../screens/Knowledge/VersionsScreen'

const Stack = createStackNavigator()

const KnowledgeStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="KnowledgeHome" component={KnowledgeHome} />
      <Stack.Screen name="AddUrlSource" component={AddUrlSource} />
      <Stack.Screen name="AddUploadSource" component={AddUploadSource} />
      <Stack.Screen name="NoteEditor" component={NoteEditor} />
      <Stack.Screen name="TrainingCenter" component={TrainingCenter} />
      <Stack.Screen name="CoverageHealth" component={CoverageHealth} />
      <Stack.Screen name="FailureLog" component={FailureLog} />
      <Stack.Screen name="TestHarness" component={TestHarness} />
      <Stack.Screen name="SourcePriority" component={SourcePriority} />
      <Stack.Screen name="RedactionRules" component={RedactionRules} />
      <Stack.Screen name="Versions" component={VersionsScreen} />
    </Stack.Navigator>
  )
}

export default KnowledgeStack


