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
      typingIndicator: container.querySelector("[data-chat-typing]"),
      csatForm: container.querySelector("[data-chat-csat-form]"),
      csatContainer: container.querySelector("[data-chat-csat]"),
      toastRoot: document.getElementById("toast-root"),
      statusBadge: container.querySelector("[data-chat-status]"),
    };
    this.streamingMessageNode = null;
    this.streamingMessageBodyEl = null;
    this.streamingMessageBubbleEl = null;
    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
    this.streamingBuffer = "";
    this.streamingRewritePending = false;
    this.markdownRenderer = this.createMarkdownRenderer();
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
    const textarea = form?.querySelector("textarea[name='message']");
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
      if (this.streamController) {
        this.streamController.abort();
      }
      this.awaitingReply = false;
      stopButton.disabled = true;
      this.elements.typingIndicator?.classList.add("hidden");
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
        this.elements.csatContainer?.classList.add("hidden");
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
    const token = data?.session?.session_token;
    if (!token) {
      throw new Error("Session token missing from bootstrap response");
    }
    this.persistSessionToken(token);
    this.renderTranscript(data.messages || []);
    this.updateStatus(data?.session?.status);
    this.updateCsatVisibility(data?.session?.status);
  }

  async sendMessage(message) {
    if (!this.sessionToken) return;
    this.resetStreamingState(true);
    this.awaitingReply = true;
    this.elements.stopButton && (this.elements.stopButton.disabled = false);
    this.elements.typingIndicator?.classList.remove("hidden");
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
      this.elements.stopButton && (this.elements.stopButton.disabled = true);
      this.elements.typingIndicator?.classList.add("hidden");
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
    if (eventType === "status") {
      try {
        const payload = data ? JSON.parse(data) : null;
        if (payload && payload.state) {
          // Keep the typing indicator visible during background work
          this.elements.typingIndicator?.classList.remove("hidden");
          if (payload.state === "reading_document") {
            this.ensureStreamingMessageNode();
            this.streamingRewritePending = true;
            this.setStreamingPendingState("reading");
          } else if (payload.state === "responding") {
            this.setStreamingPendingState("updating");
          }
        }
      } catch (_err) {
        // ignore malformed status payloads
      }
      return;
    }
    if (eventType === "delta") {
      try {
        const payload = data ? JSON.parse(data) : null;
        if (payload?.text) {
          this.appendStreamingChunk(payload.text);
        }
      } catch (error) {
        console.warn("Failed to parse stream delta", error);
      }
      this.elements.typingIndicator?.classList.remove("hidden");
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
      if (payload?.text) {
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
      if (payload?.session_status) {
        this.updateStatus(payload.session_status);
        this.updateCsatVisibility(payload.session_status);
      }
    } catch (error) {
      console.warn("Failed to parse stream payload", error);
    } finally {
      this.elements.typingIndicator?.classList.add("hidden");
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
        if (data?.status) {
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
    messages.forEach((message) => this.appendMessage(message));
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
    const authorName = raw.author?.name || (isAi ? this.agentName : isCustomer ? "You" : "System");
    const authorInitials = raw.author?.initials || (isAi ? this.agentInitials : isCustomer ? "YOU" : "SYS");
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
      if (this.streamingMessageBodyEl) {
        this.streamingMessageBodyEl.innerHTML = "";
      }
      this.streamingRewritePending = false;
      this.setStreamingPendingState("updating");
    }
    this.streamingBuffer += chunk;
    if (this.streamingMessageBodyEl) {
      this.streamingMessageBodyEl.innerHTML = this.renderMarkdown(this.streamingBuffer);
    }
    this.elements.messages.scrollTo({ top: this.elements.messages.scrollHeight, behavior: "smooth" });
  }

  ensureStreamingMessageNode() {
    if (this.streamingMessageNode && this.streamingMessageBodyEl) return;
    if (!this.elements.messages) return;
    const placeholder = this.buildMessageNode({
      sender: "ai",
      body: "",
      sent_at: new Date().toISOString(),
      author: { name: this.agentName, initials: this.agentInitials },
    });
    this.streamingMessageNode = placeholder;
    this.streamingMessageBodyEl = placeholder.querySelector("[data-message-body]");
    this.streamingMessageBubbleEl = placeholder.querySelector("[data-message-bubble]");
    this.elements.messages.appendChild(placeholder);
    this.setStreamingPendingState("drafting");
  }

  finalizeStreamingMessage(finalText) {
    const text = finalText || this.streamingBuffer;
    if (this.streamingMessageBodyEl) {
      this.streamingMessageBodyEl.innerHTML = this.renderMarkdown(text);
    } else if (text) {
      this.appendMessage({
        sender: "ai",
        body: text,
        sent_at: new Date().toISOString(),
      });
    }
    this.resetStreamingState(false);
  }

  resetStreamingState(removeNode = false) {
    this.clearStreamingPendingState();
    if (removeNode && this.streamingMessageNode && this.streamingMessageNode.parentNode) {
      this.streamingMessageNode.parentNode.removeChild(this.streamingMessageNode);
    }
    this.streamingMessageNode = null;
    this.streamingMessageBodyEl = null;
    this.streamingMessageBubbleEl = null;
    this.streamingBuffer = "";
    this.streamingRewritePending = false;
    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
  }

  setStreamingPendingState(mode = "drafting") {
    if (!this.streamingMessageBubbleEl) return;
    const labelMap = {
      drafting: "Drafting response…",
      reading: "Consulting documents…",
      updating: "Updating details…",
    };
    const label = labelMap[mode] || labelMap.drafting;
    this.streamingMessageBubbleEl.classList.add("opacity-80", "relative");
    let badge = this.streamingStatusEl;
    if (!badge) {
      badge = document.createElement("div");
      badge.dataset.streamingStatus = "true";
      badge.className = "absolute right-4 bottom-3 text-xs text-muted-foreground flex items-center gap-2 bg-background/80 px-3 py-1 rounded-full shadow";
      const dot = document.createElement("span");
      dot.className = "inline-block h-2 w-2 rounded-full bg-primary animate-pulse";
      const text = document.createElement("span");
      text.textContent = label;
      badge.appendChild(dot);
      badge.appendChild(text);
      this.streamingStatusEl = badge;
      this.streamingStatusTextEl = text;
      this.streamingMessageBubbleEl.appendChild(badge);
    } else if (this.streamingStatusTextEl) {
      this.streamingStatusTextEl.textContent = label;
    }
  }

  clearStreamingPendingState() {
    if (this.streamingMessageBubbleEl) {
      this.streamingMessageBubbleEl.classList.remove("opacity-80");
    }
    if (this.streamingStatusEl && this.streamingStatusEl.parentNode) {
      this.streamingStatusEl.parentNode.removeChild(this.streamingStatusEl);
    }
    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
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
      if (data?.session?.session_token) {
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

    const applyInlineFormatting = (value = "") => {
      let output = escapeHtml(value);
      output = output.replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        (_, label, url) =>
          `<a href="${url}" target="_blank" rel="nofollow noopener noreferrer" class="text-primary underline">${label}</a>`
      );
      output = output.replace(/`([^`]+)`/g, '<code class="bg-muted/60 px-1 py-0.5 rounded text-xs font-mono">$1</code>');
      output = output.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      output = output.replace(/__(.+?)__/g, "<strong>$1</strong>");
      output = output.replace(/(\*|_)([^*_]+)\1/g, "<em>$2</em>");
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
