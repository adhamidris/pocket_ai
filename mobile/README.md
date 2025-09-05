# AI Support Mobile (Expo / React Native)

This is the mobile app scaffolding that mirrors the web brand. It includes theming, i18n (EN/AR), and a 3‑screen onboarding flow with animated connectors and a chat preview.

## Prerequisites
- Node LTS, Expo CLI
- iOS Simulator / Android Emulator

## Install & Run
```bash
cd mobile
npm install # or yarn
npm run start
```

## What’s Included
- Theme tokens (dark/light) matching web HSL values (`src/theme.ts`)
- ThemeProvider (`src/providers/ThemeProvider.tsx`)
- i18n EN/AR with demo scenarios (`src/i18n/*`)
- Onboarding: Welcome, SimpleFlow (animated connectors 1→2→3 loop), ChatPreview (`src/screens/onboarding/*`)
- Navigation gate that persists onboarding completion in AsyncStorage

## Notes
- Add real icon/splash assets under `mobile/assets/` and update `app.json`.
- Install `react-native-reanimated` and configure Babel (already included), then reload Metro.
- If you want RTL switch at runtime, consider `I18nManager` (may require app reload).

## Next Steps
Follow BUILD_PLAN.md and PROMPTS.md in the repo root to complete Home, offline states, notifications, deep linking, and more.
