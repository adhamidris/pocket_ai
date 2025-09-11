import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import ActionsHome from '../screens/Actions/ActionsHome'
import ActionDetail from '../screens/Actions/ActionDetail'
import SecretsScreen from '../screens/Actions/SecretsScreen'
import ApprovalsInbox from '../screens/Actions/ApprovalsInbox'
import ImportExportScreen from '../screens/Actions/ImportExportScreen'
import CapabilityPacks from '../screens/Actions/CapabilityPacks'
import AllowRuleBuilder from '../screens/Actions/AllowRuleBuilder'
import RunMonitorScreen from '../screens/Actions/RunMonitorScreen'
import RunDetail from '../screens/Actions/RunDetail'

const Stack = createStackNavigator()

const ActionsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="ActionsHome" component={ActionsHome} />
      <Stack.Screen name="ActionDetail" component={ActionDetail} />
      <Stack.Screen name="Secrets" component={SecretsScreen} />
      <Stack.Screen name="Approvals" component={ApprovalsInbox} />
      <Stack.Screen name="ImportExport" component={ImportExportScreen} />
      <Stack.Screen name="CapabilityPacks" component={CapabilityPacks} />
      <Stack.Screen name="AllowRuleBuilder" component={AllowRuleBuilder} />
      <Stack.Screen name="RunMonitor" component={RunMonitorScreen} />
      <Stack.Screen name="RunDetail" component={RunDetail} />
    </Stack.Navigator>
  )
}

export default ActionsStack


