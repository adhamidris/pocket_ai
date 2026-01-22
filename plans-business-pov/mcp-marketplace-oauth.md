# Plan

1. Inspect existing MCP connection auth + credential encryption patterns.
2. Add shared OAuth provider + state models (encrypted secrets, expiring state tokens).
3. Implement backend OAuth start/callback/refresh endpoints with CSRF + safe redirects.
4. Update marketplace catalog + UI to launch OAuth popup for supported providers.
5. Add tests for OAuth start/callback/refresh and token refresh behavior.

# Business POV

## What changes for users

- Users can connect OAuth-based MCP tools (e.g., Gmail) with a standard “Sign in” flow instead of pasting tokens.
- Once connected, the MCP connection is automatically created/updated and appears as “Connected” in the marketplace.
- Tokens refresh automatically when near expiry (when a refresh token is available), reducing broken tool calls.

## Scenarios

1. **Founder connects Gmail for the first time**
   - User clicks “Add” on Gmail → a popup opens to Google OAuth → user approves → popup closes automatically.
   - Marketplace refreshes and Gmail shows as connected without the user manually entering tokens.
   - Success measured by: reduced setup friction + fewer failed “Test connection” attempts.

2. **Ops connects Slack for a sales team**
   - User clicks “Add” on Slack → Slack OAuth popup → approves scopes → popup closes and Slack becomes available to the agent.
   - If Slack is configured for token rotation, refresh tokens are stored and used automatically.

3. **Provider not configured (admin setup missing)**
   - User clicks “Add” on an OAuth MCP where `oauthProvider` is set but no matching `OAuthProvider` exists → popup shows a clear failure and closes/redirects back.
   - Expected regression: connection cannot be completed until an admin configures the provider; mitigated by clear error messaging and leaving non-mapped OAuth items on the legacy modal flow.

4. **Security: redirect parameter abuse**
   - A malicious redirect URL is provided via query string → backend rejects it and falls back to `/dashboard/mcp/`.
   - Success measured by: no open-redirects; OAuth callbacks always return users to PocketAI.

