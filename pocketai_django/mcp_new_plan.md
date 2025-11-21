• 0. Target Behavior & Constraints

  - Only Django + MCP orchestrator (McpOrchestratorService); ignore legacy orchestrator + FastAPI.
  - DeepSeek is the primary MCP provider (DeepSeekToolsProvider).
  - User‑visible stream should:
      - Never show investigative fillers like “I’ll search…”, “Let me read…”, “I’m checking…”.
      - Use status events (spinner, “Searching…”, “Reading…”) to signal internal work.
      - Show only the real answer text (and brief, explanatory meta like “I searched and found X”) as streamed chunks and in the persisted message.
  - We’re allowed to redesign the pipeline, not just patch.

  The plan below is structured in layers and phases, each with concrete code touchpoints and expected outcomes.

  ———

  1. Architecture Overview & Separation of Concerns

  Goal: Make the streaming path explicitly two‑phase: internal model/tool loop vs. user‑facing answer stream.

  Key principles

  - Phase A (tool loop): DeepSeek + tools, possibly streaming internally, but no text goes to the visitor. Only status events (searching/reading/planning).
  - Phase B (answer pass): DeepSeek (likely without tools) produces the final answer. Only this phase streams text to the user.
  - Strict interfaces:
      - MCP orchestrator manages internal streaming and tool calls.
      - Chat portal only ever sees “clean answer text” plus status events and action events.

  Impactful files

  - Orchestrator core: pocketai_django/apps/services/mcp/orchestrator.py
  - MCP provider: pocketai_django/apps/services/llm_provider.py (DeepSeekToolsProvider, _consume_chat_completion_stream)
  - Portal SSE bridge: pocketai_django/apps/api/chat_portal.py (stream_send / event_stream)
  - Prompts: pocketai_django/apps/services/mcp/prompts.py

  Expected outcome

  - The current leak of “I’ll search / I’m checking” from DeepSeek’s tool‑calling turns is structurally impossible, because only the final answer phase streams text externally.

  ———

  2. Phase 1 – Redesign MCP Streaming Flow

  Objective: Explicitly split “tool loop” streaming from “answer streaming” inside McpOrchestratorService.

  1. Refactor _execute_turn into two conceptual passes
      - Current: _execute_turn runs a loop:
          - Passes on_stream_delta directly into provider.chat(...).
          - For each iteration, DeepSeekToolsProvider streams deltas and _buffer_delta forwards them to on_response_text_delta → portal SSE.
      - Proposed:
          - Internal tool loop pass
              - Always call provider with on_stream_delta=None.
              - Use streaming HTTP on MCP only for assembling tool_calls, but do not wire on_stream_delta to the portal; suppress text.
              - Still send on_status_change events as today:
                  - “thinking…”
                  - “searching_knowledge” / “reading_document”
                  - “planning_actions”
              - Collect tool calls and tool results until we get an assistant message with no tool_calls.
              - Track diagnostic fields (tool traces, knowledge_reads) as today.
          - Final answer pass
              - Once we reach an assistant message with no tool_calls, we have:
                  - conversation, user_message
                  - tool context / knowledge_reads / coverage ledger
              - Invoke a second provider call with tools=None, using:
                  - A final‑answer system prompt (see Phase 3).
                  - A user payload that includes:
                      - Latest user message.
                      - Condensed description of relevant snippets / knowledge_reads.
                      - Maybe a short summary of what was done in the tool loop.
                  - on_stream_delta=_answer_stream_callback, which forwards to on_response_text_delta.
  2. Add final answer streaming callback in orchestrator

     In McpOrchestratorService._execute_turn:
      - Introduce answer_streamed_chunks: list[str] = [].
      - Define _answer_stream_chunk(chunk: str):
          - Append to answer_streamed_chunks.
          - Forward to on_response_text_delta (if provided) after running text sanitization (Phase 2).
      - Call provider.chat in the final answer pass with:
          - tools=None
          - on_stream_delta=_answer_stream_chunk
  3. Finalize plan from final answer pass, not from tool loop content
      - Use assistant_message (from final answer pass) as the canonical assistant_message for _build_plan_from_assistant.
      - Ensure StreamingTurnContext.streamed_chunks is set to tuple(answer_streamed_chunks) (not the tool‑loop chunks).
      - plan.response_text should be set to:
          - answer_text from final answer pass, after sanitization.
          - This ensures chat_portal finalization uses the same clean string.

  Expected outcome

  - During RAG/tool phases, the visitor only sees spinner/status. No partial natural‑language text is streamed.
  - Only the final answer pass (without tools) streams text, and all logic about placeholders can focus there.

  ———

  3. Phase 2 – Streaming Text Sanitization & Policy

  Even in the final answer pass, DeepSeek might still start with “I’ll check internally…” or similar. We therefore add hard runtime filters for both streaming and final text.

  3.1. Define a “filler sentence” classifier

  - Implement helper in MCP orchestrator (or shared util):
      - Input: sentence string (lowercased).
      - Rules:
          - Treat as filler if it:
              - Starts with:
                  - “i’ll ”, “i will ”, “let me ”, “i’m going to ”
                  - “reviewing ”, “searching ”, “checking ”, “looking into ”
              - Is exactly or begins with known placeholder phrases:
                  - “thanks for the update, i’m reviewing…”
                  - “reviewing knowledge…”
                  - “reviewing the document…”
                  - “loading details…”
              - Matches a small regex set for “meta” phrases (no domain nouns, just verbs like search/check/review).
          - Consider adding an optional “allow list” for final explanatory meta:
              - e.g. sentences starting with “I searched our policy docs and found…” are kept because they convey content and not generic progress.
  - You already have a base in legacy code:
      - _is_placeholder_text() in apps/services/ai_orchestrator.py and legacy_backup.
      - _dedupe_response() filters “I’ll check” after reads.
  - For MCP, create a new helper, e.g. _is_investigative_filler(sentence: str) -> bool with more precise patterns.

  3.2. Apply sanitization to streaming chunks

  - In _answer_stream_chunk (Phase 1):
      - Maintain an internal buffer of the last partial sentence across chunks.
      - On each chunk:
          - Append to buffer.
          - Split buffer into full sentences and residual tail.
          - For each new full sentence:
              - If _is_investigative_filler(sentence) → drop it.
              - Else → append to answer_streamed_chunks and forward to on_response_text_delta.
          - Keep only the residual tail in buffer (in case it completes later).
  - This avoids streaming obvious fillers even in the final pass.

  3.3. Apply sanitization to finalized answer text

  - After the final provider call finishes and before building the plan:
      - Take raw_answer_text (full content string).
      - Split into sentences.
      - Drop sentences where _is_investigative_filler is true.
      - Rejoin into clean_answer_text.
  - Ensure that:
      - StreamingTurnContext.response_text = clean_answer_text
      - StreamingTurnContext.streamed_chunks matches the sanitized stream (if no streaming occurred, reconstruct stream from clean_answer_text using _emit_stream_chunks).

  Expected outcome

  - Even if DeepSeek ignores prompt instructions, the visitor never sees raw filler sentences.
  - Final persisted answer and streamed chunks are consistent and filler‑free.

  ———

  4. Phase 3 – Prompt & Provider Contract Redesign for MCP + DeepSeek

  Runtime filters help, but the model should also be instructed more clearly, especially since we’re changing the orchestration flow.

  4.1. Split MCP prompts into tool‑loop prompt vs final‑answer prompt

  1. Tool‑loop prompt (current build_system_message)
      - Keep most of the existing text (knowledge rules, tool guidance).
      - Make the “no investigative fillers” instruction more prominent:
          - Move it near the top.
          - Use strong wording, e.g. “This is mandatory: do not narrate internal steps like searching or checking. Never output placeholders such as ‘I’ll check’, ‘Let me search’, or
            ‘Reviewing…’.”
      - Clarify that:
          - tool_calls and tool results are internal.
          - content should be either:
              - Empty during tool‑only steps, or
              - A real candidate answer without promising to search.
      - For DeepSeek+tools specifically:
          - Consider a provider‑specific suffix when MCP_PROVIDER == 'deepseek', reinforcing:
              - “If you want to read or search, call tools only; do not talk about searching in content.”
  2. Final‑answer prompt
      - Add a new helper, e.g. build_final_answer_messages(...) in apps/services/mcp/prompts.py:
          - System message:
              - “You are now drafting the final answer for the visitor.”
              - Clearly state: tools have already been run; answer must be:
                  - Direct, helpful, and grounded in provided snippet summaries / reads.
                  - Without narrating internal steps (“I’ll check…”, “I’m searching…”).
              - Allow limited meta explanation:
                  - “You may briefly say what sources you used (e.g., ‘From the credit card fees guide…’), but do not mention ‘searching’ or ‘checking’.”
          - User payload:
              - Latest user message.
              - Summarized knowledge_reads and coverage (from _planner_tool_note and coverage ledger).
              - Short note of what was done (“Tools executed: search_knowledge, read_document page 3 for <label>”).
      - Only this final‑answer prompt is used in the second provider call (Phase 1), with tools=None.

  4.2. Introduce response_format where feasible

  - For the tool loop, DeepSeekToolsProvider already expects JSON where possible, but streaming makes strict response_format tricky.
  - For the final‑answer pass, consider:
      - Using non‑tool provider (DeepSeekChatProvider) configured to return JSON with a response_text field (mirroring existing JSON schema).
      - Or using DeepSeekToolsProvider with tools=None and a response_format that enforces JSON and a response_text field.
  - Benefit:
      - You can instruct the model to put only user‑visible answer text in response_text and keep other commentary out.
      - The streaming extractor can then focus on response_text only (similar to _ResponseTextExtractor in DeepSeekChatProvider).

  Expected outcome

  - Model is given an explicit separation:
      - “You are now planning tools” vs “You are now writing the final answer.”
  - Clear, strong, provider‑specific instructions reduce the chance of investigative fillers in the first place.

  ———

  5. Phase 4 – Conversation History Hygiene

  Even with prompt changes, old placeholder‑style messages in history can bias DeepSeek.

  5.1. Clean up AI messages at write time

  - In chat_portal.finalize_stream_context (where you append the AI message):
      - Before saving body=response_text, run the same sanitization used for the final answer (Phase 2).
      - Ensure legacy placeholder phrasings and investigative fillers are removed from stored AI messages going forward.

  5.2. Optionally normalize historical data

  - For existing conversations, consider:
      - A batch job to:
          - Scan AI messages for known placeholders (“Reviewing Knowledge”, “I’m reviewing your note…”, etc.).
          - Rewrite or trim those messages in the DB (or mark them as “placeholder only” and exclude from LLM context).
      - Or, lighter‑weight:
          - In build_messages, when mapping conversation.messages to LLM messages:
              - Apply the same sanitization to each AI message before turning it into a {"role": "assistant", "content": ...}.

  Expected outcome

  - Over time, conversation history will contain only clean, customer‑facing responses.
  - DeepSeek will see fewer examples of “I’ll check…” in its context, reducing the likelihood of reproducing that style.

  ———

  6. Phase 5 – SSE / Portal UI Policy

  The portal already has some hooks to ignore placeholders, but they’re effectively unused.

  6.1. Harmonize server and client event semantics

  - Today:
      - event: placeholder is ignored in chat-portal.js, but the server never emits such events.
      - on_placeholder_response in chat_portal is a no‑op.

  Proposed:

  - Decide a clear contract:
      - Text deltas from the orchestrator are always final‑answer deltas, never placeholders.
      - If you ever want to send transient text (unlikely now), use event: placeholder and keep the client ignoring it.
  - Modify chat_portal (if needed) so:
      - It expects:
          - status events only for state (“searching”, “reading”, “refining”).
          - delta for answer text only.
          - No placeholder events.
  - This keeps client behavior in sync with the new orchestrator guarantees.

  Expected outcome

  - Frontend becomes simpler and relies on the invariant: “If I see text, it’s part of the answer, not a status placeholder.”

  ———

  7. Phase 6 – Observability & Guardrails

  You already log DeepSeek requests/usage to var/logs/deepseek_calls.log. Extend this to monitor placeholder suppression.

  7.1. Add structured logs for sanitized content

  - When sanitization removes sentences:
      - Log entries like:
          - mcp.sanitizer.dropped_sentence pattern="i'll check" text="<original sentence>"
          - Include:
              - conversation_id
              - business_id
              - whether it was streaming or final text.
  - Add diagnostics to AiOrchestratorPlan:
      - In _build_plan_from_assistant, include something like:
          - diagnostics["sanitized_sentences"] = count or sample of dropped sentences.

  7.2. Add tests to lock behavior

  - New tests under apps/services/tests/:
      - Orchestrator unit tests for MCP:
          - Given a fake provider that streams “I’ll check the docs. The fee is $100 per year.”:
              - Ensure streaming deltas to the portal only include “The fee is $100 per year.”
              - Ensure stored plan.response_text is also sanitized.
      - Prompt tests (non‑network) that assert:
          - The MCP system message includes the strong placeholder rules.
          - Final‑answer prompt includes “do not narrate internal steps”.

  Expected outcome

  - You can verify that filler sentences are dropped without guessing.
  - CI will prevent regressions where placeholders leak back into the answer path.

  ———

  8. Implementation Order & Expected Gains

  Suggested order

  1. Phase 1 (streaming flow split) – High impact, contained to MCP orchestrator + provider calls.
  2. Phase 2 (sanitization) – Build on new flow; enforce runtime guarantees.
  3. Phase 3 (prompt redesign) – Improve quality and reduce reliance on filters.
  4. Phase 4 (history hygiene) – Gradually improve model behavior across turns.
  5. Phase 5 (SSE policy) – Make frontend semantics explicit but minimal changes.
  6. Phase 6 (observability/tests) – Lock in behavior and give you visibility.

  Expected final behavior

  - During search/read phases:
      - The visitor sees only status (“Searching…”, “Reading document…”) and a spinner.
      - No “I’ll search that for you” text appears.
  - During answer phase:
      - Streaming begins only once the orchestrator knows we’re in “final answer” mode.
      - Text streamed and persisted:
          - Contains only customer‑facing content.
          - Is free of generic investigative fillers (“I’ll check…”, “Let me search…”).
          - May include concise, useful meta like “I searched the credit card fees guide and found that the annual fee is X”, which is allowed per your requirement.
  - Over time:
      - Historical conversations become clean and consistent.
      - DeepSeek’s behavior aligns more closely with your policy due to both prompt and context changes, with runtime filters as a safety net.
