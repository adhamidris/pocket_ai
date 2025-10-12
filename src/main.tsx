import { createRoot } from 'react-dom/client'
/* boot: seed business id from URL (?bid or ?businessId) */

import { setStoredBusinessId } from "./services/http";

(function seedBusinessFromURL(){

  try {

    const url = new URL(window.location.href);

    const bid = url.searchParams.get("bid") || url.searchParams.get("businessId");

    if (bid && bid !== "null" && bid !== "undefined") {

      setStoredBusinessId(bid);

      url.searchParams.delete("bid");

      url.searchParams.delete("businessId");

      window.history.replaceState({}, "", url.toString());

      console.info("[boot] Stored business id from URL:", bid);

    }

  } catch {}

})();
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
