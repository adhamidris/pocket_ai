/**
 * Client-side chat portal implementation.
 * 
 * Handles the complete chat widget lifecycle:
 * - Session bootstrap and token management
 * - SSE streaming for real-time AI responses
 * - Message rendering and markdown formatting
 * - Queue management for concurrent messages
 * - Status indicators and workflow state
 * 
 * Architecture:
 * - Uses Server-Sent Events (SSE) for streaming responses
 * - Maintains local state for streaming messages (buffer, rewrite flags)
 * - Queues messages when a turn is in progress
 * - Persists session tokens in localStorage for refresh resilience
 * 
 * Flow alignment:
 * - Matches backend flow in docs/llm_conversation_backend_flow.md
 * - Handles SSE events: delta, status, turnPersisted, actionsComplete, final
 * - Coordinates with backend streaming phases (searching → reading → responding)
 */
class ChatPortalClient {
  /**
   * Initialize chat portal client.
   * 
   * @param {HTMLElement} container - Root container element with data attributes
   * 
   * State initialization:
   * - Reads endpoints, slugs, tokens from container data attributes
   * - Sets up DOM element references for messages, forms, buttons
   * - Initializes streaming state (buffers, flags, node references)
   * - Creates markdown renderer for message formatting
   * 
   * Why data attributes:
   * - Allows server-side rendering to inject configuration
   * - No hardcoded endpoints (supports multi-tenant deployments)
   * - Session token can be preloaded from server-side bootstrap script
   */
  constructor(container) {
    this.container = container;
    // API endpoints (injected via data attributes for multi-tenant support)
    this.endpoints = {
      bootstrap: container.getAttribute("data-endpoint-bootstrap"),
      messages: container.getAttribute("data-endpoint-messages"),
      streamSend: container.getAttribute("data-endpoint-stream-send"),
      events: container.getAttribute("data-endpoint-events"),
      csat: container.getAttribute("data-endpoint-csat"),
    };
    // Business/agent configuration (for multi-tenant deployments)
    this.businessSlug = container.getAttribute("data-business-slug") || "";
    this.agentSlug = container.getAttribute("data-agent-slug") || "";
    this.agentName = container.getAttribute("data-agent-name") || "Pocket AI";
    this.agentInitials = container.getAttribute("data-agent-initials") || "AI";
    // Session management (token resolution: localStorage → data attr → bootstrap script)
    this.sessionToken = container.getAttribute("data-session-token") || null;
    this.sessionCacheKey = container.getAttribute("data-session-cache-key") || "";
    this.bootstrapScriptId = container.getAttribute("data-bootstrap-script-id") || "";
    this.currentStatus = container.getAttribute("data-initial-status") || "new";
    // Long-lived event stream (for status updates outside of message turns)
    this.eventSource = null;
    // Streaming state flags
    this.awaitingReply = false; // True when SSE stream is active (used by abort handler)
    this.streamController = null; // AbortController for cancelling SSE stream
    this.bootstrapPayload = null; // Cached bootstrap response (for debugging/analytics)
    // DOM element references (cached for performance, avoids repeated queries)
    this.elements = {
      messages: container.querySelector("[data-chat-messages]"),
      sendForm: container.querySelector("[data-chat-send-form]"),
      sendButton: container.querySelector("[data-chat-send-button]"),
      sendIcon: container.querySelector("[data-chat-send-icon]"),
      stopIcon: container.querySelector("[data-chat-stop-icon]"),
      csatForm: container.querySelector("[data-chat-csat-form]"),
      csatContainer: container.querySelector("[data-chat-csat]"),
      toastRoot: document.getElementById("toast-root"),
      statusBadge: container.querySelector("[data-chat-status]"),
    };
    // Streaming message DOM references (lazily created when first chunk arrives)
    this.streamingDedupDone = false; // Legacy flag (unused, kept for compatibility)
    this.streamingFinalBodyEl = null; // Container where streaming text is rendered
    this.streamingMessageNode = null; // Root message node (avatar + bubble)
    this.streamingMessageBodyEl = null; // Message body container (status + final body)
    this.streamingMessageBubbleEl = null; // Message bubble wrapper
    // Status indicator DOM references (shown during workflow phases)
    this.streamingStatusEl = null; // Status row container
    this.streamingStatusTextEl = null; // Status text element
    this.streamingStatusDotEl = null; // Animated dot indicator
    // Streaming text buffers (dual buffer strategy for normalization)
    this.streamingBuffer = ""; // Formatted text (after formatAssistantText processing)
    this.streamingRawBuffer = ""; // Raw accumulated text (for normalization)
    this.streamingRewritePending = false; // Flag: clear buffer when document read completes
    // Markdown renderer (created once, reused for all messages)
    this.markdownRenderer = this.createMarkdownRenderer();
    // Workflow state flags
    this.workflowLocked = false; // Prevents status updates after stream completes
    this.streamingActive = false; // True when delta events are being received
    this.streamFinished = false; // True when "final" event received
    this.isSending = false; // True during entire send flow (prevents concurrent sends)
    this.isStreaming = false; // True when SSE stream is active (for UI indicators)
    // Queue management
    this.flushQueueAfterTurn = false; // Flag: flush queue when turn completes
    this.pendingMessages = []; // Queue of messages waiting to send (max 1)
    // CSS injection flag (idempotent check for multiple instances)
    this.statusStyleInjected = false;
    this.ensureStatusStyle();
  }

  /**
   * Initialize chat portal (entry point).
   * 
   * Flow:
   * 1. Bind form handlers (send, CSAT)
   * 2. Disable composer (prevents sends before session ready)
   * 3. Bootstrap session (load/create conversation, restore messages)
   * 4. Enable composer and connect event stream
   * 
   * Why async init:
   * - Bootstrap requires network call (may be slow)
   * - Event stream needs session token from bootstrap
   * - Graceful error handling (shows toast, doesn't break page)
   */
  async init() {
    this.bindSendForm();
    this.bindCsatForm();
    this.setComposerAvailability(false);
    try {
      await this.bootstrapSession();
      this.setComposerAvailability(true);
      this.connectEventStream();
    } catch (error) {
      this.showToast("Unable to load chat", error.message || "Please refresh and try again.", true);
    }
  }

  /**
   * Bind send form submit handler.
   * 
   * Handles message submission with queue management:
   * - Validates message and session token
   * - Clears textarea immediately (optimistic UI)
   * - Queues message if turn in progress (prevents concurrent sends)
   * - Otherwise sends immediately
   * 
   * Why queue instead of reject:
   * - Better UX: user doesn't lose their message
   * - Allows rapid-fire messages (queues up to 1 message)
   * - Auto-flushes queue when turn completes
   */
  bindSendForm() {
    const form = this.elements.sendForm;
    const textarea = form ? form.querySelector("textarea[name='message']") : null;
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const message = (data.get("message") || "").toString().trim();
      if (!message || !this.sessionToken) {
        this.showToast("Start failed", "Session is still initialising.", true);
        return;
      }
      // Clear textarea immediately (optimistic UI, message is queued/sent)
      if (textarea) {
        textarea.value = "";
      }
      // Queue if turn in progress (prevents concurrent sends, better UX than rejection)
      if (this.isSending || this.isStreaming) {
        this.enqueueMessage(message);
        return;
      }
      await this.sendMessage(message);
    });
  }

  /**
   * Bind CSAT (Customer Satisfaction) form submit handler.
   * 
   * Handles feedback submission after conversation is resolved:
   * - Defaults to score 5 if no selection (graceful fallback)
   * - Hides CSAT form after successful submission
   * - Shows toast notifications for success/failure
   * 
   * Why default score 5:
   * - Prevents form submission errors if user clicks without selecting
   * - Neutral default (not positive or negative)
   * - Better UX than rejecting submission
   */
  bindCsatForm() {
    const form = this.elements.csatForm;
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!this.sessionToken) return;
      const checked = form.querySelector('input[name="score"]:checked');
      // Default to 5 if no selection (graceful fallback, prevents submission errors)
      const score = Number(checked ? checked.value : 5);
      try {
        await this.submitCsat({ score });
        this.showToast("Thanks for your feedback", "Your rating has been recorded.");
        // Hide form after successful submission (prevents duplicate submissions)
        if (this.elements.csatContainer) {
          this.elements.csatContainer.classList.add("hidden");
        }
      } catch (error) {
        this.showToast("Submission failed", error.message || "Could not submit feedback.", true);
      }
    });
  }

  /**
   * Bootstrap chat session (load or create conversation).
   * 
   * Matches step 1.2 in docs/llm_conversation_backend_flow.md.
   * 
   * Token resolution priority:
   * 1. localStorage (persisted from previous session)
   * 2. Container data attribute (server-side preload)
   * 3. Bootstrap script token (inline JSON in page)
   * 4. None (creates new session)
   * 
   * Why multiple token sources:
   * - localStorage: Survives page refresh (user doesn't lose conversation)
   * - Data attribute: Server can preload token in SSR scenarios
   * - Bootstrap script: Allows server to inject token without exposing in HTML
   * 
   * After bootstrap:
   * - Renders existing message transcript
   * - Updates status badge (new/live/resolved)
   * - Shows/hides CSAT form based on status
   */
  async bootstrapSession() {
    // Try multiple token sources (localStorage → data attr → bootstrap script)
    const preloadedToken = this.readBootstrapScriptToken();
    const payload = {
      business_slug: this.businessSlug,
      agent_slug: this.agentSlug,
      session_token: this.getStoredToken() || this.sessionToken || preloadedToken,
      metadata: this.buildVisitorMetadata(),
    };
    const response = await fetch(this.endpoints.bootstrap, {
      method: "POST",
      headers: this.jsonHeaders(),
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error("Bootstrap request failed");
    }
    const data = await response.json();
    this.bootstrapPayload = data;
    const token = data && data.session && data.session.session_token ? data.session.session_token : null;
    if (!token) {
      throw new Error("Session token missing from bootstrap response");
    }
    this.persistSessionToken(token);
    this.renderTranscript(data.messages || []);
    const sessionStatus = data && data.session ? data.session.status : null;
    this.updateStatus(sessionStatus);
    this.updateCsatVisibility(sessionStatus);
  }

  /**
   * Send message and handle SSE streaming response.
   * 
   * Matches step 2 in docs/llm_conversation_backend_flow.md.
   * 
   * Flow:
   * 1. Reset streaming state (clear previous turn buffers/flags)
   * 2. Append customer message to UI immediately (optimistic)
   * 3. Open SSE stream to /stream-send endpoint
   * 4. Process SSE events (delta, status, turnPersisted, final)
   * 5. Handle errors and cleanup
   * 
   * Why AbortController:
   * - Allows user to cancel streaming (stop button)
   * - Prevents memory leaks if component unmounts during stream
   * - Clean shutdown on errors
   * 
   * State flags:
   * - isSending: Prevents concurrent sends (enables queue)
   * - isStreaming: Tracks active SSE stream (for UI indicators)
   * - awaitingReply: Used by abort handler to know if stream is active
   * - workflowLocked: Prevents status updates after stream completes
   */
  async sendMessage(message) {
    if (!this.sessionToken) return;
    // Reset state from previous turn (if any)
    // removeNode=true: Remove any leftover streaming node from previous turn
    // lockWorkflow=false: Allow status updates for new turn
    this.clearStreamingStatus();
    this.resetStreamingState(true, false);
    // Initialize state flags for new turn
    this.workflowLocked = false; // Allow status updates during streaming
    this.streamFinished = false; // Track if "final" event received
    this.awaitingReply = true; // Used by abort handler to know if stream is active
    this.isSending = true; // Prevents concurrent sends (enables queue)
    this.isStreaming = true; // Tracks active SSE stream (for UI indicators)
    this.updateSendButtonState(true); // Show stop icon
    this.updateComposerNotice(true); // Show waiting cursor
    // Append customer message immediately (optimistic UI, before network call)
    // User sees their message instantly, even if network is slow
    this.appendMessage({
      sender: "customer",
      body: message,
      sent_at: new Date().toISOString(),
    });
    // AbortController allows user to cancel stream (stop button)
    // Also prevents memory leaks if component unmounts during stream
    const controller = new AbortController();
    this.streamController = controller;

    try {
      const response = await fetch(this.endpoints.streamSend, {
        method: "POST",
        headers: {
          ...this.jsonHeaders(),
          Accept: "text/event-stream",
        },
        body: JSON.stringify({
          session_token: this.sessionToken,
          body: message,
        }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error("Stream failed to initialise");
      }

      // Read SSE stream chunk by chunk
      // SSE format: "event: <type>\ndata: <json>\n\n" (double newline = event boundary)
      // Events may span multiple chunks, so we buffer until we see complete event
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = ""; // Accumulates partial events across chunks
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        // Decode with stream: true to handle multi-byte UTF-8 characters split across chunks
        // Without stream: true, multi-byte characters at chunk boundaries would be corrupted
        buffer += decoder.decode(value, { stream: true });
        // Process complete events (delimited by double newline \n\n)
        // Multiple events may arrive in single chunk, so loop until no more delimiters
        let index;
        while ((index = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, index);
          buffer = buffer.slice(index + 2); // Keep remaining partial event in buffer
          this.processStreamEvent(rawEvent);
        }
        // If buffer still has content but no delimiter, wait for next chunk
        // This handles events that span multiple network chunks
      }
    } catch (error) {
      // AbortError is expected when user clicks stop button (don't show error toast)
      // Other errors (network failures, parse errors) should be shown to user
      if (error.name !== "AbortError") {
        this.showToast("Send failed", error.message || "Message could not be delivered.", true);
      }
      this.isStreaming = false;
      // Enable queue flush even on error (allows user to retry or send next message)
      this.flushQueueAfterTurn = true;
    } finally {
      // Always reset state flags (ensures clean state even on error/abort)
      this.awaitingReply = false;
      this.isSending = false;
      this.updateSendButtonState(false);
      // Composer availability is controlled by stream finalization; do not lock here.
      // If streaming node exists but stream failed, remove it (prevents showing partial message)
      if (this.streamingMessageNode) {
        this.resetStreamingState(true, false);
      }
      // Flush queue if stream is idle and flag is set
      // This handles both success and error cases (allows next message to proceed)
      if (!this.isStreaming && this.flushQueueAfterTurn) {
        this.flushQueueAfterTurn = false;
        const next = this.pendingMessages.shift();
        if (next) {
          this.sendMessage(next);
        }
      }
    }
  }

  /**
   * Parse SSE event from raw text.
   * 
   * SSE format:
   * - "event: <type>" line specifies event type (delta, status, final, etc.)
   * - "data: <json>" line(s) contain event payload (may span multiple lines)
   * - Events separated by double newline (\n\n)
   * 
   * Why manual parsing:
   * - EventSource API doesn't support POST requests (we need POST for session_token)
   * - Manual parsing gives us control over error handling
   * - Supports multi-line data payloads (some events have large JSON)
   */
  processStreamEvent(rawEvent) {
    /**
     * Parse SSE event from raw text.
     * 
     * SSE event format:
     * event: <type>
     * data: <json>
     * 
     * Or multi-line data:
     * event: <type>
     * data: <line1>
     * data: <line2>
     * 
     * Default event type is "message" (SSE spec default).
     */
    const lines = rawEvent.split(/\r?\n/);
    let eventType = "message"; // Default SSE event type
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.replace("event:", "").trim();
      } else if (line.startsWith("data:")) {
        // Data may span multiple lines (SSE spec allows this)
        // Accumulate all data lines into single string
        data += line.replace("data:", "").trim();
      }
      // Ignore other lines (comments, empty lines, etc.)
    }
    this.handleStreamEvent(eventType, data);
  }

  /**
   * Handle SSE event by type.
   * 
   * Event types (from backend stream_send):
   * - status: Workflow state updates (searching_knowledge, reading_document, responding)
   * - delta: Streaming text chunks (token-by-token)
   * - turnPersisted: Final persisted message (may differ from streamed)
   * - actionsComplete: Background actions finished (case creation, etc.)
   * - actionsError: Background action failures
   * - final: Stream completion signal
   * 
   * Why workflowLocked:
   * - Prevents status updates after stream completes (avoids stale indicators)
   * - Set to true when final event received
   * - Prevents race conditions if late status events arrive
   */
  handleStreamEvent(eventType, data) {
    /**
     * Route SSE events to appropriate handlers.
     * 
     * Event processing order matters:
     * 1. status: Workflow state updates (must check workflowLocked first)
     * 2. delta: Streaming text chunks
     * 3. actionsComplete/actionsError: Background action notifications
     * 4. turnPersisted: Final persisted message
     * 5. final: Stream completion signal
     * 
     * Why order matters:
     * - status events may arrive after final (race condition)
     * - workflowLocked prevents stale status indicators
     * - final event must be processed last (locks workflow)
     */
    if (eventType === "status") {
      // Ignore status updates after stream completes (workflowLocked prevents stale indicators)
      // This handles race condition where status event arrives after final event
      if (this.workflowLocked) {
        return;
      }
      try {
        const payload = data ? JSON.parse(data) : null;
        if (payload) {
          const state = (payload.state || "").toString().trim();
          const label = (payload.label || "").toString().trim();

      // Ignore completion signals (handled by "final" event)
      if (state === "stream_complete" || state === "complete" || state === "done") {
        return;
      }

      if (state === "reading_document") {
            // Knowledge read: we expect content to be revised after doc load.
            // Set rewrite flag so we clear buffer when new content arrives
            this.streamingRewritePending = true;
            this.setStreamingStatus("reading", label || "Reading…");
          } else if (state === "searching_knowledge") {
            // Surface search-specific label (e.g. "Searching: billing policy").
            this.setStreamingStatus("searching", label || "Searching…");
          } else if (state === "planning_actions") {
            // Keep this internal; do not surface to the visitor.
            // Planning happens after streaming, user doesn't need to see it
            return;
          } else if (state === "responding") {
            // LLM is generating response (after knowledge search/read)
            this.setStreamingStatus("refining", "Refining answer…");
          } else if (state && state !== "responding") {
            // Generic fallback for other states (future-proofing)
            // Skip explicit "responding"/"writing" (too generic, use "refining" instead)
            const fallbackLabel = label || this.formatStatus(state);
            this.setStreamingStatus("working", fallbackLabel);
          }
          // Note: "stream_complete", "complete", "done" are handled above (early return)
        }
      } catch (_err) {
        // ignore malformed status payloads (graceful degradation)
      }
      return;
    }

    if (eventType === "delta") {
      /**
       * Delta events: Streaming text chunks from LLM.
       * 
       * Each delta contains a small text chunk (token or word).
       * We accumulate these in streamingBuffer and render incrementally.
       * 
       * Why incremental rendering:
       * - Better UX: user sees response as it's generated
       * - Feels more responsive than waiting for complete response
       * - Markdown is re-rendered on each chunk (handles partial formatting)
       */
      try {
        const payload = data ? JSON.parse(data) : null;
        if (payload && payload.text) {
          let chunk = payload.text;

          if (!this.workflowLocked) {
            this.streamingActive = true;
            this.setStreamingStatus("refining", "Refining answer…");
          }
          this.appendStreamingChunk(chunk);
        }
      } catch (error) {
        console.warn("Failed to parse stream delta", error);
      }
      return;
    }
    
    if (eventType === "actionsComplete") {
      /**
       * ActionsComplete: Background actions finished successfully.
       * 
       * Called when backend completes planned actions (case creation, customer updates, etc.).
       * These happen asynchronously after streaming completes, so user sees toast notification.
       * 
       * Why toast instead of inline message:
       * - Actions happen in background (non-blocking)
       * - User doesn't need to see action details in chat
       * - Toast is non-intrusive (doesn't clutter conversation)
       */
      try {
        const payload = data ? JSON.parse(data) : null;
        const label = payload && payload.label ? payload.label : "Follow-up tasks completed.";
        this.showToast("Workflow update", label);
      } catch (_err) {
        // Ignore malformed payload (graceful degradation)
      }
      return;
    }

    if (eventType === "actionsError") {
      /**
       * ActionsError: Background action failures.
       * 
       * Called when planned actions fail (e.g., case creation error, API failure).
       * User is notified but conversation continues (actions are non-critical).
       * 
       * Why not block conversation:
       * - Actions are secondary (main response already delivered)
       * - User can retry or contact support if needed
       * - Better UX than blocking entire conversation
       */
      try {
        const payload = data ? JSON.parse(data) : null;
        const message = payload && payload.error ? payload.error : "Background workflow failed.";
        this.showToast("Workflow issue", message, true);
      } catch (_err) {
        // Ignore malformed payload (graceful degradation)
      }
      return;
    }

    if (eventType === "turnPersisted") {
      /**
       * TurnPersisted: Final message after planner pass and persistence.
       * 
       * This may differ from streamed text because:
       * - Planner may refine the answer
       * - Sanitization may remove filler sentences
       * - Backend may apply final formatting
       * 
       * We update the latest assistant message with this authoritative version.
       */
      this.handleTurnPersistedEvent(data);
      return;
    }

    // "final" event: Stream completion signal (may have empty data)
    // Other events without data are ignored (except "error" which is handled separately)
    if (!data && eventType !== "final") return;
    if (eventType !== "final") {
      // Handle explicit error events (separate from network errors)
      if (eventType === "error") {
        this.showToast("Stream error", data, true);
      }
      return;
    }
    // Process "final" event (stream completion)
    try {
      const payload = JSON.parse(data);
      if (payload) {
        const finalText = payload.text || "";
        // If streaming node exists, finalize it (normal case: streamed message)
        // Otherwise, if final text provided, create new message (edge case: no streaming node)
        // This handles cases where stream failed before first chunk arrived
        if (this.streamingMessageNode) {
          this.finalizeStreamingMessage(finalText);
        } else if (finalText) {
          // Fallback: create message if streaming node missing (shouldn't happen normally)
          // This handles edge case where stream completes without any delta events
          this.appendMessage({
            sender: "ai",
            body: finalText,
            sent_at: new Date().toISOString(),
          });
        }
      }
      // Update session status if provided (conversation may have been resolved)
      if (payload && payload.session_status) {
        this.updateStatus(payload.session_status);
        this.updateCsatVisibility(payload.session_status);
      }
      // Reset state flags (stream is complete)
      this.awaitingReply = false;
      this.streamFinished = true;
      this.isStreaming = false;
      this.updateSendButtonState(false);
      this.setComposerAvailability(true);
      this.updateComposerNotice(false);
    } catch (error) {
      // Graceful degradation: if final event is malformed, log but don't break flow
      console.warn("Failed to parse stream payload", error);
    } finally {
      // Always lock workflow and cleanup (even if parsing failed)
      // This ensures clean state for next turn
      this.workflowLocked = true;
      this.streamingActive = false;
      this.flushQueueAfterTurn = true;
      this.clearStreamingStatus();
      this.markStreamFinished();
    }
}

  /**
   * Handle turnPersisted SSE event.
   * 
   * Called when backend finishes persisting the turn (after planner pass).
   * Updates the latest assistant message with the authoritative persisted text,
   * which may differ from streamed text due to sanitization or planner refinements.
   * 
   * Why separate from final event:
   * - turnPersisted arrives later (after planner completes)
   * - Contains final authoritative text (what's stored in DB)
   * - May include session status updates (e.g., conversation resolved)
   */
  handleTurnPersistedEvent(data) {
    try {
      const payload = data ? JSON.parse(data) : null;
      if (!payload) return;
      const text = payload.text ? payload.text.toString() : "";
      if (text) {
        this.updateLatestAssistantMessage(text);
      }
      if (payload.session_status) {
        this.updateStatus(payload.session_status);
        this.updateCsatVisibility(payload.session_status);
      }
    } catch (error) {
      console.warn("Failed to parse persisted turn", error);
    }
  }

  /**
   * Connect to long-lived event stream for status updates.
   * 
   * Uses native EventSource API (GET request) for statusChanged events.
   * Separate from streaming SSE (which uses POST + manual parsing).
   * 
   * Why separate stream:
   * - Status changes can happen outside of message turns
   * - Long-lived connection (stays open for session lifetime)
   * - Handles conversation status updates (e.g., resolved by admin)
   * 
   * Events handled:
   * - statusChanged: Conversation status update (new/live/resolved)
   */
  connectEventStream() {
    if (!this.endpoints.events || !this.sessionToken) return;
    // Close existing connection if reconnecting
    if (this.eventSource) {
      this.eventSource.close();
    }
    const url = new URL(this.endpoints.events, window.location.origin);
    url.searchParams.set("session_token", this.sessionToken);
    this.eventSource = new EventSource(url.toString());
    this.eventSource.addEventListener("statusChanged", (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data && data.status) {
          this.updateStatus(data.status);
          this.updateCsatVisibility(data.status);
        }
      } catch (error) {
        console.warn("Failed to parse status event", error);
      }
    });
  }

  /**
   * Submit CSAT (Customer Satisfaction) score to backend.
   * 
   * Called after conversation is resolved to collect user feedback.
   * Score is typically 1-5 scale (1 = very dissatisfied, 5 = very satisfied).
   * 
   * @param {Object} options - Submission options
   * @param {number} options.score - Satisfaction score (1-5)
   * 
   * @throws {Error} If submission fails (network error, invalid response)
   */
  async submitCsat({ score }) {
    const response = await fetch(this.endpoints.csat, {
      method: "POST",
      headers: this.jsonHeaders(),
      body: JSON.stringify({
        session_token: this.sessionToken,
        score,
      }),
    });
    if (!response.ok) {
      throw new Error("Failed to submit feedback");
    }
  }

  /**
   * Render full message transcript (bootstrap/restore).
   * 
   * Called during bootstrap to restore conversation history.
   * Filters out placeholder messages (internal-only, not user-facing).
   * 
   * Why filter placeholders:
   * - Backend may create placeholder messages during streaming
   * - These are internal (e.g., "I'm checking...") and shouldn't be shown
   * - Only final persisted messages are shown to user
   */
  renderTranscript(messages) {
    const container = this.elements.messages;
    if (!container) return;
    container.innerHTML = "";
    messages.forEach((message) => {
      // Skip placeholder messages (internal-only, not user-facing)
      if (message && message.metadata && message.metadata.placeholder) {
        return;
      }
      this.appendMessage(message);
    });
  }

  /**
   * Render markdown text to HTML.
   * 
   * Wrapper around markdown renderer. Returns empty string for null/undefined
   * to prevent rendering issues.
   * 
   * @param {string} text - Markdown text to render
   * @returns {string} HTML string
   */
  renderMarkdown(text) {
    if (!text) return "";
    return this.markdownRenderer.render(text);
  }

  /**
   * Append message to transcript.
   * 
   * Normalizes message data, builds DOM node, and appends to messages container.
   * Auto-scrolls to bottom to show new message.
   * 
   * Used for:
   * - Customer messages (optimistic append before network call)
   * - AI messages (from bootstrap transcript or final event)
   * - Non-streaming messages (fallback scenarios)
   * 
   * @param {Object} raw - Raw message data from backend or user input
   */
  appendMessage(raw) {
    const container = this.elements.messages;
    if (!container) return;
    const message = this.normalizeMessage(raw);
    const node = this.buildMessageNode(message);
    container.appendChild(node);
    // Auto-scroll to show new message (smooth scroll for better UX)
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }

  /**
   * Normalize raw message data to consistent format.
   * 
   * Handles variations in message format:
   * - Normalizes sender to lowercase (ai/customer/system)
   * - Extracts author name/initials (with fallbacks)
   * - Normalizes timestamp (handles sent_at vs sentAt)
   * 
   * Why normalization:
   * - Backend may use different field names (sent_at vs sentAt)
   * - Author data may be missing (use defaults)
   * - Consistent format simplifies rendering logic
   * 
   * @param {Object} raw - Raw message data
   * @returns {Object} Normalized message object
   */
  normalizeMessage(raw) {
    const sender = (raw.sender || "system").toLowerCase();
    const isAi = sender === "ai";
    const isCustomer = sender === "customer";
    // Extract author name with fallbacks (backend data → defaults)
    const authorName =
      raw.author && raw.author.name ? raw.author.name : (isAi ? this.agentName : isCustomer ? "You" : "System");
    // Extract author initials with fallbacks
    const authorInitials =
      raw.author && raw.author.initials ? raw.author.initials : (isAi ? this.agentInitials : isCustomer ? "YOU" : "SYS");
    return {
      sender,
      body: raw.body || "",
      // Handle both sent_at and sentAt field names (backend variations)
      sentAt: raw.sent_at || raw.sentAt || new Date().toISOString(),
      author: {
        name: authorName,
        initials: authorInitials,
      },
    };
  }

  /**
   * Build DOM node for message bubble.
   * 
   * Creates message structure:
   * - Wrapper with flex layout (reversed for customer messages)
   * - Avatar with author initials
   * - Bubble with author name, body (markdown), and timestamp
   * 
   * Layout differences:
   * - Customer messages: Right-aligned (flex-row-reverse), primary color
   * - AI messages: Left-aligned, muted color
   * 
   * Data attributes:
   * - [data-message-bubble]: For styling/selection
   * - [data-message-body]: For content updates (turnPersisted event)
   * 
   * @param {Object} message - Normalized message object
   * @returns {HTMLElement} Message wrapper node
   */
  buildMessageNode(message) {
    const wrapper = document.createElement("div");
    wrapper.className = "flex gap-3 items-start";
    // Customer messages: Right-aligned (reversed flex, text-right)
    if (message.sender === "customer") {
      wrapper.classList.add("flex-row-reverse", "text-right");
    }
    // Avatar with author initials (different colors for customer vs AI)
    const avatar = document.createElement("div");
    avatar.className = `h-9 w-9 rounded-full flex items-center justify-center font-semibold ${
      message.sender === "customer" ? "bg-primary text-white" : "bg-primary/15 text-primary"
    }`;
    avatar.textContent = message.author.initials;

    // Message bubble (different background colors for customer vs AI)
    const bubble = document.createElement("div");
    bubble.className = `flex-1 rounded-2xl px-4 py-3 text-sm text-foreground shadow-soft ${
      message.sender === "customer" ? "bg-primary/10" : "bg-muted/60"
    }`;
    bubble.dataset.messageBubble = "true";

    // Author name (shown above message body)
    const author = document.createElement("p");
    author.className = "font-medium text-sm text-muted-foreground mb-1";
    author.textContent = message.author.name;
    bubble.appendChild(author);

    // Message body (markdown-rendered content)
    const body = document.createElement("div");
    body.className = "space-y-2 leading-relaxed";
    body.dataset.messageBody = "true";
    body.innerHTML = this.renderMarkdown(message.body);
    bubble.appendChild(body);

    // Timestamp (formatted time, shown below message)
    const timestamp = document.createElement("p");
    timestamp.className = "mt-2 text-xs text-muted-foreground";
    timestamp.textContent = this.formatTimestamp(message.sentAt);
    bubble.appendChild(timestamp);

    // Append order differs for customer (bubble first) vs AI (avatar first)
    if (message.sender === "customer") {
      wrapper.appendChild(bubble);
      wrapper.appendChild(avatar);
    } else {
      wrapper.appendChild(avatar);
      wrapper.appendChild(bubble);
    }

    return wrapper;
  }

  /**
   * Append streaming chunk to message buffer and render.
   * 
   * Handles incremental text accumulation:
   * - Normalizes chunk (adds spaces between sentences)
   * - Handles rewrite flag (clears buffer when document read completes)
   * - Accumulates in raw buffer, formats, then renders markdown
   * - Auto-scrolls to bottom for visibility
   * 
   * Why two buffers:
   * - streamingRawBuffer: Raw accumulated text (for normalization)
   * - streamingBuffer: Formatted text (after formatAssistantText processing)
   * 
   * Why rewrite flag:
   * - When "reading_document" status arrives, we know content will be revised
   * - Clear buffer to avoid showing stale partial text
   * - Better UX: user sees clean revision instead of partial + complete
   */
  appendStreamingChunk(chunk) {
    if (!chunk || !this.elements.messages) return;
    this.ensureStreamingMessageNode();
    const normalized = this.normalizeStreamingChunk(chunk);

    // If rewrite pending (document read just completed), clear buffers
    // This prevents showing stale partial text before revised content arrives
    if (this.streamingRewritePending) {
      this.streamingBuffer = "";
      this.streamingRawBuffer = "";
      if (this.streamingFinalBodyEl) {
        this.streamingFinalBodyEl.innerHTML = "";
      }
      this.streamingRewritePending = false;
      this.setStreamingStatus("refining", "Refining answer…");
    }
    // Accumulate raw text, then format and render markdown
    this.streamingRawBuffer += normalized;
    const formatted = this.formatAssistantText(this.streamingRawBuffer);
    this.streamingBuffer = formatted;
    if (this.streamingFinalBodyEl) {
      // Re-render markdown on each chunk (handles partial formatting gracefully)
      this.streamingFinalBodyEl.innerHTML = this.renderMarkdown(formatted);
    }
    // Auto-scroll to show latest content
    this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
  }

  /**
   * Normalize streaming chunk for proper sentence spacing.
   * 
   * Handles edge case: chunks may arrive at sentence boundaries.
   * If previous buffer ends with punctuation and new chunk starts with
   * alphanumeric, add space to prevent run-on sentences.
   * 
   * Example:
   * - Buffer: "Hello."
   * - Chunk: "How are you?"
   * - Result: "Hello. How are you?" (space added)
   * 
   * Why needed:
   * - LLM may stream "Hello." then "How" as separate chunks
   * - Without normalization, would render as "Hello.How"
   * - Markdown renderer expects proper spacing
   */
  normalizeStreamingChunk(chunk) {
    let text = chunk;
    const buffer = this.streamingRawBuffer || "";
    // If the previous buffer doesn't end with whitespace and the new chunk starts
    // with alphanumeric text, prepend a space to avoid run-on sentences.
    const lastChar = buffer ? buffer[buffer.length - 1] : "";
    if (lastChar && /[.!?]/.test(lastChar) && /^[A-Za-z0-9]/.test(text)) {
      text = ` ${text}`;
    }

    return text;
  }

  /**
   * Format assistant text (placeholder for future processing).
   * 
   * Currently a pass-through, but reserved for future enhancements:
   * - Text normalization (trim, dedupe)
   * - Special formatting rules
   * - Content filtering
   * 
   * Why separate method:
   * - Allows adding formatting logic without changing call sites
   * - Consistent interface for text processing
   * - Easy to extend later
   * 
   * @param {string} text - Raw assistant text
   * @returns {string} Formatted text (currently unchanged)
   */
  formatAssistantText(text) {
    if (!text) return "";
    return text;
  }

  /**
   * Ensure streaming message node exists in DOM.
   * 
   * Creates message structure for streaming:
   * - Message wrapper with avatar and bubble
   * - Status indicator row (hidden by default, shown during streaming)
   * - Final body container (where streaming text is rendered)
   * 
   * Why lazy creation:
   * - Only create when first chunk arrives (avoids empty message if stream fails)
   * - Reuses same node for entire stream (better performance than appending)
   * - Status row allows showing "Searching...", "Reading..." indicators
   * 
   * Structure:
   * - [data-message-body]: Container for status + final body
   * - [data-streaming-status]: Status indicator row (dot + text)
   * - [data-message-final-body]: Where streaming text is rendered
   */
  ensureStreamingMessageNode() {
    if (this.streamingMessageNode && this.streamingMessageBodyEl) return;
    if (!this.elements.messages) return;
  
    const node = this.buildMessageNode({
      sender: "ai",
      body: "",
      sent_at: new Date().toISOString(),
      author: { name: this.agentName, initials: this.agentInitials },
    });
  
    this.streamingMessageNode = node;
    this.streamingMessageBodyEl = node.querySelector("[data-message-body]");
    this.streamingMessageBubbleEl = node.querySelector("[data-message-bubble]");
  
    if (this.streamingMessageBodyEl) {
      this.streamingMessageBodyEl.innerHTML = "";
      // Status indicator row (shows "Searching...", "Reading..." during workflow)
      const statusRow = document.createElement("div");
      statusRow.dataset.streamingStatus = "true";
      statusRow.className = "flex items-center gap-2 text-xs text-muted-foreground mb-2 hidden";
      const statusDot = document.createElement("span");
      statusDot.className = "inline-block h-2 w-2 rounded-full bg-primary animate-pulse";
      const statusText = document.createElement("span");
      statusText.classList.add("chat-portal-status-shimmer");
      statusText.textContent = "";
      statusRow.appendChild(statusDot);
      statusRow.appendChild(statusText);
      this.streamingMessageBodyEl.appendChild(statusRow);
      this.streamingStatusEl = statusRow;
      this.streamingStatusTextEl = statusText;
      this.streamingStatusDotEl = statusDot;

      // Final body container (where streaming text accumulates)
      const finalEl = document.createElement("div");
      finalEl.dataset.messageFinalBody = "true";
      finalEl.className = "space-y-2 leading-relaxed";
      this.streamingMessageBodyEl.appendChild(finalEl);
      this.streamingFinalBodyEl = finalEl;
    }
  
    this.elements.messages.appendChild(node);
  }

  /**
   * Finalize streaming message when "final" event arrives.
   * 
   * Handles two scenarios:
   * 1. Final text provided: Use it (may differ from streamed due to sanitization)
   * 2. No final text: Use accumulated buffer (fallback if final event missing)
   * 
   * Why final text may differ:
   * - Backend sanitizes response (removes filler sentences)
   * - Planner may refine answer after streaming
   * - Final text is authoritative (what's persisted to DB)
   * 
   * Edge case:
   * - If no text at all, remove streaming node (empty message)
   * - Prevents showing empty message bubbles
   */
  finalizeStreamingMessage(finalText) {
    const incoming = (finalText || "").toString();
    let text = "";

    // Prefer final text (authoritative, may be sanitized/refined)
    // Fallback to accumulated buffer if final text missing
    if (incoming) {
      this.streamingRawBuffer = incoming;
      this.streamingBuffer = this.formatAssistantText(incoming);
      text = this.streamingBuffer;
    } else {
      text = this.streamingBuffer || "";
    }

    // If no text at all, remove streaming node (empty message)
    if (!text) {
      if (this.streamingMessageNode) {
        this.resetStreamingState(true);
      }
      return;
    }

    // Update final body with authoritative text, or create new message if node missing
    if (this.streamingFinalBodyEl) {
      this.streamingFinalBodyEl.innerHTML = this.renderMarkdown(text);
    } else {
      this.appendMessage({ sender: "ai", body: text, sent_at: new Date().toISOString() });
    }
    this.resetStreamingState(false);
  }

  /**
   * Update latest assistant message with persisted text.
   * 
   * Called by turnPersisted event handler. Updates the most recent
   * AI message with the authoritative persisted version.
   * 
   * Why update instead of append:
   * - turnPersisted arrives after streaming completes
   * - Persisted text may differ (sanitized, refined by planner)
   * - User should see final authoritative version
   * 
   * Search strategy:
   * - Find last message body (search backwards)
   * - Skip customer messages (flex-row-reverse class)
   * - Update final body if exists (streaming message), else update body (regular message)
   */
  updateLatestAssistantMessage(text) {
    if (!text || !this.elements.messages) return;
    const normalized = this.formatAssistantText(text);
    const bodies = Array.from(this.elements.messages.querySelectorAll("[data-message-body]"));
    // Search backwards to find most recent AI message
    for (let idx = bodies.length - 1; idx >= 0; idx -= 1) {
      const body = bodies[idx];
      const wrapper = body.closest(".flex");
      // Skip customer messages (they have flex-row-reverse class)
      if (!wrapper || wrapper.classList.contains("flex-row-reverse")) {
        continue;
      }
      // Update final body if exists (streaming message), else update body (regular message)
      const finalBody = body.querySelector("[data-message-final-body]");
      if (finalBody) {
        finalBody.innerHTML = this.renderMarkdown(normalized);
      } else {
        body.innerHTML = this.renderMarkdown(normalized);
      }
      break;
    }
  }

  /**
   * Reset streaming state after turn completes or is aborted.
   * 
   * Cleans up all streaming-related state:
   * - DOM node references (message, status, body elements)
   * - Text buffers (raw and formatted)
   * - Flags (rewrite pending, workflow locked)
   * 
   * Args:
   * - removeNode: If true, removes streaming message node from DOM (for empty/aborted messages)
   * - lockWorkflow: If true, sets workflowLocked flag (prevents late status updates)
   * 
   * Why two flags:
   * - removeNode: Controls DOM cleanup (only remove if message is empty/aborted)
   * - lockWorkflow: Controls state locking (prevent race conditions with late events)
   */
  resetStreamingState(removeNode = false, lockWorkflow = true) {
    if (lockWorkflow) {
      this.workflowLocked = true;
    }
    this.streamingActive = false;
    this.isStreaming = false;
    this.clearStreamingStatus();
    // Remove node if message is empty/aborted (prevents showing empty bubbles)
    if (removeNode && this.streamingMessageNode && this.streamingMessageNode.parentNode) {
      this.streamingMessageNode.parentNode.removeChild(this.streamingMessageNode);
    }
    // Clear all streaming state references
    this.streamingDedupDone = false;
    this.streamingFinalBodyEl = null;
    this.streamingMessageNode = null;
    this.streamingMessageBodyEl = null;
    this.streamingMessageBubbleEl = null;
    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
    this.streamingStatusDotEl = null;
    this.streamingBuffer = "";
    this.streamingRawBuffer = "";
    this.streamingRewritePending = false;
  }

  /**
   * Set streaming status indicator text and style.
   * 
   * Updates the status row shown during streaming to indicate workflow phase:
   * - "Searching…" (searching_knowledge)
   * - "Reading…" (reading_document)
   * - "Refining answer…" (responding, refining)
   * - Error state (red dot, no shimmer)
   * 
   * Why status indicator:
   * - User sees what's happening (better UX than silent waiting)
   * - Different phases have different labels (searching vs reading)
   * - Error state is visually distinct (red, no animation)
   * 
   * @param {string} mode - Status mode (working, searching, reading, refining, error)
   * @param {string} labelOverride - Optional custom label (formatted with emphasis)
   */
  setStreamingStatus(mode = "working", labelOverride) {
    // Ignore if workflow locked (prevents stale status after stream completes)
    if (this.workflowLocked) return;
    this.ensureStreamingMessageNode();
    if (!this.streamingStatusEl || !this.streamingStatusTextEl) return;
    const formattedLabel = this.formatStatusLabel(labelOverride);
    // Map status modes to user-friendly labels
    const labelMap = {
      working: "Assistant is working…",
      drafting: "Refining answer…",
      reading: "Reading…",
      searching: "Searching…",
      updating: "Refining answer…",
      refining: "Refining answer…",
      error: "Workflow issue detected.",
    };
    const baseLabel = labelMap[mode] || labelMap.working;
    const label = formattedLabel || baseLabel;
    this.streamingStatusTextEl.innerHTML = label;
    this.streamingStatusEl.classList.remove("hidden");
    const isError = mode === "error";
    // Update dot color (primary for normal, destructive/red for error)
    if (this.streamingStatusDotEl) {
      this.streamingStatusDotEl.classList.toggle("bg-primary", !isError);
      this.streamingStatusDotEl.classList.toggle("bg-destructive", isError);
    }
    // Update text style (add shimmer animation for normal, remove for error)
    if (this.streamingStatusTextEl) {
      this.streamingStatusTextEl.classList.toggle("text-destructive", isError);
      if (isError) {
        this.streamingStatusTextEl.classList.remove("chat-portal-status-shimmer");
      } else {
        this.streamingStatusTextEl.classList.add("chat-portal-status-shimmer");
      }
    }
  }

  /**
   * Clear streaming status indicator.
   * 
   * Hides status row and resets to default state (primary color, shimmer animation).
   * Called when streaming completes or is aborted.
   * 
   * Why reset to defaults:
   * - Status row may be reused for next turn
   * - Ensures clean state (no leftover error styling)
   * - Shimmer animation ready for next status update
   */
  clearStreamingStatus() {
    if (this.streamingStatusEl) {
      this.streamingStatusEl.classList.add("hidden");
    }
    if (this.streamingStatusTextEl) {
      this.streamingStatusTextEl.textContent = "";
      this.streamingStatusTextEl.classList.remove("text-destructive");
      // Reset to default shimmer animation (ready for next turn)
      this.streamingStatusTextEl.classList.add("chat-portal-status-shimmer");
    }
    if (this.streamingStatusDotEl) {
      this.streamingStatusDotEl.classList.remove("bg-destructive");
      this.streamingStatusDotEl.classList.add("bg-primary");
    }
  }

  /**
   * Queue message for sending after current turn completes.
   * 
   * Queue management:
   * - Max 1 queued message (replaces existing if queue full)
   * - Auto-flushes when turn completes (flushQueueAfterTurn flag)
   * - Prevents message loss (better UX than rejection)
   * 
   * Why max 1:
   * - Prevents unbounded queue growth
   * - User can update queued message (replaces instead of appends)
   * - Simple implementation (no complex queue management)
   */
  enqueueMessage(message) {
    if (!message) return;
    // If queue full, replace existing message (user updated their queued message)
    if (this.pendingMessages.length >= 1) {
      this.pendingMessages[0] = message;
      this.showToast("Queued", "Updated your next message.");
      return;
    }
    this.pendingMessages.push(message);
    this.showToast("Queued", "I'll send this after the current reply finishes.");
  }

  /**
   * Format status label with HTML emphasis.
   * 
   * Formats status labels for display in status indicator:
   * - Text in brackets [text] becomes bold
   * - Text after colon "Label: subject" emphasizes subject
   * - Escapes HTML to prevent XSS
   * 
   * Examples:
   * - "Searching: billing policy" → "Searching: <strong>billing policy</strong>"
   * - "Reading [document.pdf]" → "Reading <strong>document.pdf</strong>"
   * 
   * Why formatting:
   * - Makes status labels more readable
   * - Highlights key information (document names, search terms)
   * - Consistent visual hierarchy
   */
  formatStatusLabel(rawLabel) {
    const text = (rawLabel || "").toString();
    if (!text) return "";
    const escape = (value = "") =>
      value
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    // Pattern 1: Text in brackets [text] → bold
    const bracketPattern = /\[(.+?)\]/g;
    let result = "";
    let lastIndex = 0;
    let match;
    let hasBracket = false;
    while ((match = bracketPattern.exec(text))) {
      hasBracket = true;
      if (match.index > lastIndex) {
        result += escape(text.slice(lastIndex, match.index));
      }
      result += `<strong>${escape(match[1].trim())}</strong>`;
      lastIndex = bracketPattern.lastIndex;
    }
    if (lastIndex < text.length) {
      result += escape(text.slice(lastIndex));
    }
    if (hasBracket) {
      return result;
    }
    // Pattern 2: Text after colon "Label: subject" → emphasize subject
    const colonIndex = text.indexOf(":");
    if (colonIndex !== -1 && colonIndex < text.length - 1) {
      const prefix = text.slice(0, colonIndex + 1);
      const subject = text.slice(colonIndex + 1);
      return `${escape(prefix)} <strong>${escape(subject.trim())}</strong>`;
    }
    return escape(text);
  }

  /**
   * Mark stream as finished and enable next turn.
   * 
   * Called when "final" event arrives. Updates state flags and UI:
   * - Marks stream as finished (prevents duplicate finalization)
   * - Enables composer (user can send next message)
   * - Sets flushQueueAfterTurn flag (allows queued message to proceed)
   * 
   * Why separate method:
   * - Called from multiple places (final event, error handler)
   * - Centralizes state updates
   * - Ensures consistent cleanup
   */
  markStreamFinished() {
    this.streamFinished = true;
    this.isStreaming = false;
    this.updateSendButtonState(false);
    this.setComposerAvailability(true);
    // Enable queue flush (allows queued message to proceed)
    this.flushQueueAfterTurn = true;
  }

  /**
   * Inject CSS for status shimmer animation (idempotent).
   * 
   * Injects shimmer animation CSS for status indicator text.
   * Uses idempotent check to prevent duplicate injection (multiple instances).
   * 
   * Why inject styles:
   * - Widget is embedded (can't assume parent page has styles)
   * - Self-contained styling (no external CSS dependencies)
   * - Shimmer animation provides visual feedback (status is active)
   * 
   * Why idempotent:
   * - Multiple widget instances on same page
   * - Prevents duplicate style tags
   * - Checks both flag and DOM (defensive)
   */
  ensureStatusStyle() {
    if (this.statusStyleInjected) return;
    const styleId = "chat-portal-status-style";
    // Check if style already injected by another instance
    if (document.getElementById(styleId)) {
      this.statusStyleInjected = true;
      return;
    }
    // Inject shimmer animation CSS
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      @keyframes chat-portal-status-shimmer {
        0% { background-position: 0% 50%; }
        100% { background-position: 200% 50%; }
      }
      .chat-portal-status-shimmer {
        background-image: linear-gradient(90deg, rgba(255,255,255,0.1), rgba(255,255,255,0.7), rgba(255,255,255,0.1));
        background-size: 200% auto;
        animation: chat-portal-status-shimmer 2.2s linear infinite;
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
      }
    `;
    document.head.appendChild(style);
    this.statusStyleInjected = true;
  }

  /**
   * Update conversation status badge.
   * 
   * Updates status badge text (e.g., "New", "Live", "Resolved").
   * Status comes from backend (bootstrap, turnPersisted, statusChanged events).
   * 
   * @param {string} status - Conversation status (new, live, resolved, etc.)
   */
  updateStatus(status) {
    if (!status) return;
    this.currentStatus = status;
    const badge = this.elements.statusBadge;
    if (!badge) return;
    badge.textContent = this.formatStatus(status);
  }

  /**
   * Show/hide CSAT form based on conversation status.
   * 
   * CSAT form is only shown when conversation is resolved:
   * - Hidden for new/live conversations
   * - Shown for resolved conversations (user can rate experience)
   * 
   * Why only on resolved:
   * - Feedback is most meaningful after issue is resolved
   * - Prevents premature feedback (user might rate before resolution)
   * - Matches typical support workflow
   * 
   * @param {string} status - Conversation status (uses currentStatus if not provided)
   */
  updateCsatVisibility(status) {
    const container = this.elements.csatContainer;
    if (!container) return;
    // Only show CSAT when conversation is resolved
    const resolved = (status || this.currentStatus) === "resolved";
    container.classList.toggle("hidden", !resolved);
  }

  /**
   * Build visitor metadata for bootstrap request.
   * 
   * Collects client-side context to help backend:
   * - Locale: User's language preference
   * - Timezone: For timestamp formatting
   * - Path: Current page URL (for analytics)
   * - Referrer: Where user came from
   * - Screen: Viewport size (for responsive handling)
   * 
   * Why collect:
   * - Helps backend personalize responses (locale-aware formatting)
   * - Analytics: Track which pages generate chat sessions
   * - Debugging: Context for support cases
   */
  buildVisitorMetadata() {
    const metadata = {
      locale: navigator.language,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      path: window.location.pathname,
      referrer: document.referrer,
    };
    if (window.screen) {
      metadata.screen = `${window.screen.width}x${window.screen.height}`;
    }
    return metadata;
  }

  /**
   * Enable/disable message composer (textarea).
   * 
   * Controls whether user can type and send messages:
   * - Disabled during bootstrap (prevents sends before session ready)
   * - Disabled during streaming (prevents concurrent sends)
   * - Enabled after stream completes (allows next message)
   * 
   * @param {boolean} enabled - Whether composer should be enabled
   */
  setComposerAvailability(enabled) {
    const form = this.elements.sendForm;
    if (!form) return;
    const textarea = form.querySelector("textarea[name='message']");
    if (textarea) {
      textarea.disabled = !enabled;
    }
  }

  /**
   * Update composer visual state (waiting cursor).
   * 
   * Shows waiting cursor on send button during streaming.
   * Visual feedback that system is processing.
   * 
   * @param {boolean} waiting - Whether to show waiting state
   */
  updateComposerNotice(waiting) {
    const button = this.elements.sendButton;
    if (!button) return;
    button.classList.toggle("cursor-wait", waiting);
  }

  /**
   * Update send button state (send icon vs stop icon).
   * 
   * Toggles between send and stop icons:
   * - Send icon: Normal state (ready to send)
   * - Stop icon: Streaming state (can cancel stream)
   * 
   * Why toggle icons:
   * - Clear visual feedback (user knows they can cancel)
   * - Better UX than separate stop button
   * - Consistent with common chat UI patterns
   * 
   * @param {boolean} isResponding - Whether stream is active (show stop icon)
   */
  updateSendButtonState(isResponding) {
    const button = this.elements.sendButton;
    const sendIcon = this.elements.sendIcon;
    const stopIcon = this.elements.stopIcon;
    if (button) {
      button.disabled = false;
      button.classList.toggle("cursor-wait", isResponding);
    }
    // Toggle icons: send icon hidden during streaming, stop icon shown
    if (sendIcon) {
      sendIcon.classList.toggle("hidden", isResponding);
    }
    if (stopIcon) {
      stopIcon.classList.toggle("hidden", !isResponding);
    }
  }

  /**
   * Abort active streaming (stop button handler).
   * 
   * Cancels SSE stream and cleans up state:
   * - Aborts fetch request (stops receiving chunks)
   * - Resets UI state (buttons, composer)
   * - Removes streaming message node (user cancelled, don't show partial)
   * - Flushes queued message if ready (allows next message)
   * 
   * Why remove node:
   * - User explicitly cancelled (stop button)
   * - Partial message would be confusing
   * - Better UX: clean slate for next message
   */
  abortStreaming() {
    if (!this.awaitingReply) return;
    // Abort fetch request (stops SSE stream)
    if (this.streamController) {
      this.streamController.abort();
    }
    this.awaitingReply = false;
    this.streamFinished = true;
    this.workflowLocked = true;
    this.isStreaming = false;
    this.clearStreamingStatus();
    this.updateSendButtonState(false);
    this.setComposerAvailability(true);
    this.updateComposerNotice(false);
    // Remove streaming node (user cancelled, don't show partial message)
    this.resetStreamingState(true, false);
    // Allow queued message to proceed
    this.flushQueuedMessageIfReady();
  }

  /**
   * Get session token from localStorage.
   * 
   * Retrieves persisted token for session restoration.
   * Returns null if localStorage unavailable (private browsing, quota).
   * 
   * Why try/catch:
   * - localStorage may throw (private browsing mode)
   * - Quota exceeded errors
   * - Graceful degradation (falls back to other token sources)
   * 
   * @returns {string|null} Session token or null if unavailable
   */
  getStoredToken() {
    if (!this.sessionCacheKey) return null;
    try {
      return window.localStorage.getItem(this.sessionCacheKey);
    } catch (error) {
      // Graceful degradation: localStorage may be unavailable
      console.warn("Unable to access localStorage", error);
      return null;
    }
  }

  /**
   * Flush queued message if stream is idle.
   * 
   * Called after stream completes or is aborted. Sends next queued message
   * if stream is idle (not sending or streaming).
   * 
   * Why check flags:
   * - Prevents race conditions (don't send if stream still active)
   * - Ensures only one message at a time
   * - Queue is auto-flushed when ready
   * 
   * Queue flushing strategy:
   * - flushQueueAfterTurn flag is set when turn completes
   * - This method is called from multiple places (abort, error, success)
   * - Flags ensure we only flush when truly idle (no concurrent sends)
   */
  flushQueuedMessageIfReady() {
    // Only flush if stream is completely idle
    // Double-check prevents race conditions (flags may change between calls)
    if (this.isSending || this.isStreaming) return;
    const next = this.pendingMessages.shift();
    if (next) {
      // Recursive call: sendMessage will handle its own state management
      // This allows queue to flush naturally as turns complete
      this.sendMessage(next);
    }
  }

  /**
   * Read session token from inline bootstrap script.
   * 
   * Server can inject session token in a <script> tag with JSON data.
   * This allows token to be available immediately without localStorage lookup.
   * 
   * Why bootstrap script:
   * - Faster than localStorage (no async lookup)
   * - Works in private browsing (localStorage may be blocked)
   * - Server can preload token during SSR
   * 
   * Format:
   * <script id="bootstrap-script">
   *   {"session": {"session_token": "..."}}
   * </script>
   */
  readBootstrapScriptToken() {
    if (!this.bootstrapScriptId) return null;
    const script = document.getElementById(this.bootstrapScriptId);
    if (!script) return null;
    try {
      const data = JSON.parse(script.textContent || "{}");
      if (data && data.session && data.session.session_token) {
        return data.session.session_token;
      }
    } catch (error) {
      console.warn("Failed to parse bootstrap script", error);
    }
    return null;
  }

  /**
   * Persist session token for refresh resilience.
   * 
   * Stores token in two places:
   * 1. Container data attribute (for server-side rendering scenarios)
   * 2. localStorage (survives page refresh, keyed by sessionCacheKey)
   * 
   * Why dual storage:
   * - Data attribute: Accessible to server-side code (SSR)
   * - localStorage: Survives page refresh (user doesn't lose conversation)
   * - Graceful degradation: If localStorage fails, data attribute still works
   * 
   * Error handling:
   * - localStorage may fail (private browsing, quota exceeded)
   * - Logs warning but doesn't break flow (data attribute still set)
   */
  persistSessionToken(token) {
    if (!token) return;
    this.sessionToken = token;
    // Store in container attribute (for SSR scenarios)
    this.container.setAttribute("data-session-token", token);
    if (!this.sessionCacheKey) return;
    // Store in localStorage (survives page refresh)
    try {
      window.localStorage.setItem(this.sessionCacheKey, token);
    } catch (error) {
      // Graceful degradation: localStorage may fail (private browsing, quota)
      console.warn("Unable to persist session token", error);
    }
  }

  /**
   * Create markdown renderer for message formatting.
   * 
   * Supports:
   * - Headings (###, ##, #)
   * - Bold (**text**, __text__)
   * - Italic (*text*, _text_)
   * - Lists (ordered, unordered)
   * - Inline code (`code`)
   * - Links ([label](url) and auto-detected URLs)
   * 
   * Security:
   * - Escapes HTML to prevent XSS
   * - Uses placeholder tokens for code/links (prevents regex conflicts)
   * - Restores placeholders after escaping
   * 
   * Why custom renderer:
   * - Lightweight (no external dependencies)
   * - Tailored for chat messages (simple formatting)
   * - Handles edge cases (URLs in sentences, code in links)
   */
  createMarkdownRenderer() {
    // Escape HTML to prevent XSS attacks
    const escapeHtml = (value = "") =>
      value
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");

    /**
     * Decode HTML entities (reverse of escapeHtml).
     * 
     * Used when processing URLs that were escaped but need to be parsed.
     * Only decodes common entities (not all HTML entities for performance).
     */
    const decodeHtmlEntities = (value = "") =>
      value
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");

    /**
     * Truncate text to limit with ellipsis.
     * 
     * Used for link labels to prevent layout breaking.
     * Preserves limit-1 chars + ellipsis (total length = limit).
     */
    const truncateText = (value = "", limit = 60) => (value.length > limit ? `${value.slice(0, limit - 1)}…` : value);

    /**
     * Prettify URL for link label display.
     * 
     * Converts full URL to readable label:
     * - Removes www. prefix
     * - Truncates long path segments
     * - Limits total length to 60 chars
     * - Falls back to domain if parsing fails
     * 
     * Examples:
     * - "https://www.example.com/path/to/page" → "example.com/path/to/page"
     * - "https://example.com/very/long/path/segment" → "example.com/very/long/…"
     * 
     * Why prettify:
     * - Long URLs break layout
     * - User-friendly labels (domain + key path segments)
     * - Consistent truncation (60 char limit)
     */
    const prettifyLinkLabel = (rawUrl = "") => {
      const cleaned = decodeHtmlEntities(rawUrl || "").trim();
      if (!cleaned) return "";
      try {
        const parsed = new URL(cleaned);
        // Remove www. prefix for cleaner display
        const host = (parsed.hostname || "").replace(/^www\./i, "") || parsed.hostname;
        const segments = parsed.pathname.split("/").filter(Boolean);
        let pathLabel = "";
        // Build path label with truncation (max 60 chars total)
        for (const segment of segments) {
          // Truncate long segments (32 char limit per segment)
          const safeSegment = segment.length > 32 ? `${segment.slice(0, 29)}…` : segment;
          const tentative = pathLabel ? `${pathLabel}/${safeSegment}` : safeSegment;
          // Stop if total length exceeds 60 chars
          if (`${host}/${tentative}`.length > 60) {
            pathLabel = pathLabel ? `${pathLabel}/…` : "…";
            break;
          }
          pathLabel = tentative;
        }
        let label = host || parsed.hostname || cleaned;
        if (pathLabel) {
          label = `${label}/${pathLabel}`;
        }
        return truncateText(label, 60);
      } catch (_error) {
        // Fallback: remove protocol, truncate to 60 chars
        return truncateText(cleaned.replace(/^https?:\/\//i, ""), 60);
      }
    };

    /**
     * Create placeholder token for code/links (prevents regex conflicts).
     * 
     * Why placeholders:
     * - Code blocks and links contain special characters (`, [, ], (, ))
     * - These conflict with markdown regex patterns
     * - Replace with unique tokens, process markdown, then restore
     * 
     * Example:
     * - Code: "`function()`" → "@@CODE_0@@" → processed → restored
     * - Link: "[label](url)" → "@@LINK_0@@" → processed → restored
     */
    const createPlaceholderToken = (prefix, collection, html) => {
      const token = `@@${prefix}_${collection.length}@@`;
      collection.push(html);
      return token;
    };

    /**
     * Restore placeholder tokens with actual HTML.
     * 
     * Replaces tokens like "@@CODE_0@@" with actual HTML after markdown processing.
     * This prevents regex conflicts (code/links processed separately).
     */
    const restorePlaceholders = (text, prefix, collection) => {
      if (!collection.length) {
        return text;
      }
      let output = text;
      collection.forEach((html, index) => {
        const token = `@@${prefix}_${index}@@`;
        output = output.split(token).join(html);
      });
      return output;
    };

    /**
     * Build anchor tag for links.
     * 
     * Security attributes:
     * - target="_blank": Opens in new tab
     * - rel="nofollow noopener noreferrer": Prevents referrer leakage, SEO nofollow
     * 
     * Why security attributes:
     * - noopener: Prevents new page from accessing window.opener (security)
     * - noreferrer: Prevents referrer header (privacy)
     * - nofollow: SEO signal (don't pass link juice to external sites)
     */
    const buildAnchor = (href, label) =>
      `<a href="${href}" target="_blank" rel="nofollow noopener noreferrer" class="text-primary underline">${label}</a>`;

    /**
     * Apply inline markdown formatting (bold, italic, code, links).
     * 
     * Processing order (critical for correctness):
     * 1. Extract code blocks (prevents regex conflicts)
     * 2. Extract markdown links (prevents regex conflicts)
     * 3. Escape HTML (security: prevent XSS)
     * 4. Process bold/italic (after escaping)
     * 5. Auto-detect URLs (after escaping, handles trailing punctuation)
     * 6. Restore code/links (after all processing)
     * 
     * Why placeholder tokens:
     * - Code blocks contain backticks that conflict with markdown patterns
     * - Links contain brackets/parens that conflict with markdown patterns
     * - Extract first, process markdown, then restore
     */
    const applyInlineFormatting = (value = "") => {
      if (!value) return "";
      const codePlaceholders = [];
      const markdownLinkPlaceholders = [];

      let working = value;

      // Step 1: Extract code blocks (prevents regex conflicts with backticks)
      working = working.replace(/`([^`]+)`/g, (_, code) =>
        createPlaceholderToken(
          "CODE",
          codePlaceholders,
          `<code class="bg-muted/60 px-1 py-0.5 rounded text-xs font-mono">${escapeHtml(code)}</code>`
        )
      );

      // Step 2: Extract markdown links (prevents regex conflicts with brackets/parens)
      working = working.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
        const displayLabel = (label || "").trim() || prettifyLinkLabel(url) || url;
        const safeHref = escapeHtml(url);
        const safeLabel = escapeHtml(displayLabel);
        return createPlaceholderToken("LINK", markdownLinkPlaceholders, buildAnchor(safeHref, safeLabel));
      });

      // Step 3: Escape HTML (security: prevent XSS attacks)
      let output = escapeHtml(working);

      // Step 4: Process bold/italic (after escaping, safe to use regex)
      output = output.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      output = output.replace(/__(.+?)__/g, "<strong>$1</strong>");
      output = output.replace(/(\*|_)([^*_]+)\1/g, "<em>$2</em>");

      // Step 5: Auto-detect URLs (handles trailing punctuation like periods)
      output = output.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, (match, prefix, url) => {
        let normalizedUrl = url;
        let trailing = "";
        // Strip trailing punctuation (periods, commas, etc.) from URL
        while (/[.,!?]$/.test(normalizedUrl)) {
          trailing = normalizedUrl.slice(-1) + trailing;
          normalizedUrl = normalizedUrl.slice(0, -1);
        }
        const decoded = decodeHtmlEntities(normalizedUrl);
        if (!decoded) {
          return match;
        }
        const safeHref = escapeHtml(decoded);
        const safeLabel = escapeHtml(prettifyLinkLabel(decoded) || decoded);
        const anchor = buildAnchor(safeHref, safeLabel);
        // Restore trailing punctuation after link
        return `${prefix || ""}${anchor}${trailing}`;
      });

      // Step 6: Restore code blocks and links (after all processing)
      output = restorePlaceholders(output, "LINK", markdownLinkPlaceholders);
      output = restorePlaceholders(output, "CODE", codePlaceholders);

      return output;
    };

    /**
     * Wrap list items in HTML list tag.
     * 
     * Applies inline formatting to each item (bold, italic, links, etc.).
     * Uses Tailwind classes for styling (list markers, spacing).
     * 
     * @param {string[]} items - List item texts
     * @param {boolean} ordered - Whether list is ordered (numbered) or unordered (bulleted)
     * @returns {string} HTML list element
     */
    const wrapList = (items, ordered) => {
      if (!items.length) return "";
      const tag = ordered ? "ol" : "ul";
      const classes = ordered ? "list-decimal pl-5 space-y-1" : "list-disc pl-5 space-y-1";
      // Apply inline formatting to each item (supports markdown within list items)
      const inner = items.map((item) => `<li>${applyInlineFormatting(item)}</li>`).join("");
      return `<${tag} class="${classes}">${inner}</${tag}>`;
    };

    /**
     * Render markdown blocks (headings, lists, paragraphs).
     * 
     * Processes markdown line-by-line:
     * - Headings (###, ##, #)
     * - Lists (ordered, unordered) with continuation handling
     * - Paragraphs (default for non-matching lines)
     * 
     * List handling:
     * - Accumulates list items until non-list line or list type change
     * - Flushes list when type changes (ordered ↔ unordered)
     * - Flushes list on blank lines (paragraph break)
     * 
     * Why line-by-line:
     * - Handles mixed content (lists + paragraphs)
     * - Preserves line breaks (blank lines = paragraph breaks)
     * - Simpler than block-level parsing
     */
    const renderBlocks = (input = "") => {
      const lines = input.replace(/\r\n/g, "\n").split("\n");
      const blocks = [];
      let currentList = null;

      // Flush accumulated list items to HTML
      /**
       * Flush accumulated list items to HTML block.
       * 
       * Called when:
       * - List type changes (ordered ↔ unordered)
       * - Non-list line encountered (paragraph break)
       * - Blank line encountered (paragraph break)
       * - End of input reached
       */
      const flushList = () => {
        if (!currentList) return;
        blocks.push(wrapList(currentList.items, currentList.ordered));
        currentList = null;
      };

      for (const line of lines) {
        // Check for unordered list item (-, *, +)
        const matchUnordered = line.match(/^\s*[-*+]\s+(.*)/);
        // Check for ordered list item (1., 2., etc.)
        const matchOrdered = line.match(/^\s*\d+\.\s+(.*)/);
        if (matchUnordered) {
          // Start new list if none exists or type changed (ordered → unordered)
          if (!currentList || currentList.ordered) {
            flushList();
            currentList = { ordered: false, items: [] };
          }
          currentList.items.push(matchUnordered[1]);
          continue;
        }
        if (matchOrdered) {
          // Start new list if none exists or type changed (unordered → ordered)
          if (!currentList || !currentList.ordered) {
            flushList();
            currentList = { ordered: true, items: [] };
          }
          currentList.items.push(matchOrdered[1]);
          continue;
        }

        const trimmed = line.trim();
        // Blank line: flush list and continue (paragraph break)
        if (!trimmed) {
          flushList();
          continue;
        }

        // Non-list line: flush list, then process as heading or paragraph
        flushList();
        const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
        if (heading) {
          const level = heading[1].length;
          // Map heading levels to HTML tags (h3, h4, h5 for ###, ##, #)
          const tag = level === 1 ? "h3" : level === 2 ? "h4" : "h5";
          const classes = "font-semibold text-foreground";
          blocks.push(`<${tag} class="${classes}">${applyInlineFormatting(heading[2])}</${tag}>`);
          continue;
        }

        // Default: treat as paragraph
        blocks.push(`<p>${applyInlineFormatting(trimmed)}</p>`);
      }

      // Flush any remaining list items
      flushList();
      // Fallback: if no blocks, apply inline formatting to entire input
      return blocks.join("") || applyInlineFormatting(input);
    };

    return {
      render: renderBlocks,
    };
  }

  /**
   * Format timestamp for display.
   * 
   * Converts ISO timestamp to localized time string (HH:MM format).
   * Returns empty string if invalid (graceful degradation).
   * 
   * @param {string} value - ISO timestamp string
   * @returns {string} Formatted time (e.g., "2:30 PM") or empty string
   */
  formatTimestamp(value) {
    if (!value) return "";
    try {
      return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (_error) {
      return "";
    }
  }

  /**
   * Format status string for display.
   * 
   * Converts snake_case status to Title Case:
   * - "new" → "New"
   * - "live" → "Live"
   * - "resolved" → "Resolved"
   * 
   * @param {string} status - Status string (snake_case or lowercase)
   * @returns {string} Formatted status (Title Case)
   */
  formatStatus(status) {
    return status.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  /**
   * Get standard JSON request headers.
   * 
   * Returns headers for JSON API requests:
   * - Content-Type: application/json
   * - X-Requested-With: XMLHttpRequest (for CSRF protection)
   * 
   * @returns {Object} Headers object for fetch requests
   */
  jsonHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
  }

  /**
   * Show toast notification.
   * 
   * Creates temporary notification panel that auto-dismisses after 2.6s.
   * Supports destructive styling (red) for errors.
   * 
   * Why toast:
   * - Non-intrusive (doesn't block UI)
   * - Auto-dismisses (no user action required)
   * - Visual feedback for async operations
   * 
   * @param {string} title - Toast title
   * @param {string} description - Toast description
   * @param {boolean} destructive - Whether to use error styling (red)
   */
  showToast(title, description, destructive = false) {
    const root = this.elements.toastRoot;
    if (!root) return;
    const panel = document.createElement("div");
    panel.className = `pointer-events-auto rounded-xl border px-4 py-3 shadow-lg backdrop-blur transition ${
      destructive
        ? "border-destructive bg-destructive/10 text-destructive"
        : "border-border bg-card text-foreground"
    }`;
    panel.innerHTML = `
      <div class="font-semibold">${title}</div>
      <div class="text-sm">${description}</div>
    `;
    root.appendChild(panel);
    // Auto-dismiss after 2.6s (fade out animation, then remove from DOM)
    setTimeout(() => {
      panel.classList.add("opacity-0", "translate-y-2");
      setTimeout(() => panel.remove(), 200);
    }, 2600);
  }
}

/**
 * Initialize chat portal when DOM is ready.
 * 
 * Entry point for chat widget. Finds container element and initializes client.
 * 
 * Why DOMContentLoaded:
 * - Ensures DOM is ready before querying elements
 * - Prevents errors if script loads before HTML
 * - Standard pattern for embedded widgets
 * 
 * Container requirement:
 * - Must have [data-chat-portal] attribute
 * - Contains all widget elements (messages, forms, buttons)
 * - Server-side rendered with data attributes for configuration
 */
document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-chat-portal]");
  if (!container) return;
  const client = new ChatPortalClient(container);
  client.init();
});
