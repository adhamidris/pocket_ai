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
    };
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
    this.markdownRenderer = this.createMarkdownRenderer();
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
  }

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

  async transitionToActiveChat() {
    // 1. Fade out welcome
    if (this.elements.welcome && !this.elements.welcome.classList.contains("hidden")) {
      this.elements.welcome.classList.add("opacity-0", "-translate-y-4");
      await new Promise((resolve) => setTimeout(resolve, 300)); // Wait for fade out
      this.elements.welcome.classList.add("hidden");
    }

    // 2. Animate layout change (Input area moves down)
    if (this.elements.inputArea) {
      this.elements.inputArea.classList.remove("flex-1", "flex", "flex-col", "justify-center");
    }

    // 3. Fade in messages
    if (this.elements.messages) {
      this.elements.messages.classList.remove("hidden");
      // Force reflow
      void this.elements.messages.offsetWidth;
      this.elements.messages.classList.remove("opacity-0", "translate-y-4");
    }
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
        textarea.value = "";
      }
      if (this.isSending || this.isStreaming) {
        this.enqueueMessage(message);
        return;
      }
      await this.sendMessage(message);
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
    this.renderTranscript(data.messages || []);
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
    return this.markdownRenderer.render(text);
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
    container.className = "space-y-1 rounded-xl bg-background/60 px-3 py-2 border border-border/60";
    if (block.heading) {
      const heading = document.createElement("p");
      heading.className = "text-sm font-semibold text-foreground";
      heading.textContent = block.heading;
      container.appendChild(heading);
    }
    lines.forEach((line) => {
      if (!line) return;
      const paragraph = document.createElement("div");
      paragraph.className = "text-sm leading-relaxed";
      paragraph.innerHTML = this.renderMarkdown(line);
      container.appendChild(paragraph);
    });
    if (block.rtl) {
      container.dir = "rtl";
      container.classList.add("text-right");
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
    wrapper.className = `flex gap-4 items-start py-2 ${isCustomer ? "flex-row-reverse" : ""}`;

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

    // Author Removed

    if (!isCustomer) {
      const timestamp = document.createElement("span");
      timestamp.className = "text-xs text-muted-foreground";
      timestamp.textContent = this.formatTimestamp(message.sentAt);
      header.appendChild(timestamp);
    }

    content.appendChild(header);

    // Message body
    const body = document.createElement("div");
    if (isCustomer) {
      body.className = "text-sm leading-relaxed bg-muted text-foreground px-5 py-3 rounded-2xl rounded-tr-sm text-left inline-block shadow-sm";
    } else {
      body.className = "text-sm text-foreground leading-relaxed";
    }
    body.dataset.messageBody = "true";
    body.dataset.messageBubble = "true";
    const cleanBody = this.stripInlineResponseBlocks(message.body || "");
    body.innerHTML = this.renderMarkdown(cleanBody);
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
      sent_at: new Date().toISOString(),
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
    if (this.streamingTextEl) {
      this.streamingTextEl.innerHTML = this.renderMarkdown(this.streamingBuffer);
      return;
    }
    if (this.streamingFinalBodyEl) {
      this.streamingFinalBodyEl.innerHTML = this.renderMarkdown(this.streamingBuffer);
      return;
    }
    if (this.streamingMessageBodyEl) {
      this.streamingMessageBodyEl.innerHTML = this.renderMarkdown(this.streamingBuffer);
    }
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

  createMarkdownRenderer() {
    const escapeHtml = (value = "") =>
      value
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");

    const decodeHtmlEntities = (value = "") =>
      value
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");

    const truncateText = (value = "", limit = 60) => (value.length > limit ? `${value.slice(0, limit - 1)}…` : value);

    const prettifyLinkLabel = (rawUrl = "") => {
      const cleaned = decodeHtmlEntities(rawUrl || "").trim();
      if (!cleaned) return "";
      try {
        const parsed = new URL(cleaned);
        const host = (parsed.hostname || "").replace(/^www\./i, "") || parsed.hostname;
        const segments = parsed.pathname.split("/").filter(Boolean);
        let pathLabel = "";
        for (const segment of segments) {
          const safeSegment = segment.length > 32 ? `${segment.slice(0, 29)}…` : segment;
          const tentative = pathLabel ? `${pathLabel}/${safeSegment}` : safeSegment;
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
        return truncateText(cleaned.replace(/^https?:\/\//i, ""), 60);
      }
    };

    const createPlaceholderToken = (prefix, collection, html) => {
      const token = `@@${prefix}_${collection.length}@@`;
      collection.push(html);
      return token;
    };

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

    const buildAnchor = (href, label) =>
      `<a href="${href}" target="_blank" rel="nofollow noopener noreferrer" class="text-primary underline">${label}</a>`;

    const applyInlineFormatting = (value = "") => {
      if (!value) return "";
      const codePlaceholders = [];
      const markdownLinkPlaceholders = [];

      let working = value;

      working = working.replace(/`([^`]+)`/g, (_, code) =>
        createPlaceholderToken(
          "CODE",
          codePlaceholders,
          `<code class="bg-muted/60 px-1 py-0.5 rounded text-xs font-mono">${escapeHtml(code)}</code>`
        )
      );

      working = working.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
        const displayLabel = (label || "").trim() || prettifyLinkLabel(url) || url;
        const safeHref = escapeHtml(url);
        const safeLabel = escapeHtml(displayLabel);
        return createPlaceholderToken("LINK", markdownLinkPlaceholders, buildAnchor(safeHref, safeLabel));
      });

      let output = escapeHtml(working);

      output = output.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      output = output.replace(/__(.+?)__/g, "<strong>$1</strong>");
      output = output.replace(/(\*|_)([^*_]+)\1/g, "<em>$2</em>");

      output = output.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, (match, prefix, url) => {
        let normalizedUrl = url;
        let trailing = "";
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
        return `${prefix || ""}${anchor}${trailing}`;
      });

      output = restorePlaceholders(output, "LINK", markdownLinkPlaceholders);
      output = restorePlaceholders(output, "CODE", codePlaceholders);

      return output;
    };

    const wrapList = (items, ordered) => {
      if (!items.length) return "";
      const tag = ordered ? "ol" : "ul";
      const classes = ordered ? "list-decimal pl-5 space-y-1" : "list-disc pl-5 space-y-1";
      const inner = items.map((item) => `<li>${applyInlineFormatting(item)}</li>`).join("");
      return `<${tag} class="${classes}">${inner}</${tag}>`;
    };

    const splitTableRow = (line = "") => {
      let text = line.trim();
      if (!text) {
        return [];
      }
      if (text.startsWith("|")) {
        text = text.slice(1);
      }
      if (text.endsWith("|")) {
        text = text.slice(0, -1);
      }
      return text.split("|").map((cell) => cell.trim());
    };

    const normalizeColumnAlignment = (token = "") => {
      const trimmed = token.trim();
      const starts = trimmed.startsWith(":");
      const ends = trimmed.endsWith(":");
      if (starts && ends) return "center";
      if (ends) return "right";
      return "left";
    };

    const isTableRowCandidate = (line = "") => {
      if (!line || typeof line !== "string") {
        return false;
      }
      if (/^\s*```/.test(line)) {
        return false;
      }
      return (line.match(/\|/g) || []).length >= 2;
    };

    const isPartialTableRowLine = (line = "") => {
      if (!line || typeof line !== "string") {
        return false;
      }
      if (/^\s*```/.test(line)) {
        return false;
      }
      const trimmed = line.trim();
      if (!trimmed) {
        return false;
      }
      return trimmed.startsWith("|");
    };

    const isTableSeparatorLine = (line = "") => {
      const cells = splitTableRow(line);
      if (!cells.length) return false;
      return cells.every((cell) => /^:?-{2,}:?$/.test(cell.replace(/\s+/g, "")));
    };

    const isPotentialSeparatorLine = (line = "") => {
      if (!line || typeof line !== "string") {
        return false;
      }
      const cells = splitTableRow(line);
      if (!cells.length) return false;
      return cells.every((cell) => /^:?-*:?$/.test(cell.replace(/\s+/g, "")));
    };

    const containsRtlCharacters = (text = "") => /[\u0590-\u08FF]/.test(text);

    const buildTableHtml = (headerCells, alignCells, rowLines, { placeholderOnly = false } = {}) => {
      const columnMeta = headerCells.map((raw, idx) => {
        const label = raw || `Column ${idx + 1}`;
        const align = normalizeColumnAlignment(alignCells[idx] || "");
        return {
          label,
          align,
        };
      });
      const bodyRows = rowLines.map((row) => {
        const cells = splitTableRow(row);
        while (cells.length < columnMeta.length) {
          cells.push("");
        }
        const rtl = containsRtlCharacters(row);
        return {
          cells: cells.slice(0, columnMeta.length),
          rtl,
        };
      });
      const wrapperClasses = "mt-3 overflow-hidden rounded-xl border border-border/60 bg-background/80 shadow-sm";
      const tableClasses = "w-full border-collapse text-sm";
      const headCells = columnMeta
        .map((col) => {
          const alignClass = col.align === "center" ? "text-center" : col.align === "right" ? "text-right" : "text-left";
          return `<th class="px-3 py-2 font-medium ${alignClass}">${applyInlineFormatting(col.label)}</th>`;
        })
        .join("");
      const bodyHtml =
        bodyRows.length === 0
          ? ""
          : bodyRows
            .map((row) => {
              const rowAlign = row.rtl ? ' dir="rtl" class="text-right"' : "";
              const cellsHtml = row.cells
                .map((cell, idx) => {
                  const alignClass =
                    columnMeta[idx] && columnMeta[idx].align === "center"
                      ? "text-center"
                      : columnMeta[idx] && columnMeta[idx].align === "right"
                        ? "text-right"
                        : "text-left";
                  return `<td class="px-3 py-2 ${alignClass}">${applyInlineFormatting(cell)}</td>`;
                })
                .join("");
              return `<tr${rowAlign}>${cellsHtml}</tr>`;
            })
            .join("");
      const placeholder =
        placeholderOnly || bodyRows.length === 0
          ? `<tr><td class="px-3 py-3 text-center text-xs text-muted-foreground" colspan="${columnMeta.length}">Formatting table…</td></tr>`
          : "";
      return `<div class="${wrapperClasses}"><table class="${tableClasses}"><thead class="bg-muted/40 text-muted-foreground"><tr>${headCells}</tr></thead><tbody>${bodyHtml || placeholder}</tbody></table></div>`;
    };

    const tryParseTable = (lines, startIndex, { intentActive } = {}) => {
      const line = lines[startIndex];
      if (!isTableRowCandidate(line)) {
        return null;
      }
      const headerCells = splitTableRow(line);
      if (headerCells.length < 2) {
        return null;
      }
      if (startIndex + 1 >= lines.length) {
        if (intentActive) {
          return {
            html: buildTableHtml(headerCells, [], [], { placeholderOnly: true }),
            nextIndex: startIndex + 1,
            awaitingSeparator: true,
          };
        }
        return null;
      }
      const separatorLine = lines[startIndex + 1];
      if (!isTableSeparatorLine(separatorLine)) {
        const remainingLines = lines.slice(startIndex + 2);
        const hasTrailingContent = remainingLines.some((nextLine) => nextLine.trim());
        if (intentActive && !hasTrailingContent && (isPotentialSeparatorLine(separatorLine) || !separatorLine.trim())) {
          return {
            html: buildTableHtml(headerCells, [], [], { placeholderOnly: true }),
            nextIndex: startIndex + 2,
            awaitingSeparator: true,
          };
        }
        return null;
      }
      const rowLines = [];
      const headerTrimmed = (line || "").trim();
      const headerHasOuterPipes = headerTrimmed.startsWith("|") || headerTrimmed.endsWith("|");
      const allowOnePipeRows = headerCells.length === 2 && !headerHasOuterPipes;
      let cursor = startIndex + 2;
      while (cursor < lines.length) {
        const rowLine = lines[cursor];
        if (!rowLine.trim()) {
          break;
        }
        const pipeCount = (rowLine.match(/\|/g) || []).length;
        const isBodyRow =
          isTableRowCandidate(rowLine) ||
          isPartialTableRowLine(rowLine) ||
          (allowOnePipeRows && pipeCount >= 1);
        if (!isBodyRow) {
          break;
        }
        rowLines.push(rowLine);
        cursor += 1;
      }
      return {
        html: buildTableHtml(headerCells, splitTableRow(separatorLine), rowLines),
        nextIndex: cursor,
      };
    };

    const renderBlocks = (input = "") => {
      const lines = input.replace(/\r\n/g, "\n").split("\n");
      const blocks = [];
      let currentList = null;

      const flushList = () => {
        if (!currentList) return;
        blocks.push(wrapList(currentList.items, currentList.ordered));
        currentList = null;
      };

      let idx = 0;
      while (idx < lines.length) {
        const line = lines[idx];
        const matchUnordered = line.match(/^\s*[-*+]\s+(.*)/);
        const matchOrdered = line.match(/^\s*\d+\.\s+(.*)/);
        if (matchUnordered) {
          if (!currentList || currentList.ordered) {
            flushList();
            currentList = { ordered: false, items: [] };
          }
          currentList.items.push(matchUnordered[1]);
          idx += 1;
          continue;
        }
        if (matchOrdered) {
          if (!currentList || !currentList.ordered) {
            flushList();
            currentList = { ordered: true, items: [] };
          }
          currentList.items.push(matchOrdered[1]);
          idx += 1;
          continue;
        }

        const trimmed = line.trim();
        if (!trimmed) {
          flushList();
          idx += 1;
          continue;
        }

        const tableCandidate = tryParseTable(lines, idx, { intentActive: this.tableIntentActive });
        if (tableCandidate) {
          flushList();
          blocks.push(tableCandidate.html);
          idx = tableCandidate.nextIndex;
          if (tableCandidate.awaitingSeparator) {
            break;
          } else {
            this.tableIntentActive = false;
          }
          continue;
        }

        flushList();
        const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
        if (heading) {
          const level = heading[1].length;
          const tag = level === 1 ? "h3" : level === 2 ? "h4" : "h5";
          const classes = "font-semibold text-foreground";
          blocks.push(`<${tag} class="${classes}">${applyInlineFormatting(heading[2])}</${tag}>`);
          idx += 1;
          continue;
        }

        blocks.push(`<p>${applyInlineFormatting(trimmed)}</p>`);
        idx += 1;
      }

      flushList();
      return blocks.join("") || applyInlineFormatting(input);
    };

    return {
      render: renderBlocks,
    };
  }

  formatTimestamp(value) {
    if (!value) return "";
    try {
      return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (_error) {
      return "";
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
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-chat-portal]");
  if (!container) return;
  const client = new ChatPortalClient(container);
  client.init();
});
