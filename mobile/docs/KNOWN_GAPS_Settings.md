KNOWN GAPS — Settings & Theming (Mobile)

Status: UI stubs are implemented; persistence and deep integrations are deferred.

Deferred/Out-of-Scope Items
- Theme validation across screens: comprehensive visual QA and automatic token contrast checks across all modules.
- Brand asset CDN: upload/storage for logos/favicons with cache-busting and image processing.
- Multi-theme support: create/manage multiple themes, environment targeting (staging/prod), and versioning.
- Per-channel overrides: allow distinct token overrides for WhatsApp/Instagram/Facebook/Web surfaces.
- Enterprise SSO brand: enforce company branding via policy and identity provider claims.
- i18n resources: full translation coverage for settings UI and live RTL beyond preview; pluralization and date/time locale packs.
- Email templates branding: apply theme tokens to transational/marketing email templates and previews.
- Export/Import theme JSON: robust schema validation, conflict resolution, and user-facing import/export flows.

Platform/Infra Gaps
- Server persistence: settings currently local-only; needs backend CRUD APIs and auth.
- Access control: role/permission system (who can publish themes, manage API keys, etc.).
- Audit log: track settings/theme changes with user, timestamp, and diffs.
- Offline queue: persist queued mutations with AsyncStorage, retries, and conflict prompts.

Analytics & QA
- Analytics granularity: expand event payloads (who/what/where) and add funnels for settings completion.
- Automated tests: unit tests for reducers and e2e flows for profile, theming, publish, locale, notifications.


