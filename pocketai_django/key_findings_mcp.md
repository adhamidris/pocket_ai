• High‑level conclusion

  - The Django MCP path is not explicitly instructing DeepSeek to say “I’ll search”, “Let me read”, etc.
  - What is happening: the MCP orchestrator streams whatever DeepSeek sends in the content field of the Chat Completions delta, with no post‑processing. DeepSeek’s tool‑calling behavior tends to
    emit short “thinking/next step” sentences before/alongside tool calls, and those are forwarded verbatim to the frontend.
  - In other words, the system relies entirely on prompt instructions to suppress fillers, but there is no runtime filter; DeepSeek often ignores that part of the prompt, so those phrases leak
    through as streamed deltas.

  Below is a detailed walk‑through of where this behavior comes from and where the knobs are.

  ———

  Where your Django + DeepSeek MCP path actually runs

  - Entrypoint is pocketai_django/apps/api/chat_portal.py:stream_send.
      - It chooses orchestrator via _business_prefers_mcp:
          - If RAG_USE_MCP_ORCHESTRATOR or business metadata mcp_orchestrator_enabled is true → uses the new MCP orchestrator.
      - For MCP it does:
          - from apps.services.llm_provider import load_mcp_provider
          - from apps.services.mcp import McpOrchestratorService
          - provider = load_mcp_provider()
          - orchestrator = McpOrchestratorService(agent=agent, provider=provider)
  - load_mcp_provider() (pocketai_django/apps/services/llm_provider.py) chooses between DeepSeekToolsProvider and OpenAIToolsProvider:
      - preferred = (MCP_PROVIDER or LLM_PROVIDER).lower()
      - If preferred == "deepseek" → DeepSeekTools first.
      - Else it tries OpenAI then DeepSeek depending on which API keys are present.
  - For DeepSeek on MCP, the concrete provider is DeepSeekToolsProvider.chat(...) in the same file, called from McpOrchestratorService._execute_turn().

  So the path in question is:

  chat_portal.stream_send → McpOrchestratorService.stream_turn/_execute_turn → DeepSeekToolsProvider.chat (stream=True) → DeepSeek HTTP API → _consume_chat_completion_stream →
  on_response_text_delta → chat_portal SSE "delta".

  ———

  What the MCP prompt actually says about placeholders

  - MCP system prompt is built in pocketai_django/apps/services/mcp/prompts.py:build_system_message:
      - It pulls in shared blocks from PromptBuilder (apps/services/ai_prompt_builder.py):
          - CASE_MANDATE
          - CONVERSATION_RULES
          - CHUNK_READ_NUDGE
          - CUSTOMER_RULES
      - And it adds MCP‑specific sections, notably:

        build_system_message (excerpt):
          - Under “Knowledge + Coverage Rules”:
              - - Avoid investigative fillers or meta-status lines about searching or checking. Respond directly with the clearest answer or limitation you can based on the current snippets and
                reads, without narrating that you are searching, checking, or reviewing.
  - In PromptBuilder.ACTION_RULES (legacy‑style bundle, used by the non‑MCP orchestrator) you also added very explicit anti‑placeholder instructions:

    apps/services/ai_prompt_builder.py:ACTION_RULES:
      - ### Placeholder Output Rules
      - - Do NOT emit placeholder replies. Provide the best directly useful answer you can with the knowledge already loaded.
      - - If a read_knowledge action is required, ... never reply with fillers like "Reviewing…" or "Searching…".
      - - Do NOT narrate internal steps like "I'll search", "Let me check", "I'm going to look this up"...
      - - Never start response_text with phrases such as "I'll", "I will", "Let me", "I'm going to", "Reviewing", or "Searching".
  - Important subtlety: for MCP you are not using ACTION_RULES at all.
      - build_system_message only injects CASE_MANDATE, CONVERSATION_RULES, CHUNK_READ_NUDGE, CUSTOMER_RULES, plus an MCP “Tool Usage Guidance” block and the “Internal Knowledge Only / Knowledge
        + Coverage Rules” sections.
      - So the very precise “never start with I’ll / I will / Let me / Searching” guidance lives in ACTION_RULES, but the MCP prompt only has the softer “Avoid investigative fillers or meta-
        status lines…” wording.

  Bottom line: MCP prompt does tell DeepSeek not to narrate searching/checking, but it’s slightly weaker / less detailed than the ACTION_RULES bullets, and there is no backend filter enforcing
  those rules.

  ———

  What the orchestrator does with streamed text (DeepSeek MCP)

  - Core MCP orchestration loop: pocketai_django/apps/services/mcp/orchestrator.py:McpOrchestratorService._execute_turn.

  Key pieces:

  1. It builds messages via prompts.build_messages(...):
      - Adds the system message above.
      - Appends full conversation history: each prior message is turned into a {"role": "assistant"|"user", "content": body} entry based purely on sender (ConversationSender.AI vs customer).

     This means if previous turns (from either legacy orchestrator or another provider) contained “I’ll search”, “Let me check” etc, those phrases are in the training context for the current
     turn. DeepSeek will tend to mimic that style.
  2. It calls the provider:

     payload = self.provider.chat(
         transcript,
         tools=self.tool_definitions,
         on_stream_delta=_buffer_delta if on_response_text_delta else None,
     )
      - tools=self.tool_definitions is the JSON tool schema from apps/services/mcp/tools.py (search_knowledge, read_document, etc.).
      - on_stream_delta is a callback that:
          - Appends every incoming chunk string to delta_buffer.
          - Immediately forwards that chunk to on_response_text_delta (which in chat_portal enqueues it as a SSE "delta" event).
  3. After the provider returns, it normalizes the payload:

     assistant_message = self._coerce_assistant_message(payload)
     tool_calls = list(assistant_message.get("tool_calls") or [])
      - _coerce_assistant_message looks at OpenAI‑style envelopes, pulls out the first choice’s message, and drops any placeholder_response key from that message.
      - It does not attempt to edit or filter the content field.
  4. It appends this assistant turn into the transcript with content intentionally blank when tools are present:

     assistant_payload = {
         "role": "assistant",
         "content": "" if tool_calls else assistant_message.get("content"),
     }
     if tool_calls:
         assistant_payload["tool_calls"] = tool_calls
     transcript.append(assistant_payload)
      - This design choice is to prevent interim filler text from polluting the conversation history.
      - But note: streaming already happened via _buffer_delta – those “I’ll search…” chunks have been sent to the frontend and stored in the streaming buffer.
  5. Knowledge tool handling & placeholder callback:

     if self._is_knowledge_tool(tool_name):
         ...
         if on_placeholder_response and not placeholder_sent:
             placeholder_text = str(assistant_message.get("content") or "").strip()
             if placeholder_text:
                 on_placeholder_response(placeholder_text)
                 placeholder_sent = True
      - For DeepSeek + tools: if the streamed completion had finish_reason == "tool_calls", _consume_chat_completion_stream (see below) sets the final message to only have tool_calls and content
        "". In that case, placeholder_text is empty and this callback never fires.
      - And in the portal, on_placeholder_response is explicitly a no‑op:

        def on_placeholder_response(text: str) -> None:
            # Placeholder responses are suppressed; status events handle UX.
            return

     So no backend code currently re‑emits placeholder text as a special channel; placeholders reach the UI solely as streaming text.
  6. When no tool calls remain, it treats assistant_message.get("content") as the final answer text; for streaming callers, chunks were already forwarded.
  7. It runs a separate planning pass (_run_planner) with tools disabled; that is non‑streaming and returns JSON to enrich actions/extractions. Any placeholder_response returned from that planner
     call is merged only into diagnostics, not into the text streamed to the user.

  ———

  How DeepSeek MCP streaming works under the hood

  - Implementation: DeepSeekToolsProvider in pocketai_django/apps/services/llm_provider.py.

  Key behavior:

  1. Request payload:

     payload = {
         "model": self.model,
         "messages": [dict(msg) for msg in messages],
         "temperature": self.temperature,
         "top_p": self.top_p,
         "stream": streaming,
     }
     if tools:
         payload["tools"] = list(tools)
         payload["tool_choice"] = "auto"
      - No response_format is set here (unlike some OpenAI usages elsewhere).
      - So DeepSeek is free to emit ordinary natural language content plus tool calls; nothing in the API call itself enforces JSON or “no meta fillers” behavior.
  2. Streaming response assembly: _consume_chat_completion_stream(stream, on_stream_delta):
      - It iterates raw SSE data: events, parses JSON, and for each delta:
          - Collects any delta["content"] text into text_parts and, if on_stream_delta is non‑None, calls on_stream_delta(text) immediately for each piece.
          - Aggregates tool_calls deltas into a tool_calls dict.
      - At the end:

        assembled_text = "".join(text_parts).strip()
        if (finish_reason == "tool_calls" or (tool_calls and not assembled_text)) and tool_calls:
            message = {"role": role or "assistant", "tool_calls": _collapse_stream_tool_calls(tool_calls)}
        else:
            message = {"role": role or "assistant", "content": assembled_text}
        return {"choices": [{"message": message}]}
      - Crucially:
          - Every text fragment DeepSeek emits in delta.content is passed to on_stream_delta in real time.
          - If the final finish_reason is "tool_calls", the returned message to the orchestrator has no content, only tool_calls, but the emitted deltas have already gone out.
  3. For non‑streaming calls (planner stage):
      - It reads the full body, logs it, parses JSON, then:
          - If there are tool_calls: returns raw envelope so orchestrator can inspect tools.
          - Else it assumes message["content"] is either:
              - JSON string with response_text/actions/extractions/placeholder_response, or
              - Plain text fallback.
      - It extracts response_text to use as the assistant’s final content, but this is for the planner pass only, not for the streamed answer you see in the chat.

  So, in the streaming turn, DeepSeek MCP behaves like “vanilla” Chat Completions: it’s free to emit “Sure, I’ll search our knowledge base for you” as normal text before or while constructing
  tool calls. That is exactly what gets forwarded to the frontend.

  ———

  How the Django chat portal turns those deltas into UI

  - SSE handling in pocketai_django/apps/api/chat_portal.py:event_stream:
      - on_response_text_delta(chunk) simply queues the raw string chunk:

        def on_response_text_delta(chunk: str) -> None:
            if chunk:
                stream_queue.put(chunk)
      - event_stream() consumes from stream_queue:

        streamed_from_provider = False
        ...
        chunk = stream_queue.get(...)
        ...
        streamed_from_provider = True
        chunk_text = str(chunk)
        streamed_text_chunks.append(chunk_text)
        yield "event: delta\n"
        yield f"data: {json.dumps({'text': chunk_text})}\n\n"
      - normalized_streamed = "".join(streamed_text_chunks).strip() holds the full concatenated text the client saw during streaming.
  - Finalization / persistence logic:
      - After streaming ends, it sends a provisional "final" event with provisional_text = normalized_streamed or context.response_text.
      - Once the plan is finalized, it builds final_payload from plan.response_text (the orchestrator’s final content).
      - Then:

        persisted_text = final_payload.get("text", "")
        effective_text = normalized_streamed or persisted_text
        if effective_text and effective_text != persisted_text and message_id_value:
            service.update_message(..., body=effective_text)
            final_payload["text"] = effective_text
      - So if any streaming text existed, the streamed text (including “I’ll search…”) wins over the orchestrator’s final response and becomes the persisted message body.
  - On the frontend (frontend/static/js/chat-portal.js):
      - handleStreamEvent("delta", data) parses payload.text and appends it directly to the streaming bubble:

        if (eventType === "delta") {
          const payload = data ? JSON.parse(data) : null;
          if (payload && payload.text) {
            let chunk = payload.text;
            ...
            this.appendStreamingChunk(chunk);
          }
          return;
        }
      - There is a special handler for "placeholder" events:

        if (eventType === "placeholder") {
          // Ignore placeholders; status spinner covers "thinking/reading".
          return;
        }

        but the backend no longer emits "event: placeholder" at all; on_placeholder_response is a no‑op, so this path never runs.
      - Status events (from on_status_change) show labels like “Searching…” or “Reading document”, but those are separate UI indicators, not part of the message text.

  Net effect: every token DeepSeek streams in content appears in the chat bubble, unfiltered, and is later treated as canonical answer text.

  ———

  Why this shows up “only when calling DeepSeek”

  Given the code, there are a few concrete reasons this problem is most visible with DeepSeek + Django MCP:

  1. FastAPI backend uses a different orchestrator and provider setup (backend/app/services/providers/openai_orchestrator.py):
      - It wraps OpenAI’s SDK and enforces response_format={"type": "json_object"}.
      - Streaming deltas are incremental JSON that directly represent the final payload, not conversational text before tool calls.
      - That orchestrator has a dedicated OutputParser and is tightly focused on OpenAI; there is no DeepSeek provider in that stack.
      - So the FastAPI path simply doesn’t exercise the same “tool‑calling + free‑form streaming text” behavior.
  2. Django legacy orchestrator vs MCP:
      - Legacy AiOrchestratorService uses PromptBuilder.build(...) and OpenAIChatProvider/DeepSeekChatProvider with strict JSON output (response_format in OpenAIChatProvider, custom JSON
        extractor _ResponseTextExtractor in DeepSeekChatProvider).
      - It also has placeholder‑aware logic and helpers like _is_placeholder_text, _compose_placeholder_response, _strip_placeholder_overlap, etc. (see apps/services/ai_orchestrator.py), and the
        legacy prompt builder (legacy_backup/ai_prompt_builder_legacy.py) actually required placeholder response_text when doing read_knowledge.
      - In the current code, those legacy placeholder mechanics are largely bypassed when MCP is enabled. For MCP:
          - You don’t use the legacy prompt builder.
          - You don’t call DeepSeekChatProvider; you call DeepSeekToolsProvider.
          - You don’t apply any placeholder filtering heuristics to streaming text.

     So DeepSeek under MCP is operating with fewer guardrails than under the old orchestrator.
  3. Model‑specific behavior:
      - The MCP prompt instructions against fillers are identical across providers, but OpenAI and DeepSeek have different RL/tuning.
      - DeepSeek appears to have a strong bias towards “assistant‑like narration” (“I’ll check”, “Let me search for that”), especially in tool‑calling contexts where it wants to justify why it’s
        calling a tool.
      - Because there is no code filter, that bias shows up directly in the streamed deltas.
  4. Existing conversation history includes placeholder‑style AI messages:
      - build_messages pulls the full conversation history from Conversation.messages and preserves all previous assistant texts.
      - Your legacy orchestrator and older prompts used to encourage placeholders for reads (e.g. “Reviewing Knowledge”), and there’s also a built‑in _compose_placeholder_response that includes
        “I’m reviewing your note… I’ll follow up…”.
      - Even though MCP now discourages placeholders, DeepSeek sees historical assistant messages that do narrate internal steps and can mimic that style.

     This amplifies the model’s tendency to continue using similar “I’ll search…” patterns.

  ———

  Is anything in the code “forcing” these placeholder sentences?

  Based on the Django MCP implementation:

  - No direct forcing:
      - There is no code that injects text like “I’ll search…”, “Let me read…”, etc. into either:
          - the system prompt, or
          - the streamed answer.
      - The only placeholder‑like string in the MCP orchestrator is a purely internal diagnostic:

        "placeholder_response": "Need to read the recommended document/page before answering. Use read_hint (doc_id + page + mode)."

        used when the orchestrator detects a read‑required but no read_document was actually executed. That never gets streamed, and chat_portal never emits it.
  - What is “forcing” them to show up:
      - The streaming pipeline blindly forwards every content delta from DeepSeek to the user.
      - There is no runtime guard to:
          - strip sentences starting with “I’ll / I will / Let me / Searching / Reading”,
          - or to hide content from any assistant message that also contains tool calls.
      - The only constraint is the prompt text, which DeepSeek often ignores in practice.

  So the root cause is lack of enforcement, not explicit instruction to generate placeholders.

  ———

  Complexities / potential harms observed in the logic

  Even though you asked not to fix anything yet, there are a few noteworthy complexities and potential risks in the current design:

  1. Streaming vs final text divergence
      - As shown, final persisted text is effectively:

        effective_text = normalized_streamed or plan.response_text
      - Any post‑processing the orchestrator applies to plan.response_text (e.g., future sanity filters, not‑found notices, confidence‑based rewrites) can be overridden if streaming produced
        something else.
      - This is especially relevant if you later add placeholder‑stripping or safety filters only to plan.response_text; unless you also apply them to streaming chunks or to effective_text, the
        user will still see and persist the unfiltered stream.
  2. Legacy placeholder machinery still present but not used with MCP
      - There is significant, complex logic around placeholders in:
          - apps/services/ai_orchestrator.py (_is_placeholder_text, _compose_placeholder_response, _strip_placeholder_overlap, _dedupe_response).
          - apps/services/legacy_backup/ai_orchestrator_legacy.py and legacy_backup/ai_prompt_builder_legacy.py.
      - This code:
          - Used to instruct the model to emit placeholders ("Reviewing Knowledge") when reads were pending.
          - Persisted those placeholders as separate messages, and then tried hard not to duplicate or leave them as the final response.
      - None of that is active for MCP. The complexity is still in the repo, which can make it easy to mis‑assume that some of those protections apply when they actually do not.
      - Harm: future refactors might accidentally re‑wire parts of this legacy behavior into MCP, re‑introducing placeholder persistence or confusing prompt semantics.
  3. DeepSeekToolsProvider lacks response_format enforcement
      - Unlike OpenAIChatProvider and parts of the FastAPI orchestrator, DeepSeekToolsProvider doesn’t set any response_format (e.g., {"type": "json_schema"}) even for non‑streaming planner
        calls.
      - It relies solely on the system prompt to produce JSON and falls back to “treat as plain text” when parsing fails.
      - Harm:
          - If DeepSeek decides to prefix JSON with conversational meta (e.g. “Sure, here is the JSON: {…}”), the JSON parse will fail and you’ll silently lose actions/extractions.
          - You already have a “text fallback” path in DeepSeekToolsProvider.chat, which hides this failure by returning {"content": text, "actions": [], "extractions": []}.
  4. Streaming assembly assumes typical OpenAI streaming semantics
      - _consume_chat_completion_stream chooses between “tool_calls only” vs “content only” based on finish_reason == "tool_calls" or tool_calls and not assembled_text.
      - If DeepSeek ever streams both content and tool calls but does not set finish_reason == "tool_calls", the assembled message to the orchestrator would be:
          - {"role": ..., "content": assembled_text} and no tool_calls.
          - The orchestrator would then:
              - treat the message as final answer,
              - never execute tools,
              - but you would have already streamed whatever content it produced.
      - That’s not observed in the logs you shared, but it’s a potential fragility if DeepSeek’s streaming semantics differ slightly from OpenAI’s.
  5. Conversation history keeps old placeholder style around
      - Historical messages created by the legacy orchestrator (or earlier prompt designs) are still part of conversation.messages.
      - Since build_messages blindly feeds that history into the MCP system, DeepSeek effectively sees “I’m reviewing your note…” / “I’ll follow up…” as prior assistant style. It may continue
        this pattern even though the current system prompt says not to.
      - Harm:
          - Behavior can differ significantly between “fresh” conversations and older ones populated under previous rules, making debugging harder.
  6. Two DeepSeek integrations with slightly different expectations
      - DeepSeekChatProvider (non‑MCP) uses the official openai SDK and a custom _ResponseTextExtractor to stream only the response_text field out of a JSON object.
      - DeepSeekToolsProvider (MCP) uses raw HTTP, streams arbitrary text, and then assumes the final non‑streaming call will produce JSON.
      - The presence of both in the same file, plus some duplicated helper definitions, can be confusing and error‑prone if future changes are made under the assumption that all DeepSeek usage is
        “JSON‑streaming” or all is “plain text”.

  ———

  Summary: why you still see “I’ll search / let me read” with DeepSeek on Django MCP

  - The new Django MCP stack uses DeepSeekToolsProvider and McpOrchestratorService.
  - The MCP prompt does discourage investigative fillers, but:
      - It is somewhat softer than the legacy ACTION_RULES, and
      - DeepSeek’s tool‑calling behavior is strongly tuned toward narrating its actions.
  - The streaming implementation _consume_chat_completion_stream forwards every content delta from DeepSeek to on_response_text_delta, which chat_portal converts into "delta" SSE events and
    renders directly.
  - There is no backend filter that removes or rewrites sentences like “I’ll search…”, nor any logic that suppresses content in assistant messages that also contain tool calls.
  - Historical assistant messages from older flows, which often did narrate internal steps, are part of the context and reinforce that pattern.
  - As a result, even though the prompt says “Avoid investigative fillers…”, DeepSeek frequently violates that instruction, and those violations are surfaced verbatim as streamed output.

  If you’d like, next step could be to design mitigation strategies (without changing core behavior too much), e.g.:

  - A lightweight streaming filter in chat_portal that strips leading “I’ll / I will / Let me / Reviewing / Searching” sentences when they occur before the model has done any reads.
  - Or altering the MCP prompt / tool descriptions in a DeepSeek‑specific way (e.g., making “do not narrate internal steps” the top, emphasized rule and explicitly telling it not to explain tool
    usage at all).