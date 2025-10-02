import React from "react";

type ThemeMode = "system" | "light" | "dark";

type ThemeContextValue = {
  mode: ThemeMode;
  isLight: boolean;
  isDark: boolean;
  resolved: "light" | "dark";
  toggle: () => void;
  setMode: (mode: ThemeMode) => void;
};

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

function getSystemPreference(): "light" | "dark" {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function applyHtmlClasses(resolved: "light" | "dark") {
  const root = document.documentElement;
  root.classList.remove("light");
  root.classList.remove("dark");
  if (resolved === "light") {
    root.classList.add("light");
  } else {
    root.classList.add("dark");
  }
}

export const ThemeProvider = ({ children }: { children: React.ReactNode }) => {
  const [mode, setMode] = React.useState<ThemeMode>(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("theme") : null;
    if (saved === "light" || saved === "dark" || saved === "system") return saved as ThemeMode;
    return "system";
  });

  const [system, setSystem] = React.useState<"light" | "dark">(() => getSystemPreference());

  React.useEffect(() => {
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const handler = (e: MediaQueryListEvent) => setSystem(e.matches ? "light" : "dark");
    if (mql.addEventListener) mql.addEventListener("change", handler);
    else mql.addListener(handler);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", handler);
      else mql.removeListener(handler);
    };
  }, []);

  const resolved: "light" | "dark" = mode === "system" ? system : mode;

  React.useEffect(() => {
    applyHtmlClasses(resolved);
  }, [resolved]);

  const value = React.useMemo<ThemeContextValue>(() => ({
    mode,
    isLight: resolved === "light",
    isDark: resolved === "dark",
    resolved,
    toggle: () => {
      const next = resolved === "light" ? "dark" : "light";
      applyHtmlClasses(next);
      localStorage.setItem("theme", next);
      // If user toggles explicitly, treat as explicit mode (not system)
      setMode(next);
    },
    setMode: (m: ThemeMode) => {
      localStorage.setItem("theme", m);
      setMode(m);
    }
  }), [mode, resolved]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

export const useThemeMode = () => {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error("useThemeMode must be used within ThemeProvider");
  return ctx;
};


