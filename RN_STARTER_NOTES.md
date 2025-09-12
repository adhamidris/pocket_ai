# RN Starter Notes (Expo)

This file captures concrete setup notes so another agent/dev can scaffold quickly and align with the web brand.

## Packages
- Core: expo, react-native, typescript
- Navigation: @react-navigation/native, @react-navigation/stack, react-native-screens, react-native-safe-area-context
- State/Async: @react-native-async-storage/async-storage
- Animation: react-native-reanimated
- UI: expo-linear-gradient, @expo-google-fonts/inter
- Haptics: expo-haptics
- i18n: your preferred (i18next, react-intl, or simple custom)
- Icons: lucide-react-native (or @expo/vector-icons as fallback)

## Scripts
- `npx expo start` — dev server
- `npx expo prebuild` — if using native modules directly
- `npx expo run:ios` / `run:android` — device testing
- EAS Build: `npx expo install eas-cli` then configure `eas.json`

## Directory Structure
```
src/
  App.tsx
  theme.ts
  providers/
    ThemeProvider.tsx
  navigation/
    index.tsx
    linking.ts
  i18n/
    index.ts
    en.ts
    ar.ts
  components/
    ui/
      Button.tsx
      Input.tsx
      Card.tsx
    system/
      OfflineBanner.tsx
      Skeleton.tsx
  screens/
    onboarding/
      OnboardingPager.tsx
      Welcome.tsx
      SimpleFlow.tsx
      ChatPreview.tsx
    home/
      Home.tsx
  utils/
    haptics.ts
    share.ts
```

## Theming
- Implement `darkTheme` and `lightTheme` from design-system.md tokens.
- Apply theme via React Context. Expose `useTheme` hook to access colors, spacing, shadows, typography.

## Fonts
- Use `@expo-google-fonts/inter` and `useFonts()` to load 400/600/700.
- Apply globally by wrapping a custom `Text` component or passing styles at screen/root.

## Gradients
- Use `expo-linear-gradient`. Provide a `GradientHero` wrapper to match hero/promo backgrounds.

## RTL & Language
- Persist `lang` in AsyncStorage; rebuild i18n resources from design-system keys (EN/AR).
- Consider reload for RTL switch with `I18nManager.forceRTL` (document behavior to users).

## Onboarding
- 3 screens: Welcome, SimpleFlow, ChatPreview; pager via `react-native-pager-view` or horizontal `FlatList`.
- Persist completion flag. Provide Skip/Continue. Haptics on primary CTA.

## Offline & Loading
- Show global OfflineBanner (top/bottom). Implement skeletons for lists/cards.
- Retry button; re-fetch on app foreground.

## Deep Linking & Sharing
- Prefixes: `aisupport://` and `https://yourwebsite.com/app`.
- Use React Navigation linking config; add associatedDomains (iOS) and intents (Android).
- `Share.share` with UTM params helper.

## Notifications
- Expo Notifications: small icon (Android), accent color, categories/actions (reply/open) → deep link to screens.

## Icons & Splash
- Icons: iOS (1024 png), Android adaptive (foreground/background). Use brand gradient + glyph.
- Splash: brand gradient background + centered mark; dark/light variants.

## QA Checklist (condensed)
- Dark/light parity; typography sizes; spacing margins/paddings
- Haptics where expected
- Onboarding persistence works
- Offline banner & skeletons behave
- Deep links open correct screens
- Notifications style & actions
- Tablet layouts (2 columns where appropriate)
- Accessibility targets and semantics
```
