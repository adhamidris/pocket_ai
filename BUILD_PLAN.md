# Mobile App Build Plan (Expo / React Native)

This plan converts the web design system into a production‑ready React Native/Expo app. Execute in order. Each step includes Objectives, Deliverables, Definition of Done (DoD), and Validation. Enforce “no placeholders”: every view must render real content pulled from tokens/i18n.

Reference: design-system.md (tokens, components, RN mapping, onboarding flow)

---

## 0) Prerequisites
- Node LTS, Yarn or pnpm
- Expo CLI installed (`npx expo --version`)
- iOS Simulator / Android Emulator

---

## 1) Project Scaffold + Theme
- Objectives:
  - Create Expo app, add TypeScript, Reanimated, Navigation, AsyncStorage, Fonts, LinearGradient, Haptics, i18n.
  - Implement theme.ts from design tokens (dark/light) and ThemeProvider.
- Deliverables:
  - `app.json`, `babel.config.js`, `tsconfig.json`, `src/theme.ts`, `src/providers/ThemeProvider.tsx`, `src/App.tsx` (wiring)
- DoD:
  - App launches with a sample screen showing brand background, primary text, and dark/light switch.
  - Inter fonts loaded and applied to base Text.
- Validation:
  - `npx expo start` → run on simulator; toggle theme and see colors change.

## 2) Core UI Primitives
- Objectives:
  - Build Button (variants/sizes), Input, Card components from tokens.
- Deliverables:
  - `src/components/ui/Button.tsx`, `Input.tsx`, `Card.tsx` and a demo screen `Screens/ComponentGallery`.
- DoD:
  - Buttons render all variants (`default`, `secondary`, `outline`, `ghost`, `link`, `premium`, `hero`, `glass`) and sizes.
  - Inputs show focus/disabled states; Card shows elevation consistent with theme.shadow.
- Validation:
  - Navigate to ComponentGallery and visually verify styles in dark/light.

## 3) Navigation + i18n/RTL
- Objectives:
  - Setup React Navigation (stack + tabs if needed) and deep linking shell.
  - i18n with EN/AR resources (seed from design-system.md keys). RTL toggle strategy documented.
- Deliverables:
  - `src/navigation/index.tsx` (linking config), `src/i18n/index.ts`, `src/i18n/en.ts`, `src/i18n/ar.ts`.
- DoD:
  - Language switch changes UI copy; RTL mirror verified for one screen (FAB/spacing).
- Validation:
  - Toggle language; ensure RTL flips obvious layout.

## 4) Onboarding (3 Screens)
- Objectives:
  - Implement Welcome (GradientHero), SimpleFlow (3 steps, animated connectors 1→2 then 2→3 loop), ChatPreview seeded from i18n demo scenarios.
  - Persist onboardingCompleted in AsyncStorage; Skip or Continue routes to Home.
- Deliverables:
  - `src/screens/onboarding/Welcome.tsx`, `SimpleFlow.tsx`, `ChatPreview.tsx`, `OnboardingPager.tsx`.
- DoD:
  - Pager swipes; connectors animate; ChatPreview shows 2–3 seeded messages.
- Validation:
  - Fresh install shows onboarding; after completion app routes to Home; relaunch skips onboarding.

## 5) Home Shell + Sections
- Objectives:
  - Build a Home screen mirroring web sections (hero analogue, feature tabs snapshot, testimonials, pricing summary, FAQ entry).
- Deliverables:
  - `src/screens/home/Home.tsx` and section components.
- DoD:
  - Sections render real content; no empty containers.
- Validation:
  - Visual parity with web style.

## 6) Offline & Loading States
- Objectives:
  - Global offline banner; skeleton loaders; retry patterns per design-system.md.
- Deliverables:
  - `src/components/system/OfflineBanner.tsx`, `Skeleton.tsx`; query utilities (TanStack Query or equivalent).
- DoD:
  - Airplane mode shows banner; skeletons visible while loading; retry works.
- Validation:
  - Toggle network; confirm behavior.

## 7) Notifications (Styling)
- Objectives:
  - Expo Notifications dev setup; small icon, accent color; categories/actions for deep link reply/open.
- Deliverables:
  - `notifications/setup.ts`, config in `app.json` as per doc.
- DoD:
  - Local notifications render with proper icons/colors; action buttons deep link.
- Validation:
  - Send test notification; tap actions navigate correctly.

## 8) Deep Linking & Sharing
- Objectives:
  - Implement linking prefixes and config; Share API with UTM parameters.
- Deliverables:
  - `src/navigation/linking.ts`, `src/utils/share.ts`.
- DoD:
  - `aisupport://chat/xyz` opens Chat; share sheet includes portal link with UTM.
- Validation:
  - Use `npx uri-scheme open` (iOS) / `adb shell am start` (Android).

## 9) Haptics & Polish
- Objectives:
  - Map haptics to key actions (CTA, selection, success/error).
- Deliverables:
  - `src/utils/haptics.ts` and usages in components.
- DoD:
  - Feelable feedback at critical interactions.
- Validation:
  - Interact on device; confirm effects.

## 10) Icons, Splash, Store Assets
- Objectives:
  - Add brand icons/splash; export store screenshots and feature graphics per doc.
- Deliverables:
  - `/assets/icons/*`, `/assets/splash/*`, `app.json` config.
- DoD:
  - Expo loads correct icons; preview splash dark/light.
- Validation:
  - `npx expo start` and EAS build previews.

## 11) QA & Docs
- Objectives:
  - Tablet breakpoints; accessibility; motion timing; performance.
- Deliverables:
  - `README.md` with run/build steps; a checklist of validations.
- DoD:
  - All checks pass; codebase aligns with design-system.md.
- Validation:
  - Device lab run; manual checks and automated lint/tests.

---

## Guardrails (Apply Every Step)
- No placeholders; all screens render real text/images from i18n/tokens.
- Keep brand tokens (colors/gradients/radii/shadows/typography) consistent.
- Enforce dark/light parity and RTL where applicable.
- Pass compile and smoke run before declaring done.
