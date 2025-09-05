# Mobile App Assets Guide (Expo)

This guide specifies the assets required for iOS and Android, how to style them to match the brand, and where to place them in this repo.

Brand reference
- Primary purple: `#644EF9` (matches web `--primary` hues)
- Gradient (hero): approx. `#6B7CFF → #9EA5FF → #C3C5FF` (use the web gradient as the visual source of truth)
- Background (dark): `#0E131A`

Directory
- App icons: `mobile/assets/icons/`
- Splash: `mobile/assets/splash/`
- Store assets (screenshots/feature graphic): `mobile/store-assets/`

App icons
- iOS App Store icon (master): 1024×1024 PNG, no transparency (Apple applies the mask).
  - Path: `mobile/assets/icons/ios-1024.png` (configure in app.json under ios.icon)
  - Safe area: keep key elements inside ~70–80% center area.
- Android adaptive icon:
  - Foreground: 1024×1024 PNG glyph with transparency → `mobile/assets/icons/adaptive-foreground.png`
  - Background: use brand purple `#644EF9` (already set in app.json)
- Notification small icon (Android): fully white transparent glyph (no color) → `mobile/assets/icons/notification-icon.png` (set in app.json)

Splash screens
- Use brand gradient or dark background (#0E131A) with centered logo.
- Paths (already configured in app.json):
  - Light: `mobile/assets/splash/logo.png`
  - Dark: `mobile/assets/splash/logo-dark.png`
- Recommended logo scale: ~30–40% of width, high contrast to background.
- Resize mode: contain

Store assets
- Google Play feature graphic: 1024×500 PNG (no rounded corners) → `mobile/store-assets/feature-graphic.png`
- App Store screenshots (recommend):
  - iPhone 6.7" (Pro Max): 1290×2796 or 1242×2688
  - iPhone 6.1" (Pro): 1170×2532
  - iPad Pro 12.9": 2048×2732
- Play screenshots:
  - 1080×1920 or higher portrait; include 4–8 images
- Visual guidance:
  - Use gradient hero backgrounds and concise captions matching the website voice.
  - Include dark and light examples to showcase theme switching.

app.json configuration
- iOS
  - `ios.icon`: `./assets/icons/ios-1024.png`
  - `supportsTablet`: true (already set)
- Android
  - `android.adaptiveIcon.foregroundImage`: `./assets/icons/adaptive-foreground.png`
  - `android.adaptiveIcon.backgroundColor`: `#644EF9`
  - `android.notification.icon`: `./assets/icons/notification-icon.png`
  - `android.notification.color`: `#644EF9`
- Splash
  - `splash.image` and `splash.dark.image` set to the logo images above

Verification
- Run `npm run start` and check the splash on both themes.
- For icons, run a quick EAS build preview or `expo run:ios` / `expo run:android` to see device icons.
- Ensure small notification icon renders as expected on Android (white-only glyph).

Notes
- Replace the placeholder PNGs with real designs.
- Keep a Figma file as the single source of truth for brand gradients and glyph construction.
