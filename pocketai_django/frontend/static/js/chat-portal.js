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
      sendForm: container.querySelector("[data-chat-send-form]"),
      stopButton: container.querySelector("[data-chat-stop]"),
      csatForm: container.querySelector("[data-chat-csat-form]"),
      csatContainer: container.querySelector("[data-chat-csat]"),
      toastRoot: document.getElementById("toast-root"),
      statusBadge: container.querySelector("[data-chat-status]"),
    };
    this.lastPlaceholderText = "";
    this.streamingDedupDone = false;
    this.streamingFinalBodyEl = null;
    this.streamingMessageNode = null;
    this.streamingMessageBodyEl = null;
    this.streamingMessageBubbleEl = null;
    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
    this.streamingStatusDotEl = null;
    this.streamingBuffer = "";
    this.streamingRewritePending = false;
    this.markdownRenderer = this.createMarkdownRenderer();
    this.workflowLocked = false;
    this.streamingActive = false;
    this.streamFinished = false;
    this.statusStyleInjected = false;
    this.ensureStatusStyle();
  }

  async init() {
    this.bindSendForm();
    this.bindStopButton();
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
      if (textarea) {
        textarea.value = "";
      }
      await this.sendMessage(message);
    });
  }

  bindStopButton() {
    const stopButton = this.elements.stopButton;
    if (!stopButton) return;
    stopButton.addEventListener("click", () => {
      if (this.streamFinished) {
        return;
      }
      if (this.streamController) {
        this.streamController.abort();
      }
      this.awaitingReply = false;
      stopButton.disabled = true;
      this.workflowLocked = true;
      this.clearStreamingStatus();
      this.streamFinished = true;
      this.resetStreamingState(true);
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
    this.workflowLocked = false;
    this.streamFinished = false;
    this.awaitingReply = true;
    if (this.elements.stopButton) {
      this.elements.stopButton.disabled = false;
    }
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
    } finally {
      this.awaitingReply = false;
      if (this.elements.stopButton) {
        this.elements.stopButton.disabled = true;
      }
      if (this.streamingMessageNode) {
        this.resetStreamingState(true);
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
    if (eventType === "placeholder") {
      // Do NOT render a placeholder; only remember it for dedupe.
      try {
        const payload = data ? JSON.parse(data) : null;
        const text = payload && payload.text ? payload.text : "";
        if (text) {
          this.lastPlaceholderText = text;
          this.streamingDedupDone = false;
          this.setStreamingStatus("reading", text);
        }
      } catch (_err) {
        // ignore malformed payloads
      }
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

          if (state === "reading_document") {
            // Knowledge read: we expect content to be revised after doc load.
            this.streamingRewritePending = true;
            this.setStreamingStatus("reading", label || "Reading documents…");
          } else if (state === "searching_knowledge") {
            // Surface search-specific label (e.g. "Searching: billing policy").
            this.setStreamingStatus("searching", label || "Searching knowledge…");
          } else if (state === "planning_actions") {
            // Keep this internal; do not surface to the visitor.
            return;
          } else if (state === "responding") {
            this.clearStreamingStatus();
          } else if (state && state !== "responding") {
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
    
    // **REPLACE THE DELTA HANDLER WITH THIS NEW VERSION**
    if (eventType === "delta") {
      try {
        const payload = data ? JSON.parse(data) : null;
        if (payload && payload.text) {
          let chunk = payload.text;

          // --- DEDUPE: strip placeholder prefix from the very first streamed delta ---
          if (this.lastPlaceholderText && !this.streamingDedupDone) {
            const base = (this.lastPlaceholderText || "").trim();
            const baseLower = base.toLowerCase();
            let chunkLower = chunk.toLowerCase();

            // Try a few strip candidates to be robust to punctuation/quotes
            const stripCandidates = [
              base,
              base.replace(/[.?!:—-]+$/g, "").trim(),         // trailing punctuation
              base.replace(/["""']+/g, "").trim(),           // smart quotes
            ];

            for (const cand of stripCandidates) {
              const cLower = cand.toLowerCase();
              if (cLower && chunkLower.startsWith(cLower)) {
                // Remove the exact-length prefix from the ORIGINAL chunk (preserve case/markup)
                chunk = chunk.slice(cand.length).replace(/^\s+/, "");
                this.streamingDedupDone = true;
                break;
              }
            }

            // If the first chunk was only the placeholder, skip appending empty text.
            if (!chunk) {
              return;
            }
          }
          // ---------------------------------------------------------------------------

          if (!this.workflowLocked) {
            if (!this.streamingActive) {
              this.streamingActive = true;
              this.setStreamingStatus("updating", "Updating details…");
            } else {
              this.setStreamingStatus("refining", "Refining response…");
            }
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
    
    if (!data && eventType !== "final") return;
    if (eventType !== "final") {
      if (eventType === "error") {
        this.showToast("Stream error", data, true);
      }
      return;
    }
    try {
      const payload = JSON.parse(data);
      if (payload && payload.text) {
        if (this.streamingMessageNode) {
          this.finalizeStreamingMessage(payload.text);
        } else {
          this.appendMessage({
            sender: "ai",
            body: payload.text,
            sent_at: new Date().toISOString(),
          });
        }
      } else if (this.streamingMessageNode) {
        this.finalizeStreamingMessage("");
      }
      if (payload && payload.session_status) {
        this.updateStatus(payload.session_status);
        this.updateCsatVisibility(payload.session_status);
      }
    } catch (error) {
      console.warn("Failed to parse stream payload", error);
    } finally {
      this.workflowLocked = true;
      this.streamingActive = false;
      this.clearStreamingStatus();
      this.markStreamFinished();
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
    const container = this.elements.messages;
    if (!container) return;
    container.innerHTML = "";
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

  appendMessage(raw) {
    const container = this.elements.messages;
    if (!container) return;
    const message = this.normalizeMessage(raw);
    const node = this.buildMessageNode(message);
    container.appendChild(node);
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }

  normalizeMessage(raw) {
    const sender = (raw.sender || "system").toLowerCase();
    const isAi = sender === "ai";
    const isCustomer = sender === "customer";
    const authorName =
      raw.author && raw.author.name ? raw.author.name : (isAi ? this.agentName : isCustomer ? "You" : "System");
    const authorInitials =
      raw.author && raw.author.initials ? raw.author.initials : (isAi ? this.agentInitials : isCustomer ? "YOU" : "SYS");
    return {
      sender,
      body: raw.body || "",
      sentAt: raw.sent_at || raw.sentAt || new Date().toISOString(),
      author: {
        name: authorName,
        initials: authorInitials,
      },
    };
  }

  buildMessageNode(message) {
    const wrapper = document.createElement("div");
    wrapper.className = "flex gap-3 items-start";
    if (message.sender === "customer") {
      wrapper.classList.add("flex-row-reverse", "text-right");
    }
    const avatar = document.createElement("div");
    avatar.className = `h-9 w-9 rounded-full flex items-center justify-center font-semibold ${
      message.sender === "customer" ? "bg-primary text-white" : "bg-primary/15 text-primary"
    }`;
    avatar.textContent = message.author.initials;

    const bubble = document.createElement("div");
    bubble.className = `flex-1 rounded-2xl px-4 py-3 text-sm text-foreground shadow-soft ${
      message.sender === "customer" ? "bg-primary/10" : "bg-muted/60"
    }`;
    bubble.dataset.messageBubble = "true";

    const author = document.createElement("p");
    author.className = "font-medium text-sm text-muted-foreground mb-1";
    author.textContent = message.author.name;
    bubble.appendChild(author);

    const body = document.createElement("div");
    body.className = "space-y-2 leading-relaxed";
    body.dataset.messageBody = "true";
    body.innerHTML = this.renderMarkdown(message.body);
    bubble.appendChild(body);

    const timestamp = document.createElement("p");
    timestamp.className = "mt-2 text-xs text-muted-foreground";
    timestamp.textContent = this.formatTimestamp(message.sentAt);
    bubble.appendChild(timestamp);

    if (message.sender === "customer") {
      wrapper.appendChild(bubble);
      wrapper.appendChild(avatar);
    } else {
      wrapper.appendChild(avatar);
      wrapper.appendChild(bubble);
    }

    return wrapper;
  }

  appendStreamingChunk(chunk) {
    if (!chunk || !this.elements.messages) return;
    this.ensureStreamingMessageNode();
    if (this.streamingRewritePending) {
      this.streamingBuffer = "";
      if (this.streamingFinalBodyEl) {
        this.streamingFinalBodyEl.innerHTML = "";
      }
      this.streamingRewritePending = false;
      this.setStreamingStatus("updating");
    }
    this.streamingBuffer += chunk;
    if (this.streamingFinalBodyEl) {
      this.streamingFinalBodyEl.innerHTML = this.renderMarkdown(this.streamingBuffer);
    }
    this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
  }

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

      const finalEl = document.createElement("div");
      finalEl.dataset.messageFinalBody = "true";
      finalEl.className = "space-y-2 leading-relaxed";
      this.streamingMessageBodyEl.appendChild(finalEl);
      this.streamingFinalBodyEl = finalEl;
    }
  
    this.elements.messages.appendChild(node);
  }

  finalizeStreamingMessage(finalText) {
    const trimmedBuffer = (this.streamingBuffer || "").trim();
    const trimmedFinal = (finalText || "").trim();
    const placeholder = (this.lastPlaceholderText || "").trim().toLowerCase();
    const finalMatchesPlaceholder = trimmedFinal && placeholder && trimmedFinal.toLowerCase() === placeholder;

    let text = "";
    if (!trimmedFinal) {
      text = this.streamingBuffer;
    } else if (finalMatchesPlaceholder) {
      text = trimmedBuffer ? this.streamingBuffer : finalText;
    } else {
      text = finalText;
      this.streamingBuffer = finalText;
    }

    if (!text) {
      text = this.streamingBuffer || "";
    }

    if (this.streamingFinalBodyEl) {
      this.streamingFinalBodyEl.innerHTML = this.renderMarkdown(text);
    } else if (text) {
      this.appendMessage({ sender: "ai", body: text, sent_at: new Date().toISOString() });
    }
    this.resetStreamingState(false);
  }

  resetStreamingState(removeNode = false, lockWorkflow = true) {
    if (lockWorkflow) {
      this.workflowLocked = true;
    }
    this.streamingActive = false;
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
    this.streamingRewritePending = false;
  }

  setStreamingStatus(mode = "working", labelOverride) {
    if (this.workflowLocked) return;
    this.ensureStreamingMessageNode();
    if (!this.streamingStatusEl || !this.streamingStatusTextEl) return;
    const formattedLabel = this.formatStatusLabel(labelOverride);
    const labelMap = {
      working: "Assistant is working…",
      drafting: "Processing…",
      reading: "Reading documents…",
      searching: "Searching knowledge…",
      updating: "Updating details…",
      refining: "Refining response…",
      error: "Workflow issue detected.",
    };
    const baseLabel = labelMap[mode] || labelMap.working;
    const label = formattedLabel || baseLabel;
    this.streamingStatusTextEl.innerHTML = label;
    this.streamingStatusEl.classList.remove("hidden");
    const isError = mode === "error";
    if (this.streamingStatusDotEl) {
      this.streamingStatusDotEl.classList.toggle("bg-primary", !isError);
      this.streamingStatusDotEl.classList.toggle("bg-destructive", isError);
    }
    if (this.streamingStatusTextEl) {
      this.streamingStatusTextEl.classList.toggle("text-destructive", isError);
      if (isError) {
        this.streamingStatusTextEl.classList.remove("chat-portal-status-shimmer");
      } else {
        this.streamingStatusTextEl.classList.add("chat-portal-status-shimmer");
      }
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
      this.streamingStatusDotEl.classList.remove("bg-destructive");
      this.streamingStatusDotEl.classList.add("bg-primary");
    }
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
    if (this.elements.stopButton) {
      this.elements.stopButton.disabled = true;
    }
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
    const submit = form.querySelector('button[type="submit"]');
    [textarea, submit].forEach((el) => {
      if (el) {
        el.disabled = !enabled;
      }
    });
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

    const renderBlocks = (input = "") => {
      const lines = input.replace(/\r\n/g, "\n").split("\n");
      const blocks = [];
      let currentList = null;

      const flushList = () => {
        if (!currentList) return;
        blocks.push(wrapList(currentList.items, currentList.ordered));
        currentList = null;
      };

      for (const line of lines) {
        const matchUnordered = line.match(/^\s*[-*+]\s+(.*)/);
        const matchOrdered = line.match(/^\s*\d+\.\s+(.*)/);
        if (matchUnordered) {
          if (!currentList || currentList.ordered) {
            flushList();
            currentList = { ordered: false, items: [] };
          }
          currentList.items.push(matchUnordered[1]);
          continue;
        }
        if (matchOrdered) {
          if (!currentList || !currentList.ordered) {
            flushList();
            currentList = { ordered: true, items: [] };
          }
          currentList.items.push(matchOrdered[1]);
          continue;
        }

        const trimmed = line.trim();
        if (!trimmed) {
          flushList();
          continue;
        }

        flushList();
        const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
        if (heading) {
          const level = heading[1].length;
          const tag = level === 1 ? "h3" : level === 2 ? "h4" : "h5";
          const classes = "font-semibold text-foreground";
          blocks.push(`<${tag} class="${classes}">${applyInlineFormatting(heading[2])}</${tag}>`);
          continue;
        }

        blocks.push(`<p>${applyInlineFormatting(trimmed)}</p>`);
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
