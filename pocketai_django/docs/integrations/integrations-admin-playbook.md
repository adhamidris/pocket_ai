# Admin Playbook — Google Sheets Knowledge Integrations

## 1. Guided Flow (Connect → Configure → Confirm)

### Step 1: Connect
1. Navigate to **Knowledge → Integrations**.
2. Click **Connect Google Drive**. In the OAuth prompt, sign in with the workspace account that owns the sheets.
3. After consent, the integration card should show `status: Connected` with the linked account email and `nextSyncAt`.
   - If it stays in `Syncing` for more than 2 minutes, refresh and check for `sync_error`; see troubleshooting below.

### Step 2: Select Sheets
1. Open the integration’s overflow menu → **Configure sheets**.
2. Search for the spreadsheets you want (limit 50 per fetch). Select the tabs you plan to ingest.
3. For each tab, set:
   - **Visibility** (`private`, `internal`, or `shared`).
   - **Sync frequency** (`hourly`, `daily`, `weekly`, or manual).
   - **Column privacy**: move any sensitive fields (SSN, account numbers, notes) into **Internal only** or **Excluded**. Required columns from your policy are listed at the top; the UI blocks saving until they’re masked.

### Step 3: Review & Confirm
1. Press **Save selection**. The backend validates masking rules and persists the resources.
2. From the integration card, hit **Sync now** to immediately pull the latest data. The toast will report `rowsIngested` and `nextSyncAt`.
3. Once the sync finishes, the Knowledge dashboard shows the resulting uploads; confirm the row counts and run a spot check for masked columns.

## 2. Standard Operating Procedures

| Scenario | Action | Owner |
| --- | --- | --- |
| **Onboarding** | Follow the Connect → Configure → Confirm flow above; document sheet names added to each tenant. | Customer Success |
| **Periodic review** | Monthly: verify that `lastSyncedAt` is recent, `staleResourceCount=0`, and privacy settings still match current policies. | Support Ops |
| **Validation before release** | Run the smoke-test checklist (`docs/integrations/integrations-qa-plan.md`) before rolling out major integrations changes. | QA Lead |

## 3. Troubleshooting Playbook

### A. OAuth or Credential Issues
- **Symptom:** Integration shows `status: Error` with `sync_error="Google token refresh failed"`.
- **Steps:**
  1. Click **Reconnect** → redo OAuth with the latest Google account.
  2. Confirm the account still has access to the selected sheets.
  3. Run **Sync now**; verify error clears.
- **Escalate if:** Reconnect fails twice; open an incident with logs from `sync_knowledge_integrations`.

### B. Missing / Renamed Sheets
- **Symptom:** `staleResourceCount > 0` or card shows “Sheet removed, reconfigure”.
- **Steps:**
  1. Open sheet configuration; stale entries appear with warnings.
  2. Re-select the renamed tabs or remove them entirely.
  3. Save, then run **Sync now**; confirm stale list clears.

### C. Google API Quota / Rate Limits
- **Symptom:** Sync response includes `status="error"` with message mentioning `429`/quota.
- **Steps:**
  1. Retry manual sync after ~5 minutes (exponential backoff is already applied).
  2. If repeated, stagger schedules (e.g., move from hourly → daily) and consider reducing sheet count.
  3. File a ticket to request higher Google API quotas if business impact persists.

### D. Privacy Violations
- **Symptom:** Save attempt returns `VALIDATION_ERROR: Resource #1 is missing masking for required columns`.
- **Steps:** Move the listed columns into **Internal only** or **Excluded**; re-save. If the column names differ (e.g., localized headers), edit the sheet to match policy naming or extend the policy list.

## 4. Training & Communication Assets
- **Deck:** 5-slide overview (existing pitch deck, section “Knowledge Integrations”) — update with new metrics screenshots.
- **Tutorial Video Outline:**  
  1. Intro (30s) — why connect sheets.  
  2. Live demo (3m) — connect, select, mask, sync.  
  3. Troubleshooting (1m) — reconnect + quota tips.  
  4. Resources (30s) — link to this playbook + QA checklist.
- **FAQ Additions:**  
  - “What happens if a sheet is deleted?” → It’s marked stale; reconnect via the configuration modal.  
  - “How do I verify my columns are masked?” → Check the privacy chips under each selected resource; they match what the backend enforces.

## 5. Owners & Next Steps
- **QA Lead:** Schedule usability sessions (3 participants) before GA.
- **Support Ops:** Maintain this playbook; review quarterly.
- **Docs / Enablement:** Publish the tutorial video and share with CSMs; gather feedback for future connector onboarding.
