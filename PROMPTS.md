# Prompts for Codex GPT‑5 High (Expo / RN)

Use these step-by-step prompts. Paste one at a time. Require Definition of Done (DoD) to pass before moving on. Reference design-system.md for all tokens and patterns. Never accept placeholders.

---

## Prompt 1 — Project Scaffold + Theme
Objective:
- Create an Expo TypeScript app and implement theming per design-system.md (dark/light). Load Inter fonts.

Constraints:
- No placeholders. App must run on simulator. Theme toggle must switch colors. Use expo-linear-gradient, expo-haptics prepared.

Inputs:
- design-system.md (Visual Design & Theming, RN/Expo Implementation Guide)

Tasks:
1) Scaffold Expo TS project (if not exists). Add deps: react-navigation, reanimated, @react-native-async-storage/async-storage, expo-linear-gradient, expo-haptics, i18n lib, @expo-google-fonts/inter.
2) Create src/theme.ts with tokens (darkTheme/lightTheme) mapped from design-system.md.
3) Create ThemeProvider; wrap App with it; apply base background and text.
4) Add a sample Home screen showing brand colors/typography and a theme toggle.

Definition of Done:
- App launches; base screen shows background/foreground colors and Inter fonts.
- Toggling theme updates colors instantly.

Validation:
- Run `npx expo start`; test on iOS/Android simulator; flip theme and observe changes.

---

## Prompt 2 — Core UI Primitives
Objective:
- Implement Button variants/sizes, Input, and Card components from tokens.

Constraints:
- Variants: default, secondary, outline, ghost, link, premium, hero, glass. Sizes: sm, md, lg, xl.
- Must mirror design-system.md styles. No stubs.

Inputs:
- design-system.md (Buttons, Form Inputs, Cards)

Tasks:
1) Create `src/components/ui/Button.tsx` with style generator for variants/sizes. Include RN gradient wrapper for premium/hero.
2) Create `src/components/ui/Input.tsx` and `src/components/ui/Card.tsx` with focus/disabled and elevation.
3) Build `Screens/ComponentGallery` to preview all variants in dark/light.

Definition of Done:
- All button variants/sizes render correctly. Input focus ring/elevation visible. Card elevation matches theme.

Validation:
- Navigate to ComponentGallery; visually verify.

---

## Prompt 3 — Navigation + i18n/RTL
Objective:
- Set up React Navigation (stack), deep linking shell, i18n (EN/AR), and RTL.

Constraints:
- Language toggle must switch strings. RTL mirroring must be demonstrated for at least one UI region.

Inputs:
- design-system.md (Deep Linking & Sharing, RTL notes)

Tasks:
1) Install @react-navigation/native + stack + dependencies; add linking config with `aisupport://` and `https://yourwebsite.com/app`.
2) Create i18n files from design-system.md keys: en/ar for nav, hero, features, pricing, demo chat.
3) Add LanguageToggle that persists choice and (optionally) triggers RTL via I18nManager on next launch.

Definition of Done:
- Language switches successfully; a screen shows direction flipped (e.g., FAB position).

Validation:
- Toggle EN/AR; relaunch to apply RTL if necessary; confirm layout.

---

## Prompt 4 — Onboarding (3 screens)
Objective:
- Implement onboarding: Welcome (GradientHero), SimpleFlow (animated 3-step connectors 1→2→3 loop), ChatPreview (2–3 seeded messages).

Constraints:
- Persist onboardingCompleted in AsyncStorage; Skip or Continue routes to Home. Haptics on primary CTA.

Inputs:
- design-system.md (Mobile Onboarding Flow)

Tasks:
1) Create OnboardingPager with Welcome, SimpleFlow, ChatPreview.
2) Welcome: gradient background, headline/subtitle, Continue + Skip.
3) SimpleFlow: three cards with bullets; two horizontal connectors that animate width (Reanimated) 1→2, then 2→3; loop.
4) ChatPreview: seed from i18n.demo.scenarios[0].
5) Persist completion and route away from onboarding on next launch.

Definition of Done:
- Pager swipes; connectors animate; chat preview renders; onboarding persists.

Validation:
- Fresh run shows onboarding; complete/skip navigates to Home; relaunch skips onboarding.

---

## Prompt 5 — Home Shell + Sections
Objective:
- Build Home screen sections mirroring web (hero analogue, features snapshot, testimonials, pricing summary, FAQ link).

Constraints:
- Real content (titles/bullets/prices) from i18n; respect tokens (colors, radius, shadows).

Inputs:
- design-system.md (Layout & Structure, Brand Elements)

Tasks:
1) Compose sections with Cards/Buttons; reuse gradients sparingly.
2) Ensure spacing, typography, and elevation match token guidance.

Definition of Done:
- Home renders meaningful content; no empty boxes.

Validation:
- Visual parity; dark/light checked.

---

## Prompt 6 — Offline/Loading/Feedback
Objective:
- Implement global offline banner, skeletons, retry, and feedback.

Constraints:
- Offline banner must appear in airplane mode; skeletons for lists/cards; retry buttons work.

Inputs:
- design-system.md (Offline & Loading States, Feedback Patterns)

Tasks:
1) Detect connectivity; display OfflineBanner.
2) Implement Skeleton components and apply to at least one list.
3) Add retry to one fetch flow; map haptics to success/error actions.

Definition of Done:
- Turning off network shows banner; skeletons visible; retry resolves after reconnect.

Validation:
- Simulate offline; verify behavior; confirm haptics fire.
