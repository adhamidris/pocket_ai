# Design System

This document captures the visual language, interaction patterns, and component guidelines extracted from the codebase. It is structured to let you reproduce the same identity in a mobile app while keeping naming/tokens aligned with the web implementation.

---

## Visual Design & Theming

### Color Palette (Token-based)
- Core tokens (HSL via CSS variables, dark by default):
  - `--background`: 215 25% 6%
  - `--foreground`: 210 20% 98%
  - `--card`: 215 20% 8%
  - `--card-foreground`: 210 20% 98%
  - `--popover`: 215 15% 12%
  - `--popover-foreground`: 210 20% 98%
  - `--primary`: 240 100% 70%
  - `--primary-light`: 250 100% 75%
  - `--primary-dark`: 230 100% 65%
  - `--primary-foreground`: 0 0% 100%
  - `--secondary`: 215 20% 15%
  - `--secondary-foreground`: 210 20% 98%
  - `--muted`: 215 20% 15%
  - `--muted-foreground`: 215 8% 65%
  - `--accent`: 215 15% 18%
  - `--accent-foreground`: 210 20% 98%
  - `--border`: 215 15% 20%
  - `--input`: 215 15% 18%
  - `--ring`: 240 100% 70%
  - Semantic: `--success` (142 71% 45%), `--success-light` (142 71% 55%), `--warning` (38 92% 50%), `--error` (0 72% 51%)
  - Neutral scale: `--neutral-50 … --neutral-900` (dark greys tuned to brand)

- Gradients:
  - `--gradient-primary`: 135° from `--primary` to `--primary-light`
  - `--gradient-hero`: 135° from `--primary` → `--primary-light` → lighter purple
  - `--gradient-surface`: for subtle container backgrounds

- Light mode overrides (class `.light` on `html`): all above tokens shift to a light UI while retaining purplish brand accents (primary ~ 240 75% 48%).

### Typography
- Font family: `Inter, system-ui, sans-serif` (token alias `font-inter`)
- Sizes & weights (common):
  - Display/H1: `text-5xl` to `text-7xl`, bold
  - H2: `text-4xl` / `text-5xl`, bold
  - H3: `text-2xl` / `text-3xl`, semi-bold
  - Body: `text-base` (md: `text-sm` in inputs), normal
  - Small: `text-sm` for meta and descriptions
- Line-height: Tailwind defaults; headings are tight (`leading-tight`) for hero/section titles

### Spacing System
- Utility-first spacing via Tailwind:
  - Section padding: `pt-16 md:pt-20 pb-24 md:pb-28`
  - Container padding: `px-6`
  - Grid gaps: `gap-4`, `gap-6`, `gap-8`
  - Card padding: `p-6`, `lg:p-8`

### Radius & Shadows
- Radius tokens: `--radius` (12px), `--radius-sm` (8px), `--radius-lg` (16px), `--radius-xl` (24px)
- Common radii in use: `rounded-lg`, `rounded-xl`, `rounded-2xl`
- Shadows (tokens → classes):
  - `--shadow-premium` → `shadow-premium` (featured containers, hero elements)
  - `--shadow-md`, `--shadow-lg`, `--shadow-xl` for subtle elevation

### Component Sizing Patterns
- Buttons: sizes `sm`/`default`/`lg`/`xl` + `icon` (see buttons below)
- Inputs: height ~ 40px (`h-10`), full width, comfortable padding
- Cards: 16–24px radius, generous padding (24–32px), subtle borders

---

## UI Components & Patterns

### Buttons (src/components/ui/button.tsx)
- Base: `inline-flex`, center-aligned, `rounded-lg`, `text-sm`, medium weight, 300ms transitions
- Variants:
  - `default`: solid primary background, white text, hover opacity/shadow
  - `destructive`: solid destructive background
  - `outline`: border, background inherits; hover accent background + primary border tint
  - `secondary`: solid secondary surface
  - `ghost`: hover accent only
  - `link`: text-only, underline on hover
  - Premium extras: `premium` (primary gradient), `glass` (blurred glass), `hero` (hero gradient, larger shadow)
- Sizes:
  - `sm`: `h-9`, small text
  - `default`: `h-10` standard
  - `lg`: `h-12`, `rounded-xl`, bold-ish
  - `xl`: `h-14`, large CTAs
  - `icon`: square `h-10 w-10`

### Form Inputs
- Text input (src/components/ui/input.tsx):
  - `h-10`, `rounded-md`, `border-input`, `bg-background`
  - Placeholder uses `text-muted-foreground`
  - Focus ring: `focus-visible:ring-2` with `--ring` color and ring offset
  - Disabled: reduced opacity + no pointer events
- Textarea similar styling; min-height 80px.
- Other controls (checkbox, radio, select, switch): shadcn/radix patterns with consistent border/radius and focus rings.

### Cards / Containers
- Card (shadcn): `rounded-lg border bg-card text-card-foreground shadow-sm`
- Marketing cards use: `rounded-2xl border border-border bg-card shadow-premium p-6 lg:p-8`
- Accents: gradient rings or faint inner highlights on hover in hero/feature sections

### Navigation Patterns
- Header (sticky top bar):
  - Desktop: logo left, inline links center/right, theme toggle + auth CTAs and language toggle on the right
  - Mobile: hamburger toggles a vertical menu (`space-y-4`), with theme + actions listed
  - Interactions: hover color to primary, subtle background hover for toggles

### Modal / Popups (Dialog)
- Overlay: opaque black `bg-black/80` fade-in/out
- Content: centered, bordered, `bg-background`, rounded, shadowed
- Motion: zoom and slide-in/out with 200ms easing; focus ring on close button

### Loading States & Animations
- Typing dots in chat: three bouncing dots with staggered delays
- Skeletons: `skeleton` components (if used) follow muted surfaces
- Animations available (classes): `animate-fade-in`, `animate-slide-up`, `animate-scale-in`, marquee scroll, gradient text shift
  - Timing tokens: `--transition-fast` 150ms, `--transition-normal` 250ms, `--transition-slow` 350ms

---

## Layout & Structure

### Grid & Breakpoints
- Container: centered, padding `2rem`, max at `2xl: 1400px`
- Breakpoints: Tailwind defaults (sm/md/lg/xl/2xl). Most responsive switches at `md`.
- Common grids: 2–3 columns for marketing sections, `gap-6` or `gap-8`.

### Page Templates
- Landing flow:
  1) Hero (headline + CTA + in-hero demo)
  2) Trusted by (logo marquee)
  3) Feature tabs (deep dives) + optional integrations grid
  4) Testimonials carousel/marquee cards
  5) Pricing (tabs + packages)
  6) FAQ (accordion)
  7) Security strip (compact assurances)
  8) Mobile app promo (store buttons)
  9) Footer (brand, links)

### Content Hierarchy
- Display headings use gradient text for emphasis (`text-gradient-hero`)
- Section intros: heading + one-line explainer in `text-muted-foreground`
- Cards: title → short paragraph → bullets / CTA

### Intra-Section Spacing
- Section blocks: ~64–80px vertical padding
- Elements: 16–24px spacing between headings and body, 24–32px around cards

---

## Interaction Design

### Hover / Focus
- Buttons: subtle shadow/opacity increase, sometimes slight scale on hero/premium
- Links: color shift to primary; `link` variant underlines on hover
- Inputs: `focus-visible:ring-2` with brand ring color and offset

### Motion & Easing
- General transitions: 250–350ms, ease-out/ease custom
- Entrance: fade-in/slide-up/scale-in on scroll or mount for cards and panels
- Marquee: continuous, linear 35s loop

### User Flows
- Primary CTA prominent in hero; secondary ghost button for demo
- Chat entry at bottom-right (LTR) and mirrored left for RTL
- Pricing toggles (monthly/yearly), schema tabs (wage/packages/self)

### Feedback Patterns
- Success: green tokens (`--success`), subtle badges
- Error/destructive: red tokens (`--destructive`), hover darken
- Loading: bouncing dots, skeletons, or muted placeholders

---

## Brand Elements

### Logo & Wordmark
- Simple square with gradient fill and an “A” letterform (in Header)
- Positioned left in header; repeated as a 10×10 logo in footer

### Iconography
- `lucide-react` icons, line-based glyphs
- Typical sizes: 16–20px inside buttons; 24–28px in cards/headers
- Primary color accents for emphasis (`text-primary`)

### Images / Media
- Logos via `simpleicons` monochrome for partners/trusted by
- Avatars via `pravatar.cc` for testimonials
- Inline media adopts rounded corners, subtle borders

### Visual Tone
- Modern, minimal, premium
- Dark-first design with elegant purplish gradients
- Polished micro-interactions (hover lift, smooth fades, soft glows)

---

## Token Summary (for Mobile)

Use these as your cross-platform design tokens:

```
{
  "color": {
    "background": "hsl(215 25% 6%)",
    "foreground": "hsl(210 20% 98%)",
    "card": "hsl(215 20% 8%)",
    "cardForeground": "hsl(210 20% 98%)",
    "primary": "hsl(240 100% 70%)",
    "primaryLight": "hsl(250 100% 75%)",
    "secondary": "hsl(215 20% 15%)",
    "muted": "hsl(215 20% 15%)",
    "mutedForeground": "hsl(215 8% 65%)",
    "accent": "hsl(215 15% 18%)",
    "border": "hsl(215 15% 20%)",
    "ring": "hsl(240 100% 70%)",
    "success": "hsl(142 71% 45%)",
    "warning": "hsl(38 92% 50%)",
    "error": "hsl(0 72% 51%)"
  },
  "gradient": {
    "primary": ["hsl(240 100% 70%)", "hsl(250 100% 75%)"],
    "hero": ["hsl(240 100% 70%)", "hsl(250 100% 75%)", "hsl(260 100% 80%)"]
  },
  "radius": { "sm": 8, "md": 12, "lg": 16, "xl": 24 },
  "shadow": {
    "premium": "0 25px 50px -12px hsla(215, 25%, 27%, 0.15)",
    "md": "0 4px 6px -1px hsla(215, 25%, 27%, 0.07), 0 2px 4px -1px hsla(215, 25%, 27%, 0.06)"
  },
  "spacing": { "xs": 8, "sm": 12, "md": 16, "lg": 24, "xl": 32 },
  "typography": {
    "display": { "size": 56, "weight": 700, "lineHeight": 1.1 },
    "title": { "size": 32, "weight": 600 },
    "body": { "size": 16, "weight": 400 },
    "small": { "size": 14, "weight": 400 }
  },
  "button": {
    "sizes": {
      "sm": { "height": 36, "radius": 12 },
      "md": { "height": 40, "radius": 12 },
      "lg": { "height": 48, "radius": 16 },
      "xl": { "height": 56, "radius": 16 }
    },
    "variants": ["default", "secondary", "outline", "ghost", "link", "premium", "hero", "glass"]
  }
}
```

---

## Accessibility & Direction
- Language + direction toggles applied at `<html>`: `lang` and `dir` mutate tokens/sides
- RTL-aware utilities used (e.g., `ms/me` logical margins, mirrored FAB/window)
- Focus rings: consistent `ring-2` with `--ring` color and offset

---

## Implementation Notes
- Theme switching: toggled via `.light` class on `html` and persisted in `localStorage`
- Animations: keep durations 200–350ms for parity with web; prefer ease-out for entrances
- Gradients: reuse brand purples for primary CTAs and hero/promo surfaces

---

## App Icons, Splash, and Store Assets

### App Icons
- Design: Use the brand square with the gradient hero background and the “A” letterform centered.
- iOS (App Store + app):
  - Source icon master: 1024×1024 PNG (no transparency), rounded mask applied by iOS.
  - Xcode/Expo will generate sizes; keep safe area (no important details within 15% from edges).
- Android (adaptive icons):
  - Foreground: 1024×1024 PNG (only glyph, transparent padding around the mark).
  - Background: solid color (brand primary) or gradient; Expo supports `android.adaptiveIcon.backgroundColor` or `*.backgroundImage`.
  - Legacy fallback: Expo auto-generates smaller sizes.
- Play Store icon: 512×512 PNG, no rounded corners in asset; Google applies mask.

### Splash Screens (Expo)
- Background: brand gradient (hero) or solid `theme.color.background` in light/dark.
- Logo: centered “A” mark; keep at ~30–40% width; PNG with transparency.
- Expo `app.json` example:
```json
{
  "expo": {
    "splash": {
      "image": "./assets/splash/logo.png",
      "backgroundColor": "#0e131a",
      "resizeMode": "contain",
      "dark": {
        "image": "./assets/splash/logo-dark.png",
        "backgroundColor": "#0e131a"
      }
    },
    "ios": { "icon": "./assets/icons/ios-1024.png" },
    "android": {
      "icon": "./assets/icons/android-legacy.png",
      "adaptiveIcon": {
        "foregroundImage": "./assets/icons/adaptive-foreground.png",
        "backgroundColor": "#644EF9"
      }
    }
  }
}
```

### Store Assets
- iOS screenshots (recommend):
  - 6.7" (iPhone Pro Max): 1290×2796 or 1242×2688
  - 6.1" (iPhone): 1170×2532
  - iPad Pro 12.9": 2048×2732
  - Use gradient hero backgrounds, short captions, and dark/light variants.
- Android Play Store:
  - Feature graphic: 1024×500 PNG (no transparency), use brand gradient and tagline.
  - Phone screenshots: at least 1080×1920 (portrait) or higher.

---

## Offline & Loading States (Mobile)

### Patterns
- Global offline banner: anchored at top/bottom using brand muted surface with an offline icon; text like “You’re offline. Some actions are unavailable.”
- Local placeholders:
  - Skeletons for lists/cards; shimmer optional.
  - Button state: disable with opacity 0.5 and keep label readable.
- Retry affordances: inline “Try again” on fetch blocks.
- Caching strategy: use TanStack Query with stale-while-revalidate, exponential backoff on failures, and `AppState` refetch on active.

### Chat-specific
- Queue outgoing messages locally with a clock/queued indicator; auto-send on reconnect.
- Prevent tool actions when offline; show an inline tip.

---

## Push Notification Styling

### Expo Notifications
- Small icon (Android): monochrome glyph, fully white on transparent; Expo config `android.notification.icon`.
- Accent color (Android): use brand primary `#644EF9` (or the hex matching `--primary`).
- iOS: badge + sound optional; keep titles concise (≤ 40 chars), body ≤ 120 chars.
- Rich push (optional): attach preview image of the agent or brand mark.
- Categories & actions:
  - “Reply” for chat messages.
  - “Open” for general notifications.
  - Map categories to deep links (see below).

Copy tone
- Friendly, concise, purposeful; mirror web voice.
- Examples:
  - “New chat waiting — Nancy replied.”
  - “Your portal link is ready. Share it now.”

---

## Deep Linking & Sharing Patterns

### Deep Linking
- Prefixes: `aisupport://` and `https://yourwebsite.com/app` for Universal/App Links.
- React Navigation linking config example:
```ts
export const linking = {
  prefixes: ['aisupport://', 'https://yourwebsite.com/app'],
  config: {
    screens: {
      home: 'home',
      onboarding: 'onboarding',
      chat: {
        path: 'chat/:agentId',
        parse: { agentId: String }
      },
      pricing: 'pricing'
    }
  }
}
```
- iOS: set `associatedDomains` for Universal Links.
- Android: intent filters for App Links; Expo handles via `scheme` and `android.intentFilters`.

### Sharing
- Use `Share.share` API to share the portal link or transcripts.
- Include UTM parameters for attribution: `?utm_source=app&utm_medium=share`.
- Inbound link handling: if app is closed, route after hydration; if open, navigate immediately.


---

## React Native / Expo Implementation Guide (Android & iOS)

The following provides a direct mapping of web tokens and patterns to a React Native/Expo app.

### 1) Theme Object (TypeScript)
Create a central theme in `src/theme.ts` and pass it via context.

```ts
// src/theme.ts
export type Color = string

export type Theme = {
  dark: boolean
  color: {
    background: Color
    foreground: Color
    card: Color
    cardForeground: Color
    primary: Color
    primaryLight: Color
    secondary: Color
    muted: Color
    mutedForeground: Color
    accent: Color
    border: Color
    ring: Color
    success: Color
    warning: Color
    error: Color
  }
  radius: { sm: number; md: number; lg: number; xl: number }
  shadow: {
    premium: { androidElevation: number; ios: { color: Color; opacity: number; radius: number; offsetY: number } }
    md: { androidElevation: number; ios: { color: Color; opacity: number; radius: number; offsetY: number } }
  }
  spacing: { xs: number; sm: number; md: number; lg: number; xl: number }
  typography: {
    display: { fontSize: number; fontWeight: '700'; lineHeight: number }
    title: { fontSize: number; fontWeight: '600' }
    body: { fontSize: number; fontWeight: '400' }
    small: { fontSize: number; fontWeight: '400' }
  }
}

export const darkTheme: Theme = {
  dark: true,
  color: {
    background: 'hsl(215,25%,6%)',
    foreground: 'hsl(210,20%,98%)',
    card: 'hsl(215,20%,8%)',
    cardForeground: 'hsl(210,20%,98%)',
    primary: 'hsl(240,100%,70%)',
    primaryLight: 'hsl(250,100%,75%)',
    secondary: 'hsl(215,20%,15%)',
    muted: 'hsl(215,20%,15%)',
    mutedForeground: 'hsl(215,8%,65%)',
    accent: 'hsl(215,15%,18%)',
    border: 'hsl(215,15%,20%)',
    ring: 'hsl(240,100%,70%)',
    success: 'hsl(142,71%,45%)',
    warning: 'hsl(38,92%,50%)',
    error: 'hsl(0,72%,51%)',
  },
  radius: { sm: 8, md: 12, lg: 16, xl: 24 },
  shadow: {
    premium: { androidElevation: 8, ios: { color: '#000', opacity: 0.15, radius: 20, offsetY: 12 } },
    md: { androidElevation: 4, ios: { color: '#000', opacity: 0.08, radius: 6, offsetY: 4 } },
  },
  spacing: { xs: 8, sm: 12, md: 16, lg: 24, xl: 32 },
  typography: {
    display: { fontSize: 56, fontWeight: '700', lineHeight: 60 },
    title: { fontSize: 32, fontWeight: '600' },
    body: { fontSize: 16, fontWeight: '400' },
    small: { fontSize: 14, fontWeight: '400' },
  },
}

export const lightTheme: Theme = {
  ...darkTheme,
  dark: false,
  color: {
    ...darkTheme.color,
    background: 'hsl(0,0%,100%)',
    foreground: 'hsl(224,9%,20%)',
    card: 'hsl(0,0%,100%)',
    cardForeground: 'hsl(224,9%,20%)',
    secondary: 'hsl(240,5%,96%)',
    muted: 'hsl(240,5%,97%)',
    mutedForeground: 'hsl(224,6%,40%)',
    accent: 'hsl(240,5%,96%)',
    border: 'hsl(240,6%,90%)',
    primary: 'hsl(240,75%,48%)',
  },
}
```

Use React Native `Appearance` to select theme; persist user choice if you support a manual toggle.

```ts
import { Appearance } from 'react-native'
const colorScheme = Appearance.getColorScheme()
const theme = colorScheme === 'dark' ? darkTheme : lightTheme
```

### 2) Gradients (Expo)
Use `expo-linear-gradient` (or `react-native-linear-gradient`).

```tsx
import { LinearGradient } from 'expo-linear-gradient'

export const GradientHero: React.FC<{ style?: any }> = ({ style, children }) => (
  <LinearGradient
    colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
    start={{ x: 0, y: 0 }}
    end={{ x: 1, y: 1 }}
    style={style}
  >
    {children}
  </LinearGradient>
)
```

### 3) Buttons (Variants & Sizes)

```tsx
type ButtonVariant = 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'premium' | 'hero' | 'glass'
type ButtonSize = 'sm' | 'md' | 'lg' | 'xl'

const BUTTON_HEIGHT: Record<ButtonSize, number> = { sm: 36, md: 40, lg: 48, xl: 56 }
const BUTTON_RADIUS: Record<ButtonSize, number> = { sm: 12, md: 12, lg: 16, xl: 16 }

function getButtonStyle(variant: ButtonVariant, size: ButtonSize) {
  const base = {
    height: BUTTON_HEIGHT[size],
    borderRadius: BUTTON_RADIUS[size],
    paddingHorizontal: size === 'xl' ? 20 : size === 'lg' ? 16 : 12,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
  }
  switch (variant) {
    case 'default':
      return [{ backgroundColor: theme.color.primary }, base]
    case 'secondary':
      return [{ backgroundColor: theme.color.secondary }, base]
    case 'outline':
      return [{ borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.background }, base]
    case 'ghost':
      return [{ backgroundColor: 'transparent' }, base]
    case 'premium':
    case 'hero':
      return [base] // wrap with GradientHero
    case 'glass':
      return [{ backgroundColor: 'rgba(255,255,255,0.08)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.2)' }, base]
    case 'link':
      return [{ backgroundColor: 'transparent' }, base]
  }
}
```

### 4) Inputs

```tsx
const inputBase = {
  height: 40,
  borderRadius: 8,
  borderWidth: 1,
  paddingHorizontal: 12,
  backgroundColor: theme.color.background,
  borderColor: theme.color.border,
  color: theme.color.foreground,
}

const inputFocus = { borderColor: theme.color.ring, shadowColor: theme.color.ring, shadowOpacity: 0.25, shadowRadius: 6 }
const inputDisabled = { opacity: 0.5 }
```

### 5) Cards / Containers

```tsx
const card = {
  backgroundColor: theme.color.card,
  borderColor: theme.color.border,
  borderWidth: 1,
  borderRadius: theme.radius.xl,
  padding: theme.spacing.lg,
  // iOS shadow
  shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 20, shadowOffset: { width: 0, height: 12 },
  // Android elevation
  elevation: 8,
}
```

### 6) Shadows & Elevation
- Android: use `elevation` (4–8 typical)
- iOS: `shadowColor`, `shadowOpacity`, `shadowRadius`, `shadowOffset`
- Premium panels: higher radius and soft shadows (see `theme.shadow.premium`)

### 7) Spacing & Layout
- Use `theme.spacing` consistently across margins/paddings.
- Mobile grid: use `flex` with `gap` polyfills or manual margins; prefer 1–2 columns.

### 7.1) Responsive Breakpoints & Tablet Layouts
- Recommended width buckets (dp) for RN:
  - xs (small phones): < 360
  - sm (phones): 360–479
  - md (large phones/phablets): 480–599
  - lg (tablet portrait): 600–839
  - xl (tablet landscape): 840–1023
  - 2xl (large tablet / desktop shells): ≥ 1024
- Layout guidance:
  - Cards: 1 column (xs–md), 2 columns (lg–xl), 3 columns (≥ 1024) where applicable.
  - Paddings: increase horizontal padding by one step on lg+.
  - Navigation: place actions inline for lg+; collapse on smaller sizes.
- Example hook:

```ts
import { useWindowDimensions } from 'react-native'

export function useBreakpoint() {
  const { width } = useWindowDimensions()
  if (width >= 1024) return '2xl'
  if (width >= 840) return 'xl'
  if (width >= 600) return 'lg'
  if (width >= 480) return 'md'
  if (width >= 360) return 'sm'
  return 'xs'
}
```

### 8) Typography in RN
- Load Inter via `@expo-google-fonts/inter`.
- Map weights 400/600/700 to loaded font variants.
- Set `lineHeight` carefully (RN requires explicit pixel values).

### 9) RTL & Language
- Use `I18nManager.allowRTL(true)` and `I18nManager.forceRTL(true|false)` at app start if you need to switch at runtime (requires app reload).
- Mirror paddings/margins using logical helpers and `flexDirection: 'row-reverse'` when `dir === 'rtl'`.

### 10) Animations
- Use `Animated` or `react-native-reanimated`.
- Default timings: 200–350ms; use ease-out for entrances.
- Examples: opacity + translateY for `animate-fade-in` / `animate-slide-up`, scale for `animate-scale-in`.

### 10.1) Haptic Feedback Patterns
- Use `expo-haptics` for consistent vibration effects:

```ts
import * as Haptics from 'expo-haptics'

// Light confirmation (e.g., minor button)
export const hapticLight = () => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)

// Selection change (e.g., tabs, toggles)
export const hapticSelection = () => Haptics.selectionAsync()

// Success / Error notifications
export const hapticSuccess = () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
export const hapticError = () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error)
```

- Recommended mapping:
  - Primary CTA press up: `impact Light`
  - Toggle/switch changes: `selection`
  - Successful action (saved, sent): `notification Success`
  - Error/blocked action: `notification Error`

### 11) Iconography
- Use `lucide-react-native` for brand-consistent icons.
- Sizes: 16–20 in buttons, 24–28 in cards/headers; color = `theme.color.primary` for emphasis.

### 12) Feedback & States
- Success: `theme.color.success`; Error: `theme.color.error`.
- Toasts/snackbars: consider `react-native-toast-message` or a custom banner using the theme.

### 13) Gradients & Surfaces
- Use `GradientHero` wrapper for premium surfaces (hero/promo).
- For CTA buttons use gradient `premium` or `hero` variants.

### 14) Accessibility
- Ensure touch targets ≥ 44×44.
- Respect `reduceMotion` and `dynamicType` (font scaling) where relevant.

### 15) Dark/Light Mode Switching Examples
- Auto-follow system:

```ts
import { Appearance } from 'react-native'
const scheme = Appearance.getColorScheme()
const theme = scheme === 'dark' ? darkTheme : lightTheme

Appearance.addChangeListener(({ colorScheme }) => {
  setTheme(colorScheme === 'dark' ? darkTheme : lightTheme)
})
```

- Manual toggle + persist:

```ts
import AsyncStorage from '@react-native-async-storage/async-storage'

async function toggleTheme() {
  setTheme(prev => (prev.dark ? lightTheme : darkTheme))
  await AsyncStorage.setItem('theme', prev.dark ? 'light' : 'dark')
}

async function loadTheme() {
  const saved = await AsyncStorage.getItem('theme')
  if (saved === 'light') setTheme(lightTheme)
  else if (saved === 'dark') setTheme(darkTheme)
  else setTheme(Appearance.getColorScheme() === 'dark' ? darkTheme : lightTheme)
}
```


---

## Mobile Onboarding Flow (RN/Expo)

Design a polished, 3‑screen onboarding that mirrors the website’s tone and visual language while fitting native expectations.

### Goals
- Communicate value fast (what the app does and why it matters).
- Demonstrate the ease of setup and the core workflow.
- Offer an instant “feel” of the AI chat through a small preview.
- Make it skippable and accessible; persist completion.

### Screen Structure (3 screens)
1) Welcome
- Visuals: GradientHero background (brand purples), logo/wordmark, headline from web hero (localized), short subtitle.
- CTAs: Primary “Continue” (hero/premium variant), secondary “Skip”.
- Footer links: Privacy / Terms (optional).

2) It’s Simple (3 steps)
- Visuals: Horizontal flow of 3 steps (title + 2–3 bullets each), animated connectors that fill 1→2 then 2→3.
- Content: Reuse how‑it‑works steps and bullets from i18n (EN/AR).
- Motion: Each step panel fades/slides in; connector fill animates continuously (2.4s per segment, looping).

3) Demo Chat Preview
- Visuals: Compact chat preview (2–3 messages from a scenario), bot/user bubbles styled like web.
- Controls: Primary “Get Started”; subtle “Try full chat later” label.
- Tip: You can seed content from `demo.scenarios` in i18n.

### Behavior & Persistence
- Store `onboardingCompleted=true` via `AsyncStorage` after finishing or skipping.
- On next app start, route to Home/Auth instead of onboarding.
- Support a language toggle in onboarding if desired (EN/AR) — write `lang` + set RTL with `I18nManager` (may require reload).

### Visual Spec
- Background: GradientHero or brand surface; subtle glow orbs (`opacity` 0.08–0.12; `blur` ~ 40–60 on web, mimic with semi‑transparent Views on RN).
- Typography: Title = theme.typography.title; subtitle/body = theme.typography.body/small.
- Cards: `borderRadius: theme.radius.xl`, `padding: theme.spacing.lg`, elevation/shadow from theme.shadow.premium.
- Buttons: use `premium`/`hero` variant for primary, `outline`/`secondary` for optional; height from BUTTON_HEIGHT.
- Connectors: Horizontal `View` (height 6–8) with LinearGradient base; animated overlay width (Animated.Value) to fill from 0→100%.

### React Navigation
- Stack: `OnboardingStack` → `AuthStack`/`HomeStack`.
- `Onboarding` → 3 screens or a single Pager with three pages.

### Pager Implementation (Example)
```tsx
import PagerView from 'react-native-pager-view'

export function OnboardingPager() {
  return (
    <PagerView style={{ flex: 1 }} initialPage={0} pageMargin={16}>
      <Welcome key="welcome" />
      <SimpleFlow key="flow" />
      <ChatPreview key="preview" />
    </PagerView>
  )
}
```

### Welcome Screen (Example)
```tsx
import { LinearGradient } from 'expo-linear-gradient'

export function Welcome({ navigation }: any) {
  return (
    <LinearGradient
      colors={[theme.color.primary, theme.color.primaryLight]}
      start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
      style={{ flex: 1, padding: 24, justifyContent: 'center' }}
    >
      <Logo />
      <Text style={{ ...theme.typography.title, color: 'white', marginTop: 16 }}>
        It is simple!
      </Text>
      <Text style={{ color: 'white', opacity: 0.85, marginTop: 8 }}>
        Deliver instant, intelligent support 24/7 with our AI-powered platform.
      </Text>
      <PrimaryHeroButton title="Continue" onPress={() => navigation.navigate('flow')} />
      <OutlineButton title="Skip" onPress={() => navigation.replace('home')} />
    </LinearGradient>
  )
}
```

### Simple Flow (3 Steps) — Connectors
```tsx
import Animated, { useSharedValue, useAnimatedStyle, withTiming, withDelay, repeat } from 'react-native-reanimated'

export function HorizontalConnectors() {
  const cycle = 4800 // ms
  const w1 = useSharedValue(0)
  const w2 = useSharedValue(0)

  React.useEffect(() => {
    w1.value = repeat(withTiming(1, { duration: cycle/2 }), -1, false)
    w2.value = repeat(withDelay(cycle/2, withTiming(1, { duration: cycle/2 })), -1, false)
  }, [])

  const s1 = useAnimatedStyle(() => ({ width: `${w1.value*100}%` }))
  const s2 = useAnimatedStyle(() => ({ width: `${w2.value*100}%` }))

  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16, paddingHorizontal: 24 }}>
      <StepCard title="Register" bullets={["Set up business profile","Upload SOP/KB","Or let AI generate it"]} />
      <View style={{ flex: 1, height: 8, borderRadius: 999, backgroundColor: 'rgba(255,255,255,0.15)', overflow: 'hidden' }}>
        <Animated.View style={[{ height: '100%', backgroundColor: theme.color.primary }, s1]} />
      </View>
      <StepCard title="Create your AI Agent" bullets={["Configure persona/tools","Get portal link"]} />
      <View style={{ flex: 1, height: 8, borderRadius: 999, backgroundColor: 'rgba(255,255,255,0.15)', overflow: 'hidden' }}>
        <Animated.View style={[{ height: '100%', backgroundColor: theme.color.primary }, s2]} />
      </View>
      <StepCard title="Paste, paste, paste" bullets={["Paste link across channels","Sit back & monitor"]} />
    </View>
  )
}
```

### Chat Preview (Example)
```tsx
export function ChatPreview() {
  const scenario = i18n.t('demo.scenarios')[0]
  const sample = scenario.conversation.slice(0, 3)
  return (
    <View style={{ margin: 24, padding: 16, borderRadius: 16, backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border }}>
      {sample.map((m: any, i: number) => (
        <View key={i} style={{ alignItems: m.isBot ? 'flex-start' : 'flex-end', marginVertical: 4 }}>
          <View style={{ maxWidth: '80%', padding: 10, borderRadius: 16, backgroundColor: m.isBot ? theme.color.card : theme.color.primary }}>
            <Text style={{ color: m.isBot ? theme.color.cardForeground : '#fff' }}>{m.text}</Text>
          </View>
        </View>
      ))}
    </View>
  )
}
```

### Navigation & Persistence
- On finishing or skipping, call `AsyncStorage.setItem('onboardingCompleted','1')` then `replace('home')`.
- If you include language selection, set `lang` then prompt for reload if using `I18nManager.forceRTL(true)`.

### Accessibility & Internationalization
- All text from i18n: reuse keys from the web for EN/AR.
- RTL mirroring: reverse row direction or use `start/end` alignments.
- Focus order (TV or screen reader): sequential; labels on buttons; large touch targets.

### Analytics (Optional)
Track `onboarding_view`, `onboarding_continue`, `onboarding_skip`, `onboarding_complete` with your analytics stack.
