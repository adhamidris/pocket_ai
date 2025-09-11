import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import AutomationsHome from '../screens/Automations/AutomationsHome'
import RuleBuilder from '../screens/Automations/RuleBuilder'
import BusinessHoursScreen from '../screens/Automations/BusinessHoursScreen'
import SlaEditor from '../screens/Automations/SlaEditor'
import AutoRespondersScreen from '../screens/Automations/AutoRespondersScreen'
import SimulatorScreen from '../screens/Automations/SimulatorScreen'
import ImportExportAudit from '../screens/Automations/ImportExportAudit'

const Stack = createStackNavigator()

const AutomationsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="AutomationsHome" component={AutomationsHome} />
      <Stack.Screen name="RuleBuilder" component={RuleBuilder} />
      <Stack.Screen name="BusinessHours" component={BusinessHoursScreen} />
      <Stack.Screen name="SlaEditor" component={SlaEditor} />
      <Stack.Screen name="AutoResponders" component={AutoRespondersScreen} />
      <Stack.Screen name="Simulator" component={SimulatorScreen} />
      <Stack.Screen name="ImportExportAudit" component={ImportExportAudit} />
    </Stack.Navigator>
  )
}

export default AutomationsStack


