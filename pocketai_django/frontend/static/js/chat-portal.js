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
      fileUpload: container.getAttribute("data-endpoint-file-upload"),
      fileDownloadUrlTemplate: container.getAttribute("data-endpoint-file-download-url-template"),
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
      fileInput: container.querySelector("[data-chat-file-input]"),
      uploadButton: container.querySelector("[data-chat-upload-button]"),
    };
	    // Session management state
	    this.sessionTokens = [];
	    this.currentSessionToken = null;
	    this.sessionStorageKey = `chat_sessions_${this.businessSlug}_${this.agentSlug}`;
	    this.toolsVisibilityKey = `chat_tools_visible_${this.businessSlug}_${this.agentSlug}`;
	    this.globalToolsVisible = this.readGlobalToolsPreference();
	    this.streamingMessageNode = null;
	    this.streamingMessageBodyEl = null;
	    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
    this.streamingStatusDotEl = null;
    this.streamingMessageId = null;
	    this.streamingBlocksEl = null;
	    // Canonical block streaming state (block_id -> DOM + buffers)
	    this.usingBlockStream = false;
	    this.streamingContentBlockEls = new Map();
	    this.streamingPendingBlockOps = new Map();
	    this.streamingTextBlockActiveIds = new Set();
	    this.streamingToolBlockActiveIds = new Set();
	    this.streamingDirtyTextBlocks = new Set();
	    this.streamingBlockRenderRaf = null;
	    this.spinnerDesiredText = "";
	    this.spinnerDesiredPending = false;
	    this.spinnerDesiredIsError = false;
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
    this.downloadFrame = null;
    // Session empty state tracking
    this.currentSessionHasMessages = false;
    this.sessionCreationInProgress = false;
    this.sessionLoadId = 0;
    this.sessionLoadInProgress = false;
    this.sessionSummaries = [];
    this.pendingSessionTitles = {};
    // Streaming UX helpers
    this.scrollToBottomRaf = null;
    this.scrollToBottomBehavior = "auto";
    this.streamingIdleStatusTimer = null;
    this.streamingIdleStatusDelayMs = 120;
    this.streamingSilenceStatusDelayMs = 450;
    this.lastStreamEventAt = 0;
    this.lastTextDeltaAt = 0;
    this.textDeltaIntervalEma = 0;
  }

  async init() {
    await this.waitForDependencies();
    this.configureMarked();
    this.bindSendForm();
    this.bindFileUpload();
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
	      this.ensureGlobalToolsToggle();
	      this.updateAllToolsVisibility();
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
	      const row = el.closest(".message-row");
	      const isCustomer = Boolean(row && row.classList.contains("flex-row-reverse"));
	      const messageId = el.dataset.messageId;
	      
	      // Add relative and group classes for AI messages only.
	      if (!isCustomer) {
	        el.classList.add("relative", "group", "pr-8");
	      }
	
	      if (!messageId) {
	        // No message ID means no markdown to render, just add copy button
	        if (!isCustomer && !el.querySelector('button[data-copy-btn]')) {
	          this.injectCopyButton(el);
        }
        return;
      }
      
      // Find the corresponding JSON script tag for markdown rendering
      const scriptTag = document.getElementById(messageId);
      if (scriptTag) {
        try {
          const rawPayload = JSON.parse(scriptTag.textContent);
          const payloadObj = rawPayload && typeof rawPayload === "object" ? rawPayload : null;
          const contentBlocks =
            payloadObj && Array.isArray(payloadObj.content_blocks)
              ? payloadObj.content_blocks
              : payloadObj && Array.isArray(payloadObj.contentBlocks)
              ? payloadObj.contentBlocks
              : [];
          const bodyText =
            typeof rawPayload === "string"
              ? rawPayload
              : payloadObj && typeof payloadObj.body === "string"
              ? payloadObj.body
              : "";

          // Prefer canonical block rendering when available.
          if (Array.isArray(contentBlocks) && contentBlocks.length) {
            this.renderMessageContentBlocks(el, contentBlocks);
          } else if (!isCustomer && bodyText) {
            const blocks = this.coerceContentBlocks([], bodyText);
            this.renderMessageContentBlocks(el, blocks);
          }

          // Inject copy button ONLY after content is set
          if (!isCustomer) {
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
                 const parsed = JSON.parse(scriptTag.textContent);
                 if (typeof parsed === "string") {
                   textToCopy = parsed;
                 } else if (parsed && typeof parsed === "object") {
                   const blocks =
                     Array.isArray(parsed.content_blocks)
                       ? parsed.content_blocks
                       : Array.isArray(parsed.contentBlocks)
                       ? parsed.contentBlocks
                       : [];
                   textToCopy = this.extractPlainTextFromContentBlocks(blocks) || (parsed.body || "");
                 }
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

  bindFileUpload() {
    const input = this.elements.fileInput;
    const button = this.elements.uploadButton;
    if (!input || !button) return;
    if (!this.endpoints.fileUpload) return;

    button.addEventListener("click", () => {
      if (this.workflowLocked) return;
      input.click();
    });

    input.addEventListener("change", async () => {
      if (!this.sessionToken) {
        this.showToast("Upload failed", "Session is still initialising.", true);
        input.value = "";
        return;
      }
      const file = input.files && input.files[0] ? input.files[0] : null;
      if (!file) return;

      // Reset immediately so selecting the same file twice triggers change.
      input.value = "";

      const name = (file.name || "").toLowerCase();
      const type = (file.type || "").toLowerCase();
      if (!(name.endsWith(".pdf") || type.includes("pdf"))) {
        this.showToast("Unsupported file", "Only PDF uploads are supported right now.", true);
        return;
      }

      const form = new FormData();
      form.append("session_token", this.sessionToken);
      form.append("file", file);

      try {
        this.showToast("Uploading…", file.name || "PDF");
        const response = await fetch(this.endpoints.fileUpload, {
          method: "POST",
          body: form,
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          const msg = data && data.error && data.error.message ? data.error.message : "Upload failed.";
          throw new Error(msg);
        }

        const message = data && data.message ? data.message : null;
        if (message) {
          this.appendMessage(message);
          if (!this.currentSessionHasMessages) {
            this.currentSessionHasMessages = true;
            this.updateSessionEmptyState(1);
          }
        }
      } catch (error) {
        this.showToast("Upload failed", error.message || "Could not upload file.", true);
      }
    });
  }

  ensureDownloadFrame() {
    if (this.downloadFrame && this.downloadFrame.parentNode) return;
    const iframe = document.createElement("iframe");
    iframe.setAttribute("aria-hidden", "true");
    iframe.tabIndex = -1;
    iframe.style.position = "absolute";
    iframe.style.width = "1px";
    iframe.style.height = "1px";
    iframe.style.left = "-9999px";
    iframe.style.top = "0";
    iframe.style.opacity = "0";
    iframe.style.pointerEvents = "none";
    document.body.appendChild(iframe);
    this.downloadFrame = iframe;
  }

  triggerDownload(downloadUrl) {
    const url = (downloadUrl || "").toString().trim();
    if (!url) return;
    const lower = url.toLowerCase();
    if (lower.startsWith("javascript:") || lower.startsWith("data:")) return;
    this.ensureDownloadFrame();
    try {
      // Reset first so repeated downloads of the same URL still trigger.
      this.downloadFrame.src = "about:blank";
    } catch (_err) {
      // ignore
    }
    this.downloadFrame.src = url;
  }

  buildFileDownloadUrlEndpoint(fileId) {
    const token = (fileId || "").toString().trim();
    if (!token) return "";
    const template = (this.endpoints.fileDownloadUrlTemplate || "").toString().trim();
    if (template && template.includes("{file_id}")) {
      return template.replace("{file_id}", encodeURIComponent(token));
    }
    return `/api/chat/portal/files/${encodeURIComponent(token)}/download-url/`;
  }

  async downloadConversationFile(fileId, filename) {
    const token = (fileId || "").toString().trim();
    if (!token) return;
    if (!this.sessionToken) {
      this.showToast("Download unavailable", "Session token missing.", true);
      return;
    }
    const endpoint = this.buildFileDownloadUrlEndpoint(token);
    if (!endpoint) return;

    try {
      const url = new URL(endpoint, window.location.origin);
      url.searchParams.set("session_token", this.sessionToken);

      const response = await fetch(url.toString(), {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const msg = payload && payload.error && payload.error.message ? payload.error.message : "Could not fetch download link.";
        throw new Error(msg);
      }
      const downloadUrl = payload && payload.download_url ? payload.download_url : "";
      if (!downloadUrl) {
        throw new Error("Missing download_url.");
      }
      this.showToast("Downloading…", filename || "File");
      this.triggerDownload(downloadUrl);
    } catch (error) {
      this.showToast("Download failed", error.message || "Could not download file.", true);
    }
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
		    const storedToken = this.getStoredToken();
		    const serverToken = this.sessionToken || preloadedToken || null;
		    const resolvedToken = options.sessionToken || storedToken || serverToken;
		    const tokenMismatch = Boolean(resolvedToken && serverToken && resolvedToken !== serverToken);
		    const shouldForceRender =
		      Boolean(options.forceRender) ||
		      tokenMismatch;
		    const payload = {
		      business_slug: this.businessSlug,
		      agent_slug: this.agentSlug,
		      session_token: resolvedToken,
		      metadata: this.buildVisitorMetadata(),
		    };

		    // If the server rendered a different session than what we plan to load, clear
		    // the transcript immediately to avoid UI "merging" between sessions while the
		    // client bootstrap fetch is in flight.
		    if (tokenMismatch) {
		      const container = this.elements.messagesInner || this.elements.messages;
		      if (container && container.children.length > 0) {
		        this.setConversationLayout(true);
		        this.renderSkeleton();
		      }
		    }
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
	    if (container && (container.children.length === 0 || shouldForceRender)) {
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
	    // Instant feedback before the first SSE event arrives.
	    this.setSpinnerText("", { pending: true });
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
    this.lastStreamEventAt = Date.now();
    if (this.sessionLoadInProgress) {
      return;
    }

    if (eventType === "block_start") {
      this.usingBlockStream = true;
      this.handleBlockStartEvent(data);
      return;
    }
    if (eventType === "block_delta") {
      this.usingBlockStream = true;
      this.handleBlockDeltaEvent(data);
      return;
    }
    if (eventType === "block_end") {
      this.usingBlockStream = true;
      this.handleBlockEndEvent(data);
      return;
    }
    if (eventType === "block_tool_use") {
      this.usingBlockStream = true;
      this.handleBlockToolUseEvent(data);
      return;
    }
    if (eventType === "block_tool_result") {
      this.usingBlockStream = true;
      this.handleBlockToolResultEvent(data);
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

          if (state === "planning_actions") {
            // Keep this internal; do not surface to the visitor.
            return;
          }
          void label;
        }
      } catch (_err) {
        // ignore malformed status payloads
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
    if (eventType === "error" && data) {
      this.showToast("Stream error", data, true);
    }
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
	    let text = payload.text || "";
      const normalized = (text || "").toString().trim().toLowerCase();
      if (normalized === "thinking…" || normalized === "thinking..." || normalized === "thinking") {
        text = "";
      }
	    const pending = payload.pending !== false;
      if (text) {
        this.clearStreamingIdleStatusTimer();
      }
      const force = Boolean(text) || this.streamingTextBlockActiveIds.size === 0;
	    this.setSpinnerText(text, { pending, force });
	  }

	  handleBlockStartEvent(data) {
	    let payload = null;
	    try {
	      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse block_start payload", error);
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const block = payload.block && typeof payload.block === "object" ? payload.block : null;
    if (!block) return;
    const messageId = (payload.message_id || payload.messageId || "").toString().trim() || null;
	    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);
	    this.upsertStreamingContentBlock(block);
	    const blockType = (block.type || "").toString().trim().toLowerCase();
	    const blockId = (block.block_id || block.blockId || "").toString().trim();
	    if (blockId && this.isStreamingTextBlock(blockType)) {
	      this.streamingTextBlockActiveIds.add(blockId);
        // Keep the loader visible until we actually receive text deltas; otherwise
        // the UI can go silent between block_start and the first block_delta.
        this.repositionStreamingStatusRow();
	    }
	  }

		  handleBlockDeltaEvent(data) {
		    let payload = null;
		    try {
		      payload = data ? JSON.parse(data) : null;
	    } catch (error) {
      console.warn("Failed to parse block_delta payload", error);
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const blockId = (payload.block_id || payload.blockId || "").toString().trim();
    const ops = Array.isArray(payload.ops) ? payload.ops : [];
    if (!blockId || !ops.length) return;

      const now = Date.now();
      if (this.lastTextDeltaAt) {
        const interval = now - this.lastTextDeltaAt;
        if (interval > 0) {
          this.textDeltaIntervalEma = this.textDeltaIntervalEma
            ? this.textDeltaIntervalEma * 0.85 + interval * 0.15
            : interval;
        }
      }
      this.lastTextDeltaAt = now;
	    const messageId = (payload.message_id || payload.messageId || "").toString().trim() || null;
	    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);

	    this.streamingTextBlockActiveIds.add(blockId);
		    if (this.streamingStatusEl) {
		      this.streamingStatusEl.classList.add("hidden");
		    }

	    const pending = this.streamingPendingBlockOps.get(blockId) || [];
	    pending.push(...ops);
	    this.streamingPendingBlockOps.set(blockId, pending);
	    this.streamingDirtyTextBlocks.add(blockId);
	    this.scheduleStreamingBlockRender();
	    this.scheduleStreamingIdleStatusReveal();
	    this.scheduleScrollToBottom({ behavior: "auto" });
	  }

	  handleBlockEndEvent(data) {
	    let payload = null;
	    try {
	      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse block_end payload", error);
      return;
    }
	    if (!payload || typeof payload !== "object") return;
	    const blockId = (payload.block_id || payload.blockId || "").toString().trim();
	    if (!blockId) return;

	    const pendingOps = this.streamingPendingBlockOps.get(blockId);
	    if (pendingOps && pendingOps.length) {
	      this.streamingPendingBlockOps.delete(blockId);
	      this.streamingDirtyTextBlocks.delete(blockId);
	      this.applyBlockOps(blockId, pendingOps);
	    }
	    this.streamingTextBlockActiveIds.delete(blockId);

	    if (this.streamingTextBlockActiveIds.size === 0) {
	      this.scheduleStreamingIdleStatusReveal();
	    }
		  }

  handleBlockToolUseEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse block_tool_use payload", error);
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const block = payload.block && typeof payload.block === "object" ? payload.block : null;
    if (!block) return;
    this.clearStreamingIdleStatusTimer();
    const messageId = (payload.message_id || payload.messageId || "").toString().trim() || null;
    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);
    const blockId = (block.block_id || block.blockId || "").toString().trim();
    const blockPayload = block.payload && typeof block.payload === "object" ? block.payload : {};
    const phase = (blockPayload.phase || "").toString().trim().toLowerCase();
    const status = (blockPayload.status || "").toString().trim().toLowerCase();
    if (blockId && (phase === "started" || phase === "approval_requested" || status === "running" || status === "pending_approval" || status === "pending")) {
      this.streamingToolBlockActiveIds.add(blockId);
    }
    this.upsertStreamingContentBlock(block);
    this.repositionStreamingStatusRow();
    this.scheduleScrollToBottom({ behavior: "auto" });
  }

  handleBlockToolResultEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse block_tool_result payload", error);
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const block = payload.block && typeof payload.block === "object" ? payload.block : null;
    if (!block) return;
    this.clearStreamingIdleStatusTimer();
    const messageId = (payload.message_id || payload.messageId || "").toString().trim() || null;
    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);
    const blockId = (block.block_id || block.blockId || "").toString().trim();
    const blockPayload = block.payload && typeof block.payload === "object" ? block.payload : {};
    const phase = (blockPayload.phase || "").toString().trim().toLowerCase();
    const status = (blockPayload.status || "").toString().trim().toLowerCase();
    if (blockId) {
      if (phase === "finished") {
        this.streamingToolBlockActiveIds.delete(blockId);
      } else if (phase === "approval_resolved") {
        if (status && status !== "approved") {
          this.streamingToolBlockActiveIds.delete(blockId);
        }
      }
    }
    this.upsertStreamingContentBlock(block);
    this.repositionStreamingStatusRow();
    this.scheduleScrollToBottom({ behavior: "auto" });
  }

  isStreamingTextBlock(type) {
    return ["paragraph", "heading", "list_item", "code_block", "text"].includes(type);
  }

  applyBlockOps(blockId, ops) {
    const wrapper = this.streamingContentBlockEls.get(blockId);
    if (!wrapper || !Array.isArray(ops)) return;
    ops.forEach((op) => {
      if (!op || typeof op !== "object") return;
      const kind = (op.op || "").toString().trim();
      if (kind === "append_inline") {
        const nodes = Array.isArray(op.nodes) ? op.nodes : op.node ? [op.node] : [];
        if (!nodes.length) return;
        const target =
          wrapper.querySelector("[data-content-block-text]") ||
          (wrapper.dataset && wrapper.dataset.contentBlockText ? wrapper : null);
        if (target) {
          this.appendInlineNodes(target, nodes);
        }
        return;
      }
      if (kind === "append_code") {
        const text = typeof op.text === "string" ? op.text : "";
        if (!text) return;
        const codeEl = wrapper.querySelector("[data-content-block-code]");
        if (codeEl) {
          codeEl.textContent = `${codeEl.textContent || ""}${text}`;
        }
      }
    });
  }

  scheduleStreamingBlockRender() {
    if (this.streamingBlockRenderRaf) return;
    this.streamingBlockRenderRaf = requestAnimationFrame(() => {
      this.streamingBlockRenderRaf = null;
      this.flushStreamingBlockRenders();
    });
  }

	  flushStreamingBlockRenders() {
	    if (!this.streamingDirtyTextBlocks.size) return;
	    const blockIds = Array.from(this.streamingDirtyTextBlocks);
	    this.streamingDirtyTextBlocks.clear();

	    blockIds.forEach((blockId) => {
	      const wrapper = this.streamingContentBlockEls.get(blockId);
	      if (!wrapper) return;
	      const ops = this.streamingPendingBlockOps.get(blockId);
	      if (!ops || !ops.length) return;
	      this.streamingPendingBlockOps.delete(blockId);
	      this.applyBlockOps(blockId, ops);
	    });
	  }

	  upsertStreamingContentBlock(block) {
	    if (!block || typeof block !== "object") return;
	    if (!this.streamingBlocksEl) return;
	    const blockType = (block.type || "").toString().trim().toLowerCase();
	    const blockId = (block.block_id || block.blockId || "").toString().trim();
    if (!blockId) return;

    const existing = this.streamingContentBlockEls.get(blockId);
    if (existing) {
      const payload = block.payload && typeof block.payload === "object" ? block.payload : {};
      if (blockType === "tool_use") {
        this.updateToolEventCard(existing, payload);
      } else if (blockType === "tool_result") {
        this.updateToolEventCard(existing, { ...payload, phase: "finished" });
      }
      if (blockType === "tool_use" || blockType === "tool_result") {
        this.updateInlineToolCardsVisibility(this.streamingMessageNode);
      }
      return;
    }

    const el = this.buildContentBlockElement(block);
    if (!el) return;
    this.streamingContentBlockEls.set(blockId, el);
    const parentId = (block.parent_block_id || block.parentBlockId || "").toString().trim();
    if (parentId && this.streamingContentBlockEls.has(parentId)) {
      const parentEl = this.streamingContentBlockEls.get(parentId);
      const container = parentEl ? parentEl.querySelector("[data-block-container]") || parentEl : null;
      if (container) {
        container.appendChild(el);
      } else {
        this.streamingBlocksEl.appendChild(el);
      }
    } else {
      this.streamingBlocksEl.appendChild(el);
    }
    if (blockType === "tool_use" || blockType === "tool_result") {
      this.updateInlineToolCardsVisibility(this.streamingMessageNode);
    }
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
      this.updateInlineToolCardsVisibility(wrapper);
    });
  }

  updateInlineToolCardsVisibility(wrapper) {
    if (!wrapper) return;
    const cards = wrapper.querySelectorAll("[data-tool-card]");
    if (!cards.length) return;

    const visible = this.globalToolsVisible !== false;
    if (visible) {
      cards.forEach((card) => card.classList.remove("hidden"));
      return;
    }

    cards.forEach((card) => {
      const approvalStatus = (card.dataset.approvalStatus || "").toString().trim().toLowerCase();
      const keep = approvalStatus === "pending" || approvalStatus === "pending_approval";
      card.classList.toggle("hidden", !keep);
    });
  }

		  buildToolEventCard(payload) {
		    const toolName = (payload?.tool_name || payload?.toolName || "").toString().trim().toLowerCase();
		    if (toolName === "mcp_search_tools") {
		      return null;
		    }
		    const eventId = (payload.event_id || payload.eventId || "").toString().trim();
		    if (!eventId) return null;

	    const card = document.createElement("div");
	    card.dataset.toolCard = "true";
	    card.dataset.toolEventId = eventId;
	    card.className = "mcp-tool-row";

    const row = document.createElement("div");
    row.className = "mcp-tool-row-line";

    const rowLeft = document.createElement("div");
    rowLeft.className = "mcp-tool-row-left";

	    const status = document.createElement("span");
	    status.dataset.toolStatus = "true";
	    status.className = "mcp-status pending";

	    const statusIcon = document.createElement("span");
	    statusIcon.dataset.toolStatusIcon = "true";
	    statusIcon.className = "mcp-status-icon";
	    // Always reserve the left-side loader slot to prevent layout shift.
	    statusIcon.innerHTML = this.getOrbitLoaderMarkup();

	    const statusLabel = document.createElement("span");
	    statusLabel.dataset.toolStatusLabel = "true";
	    statusLabel.className = "mcp-status-label";

	    status.appendChild(statusIcon);
	    status.appendChild(statusLabel);

    const title = document.createElement("div");
    title.dataset.toolTitle = "true";
    title.className = "mcp-tool-title";

    const approvalActions = document.createElement("div");
    approvalActions.dataset.toolApprovalActions = "true";
    approvalActions.className = "mcp-tool-approval-actions mcp-approval-inline-actions";
    approvalActions.hidden = true;
    approvalActions.setAttribute("aria-hidden", "true");

    const approveButton = document.createElement("button");
    approveButton.type = "button";
    approveButton.dataset.toolApprovalAction = "approve";
    approveButton.className = "mcp-approval-inline-btn mcp-approval-inline-approve";
    approveButton.textContent = "Approve";

    const denyButton = document.createElement("button");
    denyButton.type = "button";
    denyButton.dataset.toolApprovalAction = "deny";
    denyButton.className = "mcp-approval-inline-btn mcp-approval-inline-deny";
	    denyButton.textContent = "Reject";

	    approvalActions.appendChild(approveButton);
	    approvalActions.appendChild(denyButton);

	    const outcome = document.createElement("span");
	    outcome.dataset.toolOutcomeInline = "true";
	    outcome.className = "mcp-tool-outcome-inline";

	    rowLeft.appendChild(status);
	    rowLeft.appendChild(title);
	    rowLeft.appendChild(approvalActions);
	    rowLeft.appendChild(outcome);

	    row.appendChild(rowLeft);

    // Inline approval (no details panels)
    const approval = document.createElement("div");
    approval.dataset.toolApproval = "true";
    approval.className = "mcp-approval-inline hidden";

    const approvalTitle = document.createElement("div");
    approvalTitle.dataset.toolApprovalTitle = "true";
    approvalTitle.className = "mcp-approval-inline-title";

    const approvalMeta = document.createElement("div");
    approvalMeta.dataset.toolApprovalMeta = "true";
    approvalMeta.className = "mcp-approval-inline-meta";

    approval.appendChild(approvalTitle);
    approval.appendChild(approvalMeta);

    card.appendChild(row);
    card.appendChild(approval);
    this.attachToolCardEvents(card);
    return card;
  }

	  updateToolEventCard(card, payload) {
	    if (!card || !payload) return;
	    const phase = (payload.phase || "").toString().trim().toLowerCase();
	    const statusRaw = (payload.status || "").toString().trim().toLowerCase();
	    const remote = payload.remote && typeof payload.remote === "object" ? payload.remote : null;

    const connectionNameRaw = remote && remote.connection_name ? remote.connection_name.toString() : "";
    const remoteTool = remote && remote.remote_tool ? remote.remote_tool.toString() : "";
    const toolNameFallback = (payload.tool_name || payload.toolName || "").toString().trim();
    const normalizedInternalTool = toolNameFallback.toLowerCase();
    const internalToolLabel = normalizedInternalTool === "mcp_search_tools" ? "Tool discovery" : "";
    const titleEl = card.querySelector("[data-tool-title]");
    const existingTitle = titleEl ? titleEl.textContent : "";
    const displayTool = remoteTool || internalToolLabel || toolNameFallback || "";

    const connectionName = connectionNameRaw.replace(/\s*\(mcp\)\s*$/i, "").trim();
    const displayToolLabel = displayTool ? this.formatStatus(displayTool) : "";
    const titleText =
      connectionName && displayToolLabel
        ? `${connectionName} · ${displayToolLabel}`
        : displayToolLabel || connectionName || existingTitle || "External tool";

    if (titleEl) titleEl.textContent = titleText;

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

    // Expose a stable, minimal state for CSS styling (timeline notch mode)
    const previousToolState = (card.dataset.toolState || "").toString().trim().toLowerCase();
    let toolState = "success";
    if (isRunning) {
      toolState = "running";
    } else if (effectiveStatus === "pending_approval" || effectiveStatus === "pending" || effectiveStatus === "expired") {
      toolState = "pending";
    } else if (effectiveStatus === "denied" || effectiveStatus === "error" || effectiveStatus === "failed" || effectiveStatus === "failure") {
      toolState = "error";
    } else if (effectiveStatus === "blocked" || effectiveStatus === "disabled") {
      toolState = "pending";
    } else {
      toolState = "success";
    }
    card.dataset.toolState = toolState;

    const shouldCelebrateFinish =
      previousToolState === "running" && (toolState === "success" || toolState === "error") && card.dataset.toolJustFinished !== "true";
    if (shouldCelebrateFinish) {
      card.dataset.toolJustFinished = "true";
      if (card._toolFinishTimer) {
        clearTimeout(card._toolFinishTimer);
      }
      card._toolFinishTimer = setTimeout(() => {
        delete card.dataset.toolJustFinished;
        card._toolFinishTimer = null;
      }, 950);
    }

	    const statusEl = card.querySelector("[data-tool-status]");
	    if (statusEl) {
	      const mapped = this.mapToolStatus(effectiveStatus || (isRunning ? "running" : "ok"));
	      statusEl.className = mapped.className; // Use class directly from mapToolStatus
	      statusEl.title = mapped.label || "";
	      statusEl.setAttribute("aria-label", mapped.label || "");

	      const iconEl = statusEl.querySelector("[data-tool-status-icon]");
	      const labelEl = statusEl.querySelector("[data-tool-status-label]");

	      if (labelEl) labelEl.textContent = "";

	      if (iconEl) {
	        // Left slot stays an orbit loader (running animates; finished freezes via CSS).
	        iconEl.classList.add("mcp-status-icon--orbit");
	        if (!iconEl.querySelector(".mcp-orbit-loader")) {
	          iconEl.innerHTML = this.getOrbitLoaderMarkup();
	        }
	      }
	    }

	    const outcomeEl = card.querySelector("[data-tool-outcome-inline]");
	    if (outcomeEl) {
	      const showOutcome = toolState === "success" || toolState === "error";
	      outcomeEl.classList.toggle("is-visible", showOutcome);
	      if (showOutcome) {
	        outcomeEl.innerHTML = toolState === "success" ? this.getToolSuccessIconMarkup() : this.getToolFailureIconMarkup();
	      } else if (outcomeEl.innerHTML) {
	        outcomeEl.innerHTML = "";
	      }
	    }

    const approvalActionsEl = card.querySelector("[data-tool-approval-actions]");
    const showActions = approvalStatus === "pending" || effectiveStatus === "pending_approval";
    if (approvalActionsEl) {
      approvalActionsEl.classList.toggle("is-visible", showActions);
      approvalActionsEl.hidden = !showActions;
      approvalActionsEl.setAttribute("aria-hidden", showActions ? "false" : "true");
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
    const approveBtn = card.querySelector('[data-tool-approval-action="approve"]');
    const denyBtn = card.querySelector('[data-tool-approval-action="deny"]');

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
        }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : "Approval failed.";
        throw new Error(message);
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
      const maybeUrl = typeof value === "string" ? value.trim() : "";
      const lowerUrl = maybeUrl.toLowerCase();
      const isUrlLike =
        Boolean(maybeUrl) &&
        (maybeUrl.startsWith("/") || maybeUrl.startsWith("http://") || maybeUrl.startsWith("https://")) &&
        !lowerUrl.startsWith("javascript:") &&
        !lowerUrl.startsWith("data:");
      if (isUrlLike) {
        const normalizedKey = key.toLowerCase();
        if (normalizedKey === "download_url" || normalizedKey === "downloadurl") {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "portal-file-inline-download";
          button.textContent = "Download";
          button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            this.triggerDownload(maybeUrl);
          });
          valueEl.appendChild(button);
        } else {
          const link = document.createElement("a");
          link.href = maybeUrl;
          link.rel = "noopener";
          link.target = "_blank";
          link.className = "underline underline-offset-2 text-primary hover:opacity-80";
          link.textContent = maybeUrl;
          valueEl.appendChild(link);
        }
      } else {
        valueEl.textContent = this.formatToolPreviewValue(value);
      }

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

  formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let idx = 0;
    let size = bytes;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    const rounded = idx === 0 ? String(Math.round(size)) : size >= 10 ? size.toFixed(1) : size.toFixed(2);
    return `${rounded} ${units[idx]}`;
  }

  mapToolStatus(status) {
    const normalized = (status || "").toString().trim().toLowerCase();
    if (normalized === "pending_approval" || normalized === "pending") {
      return { label: "Pending", className: "mcp-status pending" };
    }
    if (normalized === "approved") {
      return { label: "Approved", className: "mcp-status success" }; // Use success color for approved
    }
    if (normalized === "denied") {
      return { label: "Denied", className: "mcp-status error" };
    }
    if (normalized === "expired") {
      return { label: "Expired", className: "mcp-status pending" };
    }
    if (normalized === "running" || normalized === "started") {
      return { label: "Running", className: "mcp-status running" };
    }
    if (normalized === "ok" || normalized === "success" || normalized === "succeeded") {
      return { label: "Succeeded", className: "mcp-status success" };
    }
    if (normalized === "blocked" || normalized === "disabled") {
      return { label: "Blocked", className: "mcp-status pending" };
    }
    if (normalized === "error" || normalized === "failed" || normalized === "failure") {
      return { label: "Failed", className: "mcp-status error" };
    }
    return {
      label: normalized ? this.formatStatus(normalized) : "Done",
      className: "mcp-status",
    };
  }

  getToolSuccessIconMarkup() {
    return `
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M3.5 8.5l3 3 6-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    `;
  }

  getToolFailureIconMarkup() {
    return `
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M4 4l8 8M12 4L4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    `;
  }

  getToolSuccessIconUrl() {
    return "/static/check.png";
  }

  getToolFailureIconUrl() {
    return "/static/cross.png";
  }

  getOrbitLoaderMarkup() {
    return `
      <span class="mcp-orbit-loader" aria-hidden="true">
        <span class="mcp-orbit-ring mcp-orbit-ring--1" aria-hidden="true"></span>
        <span class="mcp-orbit-ring mcp-orbit-ring--2" aria-hidden="true"></span>
        <span class="mcp-orbit-ring mcp-orbit-ring--3" aria-hidden="true"></span>
      </span>
    `;
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
      const contentBlocks =
        Array.isArray(payload.content_blocks) ? payload.content_blocks : Array.isArray(payload.contentBlocks) ? payload.contentBlocks : [];
      const persistedText = payload.text ? payload.text.toString() : "";

      if (contentBlocks.length) {
        this.ensureStreamingMessageNode(messageId);
        const bodyEl = this.getMessageBodyElement(messageId) || this.streamingMessageBodyEl;
        if (bodyEl) {
          this.renderMessageContentBlocks(bodyEl, contentBlocks);
          this.injectCopyButton(bodyEl);
        }
        if (messageId) {
          const scriptTag = document.getElementById(messageId);
          if (scriptTag && scriptTag.tagName === "SCRIPT") {
            const bodyText = this.extractPlainTextFromContentBlocks(contentBlocks) || persistedText || "";
            scriptTag.textContent = JSON.stringify({ body: bodyText, content_blocks: contentBlocks });
          }
        }
      } else if (persistedText) {
        if (this.streamingMessageNode) {
          this.ensureStreamingMessageNode(messageId);
          this.updateLatestAssistantMessage(persistedText, messageId);
        } else {
          this.updateLatestAssistantMessage(persistedText, messageId);
        }
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
      this.usingBlockStream = false;
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
    const raw = (text || "").toString();
    if (!raw) return "";
    const normalized = this.normalizeMarkdownForDisplay(raw);
    if (typeof marked === 'undefined') {
      // Fallback if marked not loaded
      return normalized.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    const html = marked.parse(normalized);
    if (typeof DOMPurify !== 'undefined') {
      return DOMPurify.sanitize(html, {
        ADD_ATTR: ['target'],
        FORBID_ATTR: ['style'],
      });
    }
    return html;
  }

  normalizeMarkdownForDisplay(text) {
    if (!text) return "";

    const markerIndices = [];
    let inFence = false;
    let inInline = false;

    for (let i = 0; i < text.length; i += 1) {
      if (!inInline && text.startsWith("```", i)) {
        inFence = !inFence;
        i += 2;
        continue;
      }

      const ch = text[i];
      if (!inFence && ch === "`") {
        inInline = !inInline;
        continue;
      }

      if (!inFence && !inInline && text.startsWith("**", i)) {
        markerIndices.push(i);
        i += 1;
      }
    }

    if (markerIndices.length % 2 === 1) {
      const idx = markerIndices[markerIndices.length - 1];
      return text.slice(0, idx) + text.slice(idx + 2);
    }

    return text;
  }

  extractPlainTextFromContentBlocks(blocks) {
    if (!Array.isArray(blocks) || !blocks.length) return "";
    const parts = [];
    blocks.forEach((block) => {
      if (!block || typeof block !== "object") return;
      const type = (block.type || "").toString().trim().toLowerCase();
      const payload = block.payload && typeof block.payload === "object" ? block.payload : null;
      if (!payload) return;
      if (type === "paragraph" || type === "heading" || type === "list_item") {
        const content = Array.isArray(payload.content) ? payload.content : [];
        const text = this.inlineNodesToText(content).trim();
        if (text) {
          const prefix = type === "list_item" ? "- " : "";
          parts.push(`${prefix}${text}`);
        }
        return;
      }
      if (type === "code_block") {
        const code = typeof payload.code === "string" ? payload.code : "";
        if (code.trim()) parts.push(code.trim());
        return;
      }
      if (type === "text") {
        const text = typeof payload.text === "string" ? payload.text : "";
        const cleaned = this.stripInlineResponseBlocks(text).trim();
        if (cleaned) parts.push(cleaned);
        return;
      }
      if (type === "list" || type === "quote") {
        return;
      }
      if (type === "kv") {
        const title = typeof payload.title === "string" ? payload.title.trim() : "";
        const note = typeof payload.note === "string" ? payload.note.trim() : "";
        const entries = Array.isArray(payload.entries) ? payload.entries : [];
        const lines = [];
        if (title) lines.push(title);
        entries.forEach((entry) => {
          if (!entry || typeof entry !== "object") return;
          const key = typeof entry.key === "string" ? entry.key.trim() : "";
          const value = typeof entry.value === "string" ? entry.value.trim() : "";
          if (!key) return;
          lines.push(value ? `${key}: ${value}` : `${key}:`);
        });
        if (note) lines.push(note);
        const joined = lines.join("\n").trim();
        if (joined) parts.push(joined);
        return;
      }
      if (type === "table") {
        const title = typeof payload.title === "string" ? payload.title.trim() : "";
        const note = typeof payload.note === "string" ? payload.note.trim() : "";
        const columns = Array.isArray(payload.columns) ? payload.columns : [];
        const rows = Array.isArray(payload.rows) ? payload.rows : [];
        const headers = columns
          .map((col, idx) => {
            if (typeof col === "string") return col;
            if (!col || typeof col !== "object") return `col_${idx + 1}`;
            const label = col.label || col.title || col.text || col.value;
            return typeof label === "string" && label.trim() ? label.trim() : `col_${idx + 1}`;
          })
          .filter(Boolean);
        const lines = [];
        if (title) lines.push(title);
        if (headers.length) lines.push(headers.join("\t"));
        rows.forEach((row) => {
          const cells = row && typeof row === "object" && Array.isArray(row.cells) ? row.cells : [];
          if (!cells.length) return;
          lines.push(cells.map((cell) => (typeof cell === "string" ? cell : cell == null ? "" : String(cell))).join("\t"));
        });
        if (note) lines.push(note);
        const joined = lines.join("\n").trim();
        if (joined) parts.push(joined);
      }
    });
    return parts.join("\n\n").trim();
  }

  coerceContentBlocks(blocks, fallbackBody) {
    if (Array.isArray(blocks) && blocks.length) {
      return blocks.filter((entry) => entry && typeof entry === "object");
    }
    const body = typeof fallbackBody === "string" ? fallbackBody : fallbackBody == null ? "" : String(fallbackBody);
    const cleaned = this.stripInlineResponseBlocks(body).trim();
    if (!cleaned) return [];
    return [
      {
        block_id: `blk_local_${Math.random().toString(16).slice(2)}`,
        type: "paragraph",
        created_at: new Date().toISOString(),
        payload: { content: [{ text: cleaned }] },
      },
    ];
  }

  renderMessageContentBlocks(messageBodyEl, blocks) {
    if (!messageBodyEl) return;
    const blocksRoot =
      messageBodyEl.querySelector("[data-message-blocks]") ||
      (() => {
        messageBodyEl.innerHTML = "";
        const root = document.createElement("div");
        root.dataset.messageBlocks = "true";
        root.className = "space-y-2";
        messageBodyEl.appendChild(root);
        return root;
      })();
    this.renderContentBlocksInto(blocksRoot, blocks);
  }

  renderContentBlocksInto(containerEl, blocks) {
    if (!containerEl) return;
    containerEl.innerHTML = "";
    if (!Array.isArray(blocks) || !blocks.length) return;
    const blockEls = new Map();
    blocks.forEach((block) => {
      const el = this.buildContentBlockElement(block);
      if (!el) return;
      const blockId = (block.block_id || block.blockId || "").toString().trim();
      const parentId = (block.parent_block_id || block.parentBlockId || "").toString().trim();
      if (parentId && blockEls.has(parentId)) {
        const parentEl = blockEls.get(parentId);
        const container = parentEl ? parentEl.querySelector("[data-block-container]") || parentEl : null;
        if (container) {
          container.appendChild(el);
        } else {
          containerEl.appendChild(el);
        }
      } else {
        containerEl.appendChild(el);
      }
      if (blockId) blockEls.set(blockId, el);
    });
  }

  buildContentBlockElement(block) {
    if (!block || typeof block !== "object") return null;
    const type = (block.type || "").toString().trim().toLowerCase();
    const blockId = (block.block_id || block.blockId || "").toString().trim();
    const payload = block.payload && typeof block.payload === "object" ? block.payload : {};

	    if (type === "paragraph" || type === "heading" || type === "list_item") {
	      let wrapper = null;
	      if (type === "heading") {
	        const level = Number(payload.level) || 3;
	        const tag = level <= 1 ? "h1" : level === 2 ? "h2" : "h3";
	        wrapper = document.createElement(tag);
	        wrapper.className =
	          level <= 1 ? "text-xl font-semibold" : level === 2 ? "text-lg font-semibold" : "text-base font-semibold";
	      } else if (type === "list_item") {
	        wrapper = document.createElement("li");
	        wrapper.className = "leading-relaxed";
	      } else {
	        wrapper = document.createElement("p");
	        wrapper.className = "leading-relaxed";
	      }
	      wrapper.dataset.contentBlock = "true";
	      wrapper.dataset.blockType = type;
	      wrapper.dataset.contentBlockText = "true";
      if (blockId) wrapper.dataset.blockId = blockId;
      const content = Array.isArray(payload.content) ? payload.content : [];
      this.appendInlineNodes(wrapper, content);
      return wrapper;
    }

	    if (type === "list") {
	      const ordered = payload.ordered === true;
	      const wrapper = document.createElement(ordered ? "ol" : "ul");
	      wrapper.dataset.contentBlock = "true";
	      wrapper.dataset.blockType = "list";
	      wrapper.dataset.blockContainer = "true";
	      if (blockId) wrapper.dataset.blockId = blockId;
	      wrapper.className = `${ordered ? "list-decimal" : "list-disc"} pl-6 space-y-1`;
	      const startValue = Number(payload.start);
	      if (ordered && Number.isFinite(startValue) && startValue > 0) {
	        wrapper.start = startValue;
	      }
	      return wrapper;
	    }

	    if (type === "quote") {
	      const wrapper = document.createElement("blockquote");
	      wrapper.dataset.contentBlock = "true";
	      wrapper.dataset.blockType = "quote";
	      wrapper.dataset.blockContainer = "true";
	      if (blockId) wrapper.dataset.blockId = blockId;
	      wrapper.className = "border-l-2 border-border/60 pl-4 text-muted-foreground";
	      return wrapper;
	    }

	    if (type === "code_block") {
	      const wrapper = document.createElement("pre");
	      wrapper.dataset.contentBlock = "true";
	      wrapper.dataset.blockType = "code_block";
	      if (blockId) wrapper.dataset.blockId = blockId;
	      wrapper.className = "rounded-lg bg-muted/40 p-3 overflow-x-auto text-sm";
	      const code = document.createElement("code");
	      code.dataset.contentBlockCode = "true";
	      code.className = "font-mono";
	      const codeText = typeof payload.code === "string" ? payload.code : "";
	      code.textContent = codeText;
	      wrapper.appendChild(code);
	      return wrapper;
	    }

    if (type === "text") {
      const wrapper = document.createElement("p");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "text";
      wrapper.dataset.contentBlockText = "true";
      if (blockId) wrapper.dataset.blockId = blockId;
      const text = typeof payload.text === "string" ? payload.text : "";
      const cleaned = this.stripInlineResponseBlocks(text);
      if (cleaned) {
        this.appendInlineNodes(wrapper, [{ text: cleaned }]);
      }
      return wrapper;
    }

    if (type === "tool_use") {
      const card = this.buildToolEventCard(payload);
      if (!card) return null;
      card.dataset.contentBlock = "true";
      card.dataset.blockType = "tool_use";
      if (blockId) card.dataset.blockId = blockId;
      this.updateToolEventCard(card, payload);
      return card;
    }

    if (type === "tool_result") {
      const basePayload = {
        ...payload,
        phase: "finished",
      };
      const card = this.buildToolEventCard(basePayload);
      if (!card) return null;
      card.dataset.contentBlock = "true";
      card.dataset.blockType = "tool_result";
      if (blockId) card.dataset.blockId = blockId;
      this.updateToolEventCard(card, basePayload);
      return card;
    }

    if (type === "file") {
      const fileId = (payload.file_id || payload.fileId || "").toString().trim();
      const filename = (payload.filename || "").toString().trim() || "file";
      const contentType = (payload.content_type || payload.contentType || "").toString().trim().toLowerCase();
      const kind = (payload.kind || "").toString().trim().toLowerCase();
      const status = (payload.status || "").toString().trim().toLowerCase();
      const labelRaw = (payload.label || "").toString().trim();

      const sizeBytesRaw = payload.size_bytes ?? payload.sizeBytes ?? 0;
      const pageCountRaw = payload.page_count ?? payload.pageCount ?? 0;
      const sizeBytes = Number.isFinite(Number(sizeBytesRaw)) ? Number(sizeBytesRaw) : 0;
      const pageCount = Number.isFinite(Number(pageCountRaw)) ? Number(pageCountRaw) : 0;

      const wrapper = document.createElement("div");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "file";
      wrapper.dataset.fileId = fileId;
      if (blockId) wrapper.dataset.blockId = blockId;
      wrapper.className = "portal-file-card";

      const icon = document.createElement("div");
      icon.className = "portal-file-card__icon";
      icon.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <path d="M14 2v6h6"></path>
          <path d="M8 13h8"></path>
          <path d="M8 17h5"></path>
        </svg>
      `;

      const main = document.createElement("div");
      main.className = "portal-file-card__main";

      const titleRow = document.createElement("div");
      titleRow.className = "portal-file-card__title-row";

      const nameEl = document.createElement("div");
      nameEl.className = "portal-file-card__filename";
      nameEl.textContent = filename;

      const badge = document.createElement("span");
      badge.className = "portal-file-card__badge";
      const label =
        labelRaw ||
        (kind === "upload" ? "Uploaded" : kind === "artifact" ? "Generated" : "") ||
        "";
      badge.textContent = label;
      badge.hidden = !label;

      titleRow.appendChild(nameEl);
      titleRow.appendChild(badge);

      const meta = document.createElement("div");
      meta.className = "portal-file-card__meta";

      const parts = [];
      const isPdf = contentType === "application/pdf" || filename.toLowerCase().endsWith(".pdf");
      parts.push(isPdf ? "PDF" : contentType ? contentType : "File");
      if (pageCount > 0) parts.push(`${pageCount} page${pageCount === 1 ? "" : "s"}`);
      if (sizeBytes > 0) parts.push(this.formatBytes(sizeBytes));
      meta.textContent = parts.join(" • ");

      main.appendChild(titleRow);
      main.appendChild(meta);

      const actions = document.createElement("div");
      actions.className = "portal-file-card__actions";

      const statusEl = document.createElement("div");
      statusEl.className = "portal-file-card__status";
      const statusLabel = status === "ready" ? "Ready" : status === "failed" ? "Failed" : "Processing";
      statusEl.textContent = statusLabel;
      statusEl.dataset.status = status || "";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "portal-file-card__download";
      button.textContent = status === "ready" ? "Download" : status === "failed" ? "Unavailable" : "Preparing…";
      button.disabled = status !== "ready" || !fileId;
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!fileId) return;
        void this.downloadConversationFile(fileId, filename);
      });

      actions.appendChild(statusEl);
      actions.appendChild(button);

      wrapper.appendChild(icon);
      wrapper.appendChild(main);
      wrapper.appendChild(actions);
      return wrapper;
    }

    if (type === "file_text") {
      const fileId = (payload.file_id || payload.fileId || "").toString().trim();
      const filename = (payload.filename || "").toString().trim() || "document.pdf";
      const title = (payload.title || "").toString().trim() || `Extracted text from ${filename}`;
      const text = (payload.text || "").toString();
      const collapsed = payload.collapsed !== false;

      const wrapper = document.createElement("div");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "file_text";
      wrapper.dataset.fileId = fileId;
      if (blockId) wrapper.dataset.blockId = blockId;
      wrapper.className = "portal-file-text";

      const header = document.createElement("div");
      header.className = "portal-file-text__header";

      const headerTitle = document.createElement("div");
      headerTitle.className = "portal-file-text__title";
      headerTitle.textContent = title;

      const headerActions = document.createElement("div");
      headerActions.className = "portal-file-text__actions";

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "portal-file-text__btn";
      copyBtn.textContent = "Copy";
      copyBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
          await navigator.clipboard.writeText(text);
          this.showToast("Copied", "Extracted text copied.");
        } catch (_err) {
          this.showToast("Copy failed", "Could not copy text.", true);
        }
      });

      const downloadBtn = document.createElement("button");
      downloadBtn.type = "button";
      downloadBtn.className = "portal-file-text__btn portal-file-text__btn--download";
      downloadBtn.textContent = "Download PDF";
      downloadBtn.disabled = !fileId;
      downloadBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!fileId) return;
        void this.downloadConversationFile(fileId, filename);
      });

      headerActions.appendChild(copyBtn);
      headerActions.appendChild(downloadBtn);

      header.appendChild(headerTitle);
      header.appendChild(headerActions);

      const details = document.createElement("details");
      details.className = "portal-file-text__details";
      if (!collapsed) {
        details.open = true;
      }

      const summary = document.createElement("summary");
      summary.className = "portal-file-text__summary";
      summary.textContent = collapsed ? "Show extracted text" : "Hide extracted text";
      details.addEventListener("toggle", () => {
        summary.textContent = details.open ? "Hide extracted text" : "Show extracted text";
      });

      const pre = document.createElement("pre");
      pre.className = "portal-file-text__content";
      pre.textContent = text;

      details.appendChild(summary);
      details.appendChild(pre);

      wrapper.appendChild(header);
      wrapper.appendChild(details);
      return wrapper;
    }

    if (type === "table") {
      const columns = Array.isArray(payload.columns) ? payload.columns : [];
      const rows = Array.isArray(payload.rows) ? payload.rows : [];
      if (!columns.length) return null;

      const wrapper = document.createElement("div");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "table";
      if (blockId) wrapper.dataset.blockId = blockId;
      wrapper.className = "rounded-xl border border-border/60 overflow-hidden bg-background/80 shadow-sm";

      const titleText = typeof payload.title === "string" ? payload.title.trim() : "";
      if (titleText) {
        const title = document.createElement("div");
        title.className = "px-4 py-2 border-b border-border/60 text-sm font-semibold text-foreground";
        title.textContent = titleText;
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
        th.textContent = (col.label || "").toString();
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
      rows.forEach((row, rowIndex) => {
        if (!row || typeof row !== "object") return;
        const cells = Array.isArray(row.cells) ? row.cells : [];
        if (!cells.length) return;
        const tr = document.createElement("tr");
        tr.className = rowIndex % 2 === 0 ? "bg-background" : "bg-muted/20";
        columnMeta.forEach((col, colIndex) => {
          const td = document.createElement("td");
          td.className = "px-3 py-2 align-top text-foreground/90";
          if (col.align === "center") {
            td.classList.add("text-center");
          } else if (col.align === "right") {
            td.classList.add("text-right");
          }
          const cellValue = colIndex < cells.length ? cells[colIndex] : "";
          td.innerHTML = this.renderPlainText(typeof cellValue === "string" ? cellValue : cellValue == null ? "" : String(cellValue));
          tr.appendChild(td);
        });
        if (row.rtl) {
          tr.dir = "rtl";
          tr.classList.add("text-right");
        }
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrapper.appendChild(table);

      const noteText = typeof payload.note === "string" ? payload.note.trim() : "";
      if (noteText) {
        const note = document.createElement("p");
        note.className = "px-4 py-2 text-xs text-muted-foreground border-t border-border/40";
        note.textContent = noteText;
        wrapper.appendChild(note);
      }

      if (payload.rtl) {
        wrapper.dir = "rtl";
        wrapper.classList.add("text-right");
      }
      return wrapper;
    }

    if (type === "kv") {
      const entries = Array.isArray(payload.entries) ? payload.entries : [];
      if (!entries.length) return null;

      const wrapper = document.createElement("div");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "kv";
      if (blockId) wrapper.dataset.blockId = blockId;
      wrapper.className = "rounded-xl border border-border/60 overflow-hidden bg-background/80 shadow-sm";

      const titleText = typeof payload.title === "string" ? payload.title.trim() : "";
      if (titleText) {
        const title = document.createElement("div");
        title.className = "px-4 py-2 border-b border-border/60 text-sm font-semibold text-foreground";
        title.textContent = titleText;
        wrapper.appendChild(title);
      }

      const table = document.createElement("table");
      table.className = "w-full border-collapse text-sm";
      const tbody = document.createElement("tbody");
      entries.forEach((entry, idx) => {
        if (!entry || typeof entry !== "object") return;
        const key = typeof entry.key === "string" ? entry.key.trim() : "";
        if (!key) return;
        const value = typeof entry.value === "string" ? entry.value : entry.value == null ? "" : String(entry.value);

        const tr = document.createElement("tr");
        tr.className = idx % 2 === 0 ? "bg-background" : "bg-muted/20";

        const th = document.createElement("th");
        th.className = "px-3 py-2 align-top text-left text-[10px] font-semibold uppercase tracking-wide text-muted-foreground w-24";
        th.textContent = key;

        const td = document.createElement("td");
        td.className = "px-3 py-2 align-top text-foreground/90";
        td.innerHTML = this.renderPlainText(value);

        tr.appendChild(th);
        tr.appendChild(td);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrapper.appendChild(table);

      const noteText = typeof payload.note === "string" ? payload.note.trim() : "";
      if (noteText) {
        const note = document.createElement("p");
        note.className = "px-4 py-2 text-xs text-muted-foreground border-t border-border/40";
        note.textContent = noteText;
        wrapper.appendChild(note);
      }

      if (payload.rtl) {
        wrapper.dir = "rtl";
        wrapper.classList.add("text-right");
      }
      return wrapper;
    }

    return null;
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

	    this.updateInlineToolCardsVisibility(node);

	    if (message.metadata) {
	      this.updateMessageMetadata(message.id, message.metadata);
	    }
	    if (scroller) {
      this.scheduleScrollToBottom({ behavior: "smooth", force: true });
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
    const contentBlocks =
      Array.isArray(raw.content_blocks) ? raw.content_blocks : Array.isArray(raw.contentBlocks) ? raw.contentBlocks : [];
    return {
      id: raw.id || null,
      sender,
      body: raw.body || "",
      contentBlocks,
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
    body.dataset.messageBubble = "true";

    if (isCustomer) {
      body.className = "text-base leading-relaxed bg-muted text-foreground px-5 py-3 rounded-2xl rounded-tr-sm text-start inline-block shadow-sm";
      body.dataset.messageBody = "true";
      if (message.id) {
        body.dataset.messageId = message.id;
      }
      const explicitBlocks = Array.isArray(message.contentBlocks) ? message.contentBlocks : [];
      if (explicitBlocks.length) {
        const blocksRoot = document.createElement("div");
        blocksRoot.dataset.messageBlocks = "true";
        blocksRoot.className = "space-y-2";
        body.appendChild(blocksRoot);
        const blocks = this.coerceContentBlocks(explicitBlocks, message.body);
        this.renderContentBlocksInto(blocksRoot, blocks);
      } else {
        const cleanBody = this.stripInlineResponseBlocks(message.body || "");
        body.innerHTML = this.renderMarkdown(cleanBody);
      }
    } else {
      body.dataset.messageBody = "true";
      if (message.id) {
        body.dataset.messageId = message.id;
      }
      body.className = "relative group text-base leading-relaxed text-foreground text-start max-w-none break-words pr-8";
      const blocksRoot = document.createElement("div");
      blocksRoot.dataset.messageBlocks = "true";
      blocksRoot.className = "space-y-2";
      body.appendChild(blocksRoot);

      const blocks = this.coerceContentBlocks(message.contentBlocks, message.body);
      if (blocks.length) {
        this.renderContentBlocksInto(blocksRoot, blocks);
      }
    }
    content.appendChild(body);

    // Match server-side template: keep the raw markdown available in a json_script tag (id=message.id).
    // This powers deterministic tool-card interleaving + copy-to-clipboard on client-rendered transcripts.
    if (!isCustomer && message.id) {
      const rawBody = typeof message.body === "string" ? message.body : message.body == null ? "" : String(message.body);
      const renderPayload = {
        body: rawBody,
        content_blocks: Array.isArray(message.contentBlocks) ? message.contentBlocks : [],
      };
      const scriptTag = document.createElement("script");
      scriptTag.type = "application/json";
      scriptTag.id = message.id;
      scriptTag.textContent = JSON.stringify(renderPayload);
      content.appendChild(scriptTag);
    }

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

  isNearBottom(scroller, thresholdPx = 120) {
    if (!scroller) return true;
    const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    return distance <= thresholdPx;
  }

  scheduleScrollToBottom({ behavior = "auto", force = false } = {}) {
    const scroller = this.elements.messages;
    if (!scroller) return;
    if (!force && !this.isNearBottom(scroller)) return;
    this.scrollToBottomBehavior = behavior || "auto";
    if (this.scrollToBottomRaf) return;
    this.scrollToBottomRaf = requestAnimationFrame(() => {
      this.scrollToBottomRaf = null;
      const scrollBehavior = this.scrollToBottomBehavior || "auto";
      try {
        scroller.scrollTo({ top: scroller.scrollHeight, behavior: scrollBehavior });
      } catch (_err) {
        scroller.scrollTop = scroller.scrollHeight;
      }
    });
  }

  renderPlainText(text) {
    if (!text) return "";
    const escaped = this.escapeHtml((text || "").toString());
    return escaped.replace(/\n/g, "<br>");
  }

	  appendInlineNodes(target, nodes) {
	    if (!target || !Array.isArray(nodes) || !nodes.length) return;

	    const matchesWrapper = (el, mark) => {
	      if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
	      if (typeof mark === "string") {
	        if (mark === "bold") return el.tagName === "STRONG";
	        if (mark === "italic") return el.tagName === "EM";
	        if (mark === "code") return el.tagName === "CODE";
	        return false;
	      }
	      if (mark && typeof mark === "object" && mark.type === "link") {
	        if (el.tagName !== "A") return false;
	        const expectedHref = mark.href ? mark.href.toString() : "";
	        if (!expectedHref) return true;
	        return el.getAttribute("href") === expectedHref;
	      }
	      return false;
	    };

	    const findMergeTextNode = (candidate, marks) => {
	      if (!candidate) return null;
	      if (!marks || !marks.length) {
	        return candidate.nodeType === Node.TEXT_NODE ? candidate : null;
	      }
	      let current = candidate;
	      for (let idx = marks.length - 1; idx >= 0; idx -= 1) {
	        if (!current || current.nodeType !== Node.ELEMENT_NODE) return null;
	        const mark = marks[idx];
	        const el = current;
	        if (!matchesWrapper(el, mark)) return null;
	        if (!el.firstChild || el.firstChild !== el.lastChild) return null;
	        current = el.firstChild;
	      }
	      return current && current.nodeType === Node.TEXT_NODE ? current : null;
	    };

	    const fragment = document.createDocumentFragment();
	    nodes.forEach((node) => {
	      if (!node || typeof node !== "object") return;
	      const rawText = typeof node.text === "string" ? node.text : "";
	      if (!rawText) return;
	      const marks = Array.isArray(node.marks) ? node.marks : [];

	      const appendPart = (textPart) => {
	        if (!textPart) return;
	        const last = fragment.lastChild || target.lastChild;
	        const mergeTextNode = findMergeTextNode(last, marks);
	        if (mergeTextNode) {
	          mergeTextNode.textContent = `${mergeTextNode.textContent || ""}${textPart}`;
	          return;
	        }
	        let current = document.createTextNode(textPart);
	        marks.forEach((mark) => {
	          let wrapper = null;
	          if (typeof mark === "string") {
	            if (mark === "bold") wrapper = document.createElement("strong");
	            if (mark === "italic") wrapper = document.createElement("em");
	            if (mark === "code") {
	              wrapper = document.createElement("code");
	              wrapper.className = "rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]";
	            }
	          } else if (mark && typeof mark === "object" && mark.type === "link") {
	            wrapper = document.createElement("a");
	            if (mark.href) wrapper.setAttribute("href", mark.href);
	            wrapper.setAttribute("target", "_blank");
	            wrapper.setAttribute("rel", "noopener noreferrer");
	            wrapper.className = "text-primary underline underline-offset-2 hover:text-primary/80";
	          }
	          if (wrapper) {
	            wrapper.appendChild(current);
	            current = wrapper;
	          }
	        });
	        fragment.appendChild(current);
	      };

	      const parts = rawText.split("\n");
	      parts.forEach((part, idx) => {
	        appendPart(part);
	        if (idx < parts.length - 1) {
	          fragment.appendChild(document.createElement("br"));
	        }
	      });
	    });
	    target.appendChild(fragment);
	  }

	  inlineNodesToText(nodes) {
	    if (!Array.isArray(nodes) || !nodes.length) return "";
	    return nodes
	      .map((node) => (node && typeof node.text === "string" ? node.text : ""))
	      .join("");
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
        this.streamingMessageBodyEl.dataset.messageId = messageId;

        const existing = document.getElementById(messageId);
        const metaRow = this.streamingMessageNode.querySelector("[data-message-meta]");
        const contentRoot = this.streamingMessageBodyEl.parentElement;
        if (contentRoot && metaRow && !existing) {
          const scriptTag = document.createElement("script");
          scriptTag.type = "application/json";
          scriptTag.id = messageId;
          scriptTag.textContent = JSON.stringify({ body: "", content_blocks: [] });
          contentRoot.insertBefore(scriptTag, metaRow);
        } else if (existing && existing.tagName === "SCRIPT") {
          // Keep existing render payload; it will be updated on `turnPersisted`.
        }
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

	    if (this.streamingMessageBodyEl) {
	      if (messageId) {
	        this.streamingMessageBodyEl.dataset.messageId = messageId;
	        const existing = document.getElementById(messageId);
	        const metaRow = node.querySelector("[data-message-meta]");
	        const contentRoot = this.streamingMessageBodyEl.parentElement;
	        if (contentRoot && metaRow && !existing) {
	          const scriptTag = document.createElement("script");
	          scriptTag.type = "application/json";
	          scriptTag.id = messageId;
	          scriptTag.textContent = JSON.stringify({ body: "", content_blocks: [] });
	          contentRoot.insertBefore(scriptTag, metaRow);
	        }
	      }
	      this.streamingMessageBodyEl.innerHTML = "";

	      // Canonical content-block container (replaces segment-based interleaving).
      const blocksContainer = document.createElement("div");
      blocksContainer.dataset.messageBlocks = "true";
      blocksContainer.className = "space-y-2";
      this.streamingMessageBodyEl.appendChild(blocksContainer);
      this.streamingBlocksEl = blocksContainer;

      // Streaming status row is part of the content-flow so tools can render beneath it.
      const statusRow = document.createElement("div");
      statusRow.dataset.streamingStatus = "true";
      statusRow.className = "flex items-center gap-2 text-xs text-muted-foreground hidden";
      const statusDot = document.createElement("div");
      statusDot.className = "chat-portal-status-orbit text-primary";
      statusDot.innerHTML = this.getOrbitLoaderMarkup();
      const statusText = document.createElement("span");
      statusText.classList.add("chat-portal-status-shimmer");
      statusText.textContent = "";
      statusRow.appendChild(statusDot);
      statusRow.appendChild(statusText);
      blocksContainer.appendChild(statusRow);
	      this.streamingStatusEl = statusRow;
	      this.streamingStatusTextEl = statusText;
	      this.streamingStatusDotEl = statusDot;
	      this.streamingContentBlockEls.clear();
	      this.streamingPendingBlockOps.clear();
	      this.streamingTextBlockActiveIds.clear();
	      this.streamingToolBlockActiveIds.clear();
	      this.streamingDirtyTextBlocks.clear();
	      this.usingBlockStream = false;
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
    
    const blocksRoot = body.querySelector("[data-message-blocks]");
    if (blocksRoot) {
      const blocks = this.coerceContentBlocks([], clean);
      this.renderContentBlocksInto(blocksRoot, blocks);
    } else {
      body.textContent = clean;
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

    if (Object.prototype.hasOwnProperty.call(metaPayload, "debug_tools") || Object.prototype.hasOwnProperty.call(metaPayload, "debugTools")) {
      const debugTools = metaPayload.debug_tools || metaPayload.debugTools || null;
      this.updateDebugToolsPanel(wrapper, debugTools);
    }
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

  getContextBudgetPayload(payload) {
    if (!payload || typeof payload !== "object") return null;
    const budget = payload.context_budget || payload.contextBudget || null;
    if (!budget || typeof budget !== "object") return null;
    return budget;
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
    wrap.append("Tokens (sum) ", roundSpan, " turn / ", totalSpan, " chat");
    return wrap;
  }

  formatPercentRatio(ratio) {
    if (!Number.isFinite(ratio)) return "—";
    const clamped = Math.max(0, Math.min(1, ratio));
    return `${Math.round(clamped * 100)}%`;
  }

  computePeakUsage(usage) {
    if (!usage || typeof usage !== "object") return null;
    const calls = Array.isArray(usage.calls) ? usage.calls : [];
    const pool = calls.length ? calls : [usage];
    let peakPrompt = null;
    let peakTotal = null;
    pool.forEach((entry) => {
      if (!entry || typeof entry !== "object") return;
      const prompt = Number(entry.prompt_tokens);
      const completion = Number(entry.completion_tokens);
      const totalRaw = Number(entry.total_tokens);
      const total = Number.isFinite(totalRaw) && totalRaw > 0 ? totalRaw : (Number.isFinite(prompt) ? prompt : 0) + (Number.isFinite(completion) ? completion : 0);
      if (Number.isFinite(prompt)) {
        peakPrompt = peakPrompt === null ? prompt : Math.max(peakPrompt, prompt);
      }
      if (Number.isFinite(total)) {
        peakTotal = peakTotal === null ? total : Math.max(peakTotal, total);
      }
    });
    return { peakPrompt, peakTotal };
  }

  buildContextSummarySegment(contextBudget, usage) {
    if (!contextBudget || typeof contextBudget !== "object") return null;
    const maxContext = Number(contextBudget.max_context_tokens);
    const maxInput = Number(contextBudget.max_input_tokens);
    if (!Number.isFinite(maxContext) || maxContext <= 0) return null;

    const peaks = this.computePeakUsage(usage);
    if (!peaks) return null;

    const peakTotal = Number(peaks.peakTotal);
    const peakPrompt = Number(peaks.peakPrompt);

    const wrap = document.createElement("span");
    wrap.className = "inline-flex flex-wrap items-center gap-1";

    const bits = [];
    if (Number.isFinite(peakTotal)) {
      const ctxLeft = Math.max(0, maxContext - peakTotal);
      bits.push(`Ctx left ${this.formatTokenCount(ctxLeft)} (${this.formatPercentRatio(ctxLeft / maxContext)})`);
    }
    if (Number.isFinite(maxInput) && maxInput > 0 && Number.isFinite(peakPrompt)) {
      const inputLeft = Math.max(0, maxInput - peakPrompt);
      bits.push(`Input left ${this.formatTokenCount(inputLeft)} (${this.formatPercentRatio(inputLeft / maxInput)})`);
    }

    wrap.textContent = bits.join(" • ") || "";
    return wrap.textContent ? wrap : null;
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
	    const promptBudget = Array.isArray(payload.prompt_budget) ? payload.prompt_budget : [];
	    const usage = this.getUsagePayload(payload);
	    const contextBudget = this.getContextBudgetPayload(payload);
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
	    if (promptBudget.length) summaryBits.push(`Prompt (${promptBudget.length})`);
	    if (searchHistory.length) summaryBits.push(`Searches (${searchHistory.length})`);
	    if (results.length) summaryBits.push(`Evidence (${results.length})`);
	    if (reads.length) summaryBits.push(`Reads (${reads.length})`);
    const tokenSummary = this.buildTokenSummarySegment(roundTokens, totalTokens);
    if (tokenSummary) summaryBits.push(tokenSummary);
    const contextSummary = this.buildContextSummarySegment(contextBudget, usage);
    if (contextSummary) summaryBits.push(contextSummary);
    summary.innerHTML = "";
    summaryBits.forEach((segment, idx) => {
      if (idx > 0) summary.append(" • ");
      summary.append(segment);
    });
    root.appendChild(summary);

    const container = document.createElement("div");
    container.className = "mt-2 space-y-2";

    const usageCalls = usage && typeof usage === "object" && Array.isArray(usage.calls) ? usage.calls : [];
    if (usage && typeof usage === "object") {
      const usageSection = document.createElement("div");
      const usageHeader = document.createElement("div");
      usageHeader.className = "text-[11px] font-semibold text-muted-foreground/80 uppercase tracking-wide";
      usageHeader.textContent = "LLM Usage";
      usageSection.appendChild(usageHeader);

      const usageBody = document.createElement("div");
      usageBody.className = "mt-1 rounded-md border border-border/40 bg-background/40 px-2 py-1 text-[11px] leading-relaxed text-foreground/80";

      const promptTokens = Number(usage.prompt_tokens);
      const completionTokens = Number(usage.completion_tokens);
      const totalTokensExact = Number(usage.total_tokens);
      const segments = [];
      if (Number.isFinite(totalTokensExact)) segments.push(`total=${this.formatTokenCount(totalTokensExact)}`);
      if (Number.isFinite(promptTokens)) segments.push(`prompt=${this.formatTokenCount(promptTokens)}`);
      if (Number.isFinite(completionTokens)) segments.push(`completion=${this.formatTokenCount(completionTokens)}`);
      if (typeof usage.model === "string" && usage.model.trim()) segments.push(`model=${usage.model.trim()}`);
      if (typeof usage.provider === "string" && usage.provider.trim()) segments.push(`provider=${usage.provider.trim()}`);
      if (contextBudget && typeof contextBudget === "object") {
        const maxContext = Number(contextBudget.max_context_tokens);
        const maxInput = Number(contextBudget.max_input_tokens);
        const reserve = Number(contextBudget.response_token_reserve);
        if (Number.isFinite(maxContext) && maxContext > 0) segments.push(`ctx=${this.formatTokenCount(maxContext)}`);
        if (Number.isFinite(maxInput) && maxInput > 0) segments.push(`max_input=${this.formatTokenCount(maxInput)}`);
        if (Number.isFinite(reserve) && reserve > 0) segments.push(`reserve=${this.formatTokenCount(reserve)}`);
      }
      usageBody.textContent = segments.join(" • ") || "Unavailable";
      usageSection.appendChild(usageBody);

      if (Array.isArray(usageCalls) && usageCalls.length) {
        const callsWrap = document.createElement("div");
        callsWrap.className = "mt-2 space-y-1";
        usageCalls.slice(0, 12).forEach((call, idx) => {
          if (!call || typeof call !== "object") return;
          const row = document.createElement("div");
          row.className = "rounded-md border border-border/30 bg-background/30 px-2 py-1 text-[11px] leading-relaxed text-muted-foreground";

          const stage = typeof call.stage === "string" && call.stage.trim() ? call.stage.trim() : `call_${idx + 1}`;
          const callPrompt = Number(call.prompt_tokens);
          const callCompletion = Number(call.completion_tokens);
          const callTotal = Number(call.total_tokens);
          const bits = [`${idx + 1}. ${stage}`];
          if (Number.isFinite(callTotal)) bits.push(`total=${this.formatTokenCount(callTotal)}`);
          if (Number.isFinite(callPrompt)) bits.push(`prompt=${this.formatTokenCount(callPrompt)}`);
          if (Number.isFinite(callCompletion)) bits.push(`completion=${this.formatTokenCount(callCompletion)}`);
          if (typeof call.model === "string" && call.model.trim()) bits.push(`model=${call.model.trim()}`);
          if (typeof call.provider === "string" && call.provider.trim()) bits.push(`provider=${call.provider.trim()}`);
          if (contextBudget && typeof contextBudget === "object") {
            const maxContext = Number(contextBudget.max_context_tokens);
            const maxInput = Number(contextBudget.max_input_tokens);
            if (Number.isFinite(maxInput) && maxInput > 0 && Number.isFinite(callPrompt)) {
              const inputLeft = Math.max(0, maxInput - callPrompt);
              bits.push(`input_left=${this.formatTokenCount(inputLeft)} (${this.formatPercentRatio(inputLeft / maxInput)})`);
            }
            if (Number.isFinite(maxContext) && maxContext > 0 && Number.isFinite(callTotal)) {
              const ctxLeft = Math.max(0, maxContext - callTotal);
              bits.push(`ctx_left=${this.formatTokenCount(ctxLeft)} (${this.formatPercentRatio(ctxLeft / maxContext)})`);
            }
          }
          row.textContent = bits.join(" • ");
          callsWrap.appendChild(row);
        });
        usageSection.appendChild(callsWrap);
      }

      container.appendChild(usageSection);
    }

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

	    if (!toolTrace.length && !promptBudget.length && !searchHistory.length && !results.length && !reads.length && !coverage.length && !tableRows.length) {
	      const empty = document.createElement("div");
	      empty.className = "text-[11px] text-muted-foreground";
	      empty.textContent = "No tool activity recorded for this response.";
	      container.appendChild(empty);
	    }

	    if (promptBudget.length) {
	      container.appendChild(
	        buildSection("Prompt Budget", promptBudget, (item, idx) => {
	          const stage = item && item.stage ? String(item.stage) : `call_${idx + 1}`;
	          const total = item && item.total && typeof item.total.tokens_est !== "undefined" ? `est=${this.formatTokenCount(Number(item.total.tokens_est))}` : "";
	          const actual = item && item.usage && typeof item.usage.prompt_tokens !== "undefined" ? `prompt=${this.formatTokenCount(Number(item.usage.prompt_tokens))}` : "";
	          return [stage, total, actual].filter(Boolean).join(" • ");
	        }),
	      );
	    }

	    if (toolTrace.length) {
	      container.appendChild(
	        buildSection("Tools", toolTrace, (item) => {
	          const tool = item && item.tool ? String(item.tool) : "tool";
          const status = item && item.status ? String(item.status) : "";
          return [tool, status].filter(Boolean).join(" • ");
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

  renderBufferToHtml(buffer) {
    return this.renderBufferToHtmlWithMode(buffer, { mode: "markdown" });
  }

  renderBufferToHtmlWithMode(buffer, { mode = "markdown" } = {}) {
    if (!buffer) return "";
    if (mode === "plain") {
      return this.renderPlainText(buffer);
    }
    return this.renderMarkdown(buffer);
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

				  resetStreamingState(removeNode = false, lockWorkflow = true) {
		    if (lockWorkflow) {
		      this.workflowLocked = true;
		    }
		    this.streamingActive = false;
		    this.isStreaming = false;
		    this.textDeltaIntervalEma = 0;
		    this.clearStreamingIdleStatusTimer();
		    this.clearStreamingStatus();
    if (removeNode && this.streamingMessageNode && this.streamingMessageNode.parentNode) {
      this.streamingMessageNode.parentNode.removeChild(this.streamingMessageNode);
    }
    this.streamingMessageNode = null;
    this.streamingMessageBodyEl = null;
    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
    this.streamingStatusDotEl = null;
	    this.streamingMessageId = null;
				    this.streamingBlocksEl = null;
				    this.usingBlockStream = false;
				    this.streamingContentBlockEls.clear();
				    this.streamingPendingBlockOps.clear();
			    this.streamingTextBlockActiveIds.clear();
			    this.streamingToolBlockActiveIds.clear();
			    this.streamingDirtyTextBlocks.clear();
			    if (this.streamingBlockRenderRaf) {
			      cancelAnimationFrame(this.streamingBlockRenderRaf);
		    }
		    this.streamingBlockRenderRaf = null;
		    this.spinnerDesiredText = "";
		    this.spinnerDesiredPending = false;
		    this.spinnerDesiredIsError = false;
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

  clearStreamingIdleStatusTimer() {
    if (!this.streamingIdleStatusTimer) return;
    clearTimeout(this.streamingIdleStatusTimer);
    this.streamingIdleStatusTimer = null;
  }

  scheduleStreamingIdleStatusReveal() {
    if (!this.isStreaming || this.streamFinished) return;
    if (!this.streamingStatusEl || !this.streamingStatusTextEl) return;
    if (!this.spinnerDesiredPending) return;

    this.clearStreamingIdleStatusTimer();
    const hasActiveText = this.streamingTextBlockActiveIds.size > 0;
    const adaptiveSilenceThreshold = (() => {
      if (!hasActiveText) return this.streamingSilenceStatusDelayMs;
      const ema = this.textDeltaIntervalEma || 0;
      if (!ema) return this.streamingSilenceStatusDelayMs;
      const scaled = ema * 2.5;
      return Math.min(2000, Math.max(this.streamingSilenceStatusDelayMs, scaled));
    })();
    const delay = hasActiveText ? adaptiveSilenceThreshold : this.streamingIdleStatusDelayMs;
    this.streamingIdleStatusTimer = setTimeout(() => {
      this.streamingIdleStatusTimer = null;
      if (!this.isStreaming || this.streamFinished) return;
      if (!this.spinnerDesiredPending) return;
      if (this.streamingTextBlockActiveIds.size > 0) {
        const lastDelta = this.lastTextDeltaAt || 0;
        if (lastDelta && Date.now() - lastDelta < adaptiveSilenceThreshold) {
          return;
        }
      }
      this.setSpinnerText(this.spinnerDesiredText, {
        pending: this.spinnerDesiredPending,
        isError: this.spinnerDesiredIsError,
        force: true,
      });
    }, delay);
  }

  repositionStreamingStatusRow() {
    if (!this.streamingStatusEl || !this.streamingBlocksEl) return;
    const inflightIds = this.streamingToolBlockActiveIds;
    try {
      if (inflightIds && inflightIds.size) {
        const firstInflight = Array.from(this.streamingBlocksEl.children).find((child) => {
          if (!child || child === this.streamingStatusEl) return false;
          const blockId = child.dataset ? (child.dataset.blockId || "").toString().trim() : "";
          return Boolean(blockId) && inflightIds.has(blockId);
        });
        if (firstInflight) {
          this.streamingBlocksEl.insertBefore(this.streamingStatusEl, firstInflight);
          return;
        }
      }
      this.streamingBlocksEl.appendChild(this.streamingStatusEl);
    } catch (_err) {
      // ignore reposition failures; keep streaming resilient
    }
  }

  setSpinnerText(rawText, { pending = true, isError = false, force = false } = {}) {
    if (this.workflowLocked && pending) return;
    this.ensureStreamingMessageNode(this.pendingMessageId);
    if (!this.streamingStatusEl || !this.streamingStatusTextEl) return;
    const label = (rawText || "").toString().trim();
    this.spinnerDesiredText = label;
    this.spinnerDesiredPending = pending;
    this.spinnerDesiredIsError = isError;
    if (!label) {
      if (!pending) {
        this.clearStreamingStatus();
      }
      // Pending without text: show icon-only row immediately.
      if (pending) {
        this.streamingStatusTextEl.textContent = "";
        this.repositionStreamingStatusRow();
        if (!force && this.streamingTextBlockActiveIds.size > 0) {
          this.streamingStatusEl.classList.add("hidden");
        } else {
          this.streamingStatusEl.classList.remove("hidden");
        }
        this.streamingStatusTextEl.classList.remove("chat-portal-status-shimmer");
      }
      return;
    }
    this.streamingStatusTextEl.innerHTML = this.formatStatusLabel(label);
    this.repositionStreamingStatusRow();
    if (!force && this.streamingTextBlockActiveIds.size > 0) {
      this.streamingStatusEl.classList.add("hidden");
    } else {
      this.streamingStatusEl.classList.remove("hidden");
    }
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
      .chat-portal-status-orbit {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: var(--portal-status-orbit-size, 16px);
        height: var(--portal-status-orbit-size, 16px);
        flex-shrink: 0;
      }
      .chat-portal-status-orbit .mcp-orbit-loader {
        --mcp-orbit-size: var(--portal-status-orbit-size, 16px);
        --mcp-orbit-stroke: var(--portal-status-orbit-stroke, 2px);
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
