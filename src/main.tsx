import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'
import { I18nProvider } from './i18n/I18nProvider'
import { ThemeProvider } from './contexts/ThemeProvider'
import { resources } from './i18n'

createRoot(document.getElementById("root")!).render(
  <I18nProvider resources={resources as any}>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </I18nProvider>
);
