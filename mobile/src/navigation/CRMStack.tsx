import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import CRMList from '../screens/crm/CRMList'
import ContactDetail from '../screens/crm/ContactDetail'
import DedupeCenter from '../screens/crm/DedupeCenter'
import SegmentsScreen from '../screens/crm/SegmentsScreen'
import PrivacyCenter from '../screens/security/PrivacyCenter'

const Stack = createStackNavigator()

const CRMStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="CRMList" component={CRMList} />
      <Stack.Screen name="ContactDetail" component={ContactDetail} />
      <Stack.Screen name="DedupeCenter" component={DedupeCenter} />
      <Stack.Screen name="Segments" component={SegmentsScreen} />
      <Stack.Screen name="PrivacyCenter" component={PrivacyCenter} />
    </Stack.Navigator>
  )
}

export default CRMStack


