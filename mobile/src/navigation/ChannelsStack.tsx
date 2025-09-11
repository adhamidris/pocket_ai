import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import ChannelsHome from '../screens/Channels/ChannelsHome'
import RecipeCenter from '../screens/Channels/RecipeCenter'
import WidgetSnippetScreen from '../screens/Channels/WidgetSnippetScreen'
import UtmBuilder from '../screens/Channels/UtmBuilder'
import VerificationLogs from '../screens/Channels/VerificationLogs'

const Stack = createStackNavigator()

const ChannelsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="ChannelsHome" component={ChannelsHome} />
      <Stack.Screen name="RecipeCenter" component={RecipeCenter} />
      <Stack.Screen name="WidgetSnippet" component={WidgetSnippetScreen} />
      <Stack.Screen name="UtmBuilder" component={UtmBuilder} />
      <Stack.Screen name="VerificationLogs" component={VerificationLogs} />
    </Stack.Navigator>
  )
}

export default ChannelsStack


