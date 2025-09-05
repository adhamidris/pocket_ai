import 'react-native-gesture-handler'
import { enableScreens } from 'react-native-screens'
// Disable native screens to avoid potential iOS crash in Expo Go
try { enableScreens(false) } catch {}
import { registerRootComponent } from 'expo'
// Load polyfills before anything else
import './src/polyfills'
import App from './src/App'

// Register the root component so Expo can run the app
registerRootComponent(App)
