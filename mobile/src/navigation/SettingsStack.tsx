import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import SettingsHome from '../screens/settings/SettingsHome'
import BillingHome from '../screens/Billing/BillingHome'
import PlanMatrix from '../screens/Billing/PlanMatrix'
import CheckoutScreen from '../screens/Billing/CheckoutScreen'
import ManagePlanScreen from '../screens/Billing/ManagePlanScreen'
import PaymentMethodsScreen from '../screens/Billing/PaymentMethodsScreen'
import InvoicesScreen from '../screens/Billing/InvoicesScreen'
import InvoiceDetail from '../screens/Billing/InvoiceDetail'
import TaxBillingProfile from '../screens/Billing/TaxBillingProfile'
import CouponsScreen from '../screens/Billing/CouponsScreen'
import UsageLimitsScreen from '../screens/Billing/UsageLimitsScreen'
import DunningScreen from '../screens/Billing/DunningScreen'

const Stack = createStackNavigator()

const SettingsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="SettingsHome" component={SettingsHome} />
      <Stack.Screen name="Billing" component={BillingHome} />
      <Stack.Screen name="PlanMatrix" component={PlanMatrix} />
      <Stack.Screen name="Checkout" component={CheckoutScreen} />
      <Stack.Screen name="ManagePlan" component={ManagePlanScreen} />
      <Stack.Screen name="PaymentMethods" component={PaymentMethodsScreen} />
      <Stack.Screen name="Invoices" component={InvoicesScreen} />
      <Stack.Screen name="InvoiceDetail" component={InvoiceDetail} />
      <Stack.Screen name="TaxBillingProfile" component={TaxBillingProfile} />
      <Stack.Screen name="Coupons" component={CouponsScreen} />
      <Stack.Screen name="UsageLimits" component={UsageLimitsScreen} />
      <Stack.Screen name="Dunning" component={DunningScreen} />
    </Stack.Navigator>
  )
}

export default SettingsStack


