import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

type Lang = "en" | "ar";

type Resources = Record<string, any>;

interface I18nContextValue {
  lang: Lang;
  dir: "ltr" | "rtl";
  t: <T = string>(key: string) => T;
  setLang: (lang: Lang) => void;
  toggleLang: () => void;
}

const I18nContext = createContext<I18nContextValue | undefined>(undefined);

function get(obj: any, path: string) {
  return path.split(".").reduce((acc, part) => (acc && acc[part] !== undefined ? acc[part] : undefined), obj);
}

export const I18nProvider: React.FC<{ resources: Record<Lang, Resources>; children: React.ReactNode }> = ({ resources, children }) => {
  const [lang, setLangState] = useState<Lang>(() => (localStorage.getItem("lang") as Lang) || "en");

  const dir = lang === "ar" ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang);
    document.documentElement.setAttribute("dir", dir);
    localStorage.setItem("lang", lang);
  }, [lang, dir]);

  const t = useCallback(<T = string,>(key: string): T => {
    const value = get(resources[lang], key);
    return (value as unknown) as T;
  }, [lang, resources]);

  const setLang = useCallback((l: Lang) => setLangState(l), []);
  const toggleLang = useCallback(() => setLangState(prev => (prev === "en" ? "ar" : "en")), []);

  const value = useMemo<I18nContextValue>(() => ({ lang, dir, t, setLang, toggleLang }), [lang, dir, t, setLang, toggleLang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
};

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}

