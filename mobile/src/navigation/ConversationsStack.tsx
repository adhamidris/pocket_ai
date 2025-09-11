import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import ConversationsList from '../screens/conversations/ConversationsList'
import ConversationThread from '../screens/conversations/ConversationThread'
import { ConversationsStackParamList } from './types'

const Stack = createStackNavigator<ConversationsStackParamList>()

const ConversationsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="Conversations" component={ConversationsList} />
      <Stack.Screen name="ConversationThread" component={ConversationThread} />
    </Stack.Navigator>
  )
}

export default ConversationsStack


