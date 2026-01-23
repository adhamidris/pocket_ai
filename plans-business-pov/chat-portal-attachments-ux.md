# Plan + Business POV: Chat Portal Attachment UX (Uploads + PDF Artifacts)

## Plan
1. Stream internal tool lifecycle events to the portal so “Generating…” states are visible.
2. Represent uploads and generated PDFs as first-class `content_blocks` (`file`, `file_text`) persisted in the transcript.
3. Avoid persisting signed download tokens in messages by adding a “fresh download URL” endpoint and fetching tokens on demand.
4. Update the portal UI to render attachment cards with direct download and collapsible extracted-text blocks.
5. Update prompt guidance so the assistant never prints raw `download_url` tokens.

## Business POV
This change makes file handling feel native and trustworthy in the public chat portal, similar to modern assistant UIs (artifacts/attachments), while protecting sensitive signed URLs from being copied into transcripts.

### Scenario 1 — Visitor uploads a PDF
- **Before:** Upload appears as plain text (“Uploaded file: …”) and it’s unclear if the assistant can read it.
- **After:** The transcript shows an **attachment card** (filename, PDF meta). The assistant can immediately answer questions grounded in the upload.
- **Success metric:** Higher “first question answered from upload” rate; fewer “can you access my file?” follow-ups.

### Scenario 2 — Visitor requests “Generate a PDF”
- **Before:** The portal may look idle during generation; output links can be malformed or unclickable.
- **After:** The portal shows a clear **in-message loading state** (tool lifecycle) and then a **downloadable artifact card** with a single Download CTA.
- **Success metric:** Increased download completion rate; reduced confusion/abandonment during tool execution.

### Scenario 3 — Visitor requests “Extract text from this PDF”
- **Before:** Extracted text appears as noisy tool JSON or is truncated/broken.
- **After:** The transcript shows a **collapsed/expand** extracted-text component with a Copy action and a Download PDF action.
- **Success metric:** Lower “please paste the text” repeats; better perceived quality.

### Scenario 4 — Visitor refreshes or returns later
- **Before:** Previously generated signed URLs can expire and break.
- **After:** The portal fetches a **fresh signed download URL on click**, so downloads remain reliable across page reloads.
- **Success metric:** Near-zero “download link expired” reports.

