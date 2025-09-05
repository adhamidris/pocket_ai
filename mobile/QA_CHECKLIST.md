# QA Checklist (Mobile)

Use this list to validate quality across devices and flows. Keep it updated as features evolve.

## Functional
- Onboarding
  - Welcome renders gradient; Continue/Skip works; persists onboardingCompleted
  - SimpleFlow connectors animate 1→2 then 2→3 (loop)
  - ChatPreview shows seeded messages
- Home
  - Hero CTAs pressable; toasts show feedback
  - Features snapshot loads (or skeletons → data → retry works)
  - Testimonials carousel auto-advances; swiping snaps; dots update
  - Pricing summary loads (or skeletons → data → retry works)
  - “See full pricing” navigates to Pricing screen
- Pricing
  - Monthly/Yearly toggle works; haptic selection feedback
  - Pull-to-refresh refetches data; no visual glitches
- Chat
  - Header back/ share work (share sheet opens)
  - Composer supports multiline; send disabled when empty; sends message; bot replies after typing indicator
  - Deep link `aisupport://chat/nancy` opens Chat; notification REPLY injects initial message

## Offline & Loading
- Airplane mode shows OfflineBanner; dismisses automatically on reconnect
- Skeletons visible for features/testimonials/pricing during load
- Retry controls work

## Theming
- Dark/light parity: backgrounds, cards, text, borders, gradients
- Gradients match brand look in hero/promo areas

## RTL & i18n
- Switch to AR displays localized text
- Ensure obvious UI mirroring (FABs, spacing) remains correct

## Motion & Haptics
- Animations 200–350ms, ease-out; no abrupt motion
- Haptics for primary CTA presses, selection toggle, success/error toasts

## Accessibility
- Touch targets ≥ 44×44
- Icon-only buttons have accessibilityLabel (Back, Share, Send)
- Sufficient color contrast in both themes
- Dynamic type/ font scaling doesn’t break layout (spot check)

## Tablet
- Landscape/portrait show reasonable spacing
- Multi-column opportunities identified (features/pricing) — acceptable on tablets

## Performance
- Scroll perf on Home and Chat acceptable (no stutters)
- No redundant re-renders on refresh

## Store assets
- Icons/ splash look correct on devices; notification small icon appears correctly on Android
- Screenshots composed per ASSETS_GUIDE.md

## Deep linking & Notifications
- Universal/App links navigate to expected screens
- Notification category actions (OPEN/REPLY) route correctly; REPLY text appears in Chat

---

## Known Gaps / Future Enhancements
- Replace simulated fetch with real endpoints
- Add typing indicator for user as well (optional)
- Virtualize chat messages for very long threads
- Add E2E test path for onboarding and deep link routing
