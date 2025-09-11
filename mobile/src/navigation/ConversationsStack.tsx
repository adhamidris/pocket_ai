import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import { useRoute } from '@react-navigation/native'
import ConversationsList from '../screens/conversations/ConversationsList'
import ConversationThread from '../screens/conversations/ConversationThread'
import { ConversationsStackParamList } from './types'

const Stack = createStackNavigator<ConversationsStackParamList>()

const ConversationsStack: React.FC = () => {
  const route = useRoute<any>()
  const initialParams = route?.params
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }} initialRouteName="ConversationsHome">
      <Stack.Screen name="ConversationsHome" component={ConversationsList} initialParams={initialParams} />
      <Stack.Screen name="ConversationThread" component={ConversationThread} />
    </Stack.Navigator>
  )
}

export default ConversationsStack

