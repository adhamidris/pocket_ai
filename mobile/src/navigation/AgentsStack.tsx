import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import AgentsList from '../screens/agents/AgentsList'
import AgentDetail from '../screens/agents/AgentDetail'
import RoutingRulesScreen from '../screens/agents/RoutingRulesScreen'
import PoliciesScreen from '../screens/agents/PoliciesScreen'

const Stack = createStackNavigator()

const AgentsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="AgentsList" component={AgentsList} />
      <Stack.Screen name="AgentDetail" component={AgentDetail} />
      <Stack.Screen name="RoutingRules" component={RoutingRulesScreen} />
      <Stack.Screen name="Policies" component={PoliciesScreen} />
    </Stack.Navigator>
  )
}

export default AgentsStack



