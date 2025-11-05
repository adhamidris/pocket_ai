class ChatStream {
  constructor({ sessionToken, endpoints, elements }) {
    this.sessionToken = sessionToken;
    this.endpoints = endpoints;
    this.elements = elements;
    this.eventSource = null;
    this.awaitingReply = false;
    this.streamController = null;
  }

  init() {
    this.bindSendForm();
    this.bindStopButton();
    this.bindCsatForm();
    this.connectEventStream();
  }

  bindSendForm() {
    const form = this.elements.sendForm;
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const message = (data.get("message") || "").toString().trim();
      if (!message) return;
      this.sendMessage(message);
      form.reset();
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
    });
  }

  bindCsatForm() {
    const form = this.elements.csatForm;
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const score = Number(data.get("score"));
      const comment = (data.get("comment") || "").toString();
      this.submitCsat({ score, comment });
    });
  }

  async submitCsat({ score, comment }) {
    try {
      const response = await fetch(this.endpoints.csat, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          session_token: this.sessionToken,
          score,
          comment,
        }),
      });
      if (!response.ok) throw new Error("Failed to submit feedback");
      this.showToast("Thanks for your feedback", "Your rating has been recorded.");
      this.elements.csatContainer?.classList.add("hidden");
    } catch (error) {
      this.showToast("Submission failed", error.message || "Could not submit feedback.", true);
    }
  }

  appendMessage({ author, body, sentAt }) {
    const container = this.elements.messages;
    if (!container) return;
    const wrapper = document.createElement("div");
    wrapper.className = "flex gap-3 items-start";
    wrapper.innerHTML = `
      <div class="h-9 w-9 rounded-full bg-primary/15 text-primary flex items-center justify-center font-semibold">
        ${author.initials}
      </div>
      <div class="flex-1 rounded-2xl bg-muted/60 px-4 py-3 text-sm text-foreground shadow-soft">
        <p class="font-medium text-sm text-muted-foreground mb-1">${author.name}</p>
        <p>${body}</p>
        <p class="mt-2 text-xs text-muted-foreground">${sentAt}</p>
      </div>
    `;
    container.appendChild(wrapper);
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }

  async sendMessage(message) {
    try {
      this.awaitingReply = true;
      if (this.elements.stopButton) {
        this.elements.stopButton.disabled = false;
      }
      const controller = new AbortController();
      this.streamController = controller;

      const response = await fetch(this.endpoints.streamSend, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
          "X-Requested-With": "XMLHttpRequest",
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

      const processStream = async () => {
        const { value, done } = await reader.read();
        if (done) {
          this.awaitingReply = false;
          if (this.elements.stopButton) {
            this.elements.stopButton.disabled = true;
          }
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
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
        await processStream();
      };

      await processStream();
    } catch (error) {
      if (error.name === "AbortError") return;
      this.showToast("Send failed", error.message || "Message could not be delivered.", true);
    }
  }

  handleStreamEvent(eventType, data) {
    if (!data) return;
    if (eventType === "delta") {
      this.elements.typingIndicator?.classList.remove("hidden");
    } else if (eventType === "final") {
      try {
        const payload = JSON.parse(data);
        if (payload && payload.text) {
          this.appendMessage({
            author: { initials: "AI", name: "Pocket AI" },
            body: payload.text,
            sentAt: new Date().toLocaleTimeString(),
          });
        }
      } catch (error) {
        console.warn("Failed to parse final payload", error);
      } finally {
        this.elements.typingIndicator?.classList.add("hidden");
      }
    } else if (eventType === "error") {
      this.showToast("Stream error", data, true);
    }
  }

  connectEventStream() {
    if (!this.endpoints.events) return;
    const url = new URL(this.endpoints.events, window.location.origin);
    url.searchParams.set("session_token", this.sessionToken);
    this.eventSource = new EventSource(url.toString());
    this.eventSource.addEventListener("statusChanged", (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data && data.status) {
          const badge = this.elements.statusBadge;
          if (badge) {
            badge.textContent = data.status;
          }
        }
      } catch (error) {
        console.warn("Failed to parse status event", error);
      }
    });
  }

  async submitPortalMessage(body) {
    await fetch(this.endpoints.messages, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        session_token: this.sessionToken,
        body,
      }),
    });
  }

  showToast(title, description, destructive = false) {
    const root = this.elements.toastRoot;
    if (!root) return;
    const panel = document.createElement("div");
    panel.className = `pointer-events-auto rounded-xl border px-4 py-3 shadow-lg backdrop-blur ${
      destructive ? "border-destructive bg-destructive text-destructive-foreground" : "border-border bg-card text-foreground"
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
  const sessionToken = container.getAttribute("data-session-token");
  if (!sessionToken) return;
  const endpoints = {
    messages: container.getAttribute("data-endpoint-messages"),
    streamSend: container.getAttribute("data-endpoint-stream-send"),
    events: container.getAttribute("data-endpoint-events"),
    csat: container.getAttribute("data-endpoint-csat"),
  };
  const elements = {
    messages: container.querySelector("[data-chat-messages]"),
    sendForm: container.querySelector("[data-chat-send-form]"),
    stopButton: container.querySelector("[data-chat-stop]"),
    typingIndicator: container.querySelector("[data-chat-typing]"),
    csatForm: container.querySelector("[data-chat-csat-form]"),
    csatContainer: container.querySelector("[data-chat-csat]"),
    toastRoot: document.getElementById("toast-root"),
    statusBadge: container.querySelector("[data-chat-status]"),
  };
  const stream = new ChatStream({ sessionToken, endpoints, elements });
  stream.init();
});
