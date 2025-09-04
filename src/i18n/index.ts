import en from "./en";
import ar from "./ar";

export const resources = { en, ar } as const;
export type AppLang = keyof typeof resources;

