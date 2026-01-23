# Plan: Chat Portal File Uploads + First-Party PDF Tools

## Plan (Engineering)
1. Add conversation-scoped file models for uploads + generated artifacts (binary-safe, tenant-scoped).
2. Add secure portal endpoints to upload files and download artifacts via signed, expiring links.
3. Parse uploaded PDFs on ingest and store extracted text in chunked form for retrieval.
4. Expose conversation-file tools to the MCP orchestrator (`search_conversation_files`, `read_conversation_file`) and inject “files available” context into the system prompt each turn.
5. Implement first-party PDF tools:
   - (A) Generate PDFs (text/markdown → PDF)
   - (B) Manipulate PDFs (merge + page extract)
   - (C) Extract text from PDFs (page/range bounded)
6. Update chat portal UI to support direct file upload and show download links returned by tools; add tests for auth, limits, and signed downloads.

## Business POV (User Experience)

### Scenario 1: Visitor uploads a PDF and asks questions
- Visitor uploads `policy.pdf` directly in the chat portal.
- System immediately confirms upload and (after processing) the assistant can answer: “What’s the cancellation window?” using the PDF as evidence.
- Success metric: first-answer usefulness increases; fewer “I can’t access that file” failures.

### Scenario 2: Visitor requests a generated PDF artifact
- Visitor says: “Create a PDF version of this summary for my manager.”
- Assistant uses the internal PDF generation tool and responds with a single download link.
- Success metric: visitors can self-serve shareable artifacts without leaving chat.

### Scenario 3: Visitor wants basic PDF manipulation
- Visitor uploads two PDFs and asks: “Merge these and give me one file.”
- Assistant merges and returns a download link to the merged PDF.
- Success metric: fewer manual back-and-forth steps; higher completion rate for document workflows.

### Scenario 4: Security and governance expectations
- Files are scoped to the conversation (and tenant) and are not added to the business-wide knowledge base by default.
- Download links expire and are not guessable; uploads are size/page bounded; only allowed content types are accepted.
- Success metric: no cross-tenant leakage incidents; predictable retention/cleanup behavior.

### Potential regressions to watch
- Large PDFs may take longer to process; the UI should clearly show “processing” vs “ready”.
- Some PDFs (scanned images) may extract poorly without OCR; the assistant should explain limits and request a clearer source when needed.

