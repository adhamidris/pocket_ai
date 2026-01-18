class ChatPortalClient {
  constructor(container) {
    this.container = container;
    this.endpoints = {
      bootstrap: container.getAttribute("data-endpoint-bootstrap"),
      messages: container.getAttribute("data-endpoint-messages"),
      streamSend: container.getAttribute("data-endpoint-stream-send"),
      events: container.getAttribute("data-endpoint-events"),
      csat: container.getAttribute("data-endpoint-csat"),
      toolApproval: container.getAttribute("data-endpoint-tool-approval"),
      toolHistory: container.getAttribute("data-endpoint-tool-history"),
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
    this.streamingToolsEl = null;
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
    this.sessionLoadId = 0;
    this.sessionLoadInProgress = false;
    this.sessionSummaries = [];
    this.pendingSessionTitles = {};
    this.toolEventCards = new Map();
    this.toolsVisibilityKey = `chat_portal_tools_visible_${this.businessSlug}_${this.agentSlug}`;
    this.globalToolsVisible = this.readGlobalToolsPreference();
    this.toolHistoryModal = null;
    this.toolHistoryModalBody = null;
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
      const bootstrapData = await this.bootstrapSession();
      if (!bootstrapData) return;
      this.renderExistingMessages();
      this.hydrateMessageMetadata(this.bootstrapPayload ? this.bootstrapPayload.messages : []);
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
    if (!container) return;
    const messageBodies = container.querySelectorAll('[data-message-body]');
    messageBodies.forEach((el) => {
      // data-message-body only appears on AI messages (not customer) per template
      const messageId = el.dataset.messageId;
      
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

  hydrateMessageMetadata(messages) {
    if (!Array.isArray(messages) || !messages.length) return;
    messages.forEach((message) => {
      if (!message || !message.id) return;
      if (message.metadata && message.metadata.placeholder) return;
      if (!message.metadata || typeof message.metadata !== "object") return;
      this.updateMessageMetadata(message.id, message.metadata);
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
      const wasEmpty = this.isCurrentSessionEmpty();
      if (this.isSending || this.isStreaming) {
        this.enqueueMessage(message);
        return;
      }
      if (wasEmpty) {
        this.updateSessionTitleFromMessage(message);
        this.currentSessionHasMessages = true;
        this.updateSessionEmptyState(1);
      }
      await this.sendMessage(message);
      
      // Mark session as having messages after successful send
      if (!wasEmpty && !this.currentSessionHasMessages) {
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

  async bootstrapSession(forceRender = false) {
    const options = typeof forceRender === "object" && forceRender !== null ? forceRender : { forceRender };
    const preloadedToken = this.readBootstrapScriptToken();
    const requestId = typeof options.loadId === "number" ? options.loadId : ++this.sessionLoadId;
    const resolvedToken =
      options.sessionToken || this.getStoredToken() || this.sessionToken || preloadedToken;
    const payload = {
      business_slug: this.businessSlug,
      agent_slug: this.agentSlug,
      session_token: resolvedToken,
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
    if (this.sessionLoadId !== requestId) {
      return null;
    }
    this.bootstrapPayload = data;
    const token = data && data.session && data.session.session_token ? data.session.session_token : null;
    if (!token) {
      throw new Error("Session token missing from bootstrap response");
    }
    if (options.expectedToken && token !== options.expectedToken) {
      throw new Error("Session is no longer available.");
    }
    this.persistSessionToken(token);
    const messages = Array.isArray(data.messages) ? data.messages : [];
    const effectiveMessages = messages.filter(
      (message) => !(message && message.metadata && message.metadata.placeholder),
    );
    this.currentSessionHasMessages = effectiveMessages.length > 0;
    this.updateSessionEmptyState(effectiveMessages.length);
    this.setSessionMessageCount(token, effectiveMessages.length);
    this.setConversationLayout(effectiveMessages.length > 0);

    // Fix FOUC: Only render transcript if container is empty (client-side only),
    // otherwise assume server-side rendering is correct.
    const container = this.elements.messagesInner || this.elements.messages;
    if (container && (container.children.length === 0 || options.forceRender)) {
      this.renderTranscript(messages);
    }

    const sessionStatus = data && data.session ? data.session.status : null;
    this.updateStatus(sessionStatus);
    this.updateCsatVisibility(sessionStatus);
    return data;
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
      const requestBody = {
        session_token: this.sessionToken,
        body: message,
      };
      const response = await fetch(this.endpoints.streamSend, {
        method: "POST",
        headers: {
          ...this.jsonHeaders(),
          Accept: "text/event-stream",
        },
        body: JSON.stringify(requestBody),
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
    if (this.sessionLoadInProgress) {
      return;
    }
    if (eventType === "turnPending") {
      this.handleTurnPendingEvent(data);
      return;
    }

    if (eventType === "spinnerStatus") {
      this.handleSpinnerStatusEvent(data);
      return;
    }

    if (eventType === "toolEvent") {
      this.handleToolEvent(data);
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

  handleToolEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse toolEvent payload", error);
      return;
    }
    if (!payload) return;

    const eventId = (payload.event_id || payload.eventId || payload.tool_call_id || payload.toolCallId || "")
      .toString()
      .trim();
    if (!eventId) return;
    const phase = (payload.phase || "").toString().trim().toLowerCase();
    if (!phase) return;

    const rawMessageId = (payload.message_id || payload.messageId || "").toString().trim();
    const messageId = rawMessageId || this.pendingMessageId || this.streamingMessageId || null;

    let wrapper = this.getToolEventWrapper(messageId);
    if (!wrapper) {
      this.ensureStreamingMessageNode(messageId);
      wrapper = this.getToolEventWrapper(messageId);
    }
    if (!wrapper) return;

    const toolsContainer = this.ensureToolActivityContainer(wrapper);
    if (!toolsContainer) return;

    const messageKey = wrapper.dataset.messageId || "streaming";
    const cardKey = `${messageKey}:${eventId}`;
    let card = this.toolEventCards.get(cardKey);
    if (!card) {
      card = toolsContainer.querySelector(`[data-tool-event-id="${eventId}"]`);
    }
    if (!card) {
      card = this.buildToolEventCard(payload);
      if (!card) return;
      toolsContainer.appendChild(card);
      this.toolEventCards.set(cardKey, card);
    }
    this.updateToolEventCard(card, payload);
    this.updateMessageToolsToggle(wrapper);

    if (this.elements.messages) {
      this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
    }
  }

  getToolEventWrapper(messageId) {
    if (messageId && this.elements.messages) {
      const found = this.elements.messages.querySelector(`[data-message-id="${messageId}"]`);
      if (found) return found;
    }
    return this.streamingMessageNode || null;
  }

  readGlobalToolsPreference() {
    if (!this.toolsVisibilityKey) return true;
    try {
      const stored = window.localStorage.getItem(this.toolsVisibilityKey);
      if (stored === null) return true;
      return stored === "true";
    } catch (error) {
      console.warn("Unable to read tool visibility preference", error);
      return true;
    }
  }

  setGlobalToolsVisibility(value) {
    this.globalToolsVisible = Boolean(value);
    if (this.toolsVisibilityKey) {
      try {
        window.localStorage.setItem(this.toolsVisibilityKey, String(this.globalToolsVisible));
      } catch (error) {
        console.warn("Unable to persist tool visibility preference", error);
      }
    }
    this.updateGlobalToolsToggle();
    this.updateAllToolsVisibility();
  }

  ensureGlobalToolsToggle() {
    if (!this.elements.messagesInner) return;
    let root = this.elements.messagesInner.querySelector("[data-tools-global]");
    if (!root) {
      root = document.createElement("div");
      root.dataset.toolsGlobal = "true";
      root.className = "flex justify-end gap-2";

      const button = document.createElement("button");
      button.type = "button";
      button.dataset.toolsGlobalToggle = "true";
      button.className =
        "inline-flex items-center gap-2 rounded-full border border-border/40 bg-background/90 px-3 py-1 text-[11px] font-semibold text-muted-foreground shadow-sm transition-colors hover:text-foreground hover:border-primary/40";

      const label = document.createElement("span");
      label.dataset.toolsGlobalLabel = "true";
      label.textContent = "Tool activity";

      const state = document.createElement("span");
      state.dataset.toolsGlobalState = "true";

      button.appendChild(label);
      button.appendChild(state);
      root.appendChild(button);

      const historyButton = document.createElement("button");
      historyButton.type = "button";
      historyButton.dataset.toolsHistoryToggle = "true";
      historyButton.className =
        "inline-flex items-center gap-2 rounded-full border border-border/40 bg-background/70 px-3 py-1 text-[11px] font-semibold text-muted-foreground shadow-sm transition-colors hover:text-foreground hover:border-primary/40";
      historyButton.textContent = "Activity";
      root.appendChild(historyButton);

      this.elements.messagesInner.prepend(root);
      button.addEventListener("click", () => {
        this.setGlobalToolsVisibility(!this.globalToolsVisible);
      });
      historyButton.addEventListener("click", () => this.openToolHistoryModal());
    }
    this.updateGlobalToolsToggle(root);
  }

  updateGlobalToolsToggle(root = null) {
    const container = root || (this.elements.messagesInner ? this.elements.messagesInner.querySelector("[data-tools-global]") : null);
    if (!container) return;
    const button = container.querySelector("[data-tools-global-toggle]");
    const state = container.querySelector("[data-tools-global-state]");
    if (state) {
      state.textContent = this.globalToolsVisible ? "On" : "Off";
    }
    if (button) {
      button.setAttribute("aria-pressed", this.globalToolsVisible ? "true" : "false");
      button.classList.toggle("text-foreground", this.globalToolsVisible);
    }
  }

  ensureToolHistoryModal() {
    if (this.toolHistoryModal && this.toolHistoryModalBody) return;
    const root = document.createElement("div");
    root.dataset.toolHistoryModal = "true";
    root.className = "fixed inset-0 z-50 hidden";
    root.innerHTML = `
      <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" data-tool-history-overlay></div>
      <div class="relative mx-auto mt-16 w-[min(44rem,calc(100%-2rem))] rounded-xl border border-border/60 bg-card p-5 shadow-lg">
        <div class="flex items-start justify-between gap-3">
          <div class="space-y-0.5">
            <div class="text-sm font-semibold text-foreground">Activity log</div>
            <div class="text-xs text-muted-foreground">Approvals and tool calls in this conversation.</div>
          </div>
          <button type="button" class="h-8 w-8 inline-flex items-center justify-center rounded-md border border-border/60 hover:bg-accent/50" data-tool-history-close aria-label="Close">
            <svg class="h-4 w-4 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div class="mt-4 max-h-[65vh] overflow-auto pr-1 space-y-4" data-tool-history-body></div>
      </div>
    `;
    document.body.appendChild(root);
    const overlay = root.querySelector("[data-tool-history-overlay]");
    const closeBtn = root.querySelector("[data-tool-history-close]");
    const body = root.querySelector("[data-tool-history-body]");
    this.toolHistoryModal = root;
    this.toolHistoryModalBody = body;

    const close = () => this.closeToolHistoryModal();
    if (overlay) overlay.addEventListener("click", close);
    if (closeBtn) closeBtn.addEventListener("click", close);
    if (!root.dataset.escBound) {
      root.dataset.escBound = "true";
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          this.closeToolHistoryModal();
        }
      });
    }
  }

  closeToolHistoryModal() {
    if (!this.toolHistoryModal) return;
    this.toolHistoryModal.classList.add("hidden");
  }

  async openToolHistoryModal() {
    if (!this.endpoints.toolHistory) {
      this.showToast("Unavailable", "Activity endpoint is not configured.", true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast("Unavailable", "Session token missing.", true);
      return;
    }
    this.ensureToolHistoryModal();
    if (!this.toolHistoryModal || !this.toolHistoryModalBody) return;
    this.toolHistoryModal.classList.remove("hidden");
    this.toolHistoryModalBody.innerHTML =
      '<div class="rounded-lg border border-border/60 bg-muted/30 p-4 text-sm text-muted-foreground">Loading activity…</div>';
    try {
      const response = await fetch(this.endpoints.toolHistory, {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify({ session_token: this.sessionToken, limit: 150 }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : "Unable to load activity.";
        throw new Error(message);
      }
      this.renderToolHistory(payload && payload.history ? payload.history : null);
    } catch (error) {
      console.warn("Failed to load tool history", error);
      this.toolHistoryModalBody.innerHTML =
        '<div class="rounded-lg border border-border/60 bg-muted/30 p-4 text-sm text-muted-foreground">Unable to load activity.</div>';
    }
  }

  renderToolHistory(history) {
    if (!this.toolHistoryModalBody) return;
    const approvals = history && Array.isArray(history.approvals) ? history.approvals : [];
    const toolEvents = history && Array.isArray(history.toolEvents) ? history.toolEvents : [];
    const wrap = document.createElement("div");
    wrap.className = "space-y-5";

    const buildSection = (title) => {
      const section = document.createElement("div");
      const header = document.createElement("div");
      header.className = "text-xs font-semibold uppercase tracking-wide text-muted-foreground";
      header.textContent = title;
      section.appendChild(header);
      const list = document.createElement("div");
      list.className = "mt-2 space-y-2";
      section.appendChild(list);
      return { section, list };
    };

    if (!approvals.length && !toolEvents.length) {
      wrap.innerHTML =
        '<div class="rounded-lg border border-border/60 bg-muted/30 p-4 text-sm text-muted-foreground">No activity yet.</div>';
      this.toolHistoryModalBody.innerHTML = "";
      this.toolHistoryModalBody.appendChild(wrap);
      return;
    }

    if (approvals.length) {
      const { section, list } = buildSection("Approvals");
      approvals.slice(0, 80).forEach((item) => {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between gap-3 rounded-lg border border-border/50 bg-background/60 p-3";
        const left = document.createElement("div");
        left.className = "min-w-0";
        const name = document.createElement("div");
        name.className = "text-sm font-medium text-foreground truncate";
        const conn = (item.connection_name || "").toString().trim();
        const tool = (item.remote_tool_name || item.tool_name || "").toString().trim();
        name.textContent = conn ? `${conn} • ${tool}` : tool || "Tool approval";
        const meta = document.createElement("div");
        meta.className = "text-xs text-muted-foreground";
        const requestedAt = item.requested_at || item.requestedAt || "";
        const label = requestedAt ? this.formatRelativeTime(new Date(requestedAt)) : "";
        meta.textContent = label ? `Requested ${label}` : "";
        left.appendChild(name);
        if (meta.textContent) left.appendChild(meta);

        const right = document.createElement("div");
        right.className = "flex items-center gap-2 flex-shrink-0";
        const status = (item.status || "").toString().trim().toLowerCase();
        const pill = document.createElement("span");
        const mapped = this.mapToolStatus(status);
        pill.className = `${mapped.className} inline-flex items-center justify-center text-[10px] font-semibold rounded-full px-2 py-0.5 w-[88px] text-center whitespace-nowrap`;
        pill.textContent = mapped.label;
        right.appendChild(pill);

        row.appendChild(left);
        row.appendChild(right);
        list.appendChild(row);
      });
      wrap.appendChild(section);
    }

    if (toolEvents.length) {
      const { section, list } = buildSection("Tool calls");
      toolEvents.slice(-120).forEach((item) => {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between gap-3 rounded-lg border border-border/50 bg-background/60 p-3";
        const left = document.createElement("div");
        left.className = "min-w-0";
        const name = document.createElement("div");
        name.className = "text-sm font-medium text-foreground truncate";
        const conn = (item.connection_name || "").toString().trim();
        const tool = (item.remote_tool_name || item.tool_name || "").toString().trim();
        const phase = (item.phase || "").toString().trim().toLowerCase();
        name.textContent = conn ? `${conn} • ${tool}` : tool || "Tool call";
        const meta = document.createElement("div");
        meta.className = "text-xs text-muted-foreground";
        meta.textContent = phase ? phase.replace(/_/g, " ") : "";
        left.appendChild(name);
        if (meta.textContent) left.appendChild(meta);

        const right = document.createElement("div");
        right.className = "flex items-center gap-2 flex-shrink-0";
        const status = (item.status || "").toString().trim().toLowerCase();
        const pill = document.createElement("span");
        const mapped = this.mapToolStatus(status);
        pill.className = `${mapped.className} inline-flex items-center justify-center text-[10px] font-semibold rounded-full px-2 py-0.5 w-[88px] text-center whitespace-nowrap`;
        pill.textContent = mapped.label;
        right.appendChild(pill);
        list.appendChild(row);
        row.appendChild(left);
        row.appendChild(right);
      });
      wrap.appendChild(section);
    }

    this.toolHistoryModalBody.innerHTML = "";
    this.toolHistoryModalBody.appendChild(wrap);
  }

  updateAllToolsVisibility() {
    if (!this.elements.messages) return;
    const wrappers = this.elements.messages.querySelectorAll("[data-message-id]");
    wrappers.forEach((wrapper) => {
      if (wrapper.querySelector("[data-message-tools]")) {
        this.updateMessageToolsToggle(wrapper);
      }
    });
  }

  getMessageToolsVisibility(wrapper) {
    if (!this.globalToolsVisible) {
      return this.wrapperHasPendingToolApprovals(wrapper);
    }
    if (!wrapper) return true;
    const override = wrapper.dataset.toolsVisible;
    if (override === "true") return true;
    if (override === "false") return false;
    return true;
  }

  wrapperHasPendingToolApprovals(wrapper) {
    if (!wrapper) return false;
    const toolsContainer = wrapper.querySelector("[data-message-tools]");
    if (!toolsContainer) return false;
    const cards = toolsContainer.querySelectorAll("[data-tool-card]");
    for (const card of cards) {
      const approvalStatus = (card.dataset.approvalStatus || "").toString().trim().toLowerCase();
      if (approvalStatus === "pending" || approvalStatus === "pending_approval") {
        return true;
      }
    }
    return false;
  }

  toggleMessageTools(wrapper) {
    if (!wrapper) return;
    const next = !this.getMessageToolsVisibility(wrapper);
    wrapper.dataset.toolsVisible = next ? "true" : "false";
    this.updateMessageToolsToggle(wrapper);
  }

  updateMessageToolsToggle(wrapper) {
    if (!wrapper) return;
    const toolsContainer = wrapper.querySelector("[data-message-tools]");
    const toggleRow = wrapper.querySelector("[data-message-tools-toggle]");
    if (!toolsContainer || !toggleRow) return;
    const cards = toolsContainer.querySelectorAll("[data-tool-card]");
    const count = cards.length;
    if (!count) {
      toggleRow.classList.add("hidden");
      toolsContainer.classList.add("hidden");
      return;
    }
    toggleRow.classList.remove("hidden");
    const button = toggleRow.querySelector("[data-tools-toggle-button]");
    const countEl = toggleRow.querySelector("[data-tools-toggle-count]");
    const stateEl = toggleRow.querySelector("[data-tools-toggle-state]");
    if (countEl) countEl.textContent = `(${count})`;

    const visible = this.getMessageToolsVisibility(wrapper);
    const globalOff = !this.globalToolsVisible;
    if (stateEl) {
      if (globalOff && visible) {
        stateEl.textContent = "Approval required";
      } else {
        stateEl.textContent = globalOff ? "Hidden" : visible ? "Hide" : "Show";
      }
    }
    if (button) {
      button.disabled = globalOff;
      button.setAttribute("aria-expanded", visible ? "true" : "false");
      button.classList.toggle("opacity-60", globalOff);
      button.classList.toggle("cursor-not-allowed", globalOff);
    }
    toolsContainer.classList.toggle("hidden", !visible);
  }

  ensureToolActivityContainer(wrapper) {
    if (!wrapper) return null;
    const body = wrapper.querySelector("[data-message-body]");
    if (!body) return null;

    let container = wrapper.querySelector("[data-message-tools]");
    if (!container) {
      container = document.createElement("div");
      container.dataset.messageTools = "true";
      const finalBody = body.querySelector("[data-message-final-body]");
      if (finalBody) {
        body.insertBefore(container, finalBody);
      } else {
        body.insertBefore(container, body.firstChild);
      }
    }
    container.className = "mt-2 mb-3 space-y-2 w-full flex flex-col items-start";

    let toggleRow = wrapper.querySelector("[data-message-tools-toggle]");
    if (!toggleRow) {
      toggleRow = document.createElement("div");
      toggleRow.dataset.messageToolsToggle = "true";
      toggleRow.className = "text-[11px] text-muted-foreground hidden";

      const button = document.createElement("button");
      button.type = "button";
      button.dataset.toolsToggleButton = "true";
      button.className =
        "inline-flex items-center gap-2 rounded-full border border-border/40 bg-background/70 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-colors hover:text-foreground hover:border-primary/40";

      const label = document.createElement("span");
      label.textContent = "Tools";

      const count = document.createElement("span");
      count.dataset.toolsToggleCount = "true";

      const state = document.createElement("span");
      state.dataset.toolsToggleState = "true";

      button.appendChild(label);
      button.appendChild(count);
      button.appendChild(state);
      toggleRow.appendChild(button);

      container.parentElement.insertBefore(toggleRow, container);
      button.addEventListener("click", () => this.toggleMessageTools(wrapper));
    }

    this.ensureGlobalToolsToggle();
    this.updateMessageToolsToggle(wrapper);
    return container;
  }

  buildToolEventCard(payload) {
    const eventId = (payload.event_id || payload.eventId || "").toString().trim();
    if (!eventId) return null;

    const details = document.createElement("details");
    details.dataset.toolCard = "true";
    details.dataset.toolEventId = eventId;
    details.className = "rounded-xl border border-border/50 bg-muted/20 px-3 py-2 w-fit max-w-full inline-flex flex-col overflow-hidden";
    details.style.maxWidth = "min(520px, 100%)";

    const summary = document.createElement("summary");
    summary.className = "cursor-pointer select-none inline-flex flex-col items-stretch max-w-full min-w-0";

    const header = document.createElement("div");
    header.className = "flex items-start justify-between gap-3 max-w-full min-w-0";

    const left = document.createElement("div");
    left.className = "flex items-start gap-2 min-w-0 max-w-full";

    const iconWrap = document.createElement("div");
    iconWrap.className =
      "mt-0.5 h-7 w-7 rounded-lg bg-background/60 border border-border/40 flex items-center justify-center text-foreground/80 flex-shrink-0";
    iconWrap.innerHTML = `
      <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22v-5" />
        <path d="M9 7V2" />
        <path d="M15 7V2" />
        <path d="M8 22h8" />
        <path d="M12 17a5 5 0 0 0 5-5V9H7v3a5 5 0 0 0 5 5Z" />
      </svg>
    `;

    const textWrap = document.createElement("div");
    textWrap.className = "min-w-0 max-w-full";

    const titleRow = document.createElement("div");
    titleRow.className = "flex items-center gap-2 min-w-0";

    const title = document.createElement("div");
    title.dataset.toolTitle = "true";
    title.className = "text-xs font-medium text-foreground truncate";

    const kind = document.createElement("span");
    kind.dataset.toolKind = "true";
    kind.className =
      "hidden text-[10px] font-semibold uppercase tracking-wide rounded-md px-1.5 py-0.5 bg-primary/10 text-primary";

    titleRow.appendChild(title);
    titleRow.appendChild(kind);

    const subtitle = document.createElement("div");
    subtitle.dataset.toolSubtitle = "true";
    subtitle.className = "mt-0.5 text-[11px] text-muted-foreground truncate";

    textWrap.appendChild(titleRow);
    textWrap.appendChild(subtitle);

    left.appendChild(iconWrap);
    left.appendChild(textWrap);

    const right = document.createElement("div");
    right.className = "flex items-center gap-2 flex-shrink-0 pt-0.5";

    const statusPill = document.createElement("span");
    statusPill.dataset.toolStatus = "true";
    statusPill.className =
      "inline-flex items-center justify-center text-[10px] font-semibold rounded-full px-2 py-0.5 w-[88px] text-center whitespace-nowrap";

    const duration = document.createElement("span");
    duration.dataset.toolDuration = "true";
    duration.className = "hidden text-[10px] text-muted-foreground";

    const chevron = document.createElement("div");
    chevron.dataset.toolChevron = "true";
    chevron.className = "text-muted-foreground/70 transition-transform duration-200";
    chevron.innerHTML = `
      <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    `;

    right.appendChild(statusPill);
    right.appendChild(duration);
    right.appendChild(chevron);

    header.appendChild(left);
    header.appendChild(right);

    const progress = document.createElement("div");
    progress.dataset.toolProgress = "true";
    progress.className = "mt-2 h-1 w-full rounded-full bg-muted/40 overflow-hidden hidden";
    const progressBar = document.createElement("div");
    progressBar.className = "h-full w-full skeleton-loader";
    progress.appendChild(progressBar);

    summary.appendChild(header);
    summary.appendChild(progress);

    const body = document.createElement("div");
    body.dataset.toolBody = "true";
    body.className = "mt-3 space-y-3 max-w-full min-w-0";

    const approval = document.createElement("div");
    approval.dataset.toolApproval = "true";
    approval.className =
      "hidden rounded-lg border border-border/40 bg-background/60 p-3 text-[11px] text-muted-foreground max-w-full min-w-0";

    const approvalTitle = document.createElement("div");
    approvalTitle.dataset.toolApprovalTitle = "true";
    approvalTitle.className = "text-[11px] font-semibold text-foreground";
    approvalTitle.textContent = "Approval required";

    const approvalMeta = document.createElement("div");
    approvalMeta.dataset.toolApprovalMeta = "true";
    approvalMeta.className = "mt-1 text-[11px] text-muted-foreground";

    const approvalRemember = document.createElement("label");
    approvalRemember.dataset.toolApprovalRememberWrap = "true";
    approvalRemember.className =
      "mt-2 inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground";

    const rememberCheckbox = document.createElement("input");
    rememberCheckbox.type = "checkbox";
    rememberCheckbox.dataset.toolApprovalRemember = "true";
    rememberCheckbox.className = "h-3.5 w-3.5 rounded border border-border/60 bg-background/70";

    const rememberText = document.createElement("span");
    rememberText.textContent = "Always allow this tool";

    approvalRemember.appendChild(rememberCheckbox);
    approvalRemember.appendChild(rememberText);

    const approvalActions = document.createElement("div");
    approvalActions.dataset.toolApprovalActions = "true";
    approvalActions.className = "mt-2 flex items-center gap-2";

    const approveButton = document.createElement("button");
    approveButton.type = "button";
    approveButton.dataset.toolApprovalAction = "approve";
    approveButton.className =
      "inline-flex items-center justify-center rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-600 transition-colors hover:bg-emerald-500/20";
    approveButton.textContent = "Approve";

    const denyButton = document.createElement("button");
    denyButton.type = "button";
    denyButton.dataset.toolApprovalAction = "deny";
    denyButton.className =
      "inline-flex items-center justify-center rounded-full border border-rose-500/30 bg-rose-500/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-rose-500 transition-colors hover:bg-rose-500/20";
    denyButton.textContent = "Deny";

    approvalActions.appendChild(approveButton);
    approvalActions.appendChild(denyButton);
    approval.appendChild(approvalTitle);
    approval.appendChild(approvalMeta);
    approval.appendChild(approvalRemember);
    approval.appendChild(approvalActions);

    const tabs = document.createElement("div");
    tabs.dataset.toolTabs = "true";
    tabs.className = "inline-flex items-center gap-1 rounded-full border border-border/40 bg-muted/40 p-1 text-[11px]";

    const buildTab = (label, value) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.dataset.toolTab = value;
      tab.dataset.toolTabButton = "true";
      tab.className = "px-2.5 py-1 rounded-full text-[11px] font-semibold text-muted-foreground transition-colors";
      tab.textContent = label;
      return tab;
    };

    tabs.appendChild(buildTab("Input", "input"));
    tabs.appendChild(buildTab("Output", "output"));

    const panels = document.createElement("div");
    panels.dataset.toolPanels = "true";
    panels.className = "max-w-full min-w-0";

    const buildPanel = (panelType) => {
      const panel = document.createElement("div");
      panel.dataset.toolPanel = panelType;
      panel.className = "space-y-2 max-w-full min-w-0";

      const preview = document.createElement("div");
      preview.dataset.toolPreview = panelType;
      preview.className = "space-y-1.5 max-w-full min-w-0";

      const controls = document.createElement("div");
      controls.className = "flex items-center gap-3 text-[10px] text-muted-foreground uppercase tracking-wide";

      const rawToggle = document.createElement("button");
      rawToggle.type = "button";
      rawToggle.dataset.toolRawToggle = "true";
      rawToggle.dataset.toolPanel = panelType;
      rawToggle.className = "transition-colors hover:text-foreground";
      rawToggle.textContent = "View raw";

      const copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.dataset.toolRawCopy = "true";
      copyButton.dataset.toolPanel = panelType;
      copyButton.className = "transition-colors hover:text-foreground";
      copyButton.textContent = "Copy";

      controls.appendChild(rawToggle);
      controls.appendChild(copyButton);

      const rawWrap = document.createElement("div");
      rawWrap.dataset.toolRawWrap = panelType;
      rawWrap.className = "hidden max-w-full min-w-0";

      const pre = document.createElement("pre");
      pre.dataset.toolRaw = panelType;
      pre.className =
        "max-w-full overflow-x-auto whitespace-pre-wrap break-all text-[11px] leading-relaxed rounded-lg border border-border/40 bg-background/50 p-3";
      rawWrap.appendChild(pre);

      panel.appendChild(preview);
      panel.appendChild(controls);
      panel.appendChild(rawWrap);
      return panel;
    };

    panels.appendChild(buildPanel("input"));
    panels.appendChild(buildPanel("output"));

    body.appendChild(approval);
    body.appendChild(tabs);
    body.appendChild(panels);

    details.dataset.toolTab = "input";
    details.appendChild(summary);
    details.appendChild(body);
    this.attachToolCardEvents(details);
    this.setToolCardTab(details, "input");
    return details;
  }

  updateToolEventCard(card, payload) {
    if (!card || !payload) return;
    const phase = (payload.phase || "").toString().trim().toLowerCase();
    const statusRaw = (payload.status || "").toString().trim().toLowerCase();
    const kindRaw = (payload.kind || "").toString().trim().toLowerCase();
    const remote = payload.remote && typeof payload.remote === "object" ? payload.remote : null;

    const connectionName = remote && remote.connection_name ? remote.connection_name.toString() : "";
    const remoteTool = remote && remote.remote_tool ? remote.remote_tool.toString() : "";
    const toolNameFallback = (payload.tool_name || payload.toolName || "").toString().trim();
    const titleEl = card.querySelector("[data-tool-title]");
    const subtitleEl = card.querySelector("[data-tool-subtitle]");
    const existingTitle = titleEl ? titleEl.textContent : "";
    const existingSubtitle = subtitleEl ? subtitleEl.textContent : "";
    const displayTool = remoteTool || toolNameFallback || "";

    const titleText = connectionName || existingTitle || "External tool";
    const subtitleText = displayTool ? `Calling ${displayTool}` : existingSubtitle || "Calling tool";

    if (titleEl) titleEl.textContent = titleText;
    if (subtitleEl) subtitleEl.textContent = subtitleText;

    const kindEl = card.querySelector("[data-tool-kind]");
    if (kindEl) {
      if (kindRaw) {
        const showKind = kindRaw.includes("mcp");
        kindEl.classList.toggle("hidden", !showKind);
        if (showKind) kindEl.textContent = "MCP";
      }
    }

    const approvalData = payload.approval && typeof payload.approval === "object" ? payload.approval : null;
    let approvalStatus = "";
    let approvalId =
      (payload.approval_id || payload.approvalId || (approvalData && approvalData.id) || card.dataset.approvalId || "")
        .toString()
        .trim();
    if (approvalId) {
      card.dataset.approvalId = approvalId;
    }
    if (approvalData && approvalData.status) {
      approvalStatus = approvalData.status.toString().trim().toLowerCase();
    } else if (payload.approval_status || payload.approvalStatus) {
      approvalStatus = (payload.approval_status || payload.approvalStatus || "").toString().trim().toLowerCase();
    } else if (card.dataset.approvalStatus) {
      approvalStatus = card.dataset.approvalStatus.toString().trim().toLowerCase();
    }
    if (approvalStatus) {
      card.dataset.approvalStatus = approvalStatus;
    }

    const progressEl = card.querySelector("[data-tool-progress]");
    let effectiveStatus = statusRaw;
    if (!effectiveStatus) {
      if (approvalStatus === "pending") {
        effectiveStatus = "pending_approval";
      } else if (approvalStatus) {
        effectiveStatus = approvalStatus;
      }
    }
    const isRunning = effectiveStatus === "running" || phase === "started";
    if (progressEl) progressEl.classList.toggle("hidden", !isRunning);

    const statusEl = card.querySelector("[data-tool-status]");
    if (statusEl) {
      const mapped = this.mapToolStatus(effectiveStatus || (isRunning ? "running" : "ok"));
      statusEl.textContent = mapped.label;
      statusEl.className = `${mapped.className} inline-flex items-center justify-center text-[10px] font-semibold rounded-full px-2 py-0.5 w-[88px] text-center whitespace-nowrap`;
    }

    const durationEl = card.querySelector("[data-tool-duration]");
    if (durationEl) {
      const dur = payload.duration_ms || payload.durationMs;
      const durText = Number.isFinite(Number(dur)) && Number(dur) > 0 ? this.formatDurationMs(Number(dur)) : "";
      durationEl.textContent = durText;
      durationEl.classList.toggle("hidden", !durText);
    }

    const inputProvided = Object.prototype.hasOwnProperty.call(payload, "input");
    const outputProvided = Object.prototype.hasOwnProperty.call(payload, "output");

    if (inputProvided) {
      card._toolRawInput = payload.input;
    }
    if (outputProvided) {
      card._toolRawOutput = payload.output;
    }

    const hasStoredInput = typeof card._toolRawInput !== "undefined";
    const hasStoredOutput = typeof card._toolRawOutput !== "undefined";

    const inputPayload = inputProvided ? payload.input : card._toolRawInput;
    const outputPayload = outputProvided ? payload.output : card._toolRawOutput;

    this.updateToolPanel(card, "input", inputPayload, { available: inputProvided || hasStoredInput });
    this.updateToolPanel(card, "output", outputPayload, { available: outputProvided || hasStoredOutput, pending: isRunning });
    this.updateToolApprovalPanel(card, payload);

    if (!card.dataset.toolTabUser) {
      const preferredTab = (outputProvided || hasStoredOutput) && !isRunning ? "output" : "input";
      this.setToolCardTab(card, preferredTab);
    }
  }

  updateToolApprovalPanel(card, payload) {
    if (!card) return;
    const approvalWrap = card.querySelector("[data-tool-approval]");
    if (!approvalWrap) return;

    const approval = payload && typeof payload.approval === "object" ? payload.approval : null;
    const approvalId =
      (payload && (payload.approval_id || payload.approvalId)) ||
      (approval && approval.id) ||
      card.dataset.approvalId ||
      "";
    const statusRaw =
      (approval && approval.status) ||
      (payload && (payload.approval_status || payload.approvalStatus)) ||
      card.dataset.approvalStatus ||
      "";
    const status = statusRaw.toString().trim().toLowerCase();
    if (approvalId) {
      card.dataset.approvalId = approvalId.toString().trim();
    }
    if (status) {
      card.dataset.approvalStatus = status;
    }

    const titleEl = approvalWrap.querySelector("[data-tool-approval-title]");
    const metaEl = approvalWrap.querySelector("[data-tool-approval-meta]");
    const rememberWrap = approvalWrap.querySelector("[data-tool-approval-remember-wrap]");
    const rememberCheckbox = approvalWrap.querySelector('[data-tool-approval-remember="true"]');
    const actionsEl = approvalWrap.querySelector("[data-tool-approval-actions]");
    const approveBtn = approvalWrap.querySelector('[data-tool-approval-action="approve"]');
    const denyBtn = approvalWrap.querySelector('[data-tool-approval-action="deny"]');

    if (!approvalId) {
      approvalWrap.classList.add("hidden");
      return;
    }

    approvalWrap.classList.remove("hidden");

    const isPending = status === "pending" || status === "pending_approval";
    const isApproved = status === "approved";
    const isDenied = status === "denied";
    const isExpired = status === "expired";

    if (titleEl) {
      if (isPending) {
        titleEl.textContent = "Approval required";
      } else if (isApproved) {
        titleEl.textContent = "Approval granted";
      } else if (isDenied) {
        titleEl.textContent = "Approval denied";
      } else if (isExpired) {
        titleEl.textContent = "Approval expired";
      } else {
        titleEl.textContent = "Approval update";
      }
    }

    if (metaEl) {
      const metaParts = [];
      const approvalMeta = approval && typeof approval.metadata === "object" ? approval.metadata : null;
      const operationType =
        (approval && (approval.operation_type || approval.operationType)) ||
        (approvalMeta && (approvalMeta.operation_type || approvalMeta.operationType)) ||
        "";
      const reason = (approval && approval.reason) || (approvalMeta && approvalMeta.reason) || "";
      if (operationType) {
        metaParts.push(`${operationType.toString().toUpperCase()} operation`);
      }
      if (reason) {
        metaParts.push(reason.toString());
      }
      if (!metaParts.length && isPending) {
        metaParts.push("Awaiting approval before executing this tool.");
      }
      metaEl.textContent = metaParts.join(" • ");
    }

    if (actionsEl) {
      actionsEl.classList.toggle("hidden", !isPending);
    }
    if (rememberWrap) {
      rememberWrap.classList.toggle("hidden", !isPending);
    }
    if (rememberCheckbox && !isPending) {
      rememberCheckbox.checked = false;
    }
    if (approveBtn) {
      approveBtn.disabled = !isPending;
      approveBtn.classList.toggle("opacity-50", !isPending);
      approveBtn.classList.toggle("cursor-not-allowed", !isPending);
    }
    if (denyBtn) {
      denyBtn.disabled = !isPending;
      denyBtn.classList.toggle("opacity-50", !isPending);
      denyBtn.classList.toggle("cursor-not-allowed", !isPending);
    }
  }

  async submitToolApproval(approvalId, decision, card) {
    if (!approvalId || !decision) return;
    if (!this.endpoints.toolApproval) {
      this.showToast("Approval unavailable", "Approval endpoint is not configured.", true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast("Approval unavailable", "Session token missing.", true);
      return;
    }
    if (card && card.dataset.toolApprovalBusy === "true") {
      return;
    }
    if (card) {
      card.dataset.toolApprovalBusy = "true";
    }
    const action = decision.toString().trim().toLowerCase();
    let remember = false;
    if (action === "approve" && card) {
      const checkbox = card.querySelector('[data-tool-approval-remember="true"]');
      remember = Boolean(checkbox && checkbox.checked);
    }
    const buttons = card ? card.querySelectorAll("[data-tool-approval-action]") : [];
    buttons.forEach((btn) => {
      btn.disabled = true;
      btn.classList.add("opacity-60", "cursor-not-allowed");
    });

    try {
      const response = await fetch(this.endpoints.toolApproval, {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify({
          session_token: this.sessionToken,
          approval_id: approvalId,
          decision: action,
          remember,
        }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : "Approval failed.";
        throw new Error(message);
      }
      if (remember && payload && payload.preferenceSaved === false) {
        this.showToast("Preference not saved", "Always allow requires an authenticated session.", true);
      }
      const approval = payload && payload.approval ? payload.approval : null;
      const status = approval && approval.status ? approval.status : action === "approve" ? "approved" : "denied";
      if (card) {
        this.updateToolEventCard(card, {
          event_id: card.dataset.toolEventId || "",
          phase: "approval_resolved",
          status,
          approval: { id: approvalId, status, ...(approval || {}) },
        });
      }
    } catch (error) {
      console.warn("Tool approval failed", error);
      this.showToast("Approval failed", error.message || "Please try again.", true);
    } finally {
      if (card) {
        card.dataset.toolApprovalBusy = "false";
      }
      if (card) {
        const pendingStatus = card.dataset.approvalStatus === "pending" || card.dataset.approvalStatus === "pending_approval";
        const btns = card.querySelectorAll("[data-tool-approval-action]");
        btns.forEach((btn) => {
          btn.disabled = !pendingStatus;
          btn.classList.toggle("opacity-60", !pendingStatus);
          btn.classList.toggle("cursor-not-allowed", !pendingStatus);
        });
      }
    }
  }

  attachToolCardEvents(card) {
    if (!card || card.dataset.toolEventsBound === "true") return;
    card.dataset.toolEventsBound = "true";

    const tabButtons = card.querySelectorAll("[data-tool-tab-button]");
    tabButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const tab = button.dataset.toolTab;
        this.setToolCardTab(card, tab, true);
      });
    });

    const rawToggles = card.querySelectorAll("[data-tool-raw-toggle]");
    rawToggles.forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const panelType = button.dataset.toolPanel;
        this.toggleToolRawPanel(card, panelType);
      });
    });

    const rawCopies = card.querySelectorAll("[data-tool-raw-copy]");
    rawCopies.forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const panelType = button.dataset.toolPanel;
        await this.copyToolRawPanel(card, panelType);
      });
    });

    const approvalButtons = card.querySelectorAll("[data-tool-approval-action]");
    approvalButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const decision = button.dataset.toolApprovalAction;
        const approvalId = card.dataset.approvalId;
        if (!approvalId) {
          this.showToast("Approval unavailable", "Approval request is missing an identifier.", true);
          return;
        }
        this.submitToolApproval(approvalId, decision, card);
      });
    });
  }

  setToolCardTab(card, tab, userInitiated = false) {
    if (!card) return;
    const normalized = tab === "output" ? "output" : "input";
    card.dataset.toolTab = normalized;
    if (userInitiated) {
      card.dataset.toolTabUser = "true";
    }
    const tabButtons = card.querySelectorAll("[data-tool-tab-button]");
    tabButtons.forEach((button) => {
      const active = button.dataset.toolTab === normalized;
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  toggleToolRawPanel(card, panelType) {
    if (!card || !panelType) return;
    const panel = card.querySelector(`[data-tool-panel="${panelType}"]`);
    if (!panel) return;
    const rawWrap = panel.querySelector(`[data-tool-raw-wrap="${panelType}"]`);
    const toggleBtn = panel.querySelector(`[data-tool-raw-toggle][data-tool-panel="${panelType}"]`);
    if (!rawWrap) return;
    const isOpen = !rawWrap.classList.contains("hidden");
    if (!isOpen) {
      this.fillToolRawPanel(card, panelType, rawWrap);
    }
    rawWrap.classList.toggle("hidden", isOpen);
    if (toggleBtn) toggleBtn.textContent = isOpen ? "View raw" : "Hide raw";
  }

  fillToolRawPanel(card, panelType, rawWrap) {
    if (!card || !panelType || !rawWrap) return;
    const pre = rawWrap.querySelector(`[data-tool-raw="${panelType}"]`);
    if (!pre) return;
    const rawPayload = panelType === "input" ? card._toolRawInput : card._toolRawOutput;
    const rawText = rawPayload != null ? this.safeJsonStringify(rawPayload) : "";
    pre.textContent = rawText || "—";
  }

  async copyToolRawPanel(card, panelType) {
    if (!card || !panelType) return;
    const panel = card.querySelector(`[data-tool-panel="${panelType}"]`);
    if (!panel) return;
    const rawWrap = panel.querySelector(`[data-tool-raw-wrap="${panelType}"]`);
    const pre = rawWrap ? rawWrap.querySelector(`[data-tool-raw="${panelType}"]`) : null;
    let rawText = pre ? pre.textContent.trim() : "";
    if (!rawText) {
      const rawPayload = panelType === "input" ? card._toolRawInput : card._toolRawOutput;
      rawText = rawPayload != null ? this.safeJsonStringify(rawPayload) : "";
    }
    if (!rawText) {
      this.showToast("Nothing to copy", "No raw payload available.");
      return;
    }
    try {
      await navigator.clipboard.writeText(rawText);
      this.showToast("Copied", "Raw payload copied to clipboard.");
    } catch (error) {
      console.warn("Clipboard write failed", error);
      this.showToast("Copy failed", "Clipboard access was denied.", true);
    }
  }

  updateToolPanel(card, panelType, payload, options = {}) {
    if (!card || !panelType) return;
    const panel = card.querySelector(`[data-tool-panel="${panelType}"]`);
    if (!panel) return;
    const preview = panel.querySelector(`[data-tool-preview="${panelType}"]`);
    const rawWrap = panel.querySelector(`[data-tool-raw-wrap="${panelType}"]`);
    const rawToggle = panel.querySelector(`[data-tool-raw-toggle][data-tool-panel="${panelType}"]`);
    const rawCopy = panel.querySelector(`[data-tool-raw-copy][data-tool-panel="${panelType}"]`);
    const available = Boolean(options.available);
    const pending = Boolean(options.pending);

    if (!available) {
      if (preview) {
        preview.innerHTML = "";
        const empty = document.createElement("div");
        empty.className = "text-[11px] text-muted-foreground";
        empty.textContent = pending ? "Waiting for response..." : `No ${panelType} payload.`;
        preview.appendChild(empty);
      }
      if (rawWrap) rawWrap.classList.add("hidden");
      if (rawToggle) {
        rawToggle.disabled = true;
        rawToggle.classList.add("opacity-50", "cursor-not-allowed");
        rawToggle.textContent = "View raw";
      }
      if (rawCopy) {
        rawCopy.disabled = true;
        rawCopy.classList.add("opacity-50", "cursor-not-allowed");
      }
      return;
    }

    if (rawToggle) {
      rawToggle.disabled = false;
      rawToggle.classList.remove("opacity-50", "cursor-not-allowed");
    }
    if (rawCopy) {
      rawCopy.disabled = false;
      rawCopy.classList.remove("opacity-50", "cursor-not-allowed");
    }

    if (preview) {
      const normalized = this.normalizeToolPayload(payload);
      this.renderToolPreview(preview, normalized);
    }

    if (rawWrap && !rawWrap.classList.contains("hidden")) {
      this.fillToolRawPanel(card, panelType, rawWrap);
      if (rawToggle) rawToggle.textContent = "Hide raw";
    } else if (rawToggle) {
      rawToggle.textContent = "View raw";
    }
  }

  normalizeToolPayload(payload) {
    if (typeof payload !== "string") return payload;
    const trimmed = payload.trim();
    if (!trimmed) return payload;
    const looksJson = (trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"));
    if (!looksJson) return payload;
    try {
      return JSON.parse(trimmed);
    } catch (_err) {
      return payload;
    }
  }

  renderToolPreview(container, payload) {
    if (!container) return;
    container.innerHTML = "";
    if (payload === undefined) {
      const empty = document.createElement("div");
      empty.className = "text-[11px] text-muted-foreground";
      empty.textContent = "Empty payload.";
      container.appendChild(empty);
      return;
    }

    const { entries, remaining, emptyLabel } = this.getToolPreviewEntries(payload);
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "text-[11px] text-muted-foreground";
      empty.textContent = emptyLabel || "Empty payload.";
      container.appendChild(empty);
      return;
    }

    entries.forEach(({ key, value }) => {
      const row = document.createElement("div");
      row.dataset.toolPreviewRow = "true";
      row.className = "flex items-start gap-2";

      const keyEl = document.createElement("div");
      keyEl.dataset.toolPreviewKey = "true";
      keyEl.className = "w-24 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground";
      keyEl.textContent = key;

      const valueEl = document.createElement("div");
      valueEl.dataset.toolPreviewValue = "true";
      valueEl.className = "flex-1 min-w-0 text-[11px] text-foreground/80 break-words";
      valueEl.textContent = this.formatToolPreviewValue(value);

      row.appendChild(keyEl);
      row.appendChild(valueEl);
      container.appendChild(row);
    });

    if (remaining > 0) {
      const more = document.createElement("div");
      more.className = "text-[10px] text-muted-foreground italic";
      more.textContent = `+${remaining} more`;
      container.appendChild(more);
    }
  }

  getToolPreviewEntries(payload, limit = 6) {
    if (payload === null) {
      return { entries: [{ key: "value", value: "null" }], remaining: 0 };
    }
    const type = typeof payload;
    if (type === "string" || type === "number" || type === "boolean") {
      return { entries: [{ key: "value", value: payload }], remaining: 0 };
    }
    if (Array.isArray(payload)) {
      return { entries: [{ key: "items", value: payload }], remaining: 0 };
    }
    if (type !== "object") {
      return { entries: [{ key: "value", value: String(payload) }], remaining: 0 };
    }
    const entries = Object.entries(payload || {});
    if (!entries.length) {
      return { entries: [], remaining: 0, emptyLabel: "Empty object." };
    }
    const sliced = entries.slice(0, limit).map(([key, value]) => ({ key, value }));
    return { entries: sliced, remaining: Math.max(0, entries.length - sliced.length) };
  }

  formatToolPreviewValue(value) {
    if (value === null) return "null";
    if (value === undefined) return "undefined";
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return "(empty)";
      if (trimmed.length <= 120) return trimmed;
      return `${trimmed.slice(0, 117).trim()}...`;
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    if (Array.isArray(value)) {
      return `Array(${value.length})`;
    }
    if (typeof value === "object") {
      const keys = Object.keys(value || {});
      if (!keys.length) return "Object{}";
      const preview = keys.slice(0, 3).join(", ");
      const suffix = keys.length > 3 ? ", ..." : "";
      return `Object{${preview}${suffix}}`;
    }
    return String(value);
  }

  mapToolStatus(status) {
    const normalized = (status || "").toString().trim().toLowerCase();
    if (normalized === "pending_approval" || normalized === "pending") {
      return { label: "Pending", className: "bg-amber-500/10 text-amber-700 dark:text-amber-500" };
    }
    if (normalized === "approved") {
      return { label: "Approved", className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-500" };
    }
    if (normalized === "denied") {
      return { label: "Denied", className: "bg-rose-500/10 text-rose-500" };
    }
    if (normalized === "expired") {
      return { label: "Expired", className: "bg-amber-500/10 text-amber-700 dark:text-amber-500" };
    }
    if (normalized === "running" || normalized === "started") {
      return { label: "Running", className: "bg-primary/10 text-primary" };
    }
    if (normalized === "ok" || normalized === "success" || normalized === "succeeded") {
      return { label: "Succeeded", className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-500" };
    }
    if (normalized === "blocked" || normalized === "disabled") {
      return { label: "Blocked", className: "bg-amber-500/10 text-amber-700 dark:text-amber-500" };
    }
    if (normalized === "error" || normalized === "failed" || normalized === "failure") {
      return { label: "Failed", className: "bg-destructive/10 text-destructive" };
    }
    return {
      label: normalized ? this.formatStatus(normalized) : "Done",
      className: "bg-muted text-muted-foreground",
    };
  }

  formatDurationMs(ms) {
    const value = Number(ms);
    if (!Number.isFinite(value) || value <= 0) return "";
    if (value < 1000) return `${Math.round(value)}ms`;
    if (value < 60000) return `${(value / 1000).toFixed(1)}s`;
    const minutes = Math.floor(value / 60000);
    const seconds = Math.round((value % 60000) / 1000);
    return `${minutes}m ${seconds}s`;
  }

  safeJsonStringify(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (_err) {
      try {
        return String(value);
      } catch (_err2) {
        return "(unavailable)";
      }
    }
  }

  clipText(text, limit = 120) {
    const raw = (text || "").toString();
    if (!raw) return "";
    if (raw.length <= limit) return raw;
    return `${raw.slice(0, Math.max(0, limit - 1)).trim()}…`;
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
    container.removeAttribute("data-session-skeleton");

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

    if (message.sender === "ai") {
      const debugRow = document.createElement("div");
      debugRow.dataset.messageDebug = "true";
      debugRow.className = "mt-2 text-xs text-muted-foreground hidden";
      content.appendChild(debugRow);
    }

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

      const toolsEl = document.createElement("div");
      toolsEl.dataset.messageTools = "true";
      toolsEl.className = "mt-2 mb-3 space-y-2";
      this.streamingMessageBodyEl.appendChild(toolsEl);
      this.streamingToolsEl = toolsEl;

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

    const storedToolEvents = Array.isArray(metaPayload.tool_events)
      ? metaPayload.tool_events
      : Array.isArray(metaPayload.toolEvents)
      ? metaPayload.toolEvents
      : null;
    if (storedToolEvents && storedToolEvents.length) {
      this.renderStoredToolEvents(wrapper, storedToolEvents, messageId);
    }

    if (Object.prototype.hasOwnProperty.call(metaPayload, "debug_tools") || Object.prototype.hasOwnProperty.call(metaPayload, "debugTools")) {
      const debugTools = metaPayload.debug_tools || metaPayload.debugTools || null;
      this.updateDebugToolsPanel(wrapper, debugTools);
    }
  }

  renderStoredToolEvents(wrapper, events, messageId) {
    if (!wrapper || !Array.isArray(events) || !events.length) return;
    const toolsContainer = this.ensureToolActivityContainer(wrapper);
    if (!toolsContainer) return;
    const messageKey = wrapper.dataset.messageId || messageId || "streaming";
    events.forEach((rawEvent) => {
      if (!rawEvent || typeof rawEvent !== "object") return;
      const eventId = (rawEvent.event_id || rawEvent.eventId || rawEvent.tool_call_id || rawEvent.toolCallId || "")
        .toString()
        .trim();
      if (!eventId) return;
      const phase = (rawEvent.phase || "").toString().trim().toLowerCase();
      if (!phase) return;
      const payload = { ...rawEvent };
      if (!payload.message_id && messageId) {
        payload.message_id = messageId;
      }
      const cardKey = `${messageKey}:${eventId}`;
      let card = this.toolEventCards.get(cardKey);
      if (!card) {
        card = toolsContainer.querySelector(`[data-tool-event-id="${eventId}"]`);
      }
      if (!card) {
        card = this.buildToolEventCard(payload);
        if (!card) return;
        toolsContainer.appendChild(card);
        this.toolEventCards.set(cardKey, card);
      }
      this.updateToolEventCard(card, payload);
    });
    this.updateMessageToolsToggle(wrapper);
  }

  formatTokenCount(count) {
    const safe = Number.isFinite(count) ? Math.max(0, Math.round(count)) : 0;
    return safe.toLocaleString("en-US");
  }

  getUsagePayload(payload) {
    if (!payload || typeof payload !== "object") return null;
    const usage = payload.usage || payload.llm_usage || null;
    if (!usage || typeof usage !== "object") return null;
    return usage;
  }

  getRoundTokenCountFromUsage(usage) {
    if (!usage || typeof usage !== "object") return null;
    const total = Number(usage.total_tokens);
    if (!Number.isFinite(total)) return null;
    return total;
  }

  calculateChatTokenTotal() {
    const container = this.elements.messagesInner || this.elements.messages || this.container;
    if (!container) return null;
    let total = 0;
    let hasAny = false;
    container.querySelectorAll(".message-row").forEach((row) => {
      const value = Number(row.dataset.roundTokens || NaN);
      if (!Number.isFinite(value)) return;
      total += value;
      hasAny = true;
    });
    return hasAny ? total : null;
  }

  updateTokenTotalDisplays(totalTokens) {
    const formatted = Number.isFinite(totalTokens) ? this.formatTokenCount(totalTokens) : "—";
    const container = this.elements.messagesInner || this.elements.messages || this.container;
    if (!container) return;
    container.querySelectorAll("[data-token-total]").forEach((node) => {
      node.textContent = formatted;
    });
  }

  buildTokenSummarySegment(roundTokens, totalTokens) {
    if (!Number.isFinite(roundTokens) || !Number.isFinite(totalTokens)) {
      return "Tokens unavailable";
    }
    const wrap = document.createElement("span");
    wrap.className = "inline-flex flex-wrap items-center gap-1";
    const roundSpan = document.createElement("span");
    roundSpan.dataset.tokenRound = "true";
    roundSpan.className = "font-semibold text-foreground/80";
    roundSpan.textContent = this.formatTokenCount(roundTokens);
    const totalSpan = document.createElement("span");
    totalSpan.dataset.tokenTotal = "true";
    totalSpan.className = "font-semibold text-foreground/80";
    totalSpan.textContent = this.formatTokenCount(totalTokens);
    wrap.append("Tokens (exact) ", roundSpan, " round / ", totalSpan, " chat");
    return wrap;
  }

  updateDebugToolsPanel(wrapper, debugTools) {
    if (!wrapper) return;
    const debugEl = wrapper.querySelector("[data-message-debug]");
    if (!debugEl) return;

    if (typeof debugTools === "undefined") {
      return;
    }

    const payload = debugTools && typeof debugTools === "object" ? debugTools : {};
    const toolTrace = Array.isArray(payload.tool_trace) ? payload.tool_trace : [];
    const searchHistory = Array.isArray(payload.search_history) ? payload.search_history : [];
    const results = Array.isArray(payload.knowledge_results) ? payload.knowledge_results : [];
    const reads = Array.isArray(payload.knowledge_reads) ? payload.knowledge_reads : [];
    const coverage = Array.isArray(payload.coverage_ledger) ? payload.coverage_ledger : [];
    const tableRows = Array.isArray(payload.table_aggregate_rows) ? payload.table_aggregate_rows : [];
    const usage = this.getUsagePayload(payload);
    const roundTokens = this.getRoundTokenCountFromUsage(usage);
    if (Number.isFinite(roundTokens)) {
      wrapper.dataset.roundTokens = String(roundTokens);
    } else {
      delete wrapper.dataset.roundTokens;
    }
    const totalTokens = this.calculateChatTokenTotal();

    debugEl.innerHTML = "";
    debugEl.classList.remove("hidden");

    const root = document.createElement("details");
    root.className = "rounded-lg border border-border/50 bg-muted/20 px-3 py-2";

    const summary = document.createElement("summary");
    summary.className = "cursor-pointer select-none text-xs font-medium text-muted-foreground";
    const summaryBits = [];
    summaryBits.push(`Tools (${toolTrace.length})`);
    if (searchHistory.length) summaryBits.push(`Searches (${searchHistory.length})`);
    if (results.length) summaryBits.push(`Evidence (${results.length})`);
    if (reads.length) summaryBits.push(`Reads (${reads.length})`);
    const tokenSummary = this.buildTokenSummarySegment(roundTokens, totalTokens);
    if (tokenSummary) summaryBits.push(tokenSummary);
    summary.innerHTML = "";
    summaryBits.forEach((segment, idx) => {
      if (idx > 0) summary.append(" • ");
      summary.append(segment);
    });
    root.appendChild(summary);

    const container = document.createElement("div");
    container.className = "mt-2 space-y-2";

    const buildSection = (title, items, labelBuilder) => {
      const section = document.createElement("div");
      const header = document.createElement("div");
      header.className = "text-[11px] font-semibold text-muted-foreground/80 uppercase tracking-wide";
      header.textContent = title;
      section.appendChild(header);

      items.forEach((item, idx) => {
        const itemDetails = document.createElement("details");
        itemDetails.className = "mt-1 rounded-md border border-border/40 bg-background/40 px-2 py-1";

        const itemSummary = document.createElement("summary");
        itemSummary.className = "cursor-pointer select-none";
        itemSummary.textContent = labelBuilder(item, idx);
        itemDetails.appendChild(itemSummary);

        const pre = document.createElement("pre");
        pre.className = "mt-2 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed";
        try {
          pre.textContent = JSON.stringify(item, null, 2);
        } catch (_err) {
          pre.textContent = String(item);
        }
        itemDetails.appendChild(pre);

        section.appendChild(itemDetails);
      });

      return section;
    };

    if (!toolTrace.length && !searchHistory.length && !results.length && !reads.length && !coverage.length && !tableRows.length) {
      const empty = document.createElement("div");
      empty.className = "text-[11px] text-muted-foreground";
      empty.textContent = "No tool activity recorded for this response.";
      container.appendChild(empty);
    }

    if (toolTrace.length) {
      container.appendChild(
        buildSection("Tools", toolTrace, (item) => {
          const tool = item && item.tool ? String(item.tool) : "tool";
          const status = item && item.status ? String(item.status) : "";
          const duration = item && typeof item.duration_ms === "number" ? `${Math.round(item.duration_ms)}ms` : "";
          return [tool, status, duration].filter(Boolean).join(" • ");
        }),
      );
    }

    if (searchHistory.length) {
      container.appendChild(
        buildSection("Searches", searchHistory, (item, idx) => {
          const query = item && item.query ? String(item.query) : `Search ${idx + 1}`;
          const count =
            item && typeof item.snippet_count === "number" ? `${item.snippet_count} hits` : "";
          return [query, count].filter(Boolean).join(" • ");
        }),
      );
    }

    if (results.length) {
      container.appendChild(
        buildSection("Evidence", results, (item, idx) => {
          const title = item && item.title ? String(item.title) : `Result ${idx + 1}`;
          const readState = item && item.read_state ? String(item.read_state) : "";
          const stage = item && item.search_stage ? String(item.search_stage) : "";
          return [title, readState, stage].filter(Boolean).join(" • ");
        }),
      );
    }

    if (reads.length) {
      container.appendChild(
        buildSection("Reads", reads, (item, idx) => {
          const label = item && item.label ? String(item.label) : "";
          const mode = item && item.mode ? String(item.mode) : "";
          const fallback = `Read ${idx + 1}`;
          return [label || fallback, mode].filter(Boolean).join(" • ");
        }),
      );
    }

    if (coverage.length) {
      container.appendChild(
        buildSection("Coverage", coverage, (item, idx) => {
          const label = item && item.label ? String(item.label) : "";
          const readState = item && item.read_state ? String(item.read_state) : "";
          const fallback = `Item ${idx + 1}`;
          return [label || fallback, readState].filter(Boolean).join(" • ");
        }),
      );
    }

    if (tableRows.length) {
      container.appendChild(
        buildSection("Table Rows", tableRows, (item, idx) => {
          const label = item && item.label ? String(item.label) : "";
          const rowIndex = item && typeof item.row_index !== "undefined" ? `row ${item.row_index}` : "";
          const fallback = `Row ${idx + 1}`;
          return [label || fallback, rowIndex].filter(Boolean).join(" • ");
        }),
      );
    }

    root.appendChild(container);
    debugEl.appendChild(root);
    this.updateTokenTotalDisplays(totalTokens);
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
    this.streamingToolsEl = null;
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
      @keyframes skeleton-shimmer {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
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
      .skeleton-loader {
        background: linear-gradient(90deg, 
          hsl(var(--muted)/0.5) 25%, 
          hsl(var(--muted)/0.8) 50%, 
          hsl(var(--muted)/0.5) 75%
        );
        background-size: 200% 100%;
        animation: skeleton-shimmer 2s infinite linear;
        border-radius: 0.5rem;
      }
      details[data-tool-card] > summary {
        list-style: none;
      }
      details[data-tool-card] > summary::-webkit-details-marker {
        display: none;
      }
      details[data-tool-card] {
        display: inline-flex;
        width: fit-content;
        max-width: 100%;
        align-self: flex-start;
      }
      details[data-tool-card] > summary {
        display: inline-flex;
        flex-direction: column;
        max-width: 100%;
      }
      details[data-tool-card] [data-tool-body] {
        max-width: 100%;
        min-width: 0;
        display: none;
      }
      details[data-tool-card][open] [data-tool-body] {
        display: block;
        animation: tool-body-fade-in 200ms ease-out;
      }
      @keyframes tool-body-fade-in {
        from { opacity: 0; transform: translateY(-4px); }
        to { opacity: 1; transform: translateY(0); }
      }
      details[data-tool-card] [data-tool-tabs] {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px;
        border-radius: 999px;
        border: 1px solid hsl(var(--border) / 0.4);
        background: hsl(var(--muted) / 0.4);
      }
      details[data-tool-card] [data-tool-tab-button] {
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 600;
        color: hsl(var(--muted-foreground));
        transition: color 150ms ease, background 150ms ease, box-shadow 150ms ease;
      }
      details[data-tool-card] [data-tool-tab-button]:hover {
        color: hsl(var(--foreground));
      }
      details[data-tool-card][data-tool-tab="input"] [data-tool-tab-button][data-tool-tab="input"],
      details[data-tool-card][data-tool-tab="output"] [data-tool-tab-button][data-tool-tab="output"] {
        background: hsl(var(--background));
        color: hsl(var(--foreground));
        box-shadow: 0 1px 2px hsl(var(--border) / 0.4);
      }
      details[data-tool-card][data-tool-tab="input"] [data-tool-panel="output"],
      details[data-tool-card][data-tool-tab="output"] [data-tool-panel="input"] {
        display: none;
      }
      details[data-tool-card] [data-tool-raw-wrap] pre {
        max-height: 240px;
        overflow-y: auto;
      }
      details[data-tool-card] pre {
        max-width: 100%;
        overflow-x: auto;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      details[data-tool-card] [data-tool-status] {
        min-width: 84px;
        justify-content: center;
        text-align: center;
        display: inline-flex;
      }
      details[data-tool-card][open] [data-tool-chevron] {
        transform: rotate(180deg);
      }
      [data-chat-messages][data-session-loading="true"] {
        opacity: 0.75;
        transition: opacity 200ms ease;
      }
      [data-chat-messages][data-session-loading="false"] {
        opacity: 1;
        transition: opacity 200ms ease;
      }
      [data-chat-messages][data-session-loading="true"] [data-chat-inner-container] {
        pointer-events: none;
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

      this.sessionSummaries = Array.isArray(sessions) ? sessions : [];
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

    this.applyPendingSessionTitles();
  }

  buildSessionItem(session, isActive) {
    const div = document.createElement("div");
    div.className = `flex items-center gap-2 px-2 h-10 rounded-lg cursor-pointer transition-colors text-sm font-medium ${
      isActive
        ? "bg-primary/10 text-primary"
        : "text-foreground/80 hover:bg-muted/50"
    }`;
    div.dataset.sessionToken = session.session_token;
    if (typeof session.message_count === "number") {
      div.dataset.messageCount = String(session.message_count);
    }

    // Compact title-only layout
    div.innerHTML = `
      <span class="flex-1 truncate" data-session-title>${this.escapeHtml(session.title)}</span>
    `;

    // Click to switch session
    div.addEventListener("click", () => {
      if (session.session_token !== this.currentSessionToken) {
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

  generateSessionTitle(firstMessage, maxLength = 50) {
    let text = (firstMessage || "").trim();
    if (!text) return "New conversation";
    text = text.split(/\s+/).join(" ");
    if (text.length <= maxLength) return text;
    let truncated = text.slice(0, maxLength);
    const lastSpace = truncated.lastIndexOf(" ");
    if (lastSpace > maxLength / 2) {
      truncated = truncated.slice(0, lastSpace);
    }
    return truncated.replace(/[.,!?;:]+$/, "") + "...";
  }

  applySessionTitleToDom(sessionToken, title) {
    if (!sessionToken || !this.elements.sessionsList) return false;
    const item = this.elements.sessionsList.querySelector(`[data-session-token="${sessionToken}"]`);
    if (!item) return false;
    const titleEl = item.querySelector("[data-session-title]") || item.querySelector("span");
    if (!titleEl) return false;
    if (titleEl.textContent === title) return true;
    titleEl.classList.add("transition-opacity", "duration-200");
    titleEl.classList.add("opacity-0");
    titleEl.textContent = title;
    requestAnimationFrame(() => {
      titleEl.classList.remove("opacity-0");
    });
    return true;
  }

  updateSessionTitle(sessionToken, title) {
    if (!sessionToken || !title) return;
    const applied = this.applySessionTitleToDom(sessionToken, title);
    if (!applied) {
      this.pendingSessionTitles[sessionToken] = title;
    } else if (this.pendingSessionTitles[sessionToken]) {
      delete this.pendingSessionTitles[sessionToken];
    }
    if (Array.isArray(this.sessionSummaries) && this.sessionSummaries.length) {
      this.sessionSummaries = this.sessionSummaries.map((session) => {
        if (!session || session.session_token !== sessionToken) return session;
        return { ...session, title };
      });
    }
  }

  updateSessionTitleFromMessage(messageText) {
    const token = this.currentSessionToken;
    if (!token) return;
    const title = this.generateSessionTitle(messageText);
    this.updateSessionTitle(token, title);
  }

  applyPendingSessionTitles() {
    if (!this.pendingSessionTitles || !this.elements.sessionsList) return;
    const pending = { ...this.pendingSessionTitles };
    Object.keys(pending).forEach((sessionToken) => {
      this.updateSessionTitle(sessionToken, pending[sessionToken]);
    });
  }

  findEmptySessionToken() {
    if (Array.isArray(this.sessionSummaries) && this.sessionSummaries.length) {
      const emptySummary = this.sessionSummaries.find((session) => session && session.message_count === 0);
      return emptySummary ? emptySummary.session_token : null;
    }
    if (this.elements.sessionsList) {
      const emptyItem = this.elements.sessionsList.querySelector('[data-message-count="0"]');
      return emptyItem ? emptyItem.dataset.sessionToken : null;
    }
    return null;
  }

  getSessionMessageCount(sessionToken) {
    if (!sessionToken || !this.elements.sessionsList) return null;
    const item = this.elements.sessionsList.querySelector(`[data-session-token="${sessionToken}"]`);
    if (!item) return null;
    const raw = item.dataset.messageCount;
    if (raw === undefined || raw === "") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }

  setSessionMessageCount(sessionToken, messageCount) {
    if (!sessionToken || !this.elements.sessionsList) return;
    const item = this.elements.sessionsList.querySelector(`[data-session-token="${sessionToken}"]`);
    if (!item) return;
    if (typeof messageCount === "number" && Number.isFinite(messageCount)) {
      item.dataset.messageCount = String(messageCount);
    } else {
      delete item.dataset.messageCount;
    }
    if (Array.isArray(this.sessionSummaries) && this.sessionSummaries.length) {
      this.sessionSummaries = this.sessionSummaries.map((session) => {
        if (!session || session.session_token !== sessionToken) return session;
        if (typeof messageCount !== "number" || !Number.isFinite(messageCount)) return session;
        return { ...session, message_count: messageCount };
      });
    }
  }

  setConversationLayout(hasMessages) {
    const inputArea = this.elements.inputArea;
    if (!inputArea) return;
    const welcome = this.elements.welcome;
    const messages = this.elements.messages;
    const emptyClasses = ["inset-0", "flex", "flex-col", "justify-center", "bg-background"];
    const activeClasses = [
      "bottom-0",
      "left-0",
      "right-0",
      "pb-6",
      "bg-gradient-to-t",
      "from-background",
      "via-background",
      "to-transparent",
    ];

    if (hasMessages) {
      inputArea.classList.remove(...emptyClasses);
      inputArea.classList.add(...activeClasses);
      if (welcome) {
        welcome.classList.add("hidden");
        welcome.classList.remove("opacity-0", "-translate-y-4");
      }
      if (messages) {
        messages.classList.remove("hidden", "opacity-0", "translate-y-4");
      }
    } else {
      inputArea.classList.remove(...activeClasses);
      inputArea.classList.add(...emptyClasses);
      if (welcome) {
        welcome.classList.remove("hidden", "opacity-0", "-translate-y-4");
      }
    }
  }

  async createNewSession() {
    if (this.sessionLoadInProgress) {
      this.showToast("Still loading", "Please wait for the conversation to load.", false);
      return;
    }
    const emptySessionToken = this.findEmptySessionToken();
    if (emptySessionToken) {
      if (emptySessionToken === this.currentSessionToken) {
        this.showToast(
          "Start chatting first",
          "Please send a message in this chat before creating a new one.",
          false
        );
        return;
      }
      this.switchToSession(emptySessionToken);
      return;
    }
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

  async switchToSession(sessionToken) {
    if (!sessionToken || sessionToken === this.currentSessionToken) return;

    const loadId = ++this.sessionLoadId;
    this.prepareForSessionSwitch();
    this.setSessionLoadingState(true);
    this.currentSessionHasMessages = false;

    // 1. Update internal state
    this.currentSessionToken = sessionToken;
    this.sessionToken = sessionToken;
    this.container.setAttribute("data-session-token", sessionToken);
    this.updateSessionEmptyState();
    
    // Update localStorage
    try {
      window.localStorage.setItem(this.sessionCacheKey, sessionToken);
    } catch (e) {
      console.warn("Failed to update session cache", e);
    }

    // 2. Update UI Highlight
    if (this.elements.sessionsList) {
      const items = this.elements.sessionsList.querySelectorAll('[data-session-token]');
      items.forEach(el => {
        if (el.dataset.sessionToken === sessionToken) {
           el.className = "flex items-center gap-2 px-2 h-10 rounded-lg cursor-pointer transition-colors text-sm font-medium bg-primary/10 text-primary";
        } else {
           el.className = "flex items-center gap-2 px-2 h-10 rounded-lg cursor-pointer transition-colors text-sm font-medium text-foreground/80 hover:bg-muted/50";
        }
      });
    }

    // 3. Render loading state
    const messageCount = this.getSessionMessageCount(sessionToken);
    const shouldShowSkeleton = typeof messageCount === "number" ? messageCount > 0 : true;
    if (shouldShowSkeleton) {
      this.setConversationLayout(true);
      this.renderSkeleton();
    } else {
      this.renderEmptyConversationState();
    }

    // 4. Fetch and Render Data
    try {
      // Re-use bootstrap logic but forcing the new token AND forcing render (overwriting skeleton)
      const data = await this.bootstrapSession({
        forceRender: true,
        expectedToken: sessionToken,
        sessionToken: sessionToken,
        loadId,
      });
      if (!data || this.sessionLoadId !== loadId) return;
      this.setSessionLoadingState(false);
      this.connectEventStream();
    } catch (error) {
      if (this.sessionLoadId !== loadId) return;
      this.setSessionLoadingState(false);
      this.showToast("Load failed", "Could not load the conversation.", true);
    }
  }

  renderSkeleton() {
    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return;

    container.innerHTML = "";
    container.setAttribute("data-session-skeleton", "true");
    
    // Helper to create a skeleton bubble
    const createSkeletonParams = (isCustomer, widthCls) => {
        const wrapper = document.createElement('div');
        wrapper.className = `flex gap-4 items-start py-2 message-row ${isCustomer ? "flex-row-reverse" : ""}`;
        wrapper.dataset.skeleton = "true";
        
        const content = document.createElement('div');
        content.className = `flex-1 min-w-0 flex flex-col ${isCustomer ? "items-end" : "items-start"}`;
        
        const bubble = document.createElement('div');
        // mimics the message bubble shape
        bubble.className = `skeleton-loader h-12 ${widthCls}`; 
        if (isCustomer) {
            bubble.classList.add("rounded-2xl", "rounded-tr-sm");
        } else {
            bubble.classList.add("rounded-lg");
        }
        
        content.appendChild(bubble);
        wrapper.appendChild(content);
        return wrapper;
    };

    // 1. AI Message (Left)
    container.appendChild(createSkeletonParams(false, "w-3/4 max-w-md"));
    
    // 2. User Message (Right)
    container.appendChild(createSkeletonParams(true, "w-1/2 max-w-sm"));
    
    // 3. AI Message (Left)
    container.appendChild(createSkeletonParams(false, "w-full max-w-lg"));
  }

  renderEmptyConversationState() {
    const container = this.elements.messagesInner || this.elements.messages;
    if (container) {
      container.innerHTML = "";
      container.removeAttribute("data-session-skeleton");
    }
    this.setConversationLayout(false);
  }

  isCurrentSessionEmpty() {
    /**
     * Check if current session has any messages.
     * Returns true if no messages have been sent.
     */
    if (this.sessionLoadInProgress) {
      return true;
    }
    // Check if we've tracked that messages were sent
    if (this.currentSessionHasMessages) {
      return false;
    }
    
    // Also check DOM for messages (in case of page reload)
    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return true;
    
    // Look for any real message rows (ignore skeletons)
    const messageRow = container.querySelector('.message-row:not([data-skeleton])');
    if (messageRow) {
      this.currentSessionHasMessages = true;
      return false;
    }
    
    return true;
  }

  updateSessionEmptyState(messageCount = null) {
    /**
     * Update the session empty state and button UI accordingly.
     */
    const isEmpty = typeof messageCount === "number" ? messageCount === 0 : this.isCurrentSessionEmpty();
    const btn = this.elements.newSessionBtn;
    
    if (!btn) return;
    
    if (isEmpty) {
      btn.classList.add('opacity-50', 'cursor-not-allowed');
      btn.setAttribute("aria-disabled", "true");
    } else {
      btn.classList.remove('opacity-50', 'cursor-not-allowed');
      btn.setAttribute("aria-disabled", "false");
    }
    this.currentSessionHasMessages = !isEmpty;
    if (this.currentSessionToken) {
      if (typeof messageCount === "number") {
        this.setSessionMessageCount(this.currentSessionToken, messageCount);
      } else if (!isEmpty) {
        const existing = this.getSessionMessageCount(this.currentSessionToken);
        if (!existing || existing === 0) {
          this.setSessionMessageCount(this.currentSessionToken, 1);
        }
      }
    }
  }

  setSessionLoadingState(isLoading) {
    this.sessionLoadInProgress = isLoading;
    if (this.elements.messages) {
      this.elements.messages.setAttribute("data-session-loading", isLoading ? "true" : "false");
    }
    this.setComposerAvailability(!isLoading);
    if (this.elements.sendButton) {
      this.elements.sendButton.disabled = isLoading;
      this.elements.sendButton.classList.toggle("opacity-50", isLoading);
      this.elements.sendButton.classList.toggle("cursor-not-allowed", isLoading);
    }
  }

  prepareForSessionSwitch() {
    if (this.streamController) {
      try {
        this.streamController.abort();
      } catch (_err) {
        // Ignore abort errors
      }
    }
    this.streamController = null;
    this.awaitingReply = false;
    this.isSending = false;
    this.isStreaming = false;
    this.streamFinished = true;
    this.workflowLocked = false;
    this.usingStateMachine = false;
    this.pendingMetadataVersion = 0;
    this.pendingMessageId = null;
    this.flushQueueAfterTurn = false;
    this.pendingMessages = [];
    this.resetStreamingState(true, false);
    this.clearStreamingStatus();
    this.updateSendButtonState(false);
    this.updateComposerNotice(false);
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-chat-portal]");
  if (!container) return;
  const client = new ChatPortalClient(container);
  client.init();
});
