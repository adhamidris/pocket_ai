# Phase 0 – Baseline & Parity Capture

This document freezes the current React/Vite frontend so we can reproduce the exact UI with server-rendered Django templates. It captures routes, shared components, styling tokens, interaction patterns, and API payloads that the Django implementation must honor.

## 1. Route & Page Inventory
| Path | React entry point | Key content blocks | Interaction notes |
| --- | --- | --- | --- |
| `/` | `src/pages/Index.tsx` | Header, hero, trusted-by strip, feature grid, testimonials, pricing tiers, FAQ, mobile promo, footer, floating chat widget | Animated counters, smooth-scroll to hash targets, global toasts/login modal toggles via header |
| `/register` | `src/pages/Register.tsx` | Multi-step onboarding form, role selection cards, business profile step | Client-side validation, uses `react-hook-form`, localized copy via i18n, success toast |
| `/privacy`, `/terms`, `/cookies`, `/gdpr`, `/security`, `/compliance` | Corresponding page components under `src/pages/` | Static policy copy with anchored sections and highlights | Hash navigation to sections, gradient text on active anchor |
| `/integrations` | `src/pages/Integrations.tsx` | Integrations grid, partner logos, CTA blocks | Hover animations, cards reuse shared styles |
| `/dashboard` (`Home2`) | `src/pages/Home2.tsx` | Overview dashboard, KPI cards, activity feed, quick actions | Pulls mock data, uses charts and progress components |
| `/dashboard/customers`, `/dashboard/leads`, `/dashboard/cases`, `/dashboard/conversations`, `/dashboard/agents`, `/dashboard/knowledge` | Components under `src/pages/` | Data tables, filters, detail side panels depending on page | Fetches lists via React Query hooks (`useCustomers`, `useLeads`, etc.), uses toasts for error handling |
| `/:businessSlug/:agentSlug` | `src/pages/ChatPortal.tsx` | Public chat portal, message history, input composer, CSAT prompt | Requires SSE stream, local storage for session token, toasts, download transcript, stop streaming, CSAT submission |
| `*` | `src/pages/NotFound.tsx` | Gradient 404 splash with CTA | Static aside, same design token usage as landing |

## 2. Shared Layout & Components
- `src/components/Header.tsx`, `Footer.tsx`, `LanguageToggle.tsx`, `LoginModal.tsx`: Control navigation, authentication CTAs, theme/language toggles, and modal copy. All button labels and headlines are localized via `useI18n`.
- Marketing sections (Hero, Features, Testimonials, Pricing, FAQ, MobileAppPromo, TrustedBy) each own their copy blocks and textured backgrounds. They consume translation keys from `src/i18n/en.ts` (and `ar.ts`).
- Dashboard widgets (cards, tables, charts) sit under `src/pages` but rely heavily on the ShadCN-style UI primitives in `src/components/ui/`.
- Chat experience is split into composable pieces inside `src/components/chat/` (header, transcript, message item, input, CSAT prompt). These should map to Django template partials and progressive enhancement scripts.
- `ChatWidget.tsx` and `DemoChatWidget.tsx` embed mini chat previews on marketing pages; they reuse the same message rendering logic as the portal.
- Utility wrappers such as `ScrollToTop.tsx`, `SecurityStrip.tsx`, and `ChatWidget` orchestrate global behaviors that we’ll need to mirror with Django middleware or template includes.

## 3. Styling & Design Tokens
- **Tailwind config** (`tailwind.config.ts`): Dark mode class-based, global content paths under `src/`, custom font family (`Inter`), numerous extended color tokens mapped to CSS variables, radius presets, shadows (`shadow-premium`, `shadow-soft`), keyframes (`fadeIn`, `slideUp`, `scaleIn`, `accordion-*`), and plugin `tailwindcss-animate`.
- **CSS variables** (`src/index.css`): Defines premium dark palette, gradients (`--gradient-primary`, `--gradient-hero`, `--gradient-surface`, `--gradient-glass`), semantic colors, radii, transitions. Light/dark mode share tokens with overrides in `.dark`. Additional design helpers: policy section highlight animation, streaming text effect, panel glow, handwriting font.
- **Global imports**: Google Fonts (Inter weights 300–700). Custom `@font-face` `EliteHand`.
- **Utilities**: Many components rely on structural classes such as `text-gradient-hero`, `animate-panel-glow`, `streaming-text`, etc. These must ship in the compiled `main.css`.
- Existing build relies on PostCSS pipeline (see `postcss.config.js`) and Vite. For Django we will reuse Tailwind CLI to produce `frontend/static/css/main.css` with identical tokens.

## 4. Interaction & State Patterns
- **Data fetching**: `@tanstack/react-query` manages API calls. Hooks like `useChatSession`, `useChatMessages`, `useCustomers`, `useConversations`, `useAgents` encapsulate fetch params, caching, and error handling. Django rewrite will need server-side data preparation plus vanilla JS for incremental updates where required.
- **Real-time chat**: `src/services/chat.ts` exposes `openChatEventStream` (EventSource heartbeat), `streamAssistantResponse` (SSE over POST), and `cancelAssistantResponse`. `ChatPortal.tsx` stitches these into streaming, optimistic UI updates, transcript merge, and CSAT prompts.
- **Local storage**: Session tokens persisted by `persistSessionToken` / `loadStoredSessionToken` under keys derived from business + agent handles. Business ID detection also reads `pocket_ai_business_id` and `pocket_ai_business` entries (see `services/chat.ts` and `services/http.ts`).
- **Notifications**: Global toasts via two systems—ShadCN `Toaster` and Sonner `Toaster`—both instantiated in `App.tsx`. Messages originate from hooks and service error handling.
- **Forms**: Register page leverages `react-hook-form` with validation rules, role selection toggles, and multi-step UI. Modals and drawers rely on ShadCN components (dialog, sheet, form).
- **Themes & language**: `I18nProvider` controls translation direction (`dir`), string lookup via context. Language toggle toggles `en`/`ar`. Theme toggles (light/dark) managed via CSS class on `<html>`.
- **Animations**: IntersectionObserver-based counters (`Hero`), CSS transitions/animations invoked via Tailwind classes, Carousel/Charts on dashboard (Chart component uses `recharts` via dynamic import).

## 5. API Surface & Payload Dependencies
All API traffic routes through `jsonFetch` (`src/services/http.ts`), which injects headers (`X-Request-ID`, `X-Business-Id`, optional bearer token) and handles network diagnostics. Endpoints currently referenced:

| Domain | Endpoint(s) | Method(s) | Used by |
| --- | --- | --- | --- |
| Auth | `/v1/auth/login` | POST | Login modal & Register success flow |
| Registration | `/v1/registration/sessions`, `/v1/registration/sessions/{id}/business`, `/v1/registration/businesses/{businessId}/agent`, `/v1/registration/businesses/{businessId}/uploads`, `/v1/registration/sessions/{id}/complete` | POST/GET | `src/services/registerApi.ts` for onboarding wizard |
| Agents | `/v1/agents` | GET | Dashboard agents list (`useAgents`) |
| Customers | `/v1/customers`, `/v1/customers/{id}`, `/v1/customers/{id}/notes`, `/v1/customers/{id}/notes/{noteId}`, `/v1/customers/{id}/activity`, `/v1/customers/import` | GET/POST/PATCH | Customers page + detail drawers |
| Conversations | `/v1/conversations`, `/v1/conversations/{id}` | GET | Conversation list & detail |
| Chat portal | `/v1/portal/resolve/{businessSlug}/{agentSlug}`, `/v1/chat-portal/sessions`, `/v1/chat-portal/messages`, `/v1/chat-portal/csat`, `/v1/portal/events` (SSE), `/v1/portal/stream/send` (SSE POST), `/v1/portal/stream/cancel` | GET/POST/SSE | Public chat portal (resolve handle, create session, list/send messages, CSAT, live streaming) |

Any Django migration must replicate these payload contracts or provide compatible adapters to avoid rewriting the client logic during phased rollout.

## 6. Text & Localization
- All marketing, dashboard, and chat copy lives under `src/i18n/en.ts` and `src/i18n/ar.ts`, consumed via `I18nProvider` / `useI18n`. Namespaces include `hero`, `trustedBy`, `auth`, `howItWorks`, `pricing`, `faq`, `dashboard`, `chat`, etc.
- Several components fall back to hard-coded English strings (`ChatPortal`, CSAT toasts, legal pages). These need extraction during the Django refactor so that template literals or Django i18n can supply the strings server-side.
- Directionality (`dir`) affects class names (e.g., `rtl-rotate-180`), so Django templates must expose language/direction toggles and guard CSS accordingly.

## 7. Assets & External Dependencies
- Static assets: `public/placeholder.svg`, `robots.txt`, and Netlify `_redirects`.
- Icons and illustrative SVGs are inline within components (Lucide icons via `lucide-react`, trust badges).
- JS dependencies influencing UI: `lucide-react`, `framer-motion` (if present in components), ShadCN UI primitives, React Query, Sonner, Recharts, Hero demo chat (typed logic).
- Fonts and gradient backgrounds are applied dynamically; ensure Django static pipeline preserves the same asset URLs.

## 8. Pending Capture Tasks
- Generate high-resolution screenshots (desktop/mobile) of each route, plus detail panes for dashboard pages and chat states (empty, active, CSAT). Store under `django_migration/reference/` (to be added).
- Export Tailwind build (`npm run build` currently outputs to `dist/assets`) and archive the compiled CSS as a comparison artifact before retooling the pipeline.
- Log current environment variables influencing theming/API (`VITE_API_BASE_URL`, `VITE_DEV_BUSINESS_ID`) for Django settings parity.

This baseline will anchor Phase 1, where we scaffold the Django project and begin moving shared assets while preserving the captured tokens and interactions.
