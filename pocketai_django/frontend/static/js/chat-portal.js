class ChatPortalClient {
  constructor(container) {
    this.container = container;
    this.endpoints = {
      bootstrap: container.getAttribute("data-endpoint-bootstrap"),
      messages: container.getAttribute("data-endpoint-messages"),
      streamSend: container.getAttribute("data-endpoint-stream-send"),
      events: container.getAttribute("data-endpoint-events"),
      csat: container.getAttribute("data-endpoint-csat"),
    };
    this.businessSlug = container.getAttribute("data-business-slug") || "";
    this.agentSlug = container.getAttribute("data-agent-slug") || "";
    this.agentName = container.getAttribute("data-agent-name") || "Pocket AI";
    this.agentInitials = container.getAttribute("data-agent-initials") || "AI";
    this.sessionToken = container.getAttribute("data-session-token") || null;
    this.sessionCacheKey = container.getAttribute("data-session-cache-key") || "";
    this.bootstrapScriptId = container.getAttribute("data-bootstrap-script-id") || "";
    this.currentStatus = container.getAttribute("data-initial-status") || "new";
    this.eventSource = null;
    this.awaitingReply = false;
    this.streamController = null;
    this.bootstrapPayload = null;
    this.elements = {
      messages: container.querySelector("[data-chat-messages]"),
      messagesInner: container.querySelector("[data-chat-inner-container]"),
      sendForm: container.querySelector("[data-chat-send-form]"),
      sendButton: container.querySelector("[data-chat-send-button]"),
      sendIcon: container.querySelector("[data-chat-send-icon]"),
      stopIcon: container.querySelector("[data-chat-stop-icon]"),
      csatForm: container.querySelector("[data-chat-csat-form]"),
      csatContainer: container.querySelector("[data-chat-csat]"),
      toastRoot: document.getElementById("toast-root"),
      statusBadge: container.querySelector("[data-chat-status]"),
      main: container.querySelector("[data-chat-main]"),
      welcome: container.querySelector("[data-chat-welcome]"),
      inputArea: container.querySelector("[data-chat-input-area]"),
      // Session management elements
      sessionSidebar: container.querySelector("[data-session-sidebar]"),
      sessionHistory: container.querySelector("[data-session-history]"),
      sessionsLoading: container.querySelector("[data-sessions-loading]"),
      sessionsEmpty: container.querySelector("[data-sessions-empty]"),
      sessionsList: container.querySelector("[data-sessions-list]"),
      newSessionBtn: container.querySelector("[data-new-session-btn]"),
    };
    // Session management state
    this.sessionTokens = [];
    this.currentSessionToken = null;
    this.sessionStorageKey = `chat_sessions_${this.businessSlug}_${this.agentSlug}`;
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
    this.streamingMessageId = null;
    this.streamingTextEl = null;
    this.streamingBlocksEl = null;
    this.pendingMessageId = null;
    this.pendingMetadataVersion = 0;
    this.usingStateMachine = false;
    this.workflowLocked = false;
    this.streamingActive = false;
    this.streamFinished = false;
    this.isSending = false;
    this.isStreaming = false;
    this.flushQueueAfterTurn = false;
    this.pendingMessages = [];
    this.statusStyleInjected = false;
    this.ensureStatusStyle();
    this.tableIntentActive = false;
    this.tableIntentTimestamp = 0;
    this.tableIntentWindowMs = 2500;
    // Session empty state tracking
    this.currentSessionHasMessages = false;
    this.sessionCreationInProgress = false;
  }

  async init() {
    await this.waitForDependencies();
    this.configureMarked();
    this.bindSendForm();
    this.bindCsatForm();
    this.bindScrollButton();
    this.initSessionManagement();
    this.initSidebarToggle();
    this.setComposerAvailability(false);
    try {
      await this.bootstrapSession();
      this.renderExistingMessages();
      this.setComposerAvailability(true);
      
      // Track this session in localStorage
      this.trackCurrentSession();
      // Load session history in sidebar
      this.loadSessionHistory();

      // Auto-focus input now that it is enabled
      if (this.elements.sendForm) {
        const ta = this.elements.sendForm.querySelector('textarea');
        if (ta) requestAnimationFrame(() => ta.focus());
      }

      this.connectEventStream();
    } catch (error) {
      this.showToast("Unable to load chat", error.message || "Please refresh and try again.", true);
    }
  }

  async waitForDependencies() {
    // Wait for marked and DOMPurify to be loaded (max 3 seconds)
    const maxWait = 3000;
    const interval = 50;
    let waited = 0;
    while (waited < maxWait) {
      if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        return;
      }
      await new Promise(resolve => setTimeout(resolve, interval));
      waited += interval;
    }
    console.warn('Markdown dependencies not loaded after', maxWait, 'ms');
  }

  configureMarked() {
    if (typeof marked !== 'undefined') {
      marked.setOptions({
        breaks: true,
        gfm: true,
      });
    }
  }

  renderExistingMessages() {
    const container = this.elements.messagesInner || this.elements.messages;
    console.log('[DEBUG] renderExistingMessages called, container:', container);
    if (!container) return;
    const messageBodies = container.querySelectorAll('[data-message-body]');
    console.log('[DEBUG] Found message bodies:', messageBodies.length);
    messageBodies.forEach((el) => {
      // data-message-body only appears on AI messages (not customer) per template
      const messageId = el.dataset.messageId;
      console.log('[DEBUG] Processing message ID:', messageId);
      
      // Add relative and group classes for AI messages
      el.classList.add("relative", "group", "pr-8");

      if (!messageId) {
        // No message ID means no markdown to render, just add copy button
        if (!el.querySelector('button[data-copy-btn]')) {
          this.injectCopyButton(el);
        }
        return;
      }
      
      // Find the corresponding JSON script tag for markdown rendering
      const scriptTag = document.getElementById(messageId);
      if (scriptTag) {
        try {
          const rawMarkdown = JSON.parse(scriptTag.textContent);
          if (rawMarkdown) {
            const rendered = this.renderMarkdown(rawMarkdown);
            el.innerHTML = rendered;
            // Inject copy button ONLY after innerHTML is set
            this.injectCopyButton(el);
          }
        } catch (e) {
          console.warn('Failed to parse markdown for message', messageId, e);
        }
      }
    });
  }

  injectCopyButton(container) {
      // Prevent duplicate injection
      if (container.dataset.copyInjected === 'true') return;
      if (container.querySelector('button[data-copy-btn]')) return;
      const row = container.closest(".message-row");
      if (row && row.classList.contains("flex-row-reverse")) return;
      container.dataset.copyInjected = 'true';

      // Smart positioning: try to find the last paragraph to append inline
      let target = container;
      const streamingText = container.querySelector('[data-streaming-text]');
      if (streamingText) {
          target = streamingText;
      }
      
      // If the last child is a paragraph, list item, or similar text block, append to it
      // to keep the icon inline/nearby the last word.
      let inlineTarget = null;
      if (target.lastElementChild && ["P", "LI", "SPAN", "STRONG", "EM"].includes(target.lastElementChild.tagName)) {
         inlineTarget = target.lastElementChild;
      }

      const copyBtn = document.createElement("button");
      copyBtn.dataset.copyBtn = "true";
      // inline-flex for inline, ml-2 for spacing, align-middle to center with text
      copyBtn.className = "inline-flex items-center gap-1.5 ml-2 px-2 py-1 align-bottom rounded-lg text-xs text-muted-foreground/50 hover:text-foreground hover:bg-muted/50 transition-all w-fit";
      copyBtn.type = "button";
      copyBtn.innerHTML = `<svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
      
      copyBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        let textToCopy = "";
        const messageId = container.dataset.messageId;
        const scriptTag = messageId ? document.getElementById(messageId) : null;
        
        if (scriptTag) {
             try {
                 textToCopy = JSON.parse(scriptTag.textContent);
             } catch(e) {}
        }
        if (!textToCopy) {
            textToCopy = container.innerText.replace("Copied", "").trim(); 
        }

        try {
          await navigator.clipboard.writeText(textToCopy);
          const originalHtml = copyBtn.innerHTML;
          copyBtn.innerHTML = `<svg class="h-3 w-3 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> <span class="text-[10px] font-medium text-emerald-500">Copied</span>`;
          setTimeout(() => {
            copyBtn.innerHTML = originalHtml;
          }, 2000);
        } catch (err) {
          console.warn("Clipboard write failed", err);
        }
      });
      
      if (inlineTarget) {
          inlineTarget.appendChild(copyBtn);
      } else {
          container.appendChild(copyBtn);
      }
  }

  async transitionToActiveChat() {
    const inputArea = this.elements.inputArea;
    const welcome = this.elements.welcome;
    
    // Only transition if we are in the initial centered state
    if (!inputArea || !inputArea.classList.contains("inset-0")) {
      return;
    }

    // 1. FLIP Start: Measure
    const contentWrapper = inputArea.firstElementChild;
    const startY = contentWrapper ? contentWrapper.getBoundingClientRect().top : 0;

    // 2. Change State (Synchronous)
    // Remove "centered" classes, add "bottom" classes
    inputArea.classList.remove("inset-0", "flex", "flex-col", "justify-center", "bg-background", "transition-all", "duration-500", "ease-in-out");
    inputArea.classList.add(
        "bottom-0", 
        "left-0", 
        "right-0", 
        "pb-6", 
        "bg-gradient-to-t", 
        "from-background", 
        "via-background", 
        "to-transparent",
        "transition-all", "duration-500", "ease-in-out"
    );

    // Hide welcome message (animate out)
    if (welcome) {
        welcome.classList.add("opacity-0", "-translate-y-4", "transition-all", "duration-500");
        setTimeout(() => welcome.classList.add("hidden"), 500);
    }

    // Show messages container immediately
    if (this.elements.messages) {
        this.elements.messages.classList.remove("hidden");
        // Remove initial hiding classes
        this.elements.messages.classList.remove("opacity-0", "translate-y-4");
    }

    // 3. FLIP End: Measure & Animate
    if (contentWrapper) {
        const endY = contentWrapper.getBoundingClientRect().top;
        const deltaY = startY - endY;

        // Invert: transform to emulate start position
        contentWrapper.style.transform = `translateY(${deltaY}px)`;
        contentWrapper.style.transition = "none";

        // Play: Animate to end position
        requestAnimationFrame(() => {
             // Force reflow
             void contentWrapper.offsetHeight;
             contentWrapper.style.transition = "transform 500ms cubic-bezier(0.4, 0, 0.2, 1)"; // Smooth ease
             contentWrapper.style.transform = "";
             
             // Cleanup after animation
             setTimeout(() => {
                 contentWrapper.style.transition = "";
             }, 500);
        });
    }
  }

  bindScrollButton() {
    const btn = document.querySelector("[data-scroll-bottom]");
    const scroller = this.elements.messages;
    
    if (!btn || !scroller) return;

    btn.addEventListener("click", () => {
      this.scrollToBottom(true); // Ensure scrollToBottom supports smooth behavior fallback
    });

    scroller.addEventListener("scroll", () => {
      const isNearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 100;
      if (isNearBottom) {
        btn.classList.add("hidden", "opacity-0", "translate-y-4");
        btn.classList.remove("flex");
      } else {
        btn.classList.remove("hidden");
        // slight delay to allow display:flex to apply before transition
        setTimeout(() => {
             btn.classList.remove("opacity-0", "translate-y-4");
             btn.classList.add("flex");
        }, 10);
      }
    });
  }

  bindSendForm() {
    const form = this.elements.sendForm;
    const textarea = form ? form.querySelector("textarea[name='message']") : null;
    if (!form) return;

    // Auto-resize logic (optional but good for UX)
    const resizeTextarea = () => {
      textarea.style.height = 'auto';
      textarea.style.height = textarea.scrollHeight + 'px';
    };
    if (textarea) {
      textarea.addEventListener('input', resizeTextarea);
      // Enter to send
      textarea.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          form.requestSubmit();
        }
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const message = (data.get("message") || "").toString().trim();
      if (!message || !this.sessionToken) {
        this.showToast("Start failed", "Session is still initialising.", true);
        return;
      }
      if (textarea) {
        const startHeight = textarea.style.height;
        textarea.value = "";

        // Calculate minimal height
        textarea.style.height = "auto";
        const targetHeight = textarea.scrollHeight + "px";

        // Restore start height to animate from
        if (startHeight && startHeight !== targetHeight) {
          textarea.style.height = startHeight;
          requestAnimationFrame(() => {
            textarea.style.transition = "height 0.3s ease-out";
            textarea.style.height = targetHeight;

            setTimeout(() => {
              textarea.style.transition = "";
            }, 300);
          });
        } else {
          textarea.style.height = targetHeight;
        }
      }
      if (this.isSending || this.isStreaming) {
        this.enqueueMessage(message);
        return;
      }
      await this.sendMessage(message);
      
      // Mark session as having messages after successful send
      if (!this.currentSessionHasMessages) {
        this.currentSessionHasMessages = true;
        this.updateSessionEmptyState();
      }
    });
  }

  bindCsatForm() {
    const form = this.elements.csatForm;
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!this.sessionToken) return;
      const checked = form.querySelector('input[name="score"]:checked');
      const score = Number(checked ? checked.value : 5);
      try {
        await this.submitCsat({ score });
        this.showToast("Thanks for your feedback", "Your rating has been recorded.");
        if (this.elements.csatContainer) {
          this.elements.csatContainer.classList.add("hidden");
        }
      } catch (error) {
        this.showToast("Submission failed", error.message || "Could not submit feedback.", true);
      }
    });
  }

  async bootstrapSession() {
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

    // Fix FOUC: Only render transcript if container is empty (client-side only),
    // otherwise assume server-side rendering is correct.
    const container = this.elements.messagesInner || this.elements.messages;
    if (container && container.children.length === 0) {
      this.renderTranscript(data.messages || []);
    }

    const sessionStatus = data && data.session ? data.session.status : null;
    this.updateStatus(sessionStatus);
    this.updateCsatVisibility(sessionStatus);
  }

  async sendMessage(message) {
    if (!this.sessionToken) return;
    this.clearStreamingStatus();
    this.resetStreamingState(true, false);
    this.pendingMessageId = null;
    this.pendingMetadataVersion = 0;
    this.usingStateMachine = false;
    this.workflowLocked = false;
    this.streamFinished = false;
    this.awaitingReply = true;
    this.isSending = true;
    this.isStreaming = true;
    this.updateSendButtonState(true);
    this.updateComposerNotice(true);
    this.appendMessage({
      sender: "customer",
      body: message,
      sent_at: new Date().toISOString(),
    });
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

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          this.processStreamEvent(rawEvent);
        }
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        this.showToast("Send failed", error.message || "Message could not be delivered.", true);
      }
      this.isStreaming = false;
      this.flushQueueAfterTurn = true;
    } finally {
      this.awaitingReply = false;
      this.isSending = false;
      this.updateSendButtonState(false);
      // Composer availability is controlled by stream finalization; do not lock here.
      if (this.streamingMessageNode) {
        this.resetStreamingState(true, false);
      }
      if (!this.isStreaming && this.flushQueueAfterTurn) {
        this.flushQueueAfterTurn = false;
        const next = this.pendingMessages.shift();
        if (next) {
          this.sendMessage(next);
        }
      }
    }
  }

  processStreamEvent(rawEvent) {
    const lines = rawEvent.split(/\r?\n/);
    let eventType = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.replace("event:", "").trim();
      } else if (line.startsWith("data:")) {
        data += line.replace("data:", "").trim();
      }
    }
    this.handleStreamEvent(eventType, data);
  }

  handleStreamEvent(eventType, data) {
    if (eventType === "turnPending") {
      this.handleTurnPendingEvent(data);
      return;
    }

    if (eventType === "spinnerStatus") {
      this.handleSpinnerStatusEvent(data);
      return;
    }

    if (eventType === "turnUpdated") {
      this.handleTurnUpdatedEvent(data);
      return;
    }

    if (eventType === "status") {
      if (this.workflowLocked) {
        return;
      }
      try {
        const payload = data ? JSON.parse(data) : null;
        if (payload) {
          const state = (payload.state || "").toString().trim();
          const label = (payload.label || "").toString().trim();

          if (state === "stream_complete" || state === "complete" || state === "done") {
            if (this.usingStateMachine) {
              this.setSpinnerText("", { pending: false });
            }
            return;
          }

          if (state === "reading_document" && !this.usingStateMachine) {
            // Knowledge read: we expect content to be revised after doc load.
            this.streamingRewritePending = true;
            this.setStreamingStatus("reading", label || "Reading…");
          } else if (state === "searching_knowledge" && !this.usingStateMachine) {
            // Surface search-specific label (e.g. "Searching: billing policy").
            this.setStreamingStatus("searching", label || "Searching…");
          } else if (state === "planning_actions") {
            // Keep this internal; do not surface to the visitor.
            return;
          } else if (state === "responding" && !this.usingStateMachine) {
            this.setStreamingStatus("refining", "Refining answer…");
          } else if (state && state !== "responding" && !this.usingStateMachine) {
            // Generic fallback for other states; skip explicit "responding"/"writing".
            const fallbackLabel = label || this.formatStatus(state);
            this.setStreamingStatus("working", fallbackLabel);
          }
        }
      } catch (_err) {
        // ignore malformed status payloads
      }
      return;
    }

    if (eventType === "delta") {
      if (this.usingStateMachine) {
        return;
      }
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
      try {
        const payload = data ? JSON.parse(data) : null;
        const label = payload && payload.label ? payload.label : "Follow-up tasks completed.";
        this.showToast("Workflow update", label);
      } catch (_err) {
        // ignore
      }
      return;
    }

    if (eventType === "actionsError") {
      try {
        const payload = data ? JSON.parse(data) : null;
        const message = payload && payload.error ? payload.error : "Background workflow failed.";
        this.showToast("Workflow issue", message, true);
      } catch (_err) {
        // ignore
      }
      return;
    }

    if (eventType === "turnPersisted") {
      this.handleTurnPersistedEvent(data);
      return;
    }

    if (!data && eventType !== "final") return;
    if (eventType !== "final") {
      if (eventType === "error") {
        this.showToast("Stream error", data, true);
      }
      return;
    }
    try {
      const payload = JSON.parse(data);
      if (payload) {
        const finalText = payload.text || "";
        if (this.streamingMessageNode) {
          this.finalizeStreamingMessage(finalText);
        } else if (finalText) {
          this.appendMessage({
            sender: "ai",
            body: finalText,
            sent_at: new Date().toISOString(),
          });
        }
      }
      if (payload && payload.session_status) {
        this.updateStatus(payload.session_status);
        this.updateCsatVisibility(payload.session_status);
      }
      this.awaitingReply = false;
      this.streamFinished = true;
      this.isStreaming = false;
      this.updateSendButtonState(false);
      this.setComposerAvailability(true);
      this.updateComposerNotice(false);
    } catch (error) {
      console.warn("Failed to parse stream payload", error);
    } finally {
      this.workflowLocked = true;
      this.streamingActive = false;
      this.flushQueueAfterTurn = true;
      this.clearStreamingStatus();
      this.markStreamFinished();
    }
  }

  handleTurnPendingEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse turnPending event", error);
      return;
    }
    if (!payload) return;
    const messageId = payload.message_id || this.pendingMessageId || null;
    this.pendingMessageId = messageId;
    this.usingStateMachine = true;
    if (typeof payload.metadata_version === "number") {
      this.pendingMetadataVersion = payload.metadata_version;
    }
    if (payload.session_status) {
      this.updateStatus(payload.session_status);
      this.updateCsatVisibility(payload.session_status);
    }
    this.updateStreamingText(payload.text || "", messageId);
    if (payload.spinner_text) {
      this.setSpinnerText(payload.spinner_text, { pending: payload.pending !== false });
    }
    this.awaitingReply = false;
    this.isStreaming = true;
    this.updateSendButtonState(true);
  }

  handleSpinnerStatusEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse spinner status", error);
      return;
    }
    if (!payload) return;
    this.usingStateMachine = true;
    const text = payload.text || "";
    const pending = payload.pending !== false;
    this.setSpinnerText(text, { pending });
  }

  handleTurnUpdatedEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse turnUpdated payload", error);
      return;
    }
    if (!payload) return;
    const version = typeof payload.metadata_version === "number" ? payload.metadata_version : null;
    if (version && version <= this.pendingMetadataVersion) {
      return;
    }
    if (version) {
      this.pendingMetadataVersion = version;
    }
    const messageId = payload.message_id || this.pendingMessageId || this.streamingMessageId;
    this.updateMessageMetadata(messageId, payload);
  }

  handleTurnPersistedEvent(data) {
    try {
      const payload = data ? JSON.parse(data) : null;
      if (!payload) return;
      const messageId = payload.message_id || this.pendingMessageId || this.streamingMessageId || null;
      if (typeof payload.metadata_version === "number") {
        this.pendingMetadataVersion = payload.metadata_version;
      }
      if (payload.text) {
        this.updateLatestAssistantMessage(payload.text.toString(), messageId);
      }
      this.updateMessageMetadata(messageId, payload);
      if (payload.session_status) {
        this.updateStatus(payload.session_status);
        this.updateCsatVisibility(payload.session_status);
      }
      this.setSpinnerText("", { pending: false });
      this.resetStreamingState(false, false);
      this.pendingMessageId = null;
      this.usingStateMachine = false;
      this.streamFinished = true;
      this.isStreaming = false;
      this.workflowLocked = false;
      this.updateSendButtonState(false);
      this.setComposerAvailability(true);
      this.updateComposerNotice(false);
      this.flushQueueAfterTurn = true;
    } catch (error) {
      console.warn("Failed to parse persisted turn", error);
    }
  }

  connectEventStream() {
    if (!this.endpoints.events || !this.sessionToken) return;
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

  renderTranscript(messages) {
    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return;

    container.innerHTML = "";

    // Validate message list
    if (!Array.isArray(messages)) return;

    messages.forEach((message) => {
      if (message && message.metadata && message.metadata.placeholder) {
        return;
      }
      this.appendMessage(message);
    });
  }

  renderMarkdown(text) {
    if (!text) return "";
    if (typeof marked === 'undefined') {
      // Fallback if marked not loaded
      return text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    const html = marked.parse(text);
    if (typeof DOMPurify !== 'undefined') {
      return DOMPurify.sanitize(html, {
        ADD_ATTR: ['target'],
        FORBID_ATTR: ['style'],
      });
    }
    return html;
  }

  renderResponseBlocks(bodyEl, blocks) {
    if (!bodyEl || !Array.isArray(blocks) || !blocks.length) {
      return;
    }
    try {
      const target = bodyEl.querySelector("[data-streaming-blocks]") || bodyEl;
      const existing = target.querySelector("[data-response-blocks]");
      if (existing) {
        existing.remove();
      }
      if (target === bodyEl) {
        const hasTables = blocks.some(
          (block) => block && typeof block === "object" && (block.type || "").toString().toLowerCase() === "table",
        );
        if (hasTables) {
          bodyEl.querySelectorAll("table").forEach((tableEl) => {
            tableEl.remove();
          });
        }
      }
      const blockEl = this.buildResponseBlocks(blocks);
      if (blockEl) {
        target.appendChild(blockEl);
      }
    } catch (error) {
      console.warn("Failed to render structured blocks", error);
    }
  }

  buildResponseBlocks(blocks) {
    if (!Array.isArray(blocks) || !blocks.length) {
      return null;
    }
    const wrapper = document.createElement("div");
    wrapper.dataset.responseBlocks = "true";
    wrapper.className = "mt-3 space-y-4";
    blocks.forEach((block) => {
      const section = this.buildResponseBlock(block);
      if (section) {
        wrapper.appendChild(section);
      }
    });
    if (!wrapper.children.length) {
      return null;
    }
    return wrapper;
  }

  buildResponseBlock(block) {
    if (!block || typeof block !== "object") {
      return null;
    }
    const type = (block.type || "").toString().toLowerCase();
    if (type === "text") {
      return this.buildTextBlock(block);
    }
    if (type === "table") {
      return this.buildTableBlock(block);
    }
    return null;
  }

  buildTextBlock(block) {
    const lines = Array.isArray(block.body_md)
      ? block.body_md
      : Array.isArray(block.body)
        ? block.body
        : Array.isArray(block.lines)
          ? block.lines
          : block.text
            ? [block.text]
            : [];
    if (!lines.length) {
      return null;
    }
    const container = document.createElement("div");
    container.className = "relative group space-y-1 rounded-xl bg-background/60 px-3 py-2 border border-border/60";
    if (block.heading) {
      const heading = document.createElement("p");
      heading.className = "text-sm font-semibold text-foreground";
      heading.textContent = block.heading;
      container.appendChild(heading);
    }
    const content = document.createElement("div");
    content.className = "text-base leading-relaxed";
    content.innerHTML = this.renderMarkdown(lines.join("\n"));
    container.appendChild(content);

    // Copy Button
    const copyBtn = document.createElement("button");
    // Positioned absolute bottom-right, hidden by default until group hover
    copyBtn.className = "absolute bottom-1 right-1 p-1.5 rounded-lg text-muted-foreground/50 hover:text-foreground hover:bg-muted/50 transition-all";
    copyBtn.type = "button";
    copyBtn.innerHTML = `<svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
    
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const textToCopy = lines.join("\n");
      try {
        await navigator.clipboard.writeText(textToCopy);
        const originalHtml = copyBtn.innerHTML;
        copyBtn.innerHTML = `<svg class="h-3.5 w-3.5 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
        setTimeout(() => {
          copyBtn.innerHTML = originalHtml;
        }, 2000);
      } catch (err) {
        console.warn("Clipboard write failed", err);
      }
    });
    
    container.appendChild(copyBtn);

    if (block.rtl) {
      container.dir = "rtl";
      container.classList.add("text-right");
      // Adjust button position for RTL if needed, or rely on absolute positioning which might need a flip
      copyBtn.className = copyBtn.className.replace("right-1", "left-1");
    }
    return container;
  }

  buildTableBlock(block) {
    const columns = Array.isArray(block.columns) ? block.columns : [];
    const rows = Array.isArray(block.rows) ? block.rows : [];
    if (!columns.length) {
      return null;
    }
    const wrapper = document.createElement("div");
    wrapper.className = "rounded-xl border border-border/60 overflow-hidden bg-background/80 shadow-sm";
    if (block.title) {
      const title = document.createElement("div");
      title.className = "px-4 py-2 border-b border-border/60 text-sm font-semibold text-foreground";
      title.textContent = block.title;
      wrapper.appendChild(title);
    }
    const table = document.createElement("table");
    table.className = "w-full border-collapse text-sm";
    const thead = document.createElement("thead");
    thead.className = "bg-muted/40 text-muted-foreground";
    const headerRow = document.createElement("tr");
    const columnMeta = columns.map((col, idx) => {
      if (typeof col === "string") {
        return { key: `col_${idx}`, label: col, align: "left" };
      }
      const label = col && (col.label || col.title || col.text || col.value) ? col.label || col.title || col.text || col.value : `Col ${idx + 1}`;
      const align = col && typeof col.align === "string" ? col.align.toLowerCase() : "";
      return {
        key: col && col.key ? col.key : `col_${idx}`,
        label,
        align: ["center", "right"].includes(align) ? align : "left",
      };
    });

    columnMeta.forEach((col) => {
      const th = document.createElement("th");
      th.className = "px-3 py-2 text-left font-medium";
      th.textContent = col.label || "";
      if (col.align === "center") {
        th.classList.add("text-center");
      } else if (col.align === "right") {
        th.classList.add("text-right");
      }
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    if (!rows.length) {
      const placeholderRow = document.createElement("tr");
      placeholderRow.className = "border-t border-border/40";
      const placeholderCell = document.createElement("td");
      placeholderCell.colSpan = columnMeta.length;
      placeholderCell.className = "px-3 py-4 text-center text-xs uppercase tracking-wide text-muted-foreground";
      placeholderCell.textContent = "Formatting table…";
      placeholderRow.appendChild(placeholderCell);
      tbody.appendChild(placeholderRow);
    } else {
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        tr.className = "border-t border-border/40";
        const cells = Array.isArray(row && row.cells) ? row.cells : Array.isArray(row) ? row : [];
        columnMeta.forEach((col, idx) => {
          const td = document.createElement("td");
          td.className = "px-3 py-2 text-foreground";
          const cellValue = cells[idx];
          const text =
            cellValue && typeof cellValue === "object" ? cellValue.value || cellValue.text || cellValue.label || "" : cellValue ?? "";
          td.textContent = text === null || text === undefined ? "" : text.toString();
          if (col.align === "center") {
            td.classList.add("text-center");
          } else if (col.align === "right") {
            td.classList.add("text-right");
          }
          tr.appendChild(td);
        });
        if (row.rtl) {
          tr.dir = "rtl";
          tr.classList.add("text-right");
        }
        tbody.appendChild(tr);
      });
    }
    table.appendChild(tbody);
    wrapper.appendChild(table);

    if (block.note) {
      const note = document.createElement("p");
      note.className = "px-4 py-2 text-xs text-muted-foreground border-t border-border/40";
      note.textContent = block.note;
      wrapper.appendChild(note);
    }

    if (block.rtl) {
      wrapper.dir = "rtl";
      wrapper.classList.add("text-right");
    }
    return wrapper;
  }

  appendMessage(raw) {
    const container = this.elements.messagesInner || this.elements.messages;
    const scroller = this.elements.messages;
    if (!container) return; // Should not happen if init passed

    if (this.elements.welcome && !this.elements.welcome.classList.contains("hidden")) {
      this.transitionToActiveChat();
    }

    const message = this.normalizeMessage(raw);


    const node = this.buildMessageNode(message);

    // Animation for AI messages
    if (message.sender === "ai") {
      node.classList.add("opacity-0", "translate-y-4", "transition-all", "duration-500", "ease-out");
      container.appendChild(node);
      // Inject copy button for AI messages
      const bodyEl = node.querySelector('[data-message-body]');
      if (bodyEl) this.injectCopyButton(bodyEl);
      // Trigger reflow
      void node.offsetWidth;
      node.classList.remove("opacity-0", "translate-y-4");
      // Remove transition after animation to allow instant height changes during streaming
      setTimeout(() => {
        node.classList.remove("transition-all", "duration-500", "ease-out");
      }, 500);
    } else {
      container.appendChild(node);
    }

    if (message.metadata) {
      this.updateMessageMetadata(message.id, message.metadata);
    }
    if (scroller) {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
    }
  }

  normalizeMessage(raw) {
    const sender = (raw.sender || "system").toLowerCase();
    const isAi = sender === "ai";
    const isCustomer = sender === "customer";
    const authorName =
      raw.author && raw.author.name ? raw.author.name : (isAi ? this.agentName : isCustomer ? "You" : "System");
    const authorInitials =
      raw.author && raw.author.initials ? raw.author.initials : (isAi ? this.agentInitials : isCustomer ? "YOU" : "SYS");
    const metadata = raw.metadata && typeof raw.metadata === "object" ? raw.metadata : {};
    return {
      id: raw.id || null,
      sender,
      body: raw.body || "",
      sentAt: raw.sent_at || raw.sentAt || new Date().toISOString(),
      author: {
        name: authorName,
        initials: authorInitials,
      },
      metadata,
    };
  }

  buildMessageNode(message) {
    const wrapper = document.createElement("div");
    // Match message.html structure: flex with conditional reverse for customer
    const isCustomer = message.sender === "customer";
    wrapper.className = `flex gap-4 items-start py-2 message-row ${isCustomer ? "flex-row-reverse" : ""}`;

    if (message.id) {
      wrapper.dataset.messageId = message.id;
    }

    // No Avatar

    // Content container
    const content = document.createElement("div");
    // Match message.html: flex column, items-end/start based on sender
    content.className = `flex-1 min-w-0 flex flex-col ${isCustomer ? "items-end" : "items-start"}`;

    // Header with author and timestamp
    const header = document.createElement("div");
    // Match message.html: reverse row for customer to keep author info aligned
    header.className = `flex items-baseline gap-2 mb-1 ${isCustomer ? "flex-row-reverse" : ""}`;

    // Timestamp removed
    
    content.appendChild(header);

    content.appendChild(header);

    // Message body
    const body = document.createElement("div");
    body.dir = "auto";
    const cleanBody = this.stripInlineResponseBlocks(message.body || "");
    body.innerHTML = this.renderMarkdown(cleanBody);
    body.dataset.messageBubble = "true";

    if (isCustomer) {
      body.className = "text-base leading-relaxed bg-muted text-foreground px-5 py-3 rounded-2xl rounded-tr-sm text-start inline-block shadow-sm";
    } else {
      body.dataset.messageBody = "true";
      body.className = "relative group text-base leading-relaxed text-foreground text-start max-w-none break-words pr-8";
      // Copy button will be added by injectCopyButton after message is appended
    }
    const initialBlocks = Array.isArray(message.metadata?.response_blocks) ? message.metadata.response_blocks : [];
    if (initialBlocks.length) {
      this.renderResponseBlocks(body, initialBlocks);
    }
    content.appendChild(body);

    const metadataRow = document.createElement("div");
    metadataRow.dataset.messageMeta = "true";
    metadataRow.className = "mt-2 text-xs text-muted-foreground space-y-1 hidden";
    content.appendChild(metadataRow);

    wrapper.appendChild(content);

    return wrapper;
  }

  appendStreamingChunk(chunk) {
    if (!chunk || !this.elements.messages) return;
    this.ensureStreamingMessageNode();
    const normalized = this.normalizeStreamingChunk(chunk);

    if (this.streamingRewritePending) {
      this.streamingBuffer = "";
      this.streamingRawBuffer = "";
      if (this.streamingFinalBodyEl) {
        this.streamingFinalBodyEl.innerHTML = "";
      }
      this.streamingRewritePending = false;
      this.setStreamingStatus("refining", "Refining answer…");
    }
    this.streamingRawBuffer += normalized;
    this.refreshStreamingView();
    this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
  }

  updateStreamingText(text, messageId = null) {
    if (!this.elements.messages) return;
    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);
    if (typeof text !== "string") {
      return;
    }
    this.streamingRawBuffer = text;
    this.refreshStreamingView();
    this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
  }

  normalizeStreamingChunk(chunk) {
    return chunk || "";
  }

  formatAssistantText(text) {
    if (!text) return "";
    return text;
  }

  ensureStreamingMessageNode(messageId = null) {
    if (this.streamingMessageNode && this.streamingMessageBodyEl) {
      if (messageId) {
        this.streamingMessageNode.dataset.messageId = messageId;
        this.streamingMessageId = messageId;
      }
      return;
    }

    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return;

    const node = this.buildMessageNode({
      sender: "ai",
      body: "",
      sentAt: new Date().toISOString(),
      author: { name: this.agentName, initials: this.agentInitials },
    });
    if (messageId) {
      node.dataset.messageId = messageId;
      this.streamingMessageId = messageId;
    }
    this.streamingMessageNode = node;
    this.streamingMessageBodyEl = node.querySelector("[data-message-body]");
    this.streamingMessageBubbleEl = node.querySelector("[data-message-bubble]");

    if (this.streamingMessageBodyEl) {
      this.streamingMessageBodyEl.innerHTML = "";
      const statusRow = document.createElement("div");
      statusRow.dataset.streamingStatus = "true";
      statusRow.className = "flex items-center gap-2 text-xs text-muted-foreground mb-2 hidden";
      const statusDot = document.createElement("div");
      statusDot.className = "flex items-center justify-center h-3.5 w-3.5 text-primary";
      statusDot.innerHTML = `<svg class="animate-spin h-full w-full" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>`;
      const statusText = document.createElement("span");
      statusText.classList.add("chat-portal-status-shimmer");
      statusText.textContent = "";
      statusRow.appendChild(statusDot);
      statusRow.appendChild(statusText);
      this.streamingMessageBodyEl.appendChild(statusRow);
      this.streamingStatusEl = statusRow;
      this.streamingStatusTextEl = statusText;
      this.streamingStatusDotEl = statusDot;

      const finalEl = document.createElement("div");
      finalEl.dataset.messageFinalBody = "true";
      finalEl.className = "space-y-3";
      const textEl = document.createElement("div");
      textEl.dataset.streamingText = "true";
      textEl.className = "space-y-2 leading-relaxed";
      finalEl.appendChild(textEl);
      const blocksEl = document.createElement("div");
      blocksEl.dataset.streamingBlocks = "true";
      finalEl.appendChild(blocksEl);
      this.streamingMessageBodyEl.appendChild(finalEl);
      this.streamingFinalBodyEl = finalEl;
      this.streamingTextEl = textEl;
      this.streamingBlocksEl = blocksEl;
      this.streamingBlocksEl = blocksEl;
      // Do not inject copy button yet - wait for stream to finish
    }

    // Animation for streaming AI message entry
    node.classList.add("opacity-0", "translate-y-4", "transition-all", "duration-500", "ease-out");
    container.appendChild(node);
    // Trigger reflow
    void node.offsetWidth;
    node.classList.remove("opacity-0", "translate-y-4");
    // Remove transition after animation to allow instant height changes during streaming
    setTimeout(() => {
      node.classList.remove("transition-all", "duration-500", "ease-out");
    }, 500);

    // Force scroll to show this new bubble
    if (container.closest('[data-chat-messages]')) {
        const scroller = container.closest('[data-chat-messages]');
        scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
    } else if (this.elements.messages) {
        this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
    }
  }

  finalizeStreamingMessage(finalText) {
    const incoming = (finalText || "").toString();
    if (incoming) {
      this.streamingRawBuffer = incoming;
    }
    this.refreshStreamingView();
    const text = this.streamingBuffer || "";
    if (!text) {
      if (this.streamingMessageNode) {
        this.resetStreamingState(true);
      }
      return;
    }

    if (!this.streamingFinalBodyEl) {
      this.appendMessage({ sender: "ai", body: text, sent_at: new Date().toISOString() });
    } else {
      // Stream finished using existing node - now we can show the copy button
      if (this.streamingMessageBodyEl) {
        this.injectCopyButton(this.streamingMessageBodyEl);
      }
    }
    this.resetStreamingState(false);
  }

  updateLatestAssistantMessage(text, messageId = null) {
    if (!text || !this.elements.messages) return;
    const normalized = this.formatAssistantText(text);
    const clean = this.stripInlineResponseBlocks(normalized);
    let body = this.getMessageBodyElement(messageId);
    if (!body) {
      const bodies = Array.from(this.elements.messages.querySelectorAll("[data-message-body]"));
      for (let idx = bodies.length - 1; idx >= 0; idx -= 1) {
        const candidate = bodies[idx];
        const wrapper = candidate.closest(".flex");
        if (!wrapper || wrapper.classList.contains("flex-row-reverse")) {
          continue;
        }
        body = candidate;
        break;
      }
    }
    if (!body) return;
    const finalBody = body.querySelector("[data-message-final-body]");
    if (finalBody) {
      const textTarget = finalBody.querySelector("[data-streaming-text]");
      if (textTarget) {
        textTarget.innerHTML = this.renderMarkdown(clean);
      } else {
        finalBody.innerHTML = this.renderMarkdown(clean);
      }
    } else {
      body.innerHTML = this.renderMarkdown(clean);
    }
    // Ensure copy button is present after update
    this.injectCopyButton(body);
  }

  getMessageBodyElement(messageId) {
    if (!messageId || !this.elements.messages) return null;
    const wrapper = this.elements.messages.querySelector(`[data-message-id="${messageId}"]`);
    if (!wrapper) return null;
    return wrapper.querySelector("[data-message-body]");
  }

  updateMessageMetadata(messageId, metadata) {
    if (!this.elements.messages) return;
    const metaPayload = metadata && typeof metadata === "object" ? metadata : {};
    let wrapper = null;
    if (messageId) {
      wrapper = this.elements.messages.querySelector(`[data-message-id="${messageId}"]`);
    }
    if (!wrapper && this.streamingMessageNode) {
      wrapper = this.streamingMessageNode;
    }
    if (!wrapper) return;
    const metaEl = wrapper.querySelector("[data-message-meta]");
    if (!metaEl) return;
    const fragments = [];
    if (typeof metaPayload.answer_confidence === "number") {
      const percent = Math.round(metaPayload.answer_confidence * 100);
      fragments.push(`Confidence: ${percent}%`);
    } else if (metaPayload.answer_confidence) {
      fragments.push(`Confidence: ${metaPayload.answer_confidence}`);
    }
    if (Array.isArray(metaPayload.ingestion_warnings) && metaPayload.ingestion_warnings.length) {
      metaPayload.ingestion_warnings.slice(0, 2).forEach((warning) => {
        const label = warning.label || warning.details || warning.type || "Source warning";
        fragments.push(`Note: ${label}`);
      });
    }
    if (Array.isArray(metaPayload.actions) && metaPayload.actions.length) {
      const summary = metaPayload.actions
        .map((action) => {
          const status = action.status || "queued";
          return `${action.action || "action"} (${status})`;
        })
        .slice(0, 2)
        .join(", ");
      if (summary) {
        fragments.push(`Actions: ${summary}`);
      }
    }
    metaEl.innerHTML = "";
    if (!fragments.length) {
      metaEl.classList.add("hidden");
    } else {
      fragments.forEach((line) => {
        const p = document.createElement("p");
        p.textContent = line;
        metaEl.appendChild(p);
      });
      metaEl.classList.remove("hidden");
    }
    if (Array.isArray(metaPayload.response_blocks) && metaPayload.response_blocks.length) {
      const body = wrapper.querySelector("[data-message-body]");
      this.renderResponseBlocks(body, metaPayload.response_blocks);
    }
  }

  renderStreamingText() {
    let html = "";
    if (this.streamingBuffer) {
      // Check for partial table at the end
      const lines = this.streamingBuffer.split("\n");
      
      let tableStartIndex = -1;

      // Scan backwards for contiguous table lines
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i].trim();
        if (line.startsWith("|")) {
          tableStartIndex = i;
        } else if (line === "") {
            // Gap might mean end of table block walking backwards
            continue;
        } else {
          // Found non-table text
          break;
        }
      }

      // If we found a table block at the end
      if (tableStartIndex !== -1) {
        const safeLines = lines.slice(0, tableStartIndex);
        const tableLines = lines.slice(tableStartIndex);
        
        // Only treat as table if we have at least one pipe-starting line
        if (tableLines.some(l => l.trim().startsWith('|'))) {
             const safeHtml = this.renderMarkdown(safeLines.join("\n"));
             const tableHtml = this.renderProvisionalTable(tableLines);
             html = safeHtml + tableHtml;
        } else {
             html = this.renderMarkdown(this.streamingBuffer);
        }
      } else {
        html = this.renderMarkdown(this.streamingBuffer);
      }
    }

    if (this.streamingTextEl) {
      this.streamingTextEl.innerHTML = html;
      return;
    }
    if (this.streamingFinalBodyEl) {
      this.streamingFinalBodyEl.innerHTML = html;
      return;
    }
    if (this.streamingMessageBodyEl) {
      this.streamingMessageBodyEl.innerHTML = html;
    }
  }

  renderProvisionalTable(lines) {
    if (!lines || !lines.length) return "";
    let html = '<div class="overflow-x-auto mb-3"><table class="w-full text-sm">';
    
    const rows = lines.filter(l => l.trim().startsWith('|'));
    
    // Heuristic: Determine column count from the header (first row)
    // | A | B | -> ["", " A ", " B ", ""] -> length 4, data columns = length - 2 (ignoring edges)?
    // Let's count actual separators? Or just filtered split length?
    let expectedColumns = 0;
    if (rows.length > 0) {
        const headerCells = rows[0].split("|");
        // Filter out empty start/end cells caused by standard |...| syntax
        // Or assume consistent syntax.
        // Let's just track the max columns seen if header is weird
        expectedColumns = headerCells.length; 
    }

    rows.forEach((line, index) => {
      // Check for separator line (only - : | space)
      if (/^[\s|:-]+$/.test(line)) return;

      html += "<tr>";
      let cells = line.split("|");
      
      // PAD ROW: If this row has fewer cells than expected (incomplete streaming), add empty ones.
      // Note: we're operating on the raw split array which includes empty start/end strings for |..|
      if (expectedColumns > 0 && cells.length < expectedColumns) {
           const missing = expectedColumns - cells.length;
           for(let k=0; k<missing; k++) {
               cells.push("");
           }
      }

      cells.forEach((cell, cIdx) => {
        // Skip purely empty edge cells typical of MD syntax
        if ((cIdx === 0 || cIdx === cells.length - 1) && cell.trim() === "") return;
        
        const isHeader = index === 0; 
        const tag = isHeader ? "th" : "td";
        html += `<${tag}>${this.renderMarkdown(cell.trim())}</${tag}>`;
      });
      html += "</tr>";
    });

    html += "</table></div>";
    return html;
  }





  refreshStreamingView() {
    const stripped = this.stripInlineResponseBlocks(this.streamingRawBuffer || "");
    this.updateTableIntent(stripped);
    this.streamingBuffer = this.formatAssistantText(stripped);
    this.renderStreamingText();
  }

  stripInlineResponseBlocks(text) {
    if (!text) {
      return "";
    }
    const pattern = /(?:^|\n)\s*(?:[-*+]\s*)?["'`]?response(?:_|\s)?blocks["'`]?\s*:?/gi;
    let match;
    let lastIndex = -1;
    while ((match = pattern.exec(text)) !== null) {
      lastIndex = match.index;
    }
    if (lastIndex < 0) {
      return text;
    }
    return text.slice(0, lastIndex).replace(/\s+$/, "");
  }

  updateTableIntent(bufferText) {
    const now = Date.now();
    if (this.tableIntentActive && now - this.tableIntentTimestamp > this.tableIntentWindowMs) {
      this.tableIntentActive = false;
    }
    const cues = [
      "following table",
      "table below",
      "table above",
      "table shows",
      "table presents",
      "table summarizes",
      "see table",
    ];
    const haystack = (bufferText || "").toLowerCase().slice(-400);
    if (!haystack) {
      return;
    }
    const cueFound = cues.some((phrase) => haystack.includes(phrase));
    if (cueFound) {
      this.tableIntentActive = true;
      this.tableIntentTimestamp = now;
    }
  }

  resetStreamingState(removeNode = false, lockWorkflow = true) {
    if (lockWorkflow) {
      this.workflowLocked = true;
    }
    this.streamingActive = false;
    this.isStreaming = false;
    this.clearStreamingStatus();
    if (removeNode && this.streamingMessageNode && this.streamingMessageNode.parentNode) {
      this.streamingMessageNode.parentNode.removeChild(this.streamingMessageNode);
    }
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
    this.streamingMessageId = null;
    this.streamingTextEl = null;
    this.streamingBlocksEl = null;
    if (removeNode) {
      this.pendingMessageId = null;
    }
  }

  setStreamingStatus(mode = "working", labelOverride) {
    if (this.workflowLocked) return;
    const labelMap = {
      working: "Assistant is working…",
      drafting: "Refining answer…",
      reading: "Reading…",
      searching: "Searching…",
      updating: "Refining answer…",
      refining: "Refining answer…",
      error: "Workflow issue detected.",
    };
    const baseLabel = labelOverride || labelMap[mode] || labelMap.working;
    const isError = mode === "error";
    this.setSpinnerText(baseLabel, { pending: mode !== "done", isError });
  }

  setSpinnerText(rawText, { pending = true, isError = false } = {}) {
    if (this.workflowLocked && pending) return;
    this.ensureStreamingMessageNode(this.pendingMessageId);
    if (!this.streamingStatusEl || !this.streamingStatusTextEl) return;
    const label = (rawText || "").toString().trim();
    if (!label) {
      if (!pending) {
        this.clearStreamingStatus();
      }
      return;
    }
    this.streamingStatusTextEl.innerHTML = this.formatStatusLabel(label);
    this.streamingStatusEl.classList.remove("hidden");
    if (this.streamingStatusDotEl) {
      if (isError) {
        this.streamingStatusDotEl.classList.remove("text-primary");
        this.streamingStatusDotEl.classList.add("text-destructive");
      } else {
        this.streamingStatusDotEl.classList.remove("text-destructive");
        this.streamingStatusDotEl.classList.add("text-primary");
      }
    }
    this.streamingStatusTextEl.classList.toggle("text-destructive", isError);
    if (pending && !isError) {
      this.streamingStatusTextEl.classList.add("chat-portal-status-shimmer");
    } else {
      this.streamingStatusTextEl.classList.remove("chat-portal-status-shimmer");
    }
  }

  clearStreamingStatus() {
    if (this.streamingStatusEl) {
      this.streamingStatusEl.classList.add("hidden");
    }
    if (this.streamingStatusTextEl) {
      this.streamingStatusTextEl.textContent = "";
      this.streamingStatusTextEl.classList.remove("text-destructive");
      this.streamingStatusTextEl.classList.add("chat-portal-status-shimmer");
    }
    if (this.streamingStatusDotEl) {
      this.streamingStatusDotEl.classList.remove("text-destructive");
      this.streamingStatusDotEl.classList.add("text-primary");
    }
  }

  enqueueMessage(message) {
    if (!message) return;
    if (this.pendingMessages.length >= 1) {
      this.pendingMessages[0] = message;
      this.showToast("Queued", "Updated your next message.");
      return;
    }
    this.pendingMessages.push(message);
    this.showToast("Queued", "I'll send this after the current reply finishes.");
  }

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
    const colonIndex = text.indexOf(":");
    if (colonIndex !== -1 && colonIndex < text.length - 1) {
      const prefix = text.slice(0, colonIndex + 1);
      const subject = text.slice(colonIndex + 1);
      return `${escape(prefix)} <strong>${escape(subject.trim())}</strong>`;
    }
    return escape(text);
  }

  markStreamFinished() {
    this.streamFinished = true;
    this.isStreaming = false;
    this.updateSendButtonState(false);
    this.setComposerAvailability(true);
    this.flushQueueAfterTurn = true;
  }

  ensureStatusStyle() {
    if (this.statusStyleInjected) return;
    const styleId = "chat-portal-status-style";
    if (document.getElementById(styleId)) {
      this.statusStyleInjected = true;
      return;
    }
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      @keyframes chat-portal-status-shimmer {
        0% { background-position: 0% 50%; }
        100% { background-position: 200% 50%; }
      }
      .chat-portal-status-shimmer {
        background: linear-gradient(
          90deg,
          hsl(var(--muted-foreground) / 0.5) 0%,
          hsl(var(--muted-foreground) / 1) 40%,
          hsl(var(--muted-foreground) / 0.5) 80%
        );
        background-size: 200% auto;
        animation: chat-portal-status-shimmer 3s ease-in-out infinite;
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
      }
    `;
    document.head.appendChild(style);
    this.statusStyleInjected = true;
  }

  updateStatus(status) {
    if (!status) return;
    this.currentStatus = status;
    const badge = this.elements.statusBadge;
    if (!badge) return;
    badge.textContent = this.formatStatus(status);
  }

  updateCsatVisibility(status) {
    const container = this.elements.csatContainer;
    if (!container) return;
    const resolved = (status || this.currentStatus) === "resolved";
    container.classList.toggle("hidden", !resolved);
  }

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

  setComposerAvailability(enabled) {
    const form = this.elements.sendForm;
    if (!form) return;
    const textarea = form.querySelector("textarea[name='message']");
    if (textarea) {
      textarea.disabled = !enabled;
    }
  }

  updateComposerNotice(waiting) {
    const button = this.elements.sendButton;
    if (!button) return;
    button.classList.toggle("cursor-wait", waiting);
  }

  updateSendButtonState(isResponding) {
    const button = this.elements.sendButton;
    const sendIcon = this.elements.sendIcon;
    const stopIcon = this.elements.stopIcon;
    if (button) {
      button.disabled = false;
      button.classList.toggle("cursor-wait", isResponding);
    }
    if (sendIcon) {
      sendIcon.classList.toggle("hidden", isResponding);
    }
    if (stopIcon) {
      stopIcon.classList.toggle("hidden", !isResponding);
    }
  }

  abortStreaming() {
    if (!this.awaitingReply) return;
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
    this.resetStreamingState(true, false);
    this.flushQueuedMessageIfReady();
  }

  getStoredToken() {
    if (!this.sessionCacheKey) return null;
    try {
      return window.localStorage.getItem(this.sessionCacheKey);
    } catch (error) {
      console.warn("Unable to access localStorage", error);
      return null;
    }
  }

  flushQueuedMessageIfReady() {
    if (this.isSending || this.isStreaming) return;
    const next = this.pendingMessages.shift();
    if (next) {
      this.sendMessage(next);
    }
  }

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

  persistSessionToken(token) {
    if (!token) return;
    this.sessionToken = token;
    this.container.setAttribute("data-session-token", token);
    if (!this.sessionCacheKey) return;
    try {
      window.localStorage.setItem(this.sessionCacheKey, token);
    } catch (error) {
      console.warn("Unable to persist session token", error);
    }
  }



  formatStatus(status) {
    return status.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  jsonHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
  }

  scrollToBottom(smooth = false) {
    const scroller = this.elements.messages;
    if (!scroller) return;
    scroller.scrollTo({
      top: scroller.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  }

  showToast(title, description, destructive = false) {
    const root = this.elements.toastRoot;
    if (!root) return;
    const panel = document.createElement("div");
    panel.className = `pointer-events-auto rounded-xl border px-4 py-3 shadow-lg backdrop-blur transition ${destructive
      ? "border-destructive bg-destructive/10 text-destructive"
      : "border-border bg-card text-foreground"
      }`;
    panel.innerHTML = `
      <div class="font-semibold">${title}</div>
      <div class="text-sm">${description}</div>
    `;
    root.appendChild(panel);
    setTimeout(() => {
      panel.classList.add("opacity-0", "translate-y-2");
      setTimeout(() => panel.remove(), 200);
    }, 2600);
  }

  // ------------------------------------------------------------------
  // Session Management
  // ------------------------------------------------------------------

  initSessionManagement() {
    // Bind new session button
    if (this.elements.newSessionBtn) {
      this.elements.newSessionBtn.addEventListener("click", () => this.createNewSession());
    }
    // Initialize session empty state
    this.updateSessionEmptyState();
    
    // Bind Recents toggle (expand/collapse)
    this.initRecentsToggle();
  }

  initRecentsToggle() {
    const toggleBtn = this.container.querySelector('[data-recents-toggle]');
    const chevron = this.container.querySelector('[data-recents-chevron]');
    const itemsContainer = this.container.querySelector('[data-sessions-items]');
    
    if (!toggleBtn || !itemsContainer) return;
    
    // Load saved state from localStorage
    const storageKey = `recents_collapsed_${this.businessSlug}_${this.agentSlug}`;
    const isCollapsed = localStorage.getItem(storageKey) === 'true';
    
    if (isCollapsed) {
      itemsContainer.classList.add('hidden');
      if (chevron) chevron.style.transform = 'rotate(-90deg)';
    }
    
    toggleBtn.addEventListener('click', () => {
      const nowCollapsed = !itemsContainer.classList.contains('hidden');
      
      if (nowCollapsed) {
        itemsContainer.classList.add('hidden');
        if (chevron) chevron.style.transform = 'rotate(-90deg)';
        localStorage.setItem(storageKey, 'true');
      } else {
        itemsContainer.classList.remove('hidden');
        if (chevron) chevron.style.transform = '';
        localStorage.setItem(storageKey, 'false');
      }
    });
  }

  initSidebarToggle() {
    const sidebar = this.elements.sessionSidebar;
    const toggleBtn = sidebar?.querySelector('[data-sidebar-toggle]');

    if (!sidebar || !toggleBtn) return;

    const storageKey = `portal_sidebar_collapsed_${this.businessSlug}_${this.agentSlug}`;
    const applyCollapsed = (collapsed) => {
      sidebar.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
      toggleBtn.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
      toggleBtn.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
    };

    let saved = null;
    try {
      saved = window.localStorage ? window.localStorage.getItem(storageKey) : null;
    } catch (_err) {
      saved = null;
    }
    applyCollapsed(saved === 'true');

    toggleBtn.addEventListener('click', () => {
      const next = sidebar.getAttribute('data-collapsed') !== 'true';
      applyCollapsed(next);
      try {
        if (window.localStorage) {
          window.localStorage.setItem(storageKey, next ? 'true' : 'false');
        }
      } catch (_err) {
        // ignore storage failures
      }
    });
  }

  getSessionTokens() {
    try {
      const stored = localStorage.getItem(this.sessionStorageKey);
      if (!stored) return [];
      const parsed = JSON.parse(stored);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      console.warn("Failed to load session tokens", e);
      return [];
    }
  }

  saveSessionTokens(tokens) {
    try {
      // Keep only the most recent 100 sessions
      const limited = tokens.slice(0, 100);
      localStorage.setItem(this.sessionStorageKey, JSON.stringify(limited));
      this.sessionTokens = limited;
    } catch (e) {
      console.warn("Failed to save session tokens", e);
    }
  }

  trackCurrentSession() {
    if (!this.sessionToken) return;
    
    this.currentSessionToken = this.sessionToken;
    const tokens = this.getSessionTokens();
    
    // Add current session if it doesn't exist (don't reorder if it does)
    if (!tokens.includes(this.sessionToken)) {
      // Add new session at the beginning (newest)
      tokens.unshift(this.sessionToken);
      this.saveSessionTokens(tokens);
    }
  }

  async loadSessionHistory() {
    const tokens = this.getSessionTokens();
    
    if (tokens.length === 0) {
      this.showSessionsEmpty();
      return;
    }

    this.showSessionsLoading();

    try {
      const response = await fetch("/api/chat/portal/sessions/list/", {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify({
          business_slug: this.businessSlug,
          agent_slug: this.agentSlug,
          session_tokens: tokens,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to load sessions");
      }

      const data = await response.json();
      const sessions = data.sessions || [];

      if (sessions.length === 0) {
        this.showSessionsEmpty();
      } else {
        this.renderSessionList(sessions);
      }
    } catch (error) {
      console.warn("Failed to load session history", error);
      this.showSessionsEmpty();
    }
  }

  showSessionsLoading() {
    if (this.elements.sessionsLoading) {
      this.elements.sessionsLoading.classList.remove("hidden");
    }
    if (this.elements.sessionsEmpty) {
      this.elements.sessionsEmpty.classList.add("hidden");
    }
    if (this.elements.sessionsList) {
      this.elements.sessionsList.classList.add("hidden");
    }
  }

  showSessionsEmpty() {
    if (this.elements.sessionsLoading) {
      this.elements.sessionsLoading.classList.add("hidden");
    }
    if (this.elements.sessionsEmpty) {
      this.elements.sessionsEmpty.classList.remove("hidden");
    }
    if (this.elements.sessionsList) {
      this.elements.sessionsList.classList.add("hidden");
    }
  }

  renderSessionList(sessions) {
    if (this.elements.sessionsLoading) {
      this.elements.sessionsLoading.classList.add("hidden");
    }
    if (this.elements.sessionsEmpty) {
      this.elements.sessionsEmpty.classList.add("hidden");
    }
    if (!this.elements.sessionsList) return;

    this.elements.sessionsList.classList.remove("hidden");
    
    // Find the items container (new structure) or fall back to list itself
    const itemsContainer = this.elements.sessionsList.querySelector('[data-sessions-items]') || this.elements.sessionsList;
    itemsContainer.innerHTML = "";

    for (const session of sessions) {
      const isActive = session.session_token === this.currentSessionToken;
      const item = this.buildSessionItem(session, isActive);
      itemsContainer.appendChild(item);
    }
  }

  buildSessionItem(session, isActive) {
    const div = document.createElement("div");
    div.className = `flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer transition-colors text-[13px] ${
      isActive
        ? "bg-primary/10 text-primary"
        : "text-foreground/80 hover:bg-muted/50"
    }`;
    div.dataset.sessionToken = session.session_token;

    // Compact title-only layout
    div.innerHTML = `
      <span class="flex-1 truncate">${this.escapeHtml(session.title)}</span>
    `;

    // Click to switch session
    div.addEventListener("click", () => {
      if (!isActive) {
        this.switchToSession(session.session_token);
      }
    });

    return div;
  }

  formatRelativeTime(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    
    return date.toLocaleDateString();
  }

  escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  async createNewSession() {
    // Check if current session is empty
    if (this.isCurrentSessionEmpty()) {
      this.showToast(
        "Start chatting first", 
        "Please send a message in this chat before creating a new one.",
        false
      );
      return;
    }
    
    // Prevent double-clicking
    if (this.sessionCreationInProgress) {
      return;
    }
    
    this.sessionCreationInProgress = true;

    // Disable button while creating
    if (this.elements.newSessionBtn) {
      this.elements.newSessionBtn.disabled = true;
      this.elements.newSessionBtn.classList.add("opacity-50");
    }

    try {
      const response = await fetch("/api/chat/portal/sessions/create/", {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify({
          business_slug: this.businessSlug,
          agent_slug: this.agentSlug,
          metadata: this.buildVisitorMetadata(),
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to create session");
      }

      const data = await response.json();
      const newToken = data.session?.session_token;

      if (!newToken) {
        throw new Error("No session token returned");
      }

      // Update localStorage to use new session and reload
      try {
        window.localStorage.setItem(this.sessionCacheKey, newToken);
      } catch (e) {
        console.warn("Failed to update session cache", e);
      }
      
      // Reload page with new session
      window.location.reload();
    } catch (error) {
      this.showToast("New chat failed", error.message || "Could not create new conversation.", true);
      this.sessionCreationInProgress = false;
      
      // Re-enable button
      if (this.elements.newSessionBtn) {
        this.elements.newSessionBtn.disabled = false;
        this.elements.newSessionBtn.classList.remove("opacity-50");
      }
    }
  }

  switchToSession(sessionToken) {
    if (!sessionToken || sessionToken === this.currentSessionToken) return;

    // Update localStorage to set this as the current session
    try {
      window.localStorage.setItem(this.sessionCacheKey, sessionToken);
    } catch (e) {
      console.warn("Failed to update session cache", e);
    }

    // Reload page to load the new session
    window.location.reload();
  }

  isCurrentSessionEmpty() {
    /**
     * Check if current session has any customer messages.
     * Returns true if no customer messages have been sent.
     */
    // Check if we've tracked that messages were sent
    if (this.currentSessionHasMessages) {
      return false;
    }
    
    // Also check DOM for customer messages (in case of page reload)
    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return true;
    
    // Look for customer message bubbles
    const messages = container.querySelectorAll('[data-message-body]');
    for (const msg of messages) {
      const parent = msg.closest('.message-row');
      if (parent && parent.classList.contains('flex-row-reverse')) {
        // This is a customer message (flex-row-reverse class)
        return false;
      }
    }
    
    return true;
  }

  updateSessionEmptyState() {
    /**
     * Update the session empty state and button UI accordingly.
     */
    const isEmpty = this.isCurrentSessionEmpty();
    const btn = this.elements.newSessionBtn;
    
    if (!btn) return;
    
    if (isEmpty) {
      // Disable new chat button when current session is empty
      btn.disabled = false; // Keep enabled but show message on click
      btn.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
      // Enable new chat button when session has messages
      btn.disabled = false;
      btn.classList.remove('opacity-50', 'cursor-not-allowed');
      this.currentSessionHasMessages = true;
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-chat-portal]");
  if (!container) return;
  const client = new ChatPortalClient(container);
  client.init();
});
