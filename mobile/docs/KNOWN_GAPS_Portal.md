# KNOWN GAPS — Hosted Chat Portal (Mobile Demo)

This Portal is a UI-first demo. The following items are intentionally deferred or mocked:

- Real transport layer (WebSocket/HTTP long-poll) and reconnection logic
- Audio recording upload/encoding, permissions persistence, background capture
- Streaming partial assistant responses and real typing indicators
- Emoji picker, rich text formatting, and message editing/deletion
- File virus/malware scanning, MIME sniffing, and secure storage URLs
- True TTS playback (voice selection, buffering, interruption policies)
- Voice diarization/speaker detection and accurate timestamps
- Human queue depth from backend, SLA timers, multi-queue routing
- Push channel capture (device tokens), background notifications, deep links
- Full web widget parity testing and embed SDK alignment
- Comprehensive i18n (pluralization, RTL assets, fonts) beyond basic copy bank
- Theming from backend profiles (fonts, avatar shapes, bubble radii) with caching
- Analytics batching/flush policies and offline event queue
- Accessibility full audit (focus order, rotor actions, large text scaling)
- E2E error handling (fail sends, retry with backoff, per-message status)

These are candidates for a backend integration and polish phase.

