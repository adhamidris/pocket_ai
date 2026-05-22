class ChatPortalClient {
  constructor(container) {
    this.container = container;
	    this.endpoints = {
	      bootstrap: container.getAttribute("data-endpoint-bootstrap"),
	      messages: container.getAttribute("data-endpoint-messages"),
      turnCreate: container.getAttribute("data-endpoint-turns-create"),
      turnEventsTemplate: container.getAttribute("data-endpoint-turn-events-template"),
      turnCancelTemplate: container.getAttribute("data-endpoint-turn-cancel"),
	      events: container.getAttribute("data-endpoint-events"),
	      csat: container.getAttribute("data-endpoint-csat"),
	      toolApproval: container.getAttribute("data-endpoint-tool-approval"),
	      toolHistory: container.getAttribute("data-endpoint-tool-history"),
      runApproval: container.getAttribute("data-endpoint-run-approval"),
      runUserInput: container.getAttribute("data-endpoint-run-user-input"),
      runCheckpoint: container.getAttribute("data-endpoint-run-checkpoint"),
      automationRun: container.getAttribute("data-endpoint-automation-run"),
      agentRequestUpdate: container.getAttribute("data-endpoint-agent-request-update"),
      emailSendDraft: container.getAttribute("data-endpoint-email-send-draft"),
      emailDiscardDraft: container.getAttribute("data-endpoint-email-discard-draft"),
	      fileUpload: container.getAttribute("data-endpoint-file-upload"),
      fileDownloadUrlTemplate: container.getAttribute("data-endpoint-file-download-url-template"),
		    };
    this.businessSlug = container.getAttribute("data-business-slug") || "";
    this.agentSlug = container.getAttribute("data-agent-slug") || "";
    this.agentName = container.getAttribute("data-agent-name") || "Pocket AI";
    this.agentInitials = container.getAttribute("data-agent-initials") || "AI";
    this.uiLanguage = this.resolveUiLanguage(container.getAttribute("data-ui-language") || "");
    this.locale = this.resolveLocale(this.uiLanguage);
    this.conversationId = container.getAttribute("data-conversation-id") || null;
    this.sessionToken = container.getAttribute("data-session-token") || null;
    this.sessionCacheKey = container.getAttribute("data-session-cache-key") || "";
    this.bootstrapScriptId = container.getAttribute("data-bootstrap-script-id") || "";
    this.conversationsEndpoint = container.getAttribute("data-endpoint-conversations") || "";
    this.chatSurface = container.getAttribute("data-chat-surface") || "public";
    const authenticatedChatAttr = (container.getAttribute("data-authenticated-chat") || "").toString().trim().toLowerCase();
    this.authenticatedChat = authenticatedChatAttr === "true" || authenticatedChatAttr === "1" || authenticatedChatAttr === "yes";
    const agentRunsAttr = (container.getAttribute("data-agent-runs-enabled") || "").toString().trim().toLowerCase();
    this.agentRunsEnabled = agentRunsAttr === "true" || agentRunsAttr === "1" || agentRunsAttr === "yes";
    try {
      this.agentRunDebugEnabled =
        typeof window !== "undefined" &&
        window.localStorage &&
        (window.localStorage.getItem("portalAgentRunDebug") || "") === "1";
    } catch (_err) {
      this.agentRunDebugEnabled = false;
    }
    this.currentStatus = container.getAttribute("data-initial-status") || "new";
    this.eventSource = null;
	    this.awaitingReply = false;
	    this.turnEventSource = null;
	    this.stopRequested = false;
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
      // Activity panel (agent runs)
      tasksPanel: container.querySelector("[data-tasks-panel]"),
      tasksList: container.querySelector("[data-tasks-list]"),
      tasksEmpty: container.querySelector("[data-tasks-empty]"),
      tasksCards: container.querySelector("[data-tasks-cards]"),
      tasksOpenBtn: container.querySelector("[data-tasks-open-btn]"),
      tasksCloseBtn: container.querySelector("[data-tasks-close-btn]"),
      tasksCount: container.querySelector("[data-tasks-count]"),
      // Inbox panel (agent requests)
      inboxPanel: container.querySelector("[data-inbox-panel]"),
      inboxList: container.querySelector("[data-inbox-list]"),
      inboxEmpty: container.querySelector("[data-inbox-empty]"),
      inboxCards: container.querySelector("[data-inbox-cards]"),
      inboxOpenBtn: container.querySelector("[data-inbox-open-btn]"),
      inboxCloseBtn: container.querySelector("[data-inbox-close-btn]"),
      inboxCount: container.querySelector("[data-inbox-count]"),
	    };
		    // Session management state
		    this.currentSessionKey = null;
		    this.sessionStorageKey = `chat_sessions_${this.businessSlug}_${this.agentSlug}`;
    this.lastConversationStorageKey = `dashboard_chat_last_conversation_${this.businessSlug}_${this.agentSlug}`;
    this.turnStateStorageKeyPrefix = `portal_turn_state_${this.businessSlug}_${this.agentSlug}_`;
	    this.toolsVisibilityKey = `chat_tools_visible_${this.businessSlug}_${this.agentSlug}`;
    this.tasksPanelStorageKey = `portal_tasks_panel_collapsed_${this.businessSlug}_${this.agentSlug}`;
	    this.globalToolsVisible = this.readGlobalToolsPreference();
	    this.streamingMessageNode = null;
	    this.streamingMessageBodyEl = null;
	    this.streamingStatusEl = null;
    this.streamingStatusTextEl = null;
    this.streamingStatusDotEl = null;
	    this.streamingMessageId = null;
    this.activeTurnId = null;
    this.activeTurnLastSeq = 0;
    this.lastFinalizedTurnId = null;
    this.turnCancelled = false;
	    this.streamingBlocksEl = null;
	    // Canonical block streaming state (block_id -> DOM + buffers)
	    this.usingBlockStream = false;
	    this.streamingContentBlockEls = new Map();
    this.streamingContentBlocksById = new Map();
    this.streamingPendingBlockOps = new Map();
    this.streamingTextBlockActiveIds = new Set();
    this.streamingToolBlockActiveIds = new Set();
    this.streamingDirtyTextBlocks = new Set();
    this.streamingMissingBlockWrapperCounts = new Map();
	    this.streamingBlockRenderRaf = null;
    this.streamingBlockPacerBudget = 0;
    this.streamingBlockPacerLastAt = 0;
    this.streamingBlockPacerMode = "normal";
    this.streamingBlockDeferredActions = [];
    this.streamingTextBoundaryActions = [];
    this.turnPersistedFinalizeTimer = null;
    this.streamingBlockPacerConfig = {
      baseCharsPerSecond: 60,
      maxCharsPerSecond: 120,
      backlogForMaxRate: 300,
      maxCharsPerTick: 8,
      maxBudgetChars: 20,
      boundaryModeMultiplier: 1.2,
      finalizeModeMultiplier: 1.35,
      dtCapMs: 50,
    };
	    // Snapshot of content blocks when a tool approval is pending (preserves text before approval card)
	    this.preApprovalContentBlocksSnapshot = new Map();
	    this.followScrollEnabled = false;
	    this.spinnerDesiredText = "";
	    this.spinnerDesiredPending = false;
	    this.spinnerDesiredIsError = false;
	    this.pendingMessageId = null;
	    this.pendingMetadataVersion = 0;
	    this.usingStateMachine = false;
	    this.automationLocked = false;
	    this.streamingActive = false;
    this.streamFinished = false;
    this.isSending = false;
    this.isStreaming = false;
    this.hydratingExistingMessages = false;
    this.finalizingTurn = false;
    this.flushQueueAfterTurn = false;
    this.pendingMessages = [];
    this.statusStyleInjected = false;
    this.ensureStatusStyle();
    this.downloadFrame = null;
    // Email draft tracking: maps draft_id to email preview card element
    this.draftIdToCardMap = new Map();
    // Session empty state tracking
    this.currentSessionHasMessages = false;
    this.sessionCreationInProgress = false;
    this.sessionLoadId = 0;
    this.sessionLoadInProgress = false;
    this.sessionSummaries = [];
    this.currentSessionType = (container.getAttribute("data-session-type") || "chat").toString().trim().toLowerCase() || "chat";
    this.currentCustomAssistantName = container.getAttribute("data-custom-assistant-name") || "";
    this.pendingSessionTitles = {};
    // Streaming UX helpers
    this.scrollToBottomRaf = null;
    this.scrollToBottomBehavior = "auto";
	    this.streamingIdleStatusTimer = null;
	    this.streamingIdleStatusDelayMs = 120;
	    this.lastStreamEventAt = 0;
	    this.hadToolsThisTurn = false;

    // Stream trace (debug): set localStorage.portalStreamTrace="1" to enable.
    this.streamTraceEnabled = false;
    this.streamTrace = [];
    this.streamTraceStartedAt = typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
    this.streamTraceLastAt = this.streamTraceStartedAt;
    try {
      this.streamTraceEnabled =
        typeof window !== "undefined" &&
        window.localStorage &&
        (window.localStorage.getItem("portalStreamTrace") || "") === "1";
      if (this.streamTraceEnabled && typeof window !== "undefined") {
        window.__portalStreamTrace = this.streamTrace;
      }
    } catch (_err) {
      this.streamTraceEnabled = false;
    }

	    // Agent runs/activity panel state
	    this.agentRuns = new Map(); // runId -> { run, events, expanded, seenKeys, lastEventLabel }
    this.automationAgents = new Map(); // automationId -> { automation, expanded, expandedRuns }
    this.automationManualRunBusy = new Set();
    this.tasksRenderRaf = null;
    this.tasksPanelUserHidden = false;

    // Agent requests/inbox panel state
    this.agentRequests = new Map(); // requestId -> { request, expanded }
    this.inboxRenderRaf = null;
    this.inboxPanelUserHidden = false;

    // Voice call transcript state
    this.activeVoiceCalls = new Map(); // sessionId -> { transcripts: [], expanded }
    this.pageLifecycleBound = false;
    this.pageLifecycleSuspended = false;
  }

  t(message, params) {
    const source = message == null ? "" : String(message);
    if (!source) return "";
    const i18n = typeof window !== "undefined" ? window.PocketI18n : null;
    if (i18n && typeof i18n.t === "function") {
      return i18n.t(source, params);
    }
    if (!params || typeof params !== "object") {
      return source;
    }
    return source.replace(/%\(([^)]+)\)s/g, (_match, key) => {
      if (!Object.prototype.hasOwnProperty.call(params, key)) {
        return "";
      }
      const value = params[key];
      return value == null ? "" : String(value);
    });
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
      
	      // Sync current session identity and load recent conversations from the backend when available.
	      this.trackCurrentSession();
	      this.loadSessionHistory();

      // Auto-focus input now that it is enabled
      if (this.elements.sendForm) {
        const ta = this.elements.sendForm.querySelector('textarea');
        if (ta) requestAnimationFrame(() => ta.focus());
      }

      if (this.agentRunsEnabled) {
        this.initTasksPanel();
        this.initInboxPanel();
      } else {
        // Single-agent mode: keep the portal chat-only and hide background surfaces.
        if (this.elements.tasksPanel) this.elements.tasksPanel.setAttribute("hidden", "");
        if (this.elements.inboxPanel) this.elements.inboxPanel.setAttribute("hidden", "");
        if (this.elements.tasksOpenBtn) this.elements.tasksOpenBtn.setAttribute("hidden", "");
        if (this.elements.inboxOpenBtn) this.elements.inboxOpenBtn.setAttribute("hidden", "");
      }
      this.bindPageLifecycleHandlers();
      this.connectEventStream();
      this.resumeActiveTurnIfNeeded();
    } catch (error) {
      this.showToast("Unable to load chat", error.message || "Please refresh and try again.", true);
    }
  }

  bindPageLifecycleHandlers() {
    if (this.pageLifecycleBound || typeof document === "undefined" || typeof window === "undefined") return;
    this.pageLifecycleBound = true;

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (this.finalizingTurn && this.hasStreamingBlockBacklog()) {
          this.flushStreamingBlockRenders(true);
        }
        this.pageLifecycleSuspended = true;
        this.closeSessionEventStream();
        this.closeTurnEventStream();
        return;
      }
      const wasSuspended = this.pageLifecycleSuspended;
      this.pageLifecycleSuspended = false;
      if (!wasSuspended) return;
      this.connectEventStream();
      this.resumeActiveTurnIfNeeded();
    });

    const teardown = () => {
      this.pageLifecycleSuspended = true;
      this.closeSessionEventStream();
      this.closeTurnEventStream();
    };

    window.addEventListener("pagehide", teardown);
    window.addEventListener("beforeunload", teardown);
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
        breaks: false,
        gfm: true,
      });
    }
  }

  renderExistingMessages() {
    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return;
    this.hydratingExistingMessages = true;
    const messageBodies = container.querySelectorAll('[data-message-body]');
    try {
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
            this.traceStream("hydrate.message", {
              messageId,
              blocks: Array.isArray(contentBlocks) ? contentBlocks.length : 0,
              bodyLen: bodyText.length,
            });
            const plainText =
              (Array.isArray(contentBlocks) && contentBlocks.length
                ? this.extractPlainTextFromContentBlocks(contentBlocks)
                : bodyText) || "";

            // Prefer canonical block rendering when available.
            if (Array.isArray(contentBlocks) && contentBlocks.length) {
              this.renderMessageContentBlocks(el, contentBlocks);
            } else if (!isCustomer && bodyText) {
              this.traceStream("hydrate.fallback_body", { messageId, bodyLen: bodyText.length });
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
    } finally {
      this.hydratingExistingMessages = false;
    }
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

  getMessageContentContainer(container) {
    if (!container) return null;
    const bodyEl = container.matches && container.matches("[data-message-body]") ? container : container.closest && container.closest("[data-message-body]");
    if (bodyEl) {
      const agentRunContent = bodyEl.querySelector("[data-agent-run-content]");
      if (agentRunContent) return agentRunContent;
    }
    return container;
  }

  resolveMessageIdForNode(node) {
    if (!node) return "";
    const direct = node.dataset && typeof node.dataset.messageId === "string" ? node.dataset.messageId.trim() : "";
    if (direct) return direct;
    const wrapper = node.closest ? node.closest("[data-message-id]") : null;
    const candidate = wrapper && wrapper.getAttribute ? (wrapper.getAttribute("data-message-id") || "").trim() : "";
    return candidate;
  }

  injectCopyButton(container) {
      if (!container) return;
      const row = container.closest(".message-row");
      if (row && row.classList.contains("flex-row-reverse")) return;

      const host = this.getMessageContentContainer(container);
      if (!host) return;
      // Prevent duplicate injection
      if (host.querySelector('button[data-copy-btn]')) return;

      // Smart positioning: anchor copy button at end of the last visible text block.
      let inlineTarget = null;
      const blocksRoot = host.querySelector("[data-message-blocks]") || host;
      const candidates = Array.from(
        blocksRoot.querySelectorAll(
          "[data-content-block-text], p, li, h1, h2, h3, blockquote"
        )
      );
      inlineTarget = candidates.reverse().find((el) => (el.textContent || "").trim().length);

      const copyBtn = document.createElement("button");
      copyBtn.dataset.copyBtn = "true";
      // inline-flex for inline, ml-2 for spacing, align-middle to center with text
      copyBtn.className = "inline-flex items-center gap-1.5 ml-2 px-2 py-1 align-bottom rounded-lg text-xs text-muted-foreground/50 hover:text-foreground hover:bg-muted/50 transition-all w-fit";
      copyBtn.type = "button";
      copyBtn.innerHTML = `<svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
      
      copyBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        let textToCopy = "";
        const messageId = this.resolveMessageIdForNode(host);
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
            textToCopy = host.innerText.replace("Copied", "").trim(); 
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
        host.appendChild(copyBtn);
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
	    const sendButton = this.elements.sendButton;
	    if (!form) return;

	    if (sendButton) {
	      sendButton.addEventListener("click", (event) => {
	        if (!this.awaitingReply && !this.isStreaming) {
	          return;
	        }
	        event.preventDefault();
	        event.stopPropagation();
	        void this.requestStop();
	      });
	    }

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
      if (this.automationLocked) return;
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
      const referencePayload = this.getCurrentReferencePayload();
      Object.entries(referencePayload).forEach(([key, value]) => {
        if (value) {
          form.append(key, value);
        }
      });
      form.append("file", file);

      try {
        this.showToast("Uploading…", file.name || "PDF");
	        const response = await fetch(this.endpoints.fileUpload, {
	          method: "POST",
          headers: (() => {
            const csrfToken = this.getCsrfToken();
            return csrfToken ? { "X-CSRFToken": csrfToken, "X-Requested-With": "XMLHttpRequest" } : { "X-Requested-With": "XMLHttpRequest" };
          })(),
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
        this.applyCurrentReferenceToUrl(url);

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
          const preloadedConversationId = this.readBootstrapScriptConversationId();
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
    const session = data && data.session ? data.session : null;
	    const token = data && data.session && data.session.session_token ? data.session.session_token : null;
	    if (!token) {
	      throw new Error("Session token missing from bootstrap response");
	    }
    if (options.expectedToken && token !== options.expectedToken) {
	      throw new Error("Session is no longer available.");
	    }
	    this.persistSessionToken(token);
    if (this.shouldUseConversationApi()) {
      const resolvedConversationId =
        (session && (session.conversation_id || session.conversationId)) || options.conversationId || this.conversationId || preloadedConversationId;
      if (resolvedConversationId) {
        this.setActiveSessionFromSession({ ...(session || {}), conversation_id: resolvedConversationId, session_token: token });
      }
    } else if (session) {
      this.setActiveSessionFromSession(session);
    }
	    const messages = Array.isArray(data.messages) ? data.messages : [];
	    const effectiveMessages = messages.filter(
	      (message) => !(message && message.metadata && message.metadata.placeholder),
	    );
	    this.currentSessionHasMessages = effectiveMessages.length > 0;
	    this.updateSessionEmptyState(effectiveMessages.length);
	    this.setSessionMessageCount(this.getCurrentSessionKey(), effectiveMessages.length);
	    this.setConversationLayout(effectiveMessages.length > 0);

	    // Fix FOUC: Only render transcript if container is empty (client-side only),
	    // otherwise assume server-side rendering is correct.
	    const container = this.elements.messagesInner || this.elements.messages;
	    if (container && (container.children.length === 0 || shouldForceRender)) {
        if (effectiveMessages.length > 0) {
	        this.renderTranscript(messages);
        } else {
          this.renderEmptyConversationState();
        }
	    }

    const sessionStatus = data && data.session ? data.session.status : null;
    this.updateStatus(sessionStatus);
    this.updateCsatVisibility(sessionStatus);
    return data;
  }

		  async sendMessage(message, options = {}) {
		    if (!this.sessionToken) return;
        if (!this.endpoints.turnCreate) {
          this.showToast("Send failed", "Turn endpoint is not configured.", true);
          return;
        }
        this.clearActiveTurnState();
		    this.stopRequested = false;
		    this.clearStreamingStatus();
		    this.resetStreamingState(true, false);
		    this.pendingMessageId = null;
	    this.pendingMetadataVersion = 0;
	    this.usingStateMachine = false;
      this.automationLocked = false;
	    this.streamFinished = false;
	    this.awaitingReply = true;
	    this.isSending = true;
	    this.isStreaming = true;
      this.flushQueueAfterTurn = false;
	    this.updateSendButtonState(true);
	    this.updateComposerNotice(true);
	    this.appendMessage({
	      sender: "customer",
	      body: message,
	      sent_at: new Date().toISOString(),
	    });
	    // Instant feedback before the first SSE event arrives.
	    this.setSpinnerText("", { pending: true });

    try {
      const turnMetadataOverrides =
        options && typeof options === "object" && options.turnMetadata && typeof options.turnMetadata === "object"
          ? options.turnMetadata
          : null;
	      const requestBody = {
	        body: message,
	        metadata: this.buildTurnMetadata(turnMetadataOverrides),
          ...this.getCurrentReferencePayload(),
	      };
        const turnEndpoint =
          this.shouldUseConversationApi() && this.conversationId
            ? this.getConversationTurnsUrl(this.conversationId)
            : this.endpoints.turnCreate;
	      const response = await fetch(turnEndpoint, {
	        method: "POST",
	        headers: this.jsonHeaders(),
	        body: JSON.stringify(requestBody),
	      });

      if (!response.ok) {
        throw new Error("Turn creation failed");
      }

      const data = await response.json();
      const turn = data && data.turn ? data.turn : null;
      const turnId = turn && turn.id ? turn.id : null;
      if (!turnId) {
        throw new Error("Turn id missing from response");
      }

	      if (data && data.session && data.session.status) {
          this.setActiveSessionFromSession(data.session);
	        this.updateStatus(data.session.status);
	        this.updateCsatVisibility(data.session.status);
	      }
      if (data && data.customer_message_id) {
        this.updateLatestCustomerMessageId(data.customer_message_id);
      }

      this.isSending = false;
      this.startTurnEventStream(turnId, { since: 0 });
    } catch (error) {
      this.awaitingReply = false;
      this.isSending = false;
      this.isStreaming = false;
      this.streamFinished = true;
      this.updateSendButtonState(false);
      this.setComposerAvailability(true);
      this.updateComposerNotice(false);
      this.resetStreamingState(true, false);
      this.clearActiveTurnState();
      if (error && error.name !== "AbortError") {
        this.showToast("Send failed", error.message || "Message could not be delivered.", true);
      }
      this.flushQueueAfterTurn = true;
      this.turnCancelled = false;
      if (this.flushQueueAfterTurn) {
        this.flushQueueAfterTurn = false;
        this.flushQueuedMessageIfReady();
      }
    }
  }

  handleStreamEvent(eventType, data) {
    this.lastStreamEventAt = Date.now();
    if (this.sessionLoadInProgress) {
      return;
    }

    this.traceStream(eventType, { rawLen: data ? data.length : 0 });

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
    if (eventType === "block_remove") {
      this.usingBlockStream = true;
      this.handleBlockRemoveEvent(data);
      return;
    }

    if (eventType === "text_delta") {
      this.traceStream("text_delta_ignored", { rawLen: data ? data.length : 0 });
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
      if (this.automationLocked) {
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
        this.showToast("Automation update", label);
      } catch (_err) {
        // ignore
      }
      return;
    }

    if (eventType === "actionsError") {
      try {
        const payload = data ? JSON.parse(data) : null;
        const message = payload && payload.error ? payload.error : "Background automation failed.";
        this.showToast("Automation issue", message, true);
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
      if (normalized.startsWith("waiting for approval")) {
        // Approval CTAs are rendered inline in the tool card; the extra spinner is redundant.
        this.setSpinnerText("", { pending: false, force: true });
        return;
      }
	    const pending = payload.pending !== false;
      if (text) {
        this.clearStreamingIdleStatusTimer();
      }
	      const force = Boolean(text) || this.streamingTextBlockActiveIds.size === 0;
		    this.setSpinnerText(text, { pending, force });
		  }

	  async requestStop() {
	    if (this.stopRequested) return;
	    if (!this.sessionToken) return;
	    if (!this.awaitingReply && !this.isStreaming) return;
      if (!this.activeTurnId) {
        this.showToast("Stop unavailable", "No active turn to stop.", true);
        return;
      }
	    if (!this.endpoints.turnCancelTemplate) {
	      this.showToast("Stop unavailable", "Turn cancel endpoint is not configured.", true);
	      return;
	    }
	    this.stopRequested = true;
	    this.setSpinnerText("Stopping…", { pending: true, force: true });
	    try {
        const endpoint = this.buildTurnCancelUrl(this.activeTurnId);
        if (!endpoint) {
          throw new Error("Turn cancel endpoint unavailable");
        }
		      const response = await fetch(endpoint, {
		        method: "POST",
		        headers: this.jsonHeaders(),
		        body: JSON.stringify(this.getCurrentReferencePayload()),
		      });
	      if (!response.ok) {
	        throw new Error("Stop request failed");
	      }
	    } catch (error) {
	      this.stopRequested = false;
	      this.showToast("Stop failed", error.message || "Could not stop the automation.", true);
	    }
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
	    const blockType = (block.type || "").toString().trim().toLowerCase();
	    const blockId = (block.block_id || block.blockId || "").toString().trim();
	    const messageId = (payload.message_id || payload.messageId || "").toString().trim() || null;
	    this._processBlockStart(payload, block, blockType, blockId, messageId);
	  }

		  _processBlockStart(payload, block, blockType, blockId, messageId) {
		    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);
	    if (blockId) {
	      this.streamingContentBlocksById.set(blockId, block);
	    }

        const mountBlock = () => {
		      this.upsertStreamingContentBlock(block);
		      if (blockId && this.isStreamingTextBlock(blockType)) {
		        this.streamingTextBlockActiveIds.add(blockId);
	          this.clearStreamingIdleStatusTimer();
	          // Keep the status row pinned after inserting new streaming blocks.
	          this.repositionStreamingStatusRow();
		      }
          if (blockId && this.streamingPendingBlockOps.has(blockId)) {
            this.streamingDirtyTextBlocks.add(blockId);
            this.scheduleStreamingBlockRender();
          }
        };

        if (blockType === "table" && this.hasStreamingTextRevealBacklog()) {
          this._queueAfterTextRevealDrain(mountBlock, { mode: "boundary" });
          return;
        }

        mountBlock();
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

	    const messageId = (payload.message_id || payload.messageId || "").toString().trim() || null;
	    this.ensureStreamingMessageNode(messageId || this.pendingMessageId);

		    const wrapper = this.streamingContentBlockEls.get(blockId);
	      const blockType = wrapper && wrapper.dataset ? (wrapper.dataset.blockType || "").toString().trim().toLowerCase() : "";
	      if (!blockType || this.isStreamingTextBlock(blockType)) {
		      this.streamingTextBlockActiveIds.add(blockId);
	      }
			    if (this.streamingStatusEl) {
			      this.streamingStatusEl.classList.add("hidden");
			    }

	    const pending = this.streamingPendingBlockOps.get(blockId) || [];
	    pending.push(...ops);
		    this.streamingPendingBlockOps.set(blockId, pending);
		    this.streamingDirtyTextBlocks.add(blockId);
		    this.scheduleStreamingBlockRender();
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
		      this.streamingDirtyTextBlocks.add(blockId);
		      this.scheduleStreamingBlockRender();
		    }
		    this.streamingTextBlockActiveIds.delete(blockId);
	    // When the last active text block closes, flush any deferred clarification blocks
	    // that were held back waiting for text to finish (Anthropic agentic style).
	    if (
	      this.streamingTextBlockActiveIds.size === 0 &&
	      Array.isArray(this._pendingPostTextStreamActions) &&
	      this._pendingPostTextStreamActions.length > 0
	    ) {
	      const actions = this._pendingPostTextStreamActions.splice(0);
	      this._queueAfterBlockDrain(
	        () => actions.forEach((fn) => { try { fn(); } catch (_) {} }),
	        { mode: "boundary" },
	      );
	    }
	      const wrapper = this.streamingContentBlockEls.get(blockId);
	      const type = wrapper && wrapper.dataset ? (wrapper.dataset.blockType || "").toString().trim().toLowerCase() : "";
			      if (type === "reasoning") {
		        const details = wrapper ? wrapper.querySelector("details") : null;
		        if (details) {
	          details.dataset.reasoningState = "complete";
	          const summaryLabel = details.querySelector("[data-reasoning-summary-label]");
	          if (summaryLabel) {
	            summaryLabel.textContent = "Thought";
	          }
	          if (details.open && details.dataset.userOverride !== "true") {
	            this.animateReasoningAutoCollapse(details);
	          }
			        }
			      }
		  }

	  animateReasoningAutoCollapse(details) {
	    if (!details || !details.open) return;
	    if (details.dataset && details.dataset.userOverride === "true") return;
	    if (details.dataset && details.dataset.reasoningAutoClosing === "true") return;

	    const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	    if (prefersReducedMotion) {
	      details.open = false;
	      return;
	    }

	    const content = details.querySelector(".portal-reasoning__content");
	    if (!content) {
	      details.open = false;
	      return;
	    }

	    details.dataset.reasoningAutoClosing = "true";
	    details.style.pointerEvents = "none";

	    const startHeight = content.getBoundingClientRect().height;
	    content.style.overflow = "hidden";
	    content.style.maxHeight = "none";
	    content.style.height = `${startHeight}px`;
	    content.style.opacity = "1";
	    content.style.transition = "height 220ms cubic-bezier(0.22, 1, 0.36, 1), opacity 160ms ease";

	    // Force reflow so the browser picks up the start height before collapsing.
	    void content.offsetHeight;

	    content.style.height = "0px";
	    content.style.opacity = "0";

	    let fallbackTimer = null;
	    const cleanup = () => {
	      if (fallbackTimer) {
	        clearTimeout(fallbackTimer);
	        fallbackTimer = null;
	      }
	      details.open = false;
	      delete details.dataset.reasoningAutoClosing;
	      details.style.pointerEvents = "";
	      content.style.transition = "";
	      content.style.height = "";
	      content.style.opacity = "";
	      content.style.overflow = "";
	      content.style.maxHeight = "";
	    };

	    fallbackTimer = setTimeout(() => {
	      if (details.dataset && details.dataset.reasoningAutoClosing === "true") {
	        cleanup();
	      }
	    }, 320);

	    content.addEventListener(
	      "transitionend",
	      (event) => {
	        if (event && event.target === content && event.propertyName === "height") {
	          cleanup();
	        }
	      },
	      { once: true },
	    );
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

	    this._queueAfterBlockDrain(
      () => {
        const blockPayload = block.payload && typeof block.payload === "object" ? block.payload : {};
        const phase = (blockPayload.phase || "").toString().trim().toLowerCase();
        const status = (blockPayload.status || "").toString().trim().toLowerCase();
        const isApprovalPending = phase === "approval_requested" || status === "pending_approval";
        if (
          blockId &&
          (phase === "started" || phase === "approval_requested" || status === "running" || status === "pending_approval")
        ) {
          this.streamingToolBlockActiveIds.add(blockId);
          this.hadToolsThisTurn = true;
        }
        if (isApprovalPending && this.streamingContentBlocksById && this.streamingContentBlocksById.size > 0) {
          this.streamingContentBlocksById.forEach((blockData, id) => {
            if (!this.preApprovalContentBlocksSnapshot.has(id)) {
              this.preApprovalContentBlocksSnapshot.set(id, JSON.parse(JSON.stringify(blockData)));
            }
          });
        }
        this.upsertStreamingContentBlock(block);
        this.syncSpinnerFromActiveToolBlocks();
        this.repositionStreamingStatusRow();
        this.scheduleScrollToBottom({ behavior: "auto" });
      },
      { mode: "boundary" },
    );
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

    this._queueAfterBlockDrain(
      () => {
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
        this.syncSpinnerFromActiveToolBlocks();
        this.repositionStreamingStatusRow();
        this.scheduleScrollToBottom({ behavior: "auto" });
      },
      { mode: "boundary" },
    );
  }

  handleBlockRemoveEvent(data) {
    let payload = null;
    try {
      payload = data ? JSON.parse(data) : null;
    } catch (error) {
      console.warn("Failed to parse block_remove payload", error);
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const blockIds = Array.isArray(payload.block_ids)
      ? payload.block_ids.map((value) => (value == null ? "" : value.toString().trim())).filter(Boolean)
      : [];
    if (!blockIds.length) return;

    this._queueAfterBlockDrain(
      () => {
        blockIds.forEach((blockId) => {
          this.removeStreamingContentBlockById(blockId);
        });
        this.syncSpinnerFromActiveToolBlocks();
      },
      { mode: "boundary" },
    );
  }

  traceStream(event, meta) {
    if (!this.streamTraceEnabled) return;
    if (this.streamTrace.length >= 20_000) return;

    const now = typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
    const dt = now - (this.streamTraceLastAt || now);
    this.streamTraceLastAt = now;
    const rec = {
      t: Math.round(now - (this.streamTraceStartedAt || now)),
      dt: Math.round(dt),
      event: (event || "").toString(),
      ...(meta && typeof meta === "object" ? meta : {}),
    };
    this.streamTrace.push(rec);

    // Only spam console on anomalies; full trace remains in window.__portalStreamTrace.
    const shouldLog =
      rec.event === "turn_persisted" ||
      rec.event === "turnUpdated" ||
      rec.event === "spinnerStatus" ||
      rec.dt >= 250;
    if (shouldLog) {
      try {
        console.debug("[portal stream]", rec);
      } catch (_err) {
        // ignore
      }
    }
  }

		  isStreamingTextBlock(type) {
		    // Leaf text blocks that actually receive block_delta/block_end events.
		    // (Container blocks like list/quote don't emit block_end; avoid "stuck" active ids.)
		    return ["paragraph", "heading", "list_item", "code_block", "text", "reasoning"].includes(type);
		  }

  applyBlockOps(blockId, ops) {
    if (!Array.isArray(ops)) return;
    const blockModel = this.streamingContentBlocksById.get(blockId);
    if (blockModel) {
      this.applyBlockOpsToBlockModel(blockModel, ops);
    }
    const wrapper = this.streamingContentBlockEls.get(blockId);
    if (!wrapper) return;
    if (this.shouldRebuildStreamingTextBlock(wrapper, blockModel)) {
      const updated = this.updateContentBlockElement(wrapper, blockModel, { streaming: true }) || wrapper;
      if (updated && updated !== wrapper) {
        this.streamingContentBlockEls.set(blockId, updated);
      }
      return;
    }
    const wrapperType = wrapper && wrapper.dataset ? (wrapper.dataset.blockType || "").toString().trim().toLowerCase() : "";
    if (wrapperType === "table" && blockModel && typeof blockModel === "object") {
      const updated = this.updateStreamingTableBlock(wrapper, blockModel.payload && typeof blockModel.payload === "object" ? blockModel.payload : {});
      if (updated && updated !== wrapper) {
        this.streamingContentBlockEls.set(blockId, updated);
      }
      return;
    }
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
          if (wrapper && wrapper.dataset && wrapper.dataset.blockType === "list_item") {
            this.syncListItemBulletVisibility(wrapper);
          }
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

  shouldRebuildStreamingTextBlock(wrapper, blockModel) {
    if (!wrapper || !blockModel || typeof blockModel !== "object") return false;
    const type = (blockModel.type || "").toString().trim().toLowerCase();
    if (!["paragraph", "heading", "list_item"].includes(type)) return false;
    const payload = blockModel.payload && typeof blockModel.payload === "object" ? blockModel.payload : {};
    const content = Array.isArray(payload.content) ? payload.content : [];
    const rawText = this.inlineNodesToText(content);
    const wrapperIsMarkdown =
      Boolean(wrapper.dataset && (wrapper.dataset.markdownRichText === "true" || wrapper.dataset.markdownTable === "true"));
    if (wrapperIsMarkdown) return true;
    return type !== "list_item" && this.shouldRenderStreamingInlineContentAsMarkdown(rawText);
  }

  shouldRenderStreamingInlineContentAsMarkdown(text) {
    const raw = (text || "").toString();
    if (!raw) return false;
    // Live streaming should only switch into markdown-wrapper rendering for
    // strong block-level markdown. Inline marks are already handled by the
    // structured inline-node path, so speculative upgrades on partial list or
    // pipe fragments create visible jitter without improving fidelity.
    if (this.containsMarkdownTable(raw)) return true;
    if (this.containsMarkdownList(raw)) return true;
    return false;
  }

  applyBlockOpsToBlockModel(block, ops) {
    if (!block || typeof block !== "object" || !Array.isArray(ops)) return;
    const payload = block.payload && typeof block.payload === "object" ? block.payload : {};
    if (!block.payload || typeof block.payload !== "object") {
      block.payload = payload;
    }
    ops.forEach((op) => {
      if (!op || typeof op !== "object") return;
      const kind = (op.op || "").toString().trim();
      if (kind === "append_inline") {
        const nodes = Array.isArray(op.nodes) ? op.nodes : op.node ? [op.node] : [];
        if (!nodes.length) return;
        let content = Array.isArray(payload.content) ? payload.content : [];
        if (!Array.isArray(payload.content)) {
          payload.content = content;
        }
        nodes.forEach((node) => {
          if (!node || typeof node !== "object") return;
          const text = typeof node.text === "string" ? node.text : "";
          if (!text) return;
          const marks = Array.isArray(node.marks) ? node.marks : null;
          const outNode = { text };
          if (marks && marks.length) {
            outNode.marks = marks;
          }
          content.push(outNode);
        });
        return;
      }
      if (kind === "append_code") {
        const text = typeof op.text === "string" ? op.text : "";
        if (!text) return;
        const existing = typeof payload.code === "string" ? payload.code : "";
        payload.code = `${existing}${text}`;
        return;
      }
      if (kind === "append_table_row") {
        const cells = Array.isArray(op.cells) ? op.cells : [];
        if (!cells.length) return;
        let rows = Array.isArray(payload.rows) ? payload.rows : [];
        if (!Array.isArray(payload.rows)) {
          payload.rows = rows;
        }
        const row = { cells: cells.map((cell) => (typeof cell === "string" ? cell : cell == null ? "" : String(cell))) };
        row.__streamComplete = true;
        if (op.rtl === true) {
          row.rtl = true;
        }
        rows.push(row);
        return;
      }
      if (kind === "set_table_cell_text") {
        const rowIndex = Number.isInteger(op.row_index) ? op.row_index : parseInt(op.row_index, 10);
        const cellIndex = Number.isInteger(op.cell_index) ? op.cell_index : parseInt(op.cell_index, 10);
        if (!Number.isFinite(rowIndex) || !Number.isFinite(cellIndex) || rowIndex < 0 || cellIndex < 0) return;
        let rows = Array.isArray(payload.rows) ? payload.rows : [];
        if (!Array.isArray(payload.rows)) {
          payload.rows = rows;
        }
        while (rows.length <= rowIndex) {
          rows.push({ cells: [] });
        }
        const row = rows[rowIndex] && typeof rows[rowIndex] === "object" ? rows[rowIndex] : { cells: [] };
        const cells = Array.isArray(row.cells) ? row.cells.slice() : [];
        while (cells.length <= cellIndex) {
          cells.push("");
        }
        cells[cellIndex] = typeof op.text === "string" ? op.text : op.text == null ? "" : String(op.text);
        row.cells = cells;
        if (op.row_complete === true || op.rowComplete === true) {
          row.__streamComplete = true;
          delete row.__streamDraft;
        } else if (row.__streamComplete !== true) {
          row.__streamDraft = true;
        }
        if (op.rtl === true) {
          row.rtl = true;
        }
        rows[rowIndex] = row;
      }
    });
  }

  scheduleStreamingBlockRender() {
    if (this.streamingBlockRenderRaf) return;
    this.streamingBlockRenderRaf = requestAnimationFrame(() => {
      this.streamingBlockRenderRaf = null;
      this.flushStreamingBlockRenders(false);
    });
  }

  estimateBlockOpsChars(ops) {
    if (!Array.isArray(ops) || !ops.length) return 0;
    let total = 0;
    ops.forEach((op) => {
      if (!op || typeof op !== "object") return;
      const kind = (op.op || "").toString().trim();
      if (kind === "append_inline") {
        const nodes = Array.isArray(op.nodes) ? op.nodes : op.node ? [op.node] : [];
        nodes.forEach((node) => {
          if (!node || typeof node !== "object") return;
          const text = typeof node.text === "string" ? node.text : "";
          total += text.length;
        });
        return;
      }
      if (kind === "append_code") {
        const text = typeof op.text === "string" ? op.text : "";
        total += text.length;
        return;
      }
      if (kind === "append_table_row") {
        total += 1;
        return;
      }
      if (kind === "set_table_cell_text") {
        const text = typeof op.text === "string" ? op.text : op.text == null ? "" : String(op.text);
        total += text.length || 1;
      }
    });
    return total;
  }

  estimateStreamingPendingChars() {
    let total = 0;
    this.streamingPendingBlockOps.forEach((ops) => {
      total += this.estimateBlockOpsChars(ops);
    });
    return total;
  }

  hasStreamingBlockBacklog() {
    if (this.streamingDirtyTextBlocks && this.streamingDirtyTextBlocks.size) return true;
    if (this.streamingPendingBlockOps && this.streamingPendingBlockOps.size) {
      return this.estimateStreamingPendingChars() > 0;
    }
    return false;
  }

  hasStreamingTextRevealBacklog() {
    if (!this.streamingPendingBlockOps || !this.streamingPendingBlockOps.size) return false;
    for (const [blockId, ops] of this.streamingPendingBlockOps.entries()) {
      if (!Array.isArray(ops) || !ops.length) continue;
      const wrapper = this.streamingContentBlockEls.get(blockId);
      let type = wrapper && wrapper.dataset ? (wrapper.dataset.blockType || "").toString().trim().toLowerCase() : "";
      if (!type) {
        const blockModel = this.streamingContentBlocksById.get(blockId);
        type = blockModel && typeof blockModel === "object" ? (blockModel.type || "").toString().trim().toLowerCase() : "";
      }
      if (type && this.isStreamingTextBlock(type)) {
        return true;
      }
    }
    return false;
  }

  splitInlineNodesByBudget(nodes, budget) {
    if (!Array.isArray(nodes) || !nodes.length) {
      return { emittedNodes: [], remainingNodes: [], consumed: 0 };
    }
    let remainingBudget = Math.max(0, Number(budget) || 0);
    const emittedNodes = [];
    const remainingNodes = [];
    let consumed = 0;
    for (let idx = 0; idx < nodes.length; idx += 1) {
      const node = nodes[idx];
      if (!node || typeof node !== "object") continue;
      const text = typeof node.text === "string" ? node.text : "";
      if (!text) {
        if (remainingBudget > 0) {
          emittedNodes.push({ ...node });
        } else {
          remainingNodes.push({ ...node });
        }
        continue;
      }
      if (remainingBudget <= 0) {
        remainingNodes.push({ ...node });
        continue;
      }
      if (text.length <= remainingBudget) {
        emittedNodes.push({ ...node });
        remainingBudget -= text.length;
        consumed += text.length;
        continue;
      }
      const headText = text.slice(0, remainingBudget);
      const tailText = text.slice(remainingBudget);
      emittedNodes.push({ ...node, text: headText });
      remainingNodes.push({ ...node, text: tailText });
      consumed += remainingBudget;
      remainingBudget = 0;
      for (let j = idx + 1; j < nodes.length; j += 1) {
        const tailNode = nodes[j];
        if (tailNode && typeof tailNode === "object") {
          remainingNodes.push({ ...tailNode });
        }
      }
      break;
    }
    return { emittedNodes, remainingNodes, consumed };
  }

  sliceBlockOpsForBudget(ops, budgetChars) {
    const budget = Math.max(0, Number(budgetChars) || 0);
    if (!Array.isArray(ops) || !ops.length || budget <= 0) {
      return { emitOps: [], remainingOps: Array.isArray(ops) ? ops.slice(0) : [], consumedChars: 0 };
    }
    const emitOps = [];
    const remainingOps = [];
    let remainingBudget = budget;
    let consumedChars = 0;

    for (let idx = 0; idx < ops.length; idx += 1) {
      const op = ops[idx];
      if (!op || typeof op !== "object") continue;
      const kind = (op.op || "").toString().trim();

      if (kind === "append_inline") {
        const nodes = Array.isArray(op.nodes) ? op.nodes : op.node ? [op.node] : [];
        const split = this.splitInlineNodesByBudget(nodes, remainingBudget);
        if (split.emittedNodes.length) {
          emitOps.push({ ...op, nodes: split.emittedNodes });
        }
        consumedChars += split.consumed;
        remainingBudget = Math.max(0, remainingBudget - split.consumed);
        if (split.remainingNodes.length) {
          remainingOps.push({ ...op, nodes: split.remainingNodes });
          for (let j = idx + 1; j < ops.length; j += 1) remainingOps.push(ops[j]);
          break;
        }
        continue;
      }

      if (kind === "append_code") {
        const text = typeof op.text === "string" ? op.text : "";
        if (!text) continue;
        if (text.length <= remainingBudget) {
          emitOps.push({ ...op });
          consumedChars += text.length;
          remainingBudget = Math.max(0, remainingBudget - text.length);
          continue;
        }
        if (remainingBudget > 0) {
          emitOps.push({ ...op, text: text.slice(0, remainingBudget) });
          remainingOps.push({ ...op, text: text.slice(remainingBudget) });
          consumedChars += remainingBudget;
          remainingBudget = 0;
        } else {
          remainingOps.push({ ...op });
        }
        for (let j = idx + 1; j < ops.length; j += 1) remainingOps.push(ops[j]);
        break;
      }

      // Non-text ops: pass through immediately.
      emitOps.push({ ...op });
      
      if (kind === "append_table_row") {
        consumedChars += 1;
        remainingBudget -= 1;
      } else if (kind === "set_table_cell_text") {
        const textLen = typeof op.text === "string" ? op.text.length : 1;
        consumedChars += textLen || 1;
        remainingBudget -= (textLen || 1);
      } else {
        // Generic fallback cost for unknown ops
        consumedChars += 1;
        remainingBudget -= 1;
      }

      if (remainingBudget <= 0) {
        for (let j = idx + 1; j < ops.length; j += 1) remainingOps.push(ops[j]);
        break;
      }
    }

    return { emitOps, remainingOps, consumedChars };
  }

  _queueAfterBlockDrain(fn, { mode = "boundary" } = {}) {
    if (typeof fn !== "function") return;
    if (!this.hasStreamingBlockBacklog()) {
      fn();
      return;
    }
    if (!Array.isArray(this.streamingBlockDeferredActions)) {
      this.streamingBlockDeferredActions = [];
    }
    this.streamingBlockDeferredActions.push(fn);
    this.streamingBlockPacerMode = (mode || "boundary").toString();
    this.scheduleStreamingBlockRender();
  }

  _queueAfterTextRevealDrain(fn, { mode = "boundary" } = {}) {
    if (typeof fn !== "function") return;
    if (!this.hasStreamingTextRevealBacklog()) {
      fn();
      return;
    }
    if (!Array.isArray(this.streamingTextBoundaryActions)) {
      this.streamingTextBoundaryActions = [];
    }
    this.streamingTextBoundaryActions.push(fn);
    this.streamingBlockPacerMode = (mode || "boundary").toString();
    this.scheduleStreamingBlockRender();
  }

  _flushTextBoundaryActionsIfReady() {
    if (!Array.isArray(this.streamingTextBoundaryActions) || !this.streamingTextBoundaryActions.length) {
      return;
    }
    if (this.hasStreamingTextRevealBacklog()) {
      return;
    }
    const actions = this.streamingTextBoundaryActions.slice(0);
    this.streamingTextBoundaryActions = [];
    actions.forEach((fn) => {
      try {
        fn();
      } catch (_err) {
        // ignore
      }
    });
  }

  flushStreamingBlockRenders(force = false) {
    this._flushTextBoundaryActionsIfReady();
    if (!this.streamingDirtyTextBlocks.size) {
      if (this.streamingPendingBlockOps && this.streamingPendingBlockOps.size) {
        let seeded = 0;
        this.streamingPendingBlockOps.forEach((ops, blockId) => {
          if (!Array.isArray(ops) || !ops.length) {
            this.streamingPendingBlockOps.delete(blockId);
            this.streamingMissingBlockWrapperCounts.delete(blockId);
            return;
          }
          if (!blockId) return;
          this.streamingDirtyTextBlocks.add(blockId);
          seeded += 1;
        });
        if (seeded > 0) {
          this.scheduleStreamingBlockRender();
          return;
        }
      }
      if (this.streamingBlockDeferredActions && this.streamingBlockDeferredActions.length && !this.hasStreamingBlockBacklog()) {
        const actions = this.streamingBlockDeferredActions.slice(0);
        this.streamingBlockDeferredActions = [];
        actions.forEach((fn) => {
          try {
            fn();
          } catch (_err) {
            // ignore
          }
        });
      }
      return;
    }
    const blockIds = Array.from(this.streamingDirtyTextBlocks);
    this.streamingDirtyTextBlocks.clear();

    if (force) {
      blockIds.forEach((blockId) => {
        const wrapper = this.streamingContentBlockEls.get(blockId);
        const ops = this.streamingPendingBlockOps.get(blockId);
        if (!ops || !ops.length) return;
        if (!wrapper) {
          return;
        }
        this.streamingPendingBlockOps.delete(blockId);
        this.applyBlockOps(blockId, ops);
      });
      this.streamingBlockPacerBudget = 0;
      this.streamingBlockPacerLastAt = 0;
      this.streamingBlockPacerMode = "normal";
      if (this.streamingBlockDeferredActions && this.streamingBlockDeferredActions.length) {
        const actions = this.streamingBlockDeferredActions.slice(0);
        this.streamingBlockDeferredActions = [];
        actions.forEach((fn) => {
          try {
            fn();
          } catch (_err) {
            // ignore
          }
        });
      }
      return;
    }

    const backlogChars = this.estimateStreamingPendingChars();
    if (backlogChars <= 0) return;

    const now = typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
    const lastAt = this.streamingBlockPacerLastAt || now;
    let dtMs = now - lastAt;
    if (!Number.isFinite(dtMs) || dtMs < 0) dtMs = 0;
    const pacerCfg = this.streamingBlockPacerConfig || {};
    const dtCapMs = Number.isFinite(Number(pacerCfg.dtCapMs)) ? Number(pacerCfg.dtCapMs) : 50;
    dtMs = Math.min(dtMs, Math.max(1, dtCapMs));
    this.streamingBlockPacerLastAt = now;

    const baseCharsPerSecond = Number.isFinite(Number(pacerCfg.baseCharsPerSecond))
      ? Number(pacerCfg.baseCharsPerSecond)
      : 72;
    const maxCharsPerSecond = Number.isFinite(Number(pacerCfg.maxCharsPerSecond))
      ? Number(pacerCfg.maxCharsPerSecond)
      : 165;
    const backlogForMaxRate = Number.isFinite(Number(pacerCfg.backlogForMaxRate))
      ? Number(pacerCfg.backlogForMaxRate)
      : 300;
    const maxCharsPerTick = Number.isFinite(Number(pacerCfg.maxCharsPerTick))
      ? Number(pacerCfg.maxCharsPerTick)
      : 16;
    const maxBudgetChars = Number.isFinite(Number(pacerCfg.maxBudgetChars))
      ? Number(pacerCfg.maxBudgetChars)
      : 36;
    const boundaryModeMultiplier = Number.isFinite(Number(pacerCfg.boundaryModeMultiplier))
      ? Number(pacerCfg.boundaryModeMultiplier)
      : 1.2;
    const finalizeModeMultiplier = Number.isFinite(Number(pacerCfg.finalizeModeMultiplier))
      ? Number(pacerCfg.finalizeModeMultiplier)
      : 1.35;

    const backlogFactor = Math.min(1, backlogChars / Math.max(1, backlogForMaxRate));
    const effectiveBase = Math.max(1, Math.min(baseCharsPerSecond, maxCharsPerSecond));
    const effectiveMax = Math.max(effectiveBase, maxCharsPerSecond);
    let charsPerSecond = effectiveBase + (effectiveMax - effectiveBase) * backlogFactor;
    const mode = (this.streamingBlockPacerMode || "normal").toString();
    if (mode === "boundary") {
      charsPerSecond *= boundaryModeMultiplier;
    } else if (mode === "finalize") {
      charsPerSecond *= finalizeModeMultiplier;
    }
    charsPerSecond = Math.max(1, charsPerSecond);

    this.streamingBlockPacerBudget =
      (this.streamingBlockPacerBudget || 0) + charsPerSecond * (Math.max(0, dtMs || 16) / 1000);
    this.streamingBlockPacerBudget = Math.min(maxBudgetChars, Math.max(0, this.streamingBlockPacerBudget || 0));

    let revealBudget = Math.floor(this.streamingBlockPacerBudget);
    if (revealBudget <= 0 && this.streamingBlockPacerBudget >= 0.75) revealBudget = 1;
    revealBudget = Math.min(revealBudget, maxCharsPerTick, backlogChars);
    if (revealBudget <= 0) {
      this.scheduleStreamingBlockRender();
      return;
    }

    const nextDirty = new Set();
    let consumedTotal = 0;
    for (let idx = 0; idx < blockIds.length; idx += 1) {
      const blockId = blockIds[idx];
      const wrapper = this.streamingContentBlockEls.get(blockId);
      const ops = this.streamingPendingBlockOps.get(blockId);
      if (!ops || !ops.length) continue;
      if (!wrapper) {
        // The block is registered but its wrapper is delayed (e.g. queued after text reveal).
        // It will be added back to dirty blocks when mountBlock() eventually runs.
        // We do not add it to nextDirty to avoid an infinite RAF busy loop.
        continue;
      }

      if (revealBudget <= 0) {
        nextDirty.add(blockId);
        continue;
      }

      const sliced = this.sliceBlockOpsForBudget(ops, revealBudget);
      if (sliced.emitOps.length) {
        this.applyBlockOps(blockId, sliced.emitOps);
      }
      const consumed = Math.max(0, Number(sliced.consumedChars) || 0);
      consumedTotal += consumed;
      revealBudget = Math.max(0, revealBudget - consumed);

      if (sliced.remainingOps.length) {
        this.streamingPendingBlockOps.set(blockId, sliced.remainingOps);
        nextDirty.add(blockId);
      } else {
        this.streamingPendingBlockOps.delete(blockId);
        this.streamingMissingBlockWrapperCounts.delete(blockId);
      }
    }

    this.streamingBlockPacerBudget = Math.max(0, (this.streamingBlockPacerBudget || 0) - consumedTotal);
    this.streamingDirtyTextBlocks = nextDirty;
    this._flushTextBoundaryActionsIfReady();
    if (this.streamingDirtyTextBlocks.size) {
      this.scheduleStreamingBlockRender();
      return;
    }
    if (this.streamingBlockDeferredActions && this.streamingBlockDeferredActions.length) {
      const actions = this.streamingBlockDeferredActions.slice(0);
      this.streamingBlockDeferredActions = [];
      this.streamingBlockPacerMode = "normal";
      actions.forEach((fn) => {
        try {
          fn();
        } catch (_err) {
          // ignore
        }
      });
      if (this.hasStreamingBlockBacklog()) {
        this.scheduleStreamingBlockRender();
      }
      return;
    }
    this.streamingBlockPacerMode = "normal";
  }

  upsertStreamingContentBlock(block) {
	    if (!block || typeof block !== "object") return;
	    if (!this.streamingBlocksEl) return;
	    const blockType = (block.type || "").toString().trim().toLowerCase();
	    const blockId = (block.block_id || block.blockId || "").toString().trim();
    if (!blockId) return;
    this.streamingContentBlocksById.set(blockId, block);

    // Special handling for email_send_draft: update the existing draft card (avoid duplicate cards)
    if (blockType === "tool_use") {
      const payload = block.payload && typeof block.payload === "object" ? block.payload : {};
      const toolName = (payload.tool_name || "").toString().trim().toLowerCase();

	      if (toolName === "email_send_draft") {
	        const draftId = this.getEmailDraftIdFromToolPayload(payload);
	        const existingDraftCard = draftId ? this.resolveEmailCardByDraftId(draftId) : null;
	        if (existingDraftCard) {
	          const existing = this.streamingContentBlockEls.get(blockId);
	          if (existing && existing !== existingDraftCard && existing.parentNode) {
	            existing.remove();
	          }
	          this.updateEmailPreviewCard(existingDraftCard, payload);
	          // Keep the original block_id on the draft card so the persisted render matches
	          // the streamed DOM (email_send_draft is a state update, not a new UI block).
	          this.streamingContentBlockEls.set(blockId, existingDraftCard);
	          this.updateInlineToolCardsVisibility(this.streamingMessageNode);
	          return;
	        }
	      }
	    }

    const existing = this.streamingContentBlockEls.get(blockId);
    if (existing) {
      const payload = block.payload && typeof block.payload === "object" ? block.payload : {};
      if (blockType === "table") {
        const updated = this.updateStreamingTableBlock(existing, payload);
        if (updated && updated !== existing) {
          this.streamingContentBlockEls.set(blockId, updated);
        }
        this.scheduleScrollToBottom({ behavior: "auto" });
        return;
      }
      if (blockType === "kv") {
        const updated = this.updateStreamingKvBlock(existing, payload);
        if (updated && updated !== existing) {
          this.streamingContentBlockEls.set(blockId, updated);
        }
        this.scheduleScrollToBottom({ behavior: "auto" });
        return;
      }
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

    const el = this.buildContentBlockElement(block, { streaming: true });
    if (!el) return;
    this.applyStreamingBlockEnterAnimation(el);
    this.streamingContentBlockEls.set(blockId, el);
    const parentId = (block.parent_block_id || block.parentBlockId || "").toString().trim();
    if (parentId && this.streamingContentBlockEls.has(parentId)) {
      const parentEl = this.streamingContentBlockEls.get(parentId);
      const container = this.resolveBlockContainer(parentEl);
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

  tableRowsReadyForRender(payload, { streaming = false } = {}) {
    const columns = payload && typeof payload === "object" && Array.isArray(payload.columns) ? payload.columns : [];
    const rows = payload && typeof payload === "object" && Array.isArray(payload.rows) ? payload.rows : [];
    if (!streaming || !columns.length) return rows;

    const readyRows = [];
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      const row = rows[rowIndex];
      if (!row || typeof row !== "object") continue;

      const cells = Array.isArray(row.cells) ? row.cells : [];
      if (!cells.length) continue;

      if (row.__streamComplete !== true) continue;

      readyRows.push(row);
    }
    return readyRows;
  }

  updateStreamingTableBlock(wrapper, payload) {
    if (!wrapper || wrapper.nodeType !== Node.ELEMENT_NODE) return null;
    if (!payload || typeof payload !== "object") return wrapper;
    const columns = Array.isArray(payload.columns) ? payload.columns : [];
    const rows = this.tableRowsReadyForRender(payload, { streaming: true });
    if (!columns.length) return wrapper;

    const existingTable = wrapper.querySelector("table");
    const existingBody = existingTable ? existingTable.querySelector("tbody") : null;
    const existingHead = existingTable ? existingTable.querySelector("thead") : null;

    // If the structure isn't what we expect, fall back to rebuilding.
    if (!existingTable || !existingBody || !existingHead) {
      const replacement = this.buildContentBlockElement(
        { type: "table", block_id: wrapper.dataset.blockId || "", payload },
        { streaming: true },
      );
      if (replacement && wrapper.parentNode) {
        wrapper.replaceWith(replacement);
        return replacement;
      }
      return wrapper;
    }

    // If column count changed, rebuild to avoid drift.
    const headCells = existingHead.querySelectorAll("th");
    if (headCells.length !== columns.length) {
      const replacement = this.buildContentBlockElement(
        { type: "table", block_id: wrapper.dataset.blockId || "", payload },
        { streaming: true },
      );
      if (replacement && wrapper.parentNode) {
        wrapper.replaceWith(replacement);
        return replacement;
      }
      return wrapper;
    }

    // Update title (if present/changed).
    const titleText = typeof payload.title === "string" ? payload.title.trim() : "";
    const existingTitle = wrapper.firstElementChild && wrapper.firstElementChild.tagName === "DIV" ? wrapper.firstElementChild : null;
    if (titleText) {
      if (existingTitle && existingTitle.classList.contains("font-semibold")) {
        if (existingTitle.textContent !== titleText) existingTitle.textContent = titleText;
      }
    }

    // Ensure tbody rows exist and update cell content.
    const existingRows = Array.from(existingBody.querySelectorAll("tr"));
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      const row = rows[rowIndex];
      if (!row || typeof row !== "object") continue;
      const cells = Array.isArray(row.cells) ? row.cells : [];
      

      let tr = existingRows[rowIndex] || null;
      if (!tr) {
        tr = document.createElement("tr");
        existingBody.appendChild(tr);
        existingRows.push(tr);
      }
      tr.className = rowIndex % 2 === 0 ? "bg-background" : "bg-muted/20";
      if (row.rtl) {
        tr.dir = "rtl";
        tr.classList.add("text-right");
      } else {
        tr.removeAttribute("dir");
        tr.classList.remove("text-right");
      }

      const tds = Array.from(tr.querySelectorAll("td"));
      while (tds.length < columns.length) {
        const td = document.createElement("td");
        td.className = "px-3 py-2 align-top text-foreground/90";
        tr.appendChild(td);
        tds.push(td);
      }

      for (let colIndex = 0; colIndex < columns.length; colIndex += 1) {
        const td = tds[colIndex];
        const cellValue = colIndex < cells.length ? cells[colIndex] : "";
        const rendered = this.renderInlineMarkdown(typeof cellValue === "string" ? cellValue : cellValue == null ? "" : String(cellValue));
        if (td.innerHTML !== rendered) {
          td.innerHTML = rendered;
        }
      }
    }

    // Remove extra DOM rows if the payload shrank (rare, but keep consistent).
    if (existingRows.length > rows.length) {
      for (let idx = rows.length; idx < existingRows.length; idx += 1) {
        existingRows[idx].remove();
      }
    }

    // Update note.
    const noteText = typeof payload.note === "string" ? payload.note.trim() : "";
    const noteEl = wrapper.querySelector("p.text-xs");
    if (noteText) {
      if (noteEl) {
        if (noteEl.textContent !== noteText) noteEl.textContent = noteText;
      } else {
        const note = document.createElement("p");
        note.className = "px-4 py-2 text-xs text-muted-foreground border-t border-border/40";
        note.textContent = noteText;
        wrapper.appendChild(note);
      }
    } else if (noteEl) {
      noteEl.remove();
    }

    if (payload.rtl) {
      wrapper.dir = "rtl";
      wrapper.classList.add("text-right");
    } else {
      wrapper.removeAttribute("dir");
      wrapper.classList.remove("text-right");
    }

    return wrapper;
  }

  updateStreamingKvBlock(wrapper, payload) {
    if (!wrapper || wrapper.nodeType !== Node.ELEMENT_NODE) return null;
    if (!payload || typeof payload !== "object") return wrapper;
    const entries = Array.isArray(payload.entries) ? payload.entries : [];
    if (!entries.length) return wrapper;

    const existingTable = wrapper.querySelector("table");
    const existingBody = existingTable ? existingTable.querySelector("tbody") : null;
    if (!existingTable || !existingBody) {
      const replacement = this.buildContentBlockElement({ type: "kv", block_id: wrapper.dataset.blockId || "", payload });
      if (replacement && wrapper.parentNode) {
        wrapper.replaceWith(replacement);
        return replacement;
      }
      return wrapper;
    }

    const existingRows = Array.from(existingBody.querySelectorAll("tr"));
    for (let idx = 0; idx < entries.length; idx += 1) {
      const entry = entries[idx];
      if (!entry || typeof entry !== "object") continue;
      const key = typeof entry.key === "string" ? entry.key.trim() : "";
      if (!key) continue;
      const value = typeof entry.value === "string" ? entry.value : entry.value == null ? "" : String(entry.value);

      let tr = existingRows[idx] || null;
      if (!tr) {
        tr = document.createElement("tr");
        existingBody.appendChild(tr);
        existingRows.push(tr);
      }
      tr.className = idx % 2 === 0 ? "bg-background" : "bg-muted/20";

      let th = tr.querySelector("th");
      let td = tr.querySelector("td");
      if (!th) {
        th = document.createElement("th");
        th.className = "px-3 py-2 align-top text-left text-[10px] font-semibold uppercase tracking-wide text-muted-foreground w-24";
        tr.appendChild(th);
      }
      if (!td) {
        td = document.createElement("td");
        td.className = "px-3 py-2 align-top text-foreground/90";
        tr.appendChild(td);
      }

      if (th.textContent !== key) th.textContent = key;
      const rendered = this.renderInlineMarkdown(value);
      if (td.innerHTML !== rendered) td.innerHTML = rendered;
    }

    if (existingRows.length > entries.length) {
      for (let idx = entries.length; idx < existingRows.length; idx += 1) {
        existingRows[idx].remove();
      }
    }

    // Update title/note (same approach as table).
    const titleText = typeof payload.title === "string" ? payload.title.trim() : "";
    const existingTitle = wrapper.firstElementChild && wrapper.firstElementChild.tagName === "DIV" ? wrapper.firstElementChild : null;
    if (titleText) {
      if (existingTitle && existingTitle.classList.contains("font-semibold")) {
        if (existingTitle.textContent !== titleText) existingTitle.textContent = titleText;
      }
    }

    const noteText = typeof payload.note === "string" ? payload.note.trim() : "";
    const noteEl = wrapper.querySelector("p.text-xs");
    if (noteText) {
      if (noteEl) {
        if (noteEl.textContent !== noteText) noteEl.textContent = noteText;
      } else {
        const note = document.createElement("p");
        note.className = "px-4 py-2 text-xs text-muted-foreground border-t border-border/40";
        note.textContent = noteText;
        wrapper.appendChild(note);
      }
    } else if (noteEl) {
      noteEl.remove();
    }

    if (payload.rtl) {
      wrapper.dir = "rtl";
      wrapper.classList.add("text-right");
    } else {
      wrapper.removeAttribute("dir");
      wrapper.classList.remove("text-right");
    }

    return wrapper;
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
	        body: JSON.stringify(this.getCurrentReferencePayload({ limit: 150 })),
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
    // Use custom email preview card for email tools
    if (toolName === "email_create_draft" || toolName === "email_send_draft") {
      return this.buildEmailPreviewCard(payload, toolName);
    }
    if (toolName === "initiate_phone_call" || toolName === "phone_call") {
      return this.buildPhoneCallApprovalCard(payload);
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

    rowLeft.appendChild(status);
    rowLeft.appendChild(title);
    rowLeft.appendChild(approvalActions);

    row.appendChild(rowLeft);

    const outcome = document.createElement("span");
    outcome.dataset.toolOutcomeInline = "true";
    outcome.className = "mcp-tool-outcome-inline";
    row.appendChild(outcome);

    card.appendChild(row);

    const clarification = document.createElement("div");
    clarification.dataset.toolClarification = "true";
    clarification.className = "mcp-tool-clarification";
    clarification.hidden = true;

    const clarificationText = document.createElement("p");
    clarificationText.dataset.toolClarificationText = "true";
    clarificationText.className = "mcp-tool-clarification__text";
    clarification.appendChild(clarificationText);

    const clarificationActions = document.createElement("div");
    clarificationActions.dataset.toolClarificationActions = "true";
    clarificationActions.className = "mcp-tool-clarification__chips";
    clarification.appendChild(clarificationActions);

    const clarificationMore = document.createElement("div");
    clarificationMore.dataset.toolClarificationMore = "true";
    clarificationMore.className = "mcp-tool-clarification__more";
    clarificationMore.hidden = true;

    const clarificationMoreLabel = document.createElement("div");
    clarificationMoreLabel.dataset.toolClarificationMoreLabel = "true";
    clarificationMoreLabel.className = "mcp-tool-clarification__more-label";
    clarificationMore.appendChild(clarificationMoreLabel);

    const clarificationMoreActions = document.createElement("div");
    clarificationMoreActions.dataset.toolClarificationMoreActions = "true";
    clarificationMoreActions.className = "mcp-tool-clarification__chips";
    clarificationMore.appendChild(clarificationMoreActions);

    clarification.appendChild(clarificationMore);
    card.appendChild(clarification);
    this.attachToolCardEvents(card);
    return card;
  }

  buildPhoneCallApprovalCard(payload) {
    const eventId = (payload?.event_id || payload?.eventId || "").toString().trim();
    if (!eventId) return null;

    const runId = (payload?.run_id || payload?.runId || "").toString().trim();
    const input = payload && typeof payload.input === "object" ? payload.input : {};
    const output = payload && typeof payload.output === "object" ? payload.output : {};
    const phoneNumber = (
      input.phone_number ||
      input.phoneNumber ||
      input.to_phone_number ||
      input.toPhoneNumber ||
      input.phone ||
      input.to ||
      output.to_phone_number ||
      output.toPhoneNumber ||
      output.phone_number ||
      output.phoneNumber ||
      output.to ||
      ""
    )
      .toString()
      .trim();
    const contactName = (input.contact_name || input.contactName || input.name || input.recipient_name || input.recipientName || "")
      .toString()
      .trim();
    const objective = (input.objective || input.reason || input.topic || output.objective || output.reason || output.topic || "")
      .toString()
      .trim();
    const language = (input.language || output.language || "").toString().trim().toLowerCase();
    const rtl = language.startsWith("ar") || /[\u0600-\u06FF]/.test(objective);

    const card = document.createElement("div");
    card.dataset.toolCard = "true";
    card.dataset.toolEventId = eventId;
    card.dataset.toolName = "initiate_phone_call";
    card.dataset.callApprovalCard = "true";
    card.className = "portal-call-approval";
    if (runId) {
      card.dataset.runId = runId;
    }
    if (rtl) {
      card.dir = "rtl";
    }

    const left = document.createElement("div");
    left.className = "portal-call-approval__left";

    const icon = document.createElement("span");
    icon.className = "portal-call-approval__icon";
    icon.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M224.2 89C216.3 70.1 195.7 60.1 176.1 65.4L170.6 66.9C106 84.5 50.8 147.1 66.9 223.3C104 398.3 241.7 536 416.7 573.1C493 589.3 555.5 534 573.1 469.4L574.6 463.9C580 444.2 569.9 423.6 551.1 415.8L453.8 375.3C437.3 368.4 418.2 373.2 406.8 387.1L368.2 434.3C297.9 399.4 241.3 341 208.8 269.3L253 233.3C266.9 222 271.6 202.9 264.8 186.3L224.2 89z"/></svg>`;

    const meta = document.createElement("div");
    meta.className = "portal-call-approval__meta";

    const title = document.createElement("div");
    title.className = "portal-call-approval__title";
    title.dataset.callApprovalTitle = "true";
    title.textContent = contactName || phoneNumber || this.t("Phone call");

    const subtitle = document.createElement("div");
    subtitle.className = "portal-call-approval__subtitle";
    subtitle.dataset.callApprovalSubtitle = "true";
    const subtitleParts = [];
    if (objective) subtitleParts.push(objective);
    if (phoneNumber && contactName) subtitleParts.push(phoneNumber);
    subtitle.textContent = subtitleParts.join(" · ") || this.t("Outgoing call");
    if (objective) subtitle.title = objective;

    meta.appendChild(title);
    if (subtitle.textContent) meta.appendChild(subtitle);

    left.appendChild(icon);
    left.appendChild(meta);

    const right = document.createElement("div");
    right.className = "portal-call-approval__right";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "portal-call-approval__toggle";
    toggle.dataset.callApprovalToggle = "true";
    toggle.setAttribute("aria-label", this.t("Toggle call details"));
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = `<svg class="portal-call-approval__toggle-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6.5 8.25l3.5 3.5 3.5-3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const pill = document.createElement("span");
    pill.className = "portal-call-approval__pill";
    pill.dataset.callApprovalPill = "true";
    pill.hidden = true;

    const actions = document.createElement("div");
    actions.className = "portal-call-approval__actions";
    actions.dataset.toolApprovalActions = "true";
    actions.hidden = true;
    actions.setAttribute("aria-hidden", "true");

    const approveButton = document.createElement("button");
    approveButton.type = "button";
    approveButton.dataset.toolApprovalAction = "approve";
    approveButton.className = "portal-call-approval__btn portal-call-approval__btn--accept";
    approveButton.setAttribute("aria-label", this.t("Accept call request"));
    approveButton.title = this.t("Accept");
    approveButton.textContent = this.t("Accept");

    const denyButton = document.createElement("button");
    denyButton.type = "button";
    denyButton.dataset.toolApprovalAction = "deny";
    denyButton.className = "portal-call-approval__btn portal-call-approval__btn--deny";
    denyButton.setAttribute("aria-label", this.t("Reject call request"));
    denyButton.title = this.t("Reject");
    denyButton.textContent = this.t("Reject");

    actions.appendChild(approveButton);
    actions.appendChild(denyButton);

    right.appendChild(toggle);
    right.appendChild(pill);
    right.appendChild(actions);

    const row = document.createElement("div");
    row.className = "portal-call-approval__row";
    row.appendChild(left);
    row.appendChild(right);
    card.appendChild(row);

    const details = document.createElement("div");
    details.className = "portal-call-approval__details";
    details.dataset.callApprovalDetails = "true";
    details.hidden = true;
    details.setAttribute("aria-hidden", "true");
    card.appendChild(details);

    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.toggleCallApprovalDetails(card);
    });

    this.attachToolCardEvents(card);
    this.updatePhoneCallApprovalCard(card, payload);
    return card;
  }

  updatePhoneCallApprovalCard(card, payload) {
    if (!card || !payload) return;

    // Preserve the latest payload so manual approval actions (e.g., run approvals) can
    // update the card without losing the original input fields.
    if (payload && typeof payload === "object") {
      const previous = card._callApprovalLastPayload && typeof card._callApprovalLastPayload === "object" ? card._callApprovalLastPayload : {};
      const merged = { ...previous, ...payload };
      card._callApprovalLastPayload = merged;
      payload = merged;
    }

    const runIdRaw = (payload.run_id || payload.runId || "").toString().trim();
    if (runIdRaw) {
      card.dataset.runId = runIdRaw;
    }

    const phase = (payload.phase || "").toString().trim().toLowerCase();
    const statusRaw = (payload.status || "").toString().trim().toLowerCase();
    const approvalData = payload.approval && typeof payload.approval === "object" ? payload.approval : null;

    const approvalId =
      (payload.approval_id || payload.approvalId || (approvalData && approvalData.id) || card.dataset.approvalId || "")
        .toString()
        .trim();
    if (approvalId) {
      card.dataset.approvalId = approvalId;
    }

    let approvalStatus = "";
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

    const input = payload && typeof payload.input === "object" ? payload.input : {};
    const output = payload && typeof payload.output === "object" ? payload.output : {};
    const phoneNumber = (
      input.phone_number ||
      input.phoneNumber ||
      input.to_phone_number ||
      input.toPhoneNumber ||
      input.phone ||
      input.to ||
      output.to_phone_number ||
      output.toPhoneNumber ||
      output.phone_number ||
      output.phoneNumber ||
      output.to ||
      ""
    )
      .toString()
      .trim();
    const contactName = (input.contact_name || input.contactName || input.name || input.recipient_name || input.recipientName || "")
      .toString()
      .trim();
    const objective = (input.objective || input.reason || input.topic || output.objective || output.reason || output.topic || "")
      .toString()
      .trim();
    const language = (input.language || output.language || "").toString().trim().toLowerCase();
    const rtl = language.startsWith("ar") || /[\u0600-\u06FF]/.test(objective);
    if (rtl) {
      card.dir = "rtl";
    } else if (card.getAttribute("dir") === "rtl") {
      card.removeAttribute("dir");
    }

    const titleEl = card.querySelector("[data-call-approval-title]");
    const subtitleEl = card.querySelector("[data-call-approval-subtitle]");
    if (titleEl) {
      titleEl.textContent = contactName || phoneNumber || this.t("Phone call");
    }
    if (subtitleEl) {
      const subtitleParts = [];
      if (objective) subtitleParts.push(objective);
      if (phoneNumber && contactName) subtitleParts.push(phoneNumber);
      subtitleEl.textContent = subtitleParts.join(" · ") || this.t("Outgoing call");
      subtitleEl.title = objective || subtitleEl.textContent;
    }

    const approveBtn = card.querySelector('[data-tool-approval-action="approve"]');
    const denyBtn = card.querySelector('[data-tool-approval-action="deny"]');
    const actionsEl = card.querySelector("[data-tool-approval-actions]");
    const pillEl = card.querySelector("[data-call-approval-pill]");

    const isApproved = approvalStatus === "approved" || statusRaw === "approved";
    const isDenied = approvalStatus === "denied" || statusRaw === "denied";

    const expiresAtRaw =
      (approvalData && (approvalData.expires_at || approvalData.expiresAt)) ||
      payload.expires_at ||
      payload.expiresAt ||
      "";
    let expiredByTime = false;
    if (expiresAtRaw) {
      const parsed = new Date(expiresAtRaw.toString());
      if (!Number.isNaN(parsed.getTime())) {
        expiredByTime = Date.now() >= parsed.getTime();
      }
    }

    const isExpired = approvalStatus === "expired" || statusRaw === "expired" || (expiredByTime && !isApproved && !isDenied);
    const isPending =
      Boolean(approvalId) &&
      !isExpired &&
      (approvalStatus === "pending" || statusRaw === "pending_approval" || statusRaw === "pending" || phase === "approval_requested");

    if (actionsEl) {
      actionsEl.hidden = !isPending;
      actionsEl.setAttribute("aria-hidden", isPending ? "false" : "true");
    }

    if (approveBtn) {
      approveBtn.disabled = !isPending;
      approveBtn.classList.toggle("opacity-60", !isPending);
      approveBtn.classList.toggle("cursor-not-allowed", !isPending);
    }
    if (denyBtn) {
      denyBtn.disabled = !isPending;
      denyBtn.classList.toggle("opacity-60", !isPending);
      denyBtn.classList.toggle("cursor-not-allowed", !isPending);
    }

    let pillLabel = "";
    let pillVariant = "muted";

    const hasCallSession =
      output &&
      (output.call_session_id ||
        output.callSessionId ||
        output.call_session ||
        output.callSession ||
        output.request_id ||
        output.requestId);

    if (isPending) {
      pillLabel = "";
    } else if (isDenied) {
      pillLabel = this.t("Rejected");
      pillVariant = "error";
    } else if (isExpired) {
      pillLabel = this.t("Approval Expired");
      pillVariant = "muted";
    } else if (isApproved && (statusRaw === "running" || phase === "started")) {
      pillLabel = this.t("Calling…");
      pillVariant = "muted";
    } else if (isApproved) {
      pillLabel = this.t("Approved");
      pillVariant = "success";
    } else if (statusRaw === "running" || phase === "started") {
      pillLabel = this.t("Calling…");
      pillVariant = "muted";
    } else if (hasCallSession && (statusRaw === "ok" || statusRaw === "needs_external" || phase === "finished")) {
      pillLabel = this.t("Queued");
      pillVariant = "muted";
    } else if (statusRaw === "throttled") {
      pillLabel = this.t("Throttled");
      pillVariant = "error";
    } else if (statusRaw === "error" || statusRaw === "failed" || statusRaw === "failure") {
      pillLabel = this.t("Failed");
      pillVariant = "error";
    }

    if (pillEl) {
      pillEl.textContent = pillLabel;
      pillEl.hidden = Boolean(isPending) || !pillLabel;
      pillEl.dataset.variant = pillVariant;
    }

    this.updateCallApprovalDetails(card, {
      input,
      approvalData,
      phoneNumber,
      contactName,
      objective,
      isPending,
    });
  }

  updateCallApprovalDetails(card, { input, approvalData, phoneNumber, contactName, objective, isPending } = {}) {
    if (!card) return;
    const wrap = card.querySelector("[data-call-approval-details]");
    if (!wrap) return;

    const preview = approvalData && typeof approvalData.preview === "object" ? approvalData.preview : null;
    const previewFields = preview && Array.isArray(preview.fields) ? preview.fields : null;
    const previewBody =
      preview && typeof preview.body === "string"
        ? preview.body.toString().trim()
        : preview && typeof preview.body_text === "string"
          ? preview.body_text.toString().trim()
          : preview && typeof preview.bodyText === "string"
            ? preview.bodyText.toString().trim()
            : "";
    const items = [];

    const pushItem = (label, value) => {
      const key = (label || "").toString().trim();
      const val = value != null ? value.toString().trim() : "";
      if (!key || !val) return;
      items.push({ key, val });
    };

    const seenKeys = new Set();
    const recordKey = (label) => {
      const normalized = (label || "").toString().trim().toLowerCase();
      if (!normalized) return;
      seenKeys.add(normalized);
    };

    if (previewFields && previewFields.length) {
      previewFields.forEach((field) => {
        if (!field || typeof field !== "object") return;
        const label = field.label;
        const value = field.value;
        pushItem(label, value);
        recordKey(label);
      });
    }

    // Ensure core call inputs are always visible, even if the tool preview is incomplete.
    const inputObj = input && typeof input === "object" ? input : {};
    const ensureItem = (label, value) => {
      const normalized = (label || "").toString().trim().toLowerCase();
      if (!normalized) return;
      if (seenKeys.has(normalized)) return;
      pushItem(label, value);
      recordKey(label);
    };

    ensureItem(this.t("To"), phoneNumber || "");
    ensureItem(this.t("Contact"), contactName || "");
    ensureItem(this.t("Objective"), objective || "");
    ensureItem(this.t("Type"), (inputObj.call_type || inputObj.callType || "").toString().trim());
    ensureItem(this.t("Language"), (inputObj.language || "").toString().trim());
    const maxDuration = inputObj.max_duration_minutes || inputObj.maxDurationMinutes || inputObj.max_duration || "";
    if (maxDuration !== "" && maxDuration != null) {
      const num = Number(maxDuration);
      const label = Number.isFinite(num) && num > 0 ? `${num} min` : maxDuration.toString();
      ensureItem(this.t("Max duration"), label);
    }

    wrap.innerHTML = "";
    if (!items.length) {
      // If we have no details, keep it hidden and keep the toggle but inert.
      this.setCallApprovalDetailsOpen(card, false, { userAction: false });
      return;
    }

    const grid = document.createElement("div");
    grid.className = "portal-call-approval__kv";
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "portal-call-approval__kv-row";
      const keyEl = document.createElement("div");
      keyEl.className = "portal-call-approval__kv-key";
      keyEl.textContent = item.key;
      const valEl = document.createElement("div");
      valEl.className = "portal-call-approval__kv-val";
      valEl.textContent = item.val;
      row.appendChild(keyEl);
      row.appendChild(valEl);
      grid.appendChild(row);
    });
    wrap.appendChild(grid);

    if (previewBody) {
      const formattedContext = this.formatCallApprovalContextText(previewBody);
      const context = document.createElement("div");
      context.className = "portal-call-approval__context";

      const contextLabel = document.createElement("div");
      contextLabel.className = "portal-call-approval__context-label";
      contextLabel.textContent = this.t("Context");

      const contextBody = document.createElement("div");
      contextBody.className = "portal-call-approval__context-body";
      contextBody.textContent = formattedContext || previewBody;

      context.appendChild(contextLabel);
      context.appendChild(contextBody);
      wrap.appendChild(context);
    }

    const hasUserOverride = card.dataset.callApprovalDetailsUser === "true";
    if (!hasUserOverride) {
      // Default: open while pending (so the user can review what they're approving),
      // otherwise keep it collapsed.
      this.setCallApprovalDetailsOpen(card, Boolean(isPending), { userAction: false });
    }
  }

  formatCallApprovalContextText(text) {
    const raw = (text || "").toString().trim();
    if (!raw) return "";

    const formatKey = (key) => {
      const src = (key || "").toString().trim();
      if (!src) return "";
      const normalized = src
        .replace(/[_-]+/g, " ")
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .replace(/\s+/g, " ")
        .trim();
      if (!normalized) return "";
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    };

    const stringifyValue = (val) => {
      if (val == null) return "";
      if (typeof val === "string") return val.trim();
      if (typeof val === "number" || typeof val === "boolean") return String(val);
      if (Array.isArray(val)) {
        const simple = val.every((item) => item == null || ["string", "number", "boolean"].includes(typeof item));
        if (simple) {
          return val
            .map((item) => (item == null ? "" : String(item).trim()))
            .filter(Boolean)
            .join(", ")
            .trim();
        }
        try {
          return JSON.stringify(val);
        } catch (err) {
          return String(val).trim();
        }
      }
      if (typeof val === "object") {
        const obj = val;
        const title = (obj.title || obj.label || obj.name || "").toString().trim();
        const value = (obj.value || obj.content || obj.text || "").toString().trim();
        if (title && value) return `${title}: ${value}`;
        if (value) return value;
        if (title) return title;
        try {
          return JSON.stringify(obj);
        } catch (err) {
          return String(obj).trim();
        }
      }
      return String(val).trim();
    };

    const tryParseJson = (line) => {
      const trimmed = (line || "").toString().trim();
      if (!trimmed) return null;
      const looksJson =
        (trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"));
      if (!looksJson) return null;
      try {
        return JSON.parse(trimmed);
      } catch (err) {
        return null;
      }
    };

    const outputLines = [];
    const pushLine = (line) => {
      const val = (line || "").toString();
      if (!val.trim()) return;
      outputLines.push(val);
    };

    raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        const parsed = tryParseJson(line);
        if (parsed && typeof parsed === "object") {
          if (Array.isArray(parsed)) {
            parsed.forEach((item) => {
              const textVal = stringifyValue(item);
              if (textVal) pushLine(`- ${textVal}`);
            });
          } else {
            Object.entries(parsed).forEach(([key, val]) => {
              const textVal = stringifyValue(val);
              if (!textVal) return;
              const label = formatKey(key);
              pushLine(label ? `${label}: ${textVal}` : textVal);
            });
          }
          return;
        }

        // Heuristic: treat short "Label: Value" lines as structured.
        const colonIndex = line.indexOf(":");
        if (colonIndex > 0 && colonIndex < 40) {
          const left = line.slice(0, colonIndex).trim();
          const right = line.slice(colonIndex + 1).trim();
          const leftHasUrl = left.includes("http") || left.includes("://");
          if (left && right && !leftHasUrl) {
            const label = formatKey(left);
            pushLine(label ? `${label}: ${right}` : line);
            return;
          }
        }

        pushLine(line);
      });

    return outputLines.join("\n").trim();
  }

  setCallApprovalDetailsOpen(card, open, { userAction = true } = {}) {
    if (!card) return;
    const wrap = card.querySelector("[data-call-approval-details]");
    const toggle = card.querySelector("[data-call-approval-toggle]");
    const desired = Boolean(open);

    if (wrap) {
      wrap.hidden = !desired;
      wrap.setAttribute("aria-hidden", desired ? "false" : "true");
    }
    if (toggle) {
      toggle.setAttribute("aria-expanded", desired ? "true" : "false");
      toggle.title = desired ? this.t("Hide details") : this.t("Show details");
    }
    card.dataset.callApprovalDetailsOpen = desired ? "true" : "false";
    if (userAction) {
      card.dataset.callApprovalDetailsUser = "true";
    }
  }

  toggleCallApprovalDetails(card) {
    if (!card) return;
    const current = card.dataset.callApprovalDetailsOpen === "true";
    this.setCallApprovalDetailsOpen(card, !current, { userAction: true });
  }

  buildEmailPreviewCard(payload, toolName) {
    const eventId = (payload.event_id || payload.eventId || "").toString().trim();
    if (!eventId) return null;

    const card = document.createElement("div");
    card.className = "email-preview-card";
    card.dataset.emailCard = "true";
    card.dataset.toolEventId = eventId;
    card.dataset.toolName = toolName;

    // Extract email data from tool arguments
    const input = payload.input || {};
    const to = Array.isArray(input.to) ? input.to : [input.to].filter(Boolean);
    const cc = Array.isArray(input.cc) ? input.cc : [];
    const bcc = Array.isArray(input.bcc) ? input.bcc : [];
    const subject = input.subject || "";
    const body = input.body_text || input.body || "";
    const shouldStream = this.isStreaming && !this.hydratingExistingMessages && !this.finalizingTurn;
    const setFieldValue = (element, value) => {
      if (!element) return;
      const text = value != null ? String(value) : "";
      element.dataset.fullText = this.escapeHtml(text);
      if (!shouldStream) {
        element.textContent = text;
        element.classList.add("email-stream-complete");
      }
    };

    // Store original content for streaming
    card._emailData = { to, cc, bcc, subject, body };
    card._emailStreamTimers = [];

    // Build email preview container
    const container = document.createElement("div");
    container.className = "email-preview-container";

    // Header
    const header = document.createElement("div");
    header.className = "email-preview-header";

    const headerMain = document.createElement("div");
    headerMain.className = "email-preview-header-main";

    const icon = document.createElement("span");
    icon.className = "email-preview-icon";
    icon.textContent = "✉️";

    const status = document.createElement("span");
    status.className = "email-preview-status";
    status.textContent = toolName === "email_send_draft" ? this.t("Sending email...") : this.t("Creating draft...");

    const summary = document.createElement("span");
    summary.className = "email-preview-summary";

    headerMain.appendChild(icon);
    headerMain.appendChild(status);
    headerMain.appendChild(summary);

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "email-preview-toggle";
    toggleBtn.dataset.action = "toggle";
    toggleBtn.textContent = this.t("Hide email");
    toggleBtn.setAttribute("aria-expanded", "true");

    header.appendChild(headerMain);
    header.appendChild(toggleBtn);

    // Fields container
    const fieldsContainer = document.createElement("div");
    fieldsContainer.className = "email-preview-fields";

    // To field
    const toField = document.createElement("div");
    toField.className = "email-field";
    toField.dataset.field = "to";
    const toLabel = document.createElement("span");
    toLabel.className = "email-field-label";
    toLabel.textContent = `${this.t("To")}:`;
    const toValue = document.createElement("span");
    toValue.className = "email-field-value";
    setFieldValue(toValue, to.join(", "));
    toField.appendChild(toLabel);
    toField.appendChild(toValue);
    fieldsContainer.appendChild(toField);

    // CC field (if present)
    if (cc.length > 0) {
      const ccField = document.createElement("div");
      ccField.className = "email-field";
      ccField.dataset.field = "cc";
      const ccLabel = document.createElement("span");
      ccLabel.className = "email-field-label";
      ccLabel.textContent = `${this.t("CC")}:`;
      const ccValue = document.createElement("span");
      ccValue.className = "email-field-value";
      setFieldValue(ccValue, cc.join(", "));
      ccField.appendChild(ccLabel);
      ccField.appendChild(ccValue);
      fieldsContainer.appendChild(ccField);
    }

    // Subject field
    const subjectField = document.createElement("div");
    subjectField.className = "email-field";
    subjectField.dataset.field = "subject";
    const subjectLabel = document.createElement("span");
    subjectLabel.className = "email-field-label";
    subjectLabel.textContent = `${this.t("Subject")}:`;
    const subjectValue = document.createElement("span");
    subjectValue.className = "email-field-value";
    setFieldValue(subjectValue, subject);
    subjectField.appendChild(subjectLabel);
    subjectField.appendChild(subjectValue);
    fieldsContainer.appendChild(subjectField);

    // Body field
    const bodyField = document.createElement("div");
    bodyField.className = "email-field email-field-body";
    bodyField.dataset.field = "body";
    const bodyLabel = document.createElement("span");
    bodyLabel.className = "email-field-label";
    bodyLabel.textContent = `${this.t("Message")}:`;
    const bodyContent = document.createElement("div");
    bodyContent.className = "email-field-value email-body-content";
    setFieldValue(bodyContent, body);
    bodyField.appendChild(bodyLabel);
    bodyField.appendChild(bodyContent);
    fieldsContainer.appendChild(bodyField);

    // Edit actions container (hidden by default)
    const editActions = document.createElement("div");
    editActions.className = "email-preview-actions";
    editActions.hidden = true;
    const editBtn = document.createElement("button");
    editBtn.className = "email-action-btn email-edit-btn";
    editBtn.dataset.action = "edit";
    editBtn.textContent = "Edit";
    editActions.appendChild(editBtn);

    // Approval actions container (hidden by default)
    const approvalActions = document.createElement("div");
    approvalActions.className = "email-approval-actions";
    approvalActions.hidden = true;
    const approveBtn = document.createElement("button");
    approveBtn.className = "email-action-btn email-approve-btn";
    approveBtn.dataset.action = "approve";
    approveBtn.textContent = "Send Email";
    const rejectBtn = document.createElement("button");
    rejectBtn.className = "email-action-btn email-reject-btn";
    rejectBtn.dataset.action = "reject";
    rejectBtn.textContent = "Don't Send";
    approvalActions.appendChild(approveBtn);
    approvalActions.appendChild(rejectBtn);

    const bodyWrap = document.createElement("div");
    bodyWrap.className = "email-preview-body";
    bodyWrap.appendChild(fieldsContainer);
    bodyWrap.appendChild(editActions);
    bodyWrap.appendChild(approvalActions);

    // Assemble container
    container.appendChild(header);
    container.appendChild(bodyWrap);
    card.appendChild(container);

    // Attach event listeners
    this.attachEmailCardEvents(card);

    // Apply approval/status state if present (ensures approval_id is captured)
    this.updateEmailPreviewCard(card, payload);
    this.updateEmailPreviewSummary(card, { to, subject });
    this.setEmailPreviewCollapsed(card, false);

    if (shouldStream) {
      requestAnimationFrame(() => {
        if (!this.isStreaming || this.finalizingTurn || card.dataset.emailStreamCancelled === "true") return;
        card.classList.add("email-card-animate-in");
        const fields = fieldsContainer.querySelectorAll(".email-field");
        fields.forEach((field, index) => {
          const timer = setTimeout(() => {
            if (!this.isStreaming || this.finalizingTurn || card.dataset.emailStreamCancelled === "true") return;
            field.classList.add("email-field-visible");
            const valueEl = field.querySelector(".email-field-value");
            if (valueEl && valueEl.dataset.fullText) {
              this.streamEmailFieldContent(valueEl, valueEl.dataset.fullText);
            }
          }, index * 150);
          card._emailStreamTimers.push(timer);
        });
      });
    } else {
      const fields = fieldsContainer.querySelectorAll(".email-field");
      fields.forEach((field) => {
        field.classList.add("email-field-visible");
      });
    }

    return card;
  }

  streamEmailFieldContent(element, fullText) {
    if (!element || !fullText) return;

    // Decode HTML entities for display
    const tempDiv = document.createElement("div");
    tempDiv.innerHTML = fullText;
    const decodedText = tempDiv.textContent || tempDiv.innerText || "";

    if (!this.isStreaming || this.finalizingTurn) {
      element.textContent = decodedText;
      element.classList.add("email-stream-complete");
      return;
    }

    if (element._streamInterval) {
      clearInterval(element._streamInterval);
      element._streamInterval = null;
    }

    let currentIndex = 0;
    const isBodyContent = element.classList.contains("email-body-content");

    // Faster streaming for shorter content, slower for body
    const chunkSize = isBodyContent ? 3 : 5;
    const intervalTime = isBodyContent ? 8 : 5;

    element.textContent = "";

    const streamInterval = setInterval(() => {
      if (!this.isStreaming || this.finalizingTurn) {
        clearInterval(streamInterval);
        element._streamInterval = null;
        element.textContent = decodedText;
        element.classList.add("email-stream-complete");
        return;
      }
      if (currentIndex >= decodedText.length) {
        clearInterval(streamInterval);
        element._streamInterval = null;
        element.classList.add("email-stream-complete");
        return;
      }

      const nextChunk = decodedText.slice(currentIndex, currentIndex + chunkSize);
      element.textContent += nextChunk;
      currentIndex += chunkSize;

      // Auto-scroll body content as it streams
      if (isBodyContent && element.scrollHeight > element.clientHeight) {
        element.scrollTop = element.scrollHeight;
      }
    }, intervalTime);

    // Store interval ID in case we need to cancel it
    element._streamInterval = streamInterval;
  }

	  updateToolEventCard(card, payload, { skipMinRunDelay = false } = {}) {
	    if (!card || !payload) return;
	    // Handle email preview cards with custom logic
	    if (card.dataset.emailCard === "true") {
	      this.updateEmailPreviewCard(card, payload);
	      return;
	    }
      if (card.dataset.callApprovalCard === "true") {
        this.updatePhoneCallApprovalCard(card, payload);
        return;
      }
	    const phase = (payload.phase || "").toString().trim().toLowerCase();
	    const statusRaw = (payload.status || "").toString().trim().toLowerCase();
	    const remote = payload.remote && typeof payload.remote === "object" ? payload.remote : null;

    const connectionNameRaw = remote && remote.connection_name ? remote.connection_name.toString() : "";
    const remoteTool = remote && remote.remote_tool ? remote.remote_tool.toString() : "";
    const toolNameFallback = (payload.tool_name || payload.toolName || "").toString().trim();
    const effectiveInternalTool = this.getEffectiveToolName(toolNameFallback, payload);
    const normalizedInternalTool = effectiveInternalTool || this.normalizeToolName(toolNameFallback);
    const internalToolLabel = normalizedInternalTool === "mcp_search_tools" ? "Tool discovery" : "";
    const titleEl = card.querySelector("[data-tool-title]");
    const existingTitle = titleEl ? titleEl.textContent : "";
    const displayTool = remoteTool || internalToolLabel || effectiveInternalTool || toolNameFallback || "";

    const connectionName = connectionNameRaw.replace(/\s*\(mcp\)\s*$/i, "").trim();
    const displayToolLabel = displayTool ? this.formatStatus(displayTool) : "";
    const titleText =
      connectionName && displayToolLabel
        ? `${connectionName} · ${displayToolLabel}`
        : displayToolLabel || connectionName || existingTitle || "External tool";

    if (titleEl) titleEl.textContent = titleText;
    if (effectiveInternalTool) {
      card.dataset.toolName = effectiveInternalTool;
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

    let effectiveStatus = statusRaw;
    if (!effectiveStatus) {
      if (approvalStatus === "pending") {
        effectiveStatus = "pending_approval";
      } else if (approvalStatus) {
        effectiveStatus = approvalStatus;
      }
    }
	    const isRunning = effectiveStatus === "running" || phase === "started";

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

    // Ensure "running" is perceptible even when the tool completes extremely fast.
    // Without this, started->finished can happen before the browser paints, and the user
    // only ever sees the final dot. Modern UIs typically enforce a small minimum.
    const nowMs = Date.now();
    const minRunningMs = 180;
    if (toolState === "running") {
      if (!card._toolRunningSince) {
        card._toolRunningSince = nowMs;
      }
      if (card._toolFinalizeTimer) {
        clearTimeout(card._toolFinalizeTimer);
        card._toolFinalizeTimer = null;
      }
      card._toolFinalizePendingPayload = null;
    } else if (
      !skipMinRunDelay &&
      this.isStreaming &&
      !this.finalizingTurn &&
      previousToolState === "running" &&
      (toolState === "success" || toolState === "error")
    ) {
      const startedAt = Number(card._toolRunningSince || 0) || nowMs;
      const elapsed = nowMs - startedAt;
      if (elapsed >= 0 && elapsed < minRunningMs) {
        const delay = Math.max(0, minRunningMs - elapsed);
        card._toolFinalizePendingPayload = payload;
        if (card._toolFinalizeTimer) {
          clearTimeout(card._toolFinalizeTimer);
        }
        card._toolFinalizeTimer = setTimeout(() => {
          card._toolFinalizeTimer = null;
          const pendingPayload = card._toolFinalizePendingPayload;
          card._toolFinalizePendingPayload = null;
          if (!pendingPayload || !card.isConnected) return;
          this.updateToolEventCard(card, pendingPayload, { skipMinRunDelay: true });
        }, delay);
        return;
      }
    }
    card.dataset.toolState = toolState;
    if (toolState !== "running") {
      card._toolRunningSince = null;
    }
    if (card._toolFinalizeTimer) {
      clearTimeout(card._toolFinalizeTimer);
      card._toolFinalizeTimer = null;
    }
    card._toolFinalizePendingPayload = null;

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
			        const spinnerEl = iconEl.querySelector(".mcp-status-spinner");
			        const dotEl = iconEl.querySelector(".mcp-status-dot");
			        const legacyDone = iconEl.querySelector(".mcp-status-done");
			        if (legacyDone) legacyDone.remove();

			        if (isRunning) {
			          if (dotEl) dotEl.remove();
			          if (!spinnerEl) {
			            iconEl.innerHTML = this.getOrbitLoaderMarkup();
			          }
			        } else {
			          if (spinnerEl) spinnerEl.remove();
			          if (!dotEl) {
			            iconEl.innerHTML = this.getStatusDotMarkup();
			          }
			        }
			      }
			    }

			    const outcomeEl = card.querySelector("[data-tool-outcome-inline]");
			    if (outcomeEl) {
			      const showOutcome = toolState === "success" || toolState === "error";
			      outcomeEl.classList.toggle("is-visible", showOutcome);
			      outcomeEl.dataset.outcome = showOutcome ? toolState : "";
			      outcomeEl.setAttribute("aria-hidden", showOutcome ? "false" : "true");
			      outcomeEl.title = toolState === "success" ? "Succeeded" : toolState === "error" ? "Failed" : "";
			      if (showOutcome) {
			        outcomeEl.innerHTML = toolState === "success"
			          ? `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 8.5l2.5 2.5L12 5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`
			          : `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M5 5l6 6M11 5l-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
			      } else {
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
    this.renderToolScopeClarification(card, payload);
    this.updateToolApprovalPanel(card, payload);

    if (!card.dataset.toolTabUser) {
      const preferredTab = (outputProvided || hasStoredOutput) && !isRunning ? "output" : "input";
      this.setToolCardTab(card, preferredTab);
    }
  }

  updateToolApprovalPanel(card, payload) {
    if (!card) return;
    const approvalWrap = card.querySelector("[data-tool-approval]");
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

    const approveBtn = card.querySelector('[data-tool-approval-action="approve"]');
    const denyBtn = card.querySelector('[data-tool-approval-action="deny"]');

    const isPending = status === "pending" || status === "pending_approval";
    const isApproved = status === "approved";
    const isDenied = status === "denied";
    const isExpired = status === "expired";

    if (approvalWrap) {
      if (!approvalId) {
        approvalWrap.classList.add("hidden");
      } else {
        approvalWrap.classList.remove("hidden");
        const titleEl = approvalWrap.querySelector("[data-tool-approval-title]");
        const metaEl = approvalWrap.querySelector("[data-tool-approval-meta]");
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
      }
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
	        body: JSON.stringify(
            this.getCurrentReferencePayload({
              approval_id: approvalId,
              decision: action,
            }),
          ),
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

  getEmailDraftIdFromToolPayload(payload) {
    if (!payload || typeof payload !== "object") return "";
    const input = payload.input && typeof payload.input === "object" ? payload.input : null;
    const output = payload.output && typeof payload.output === "object" ? payload.output : null;
    const outputPreview = payload.output_preview && typeof payload.output_preview === "object" ? payload.output_preview : null;

    const draftId =
      (input && (input.draft_id || input.draftId)) ||
      (output && (output.draft_id || output.draftId)) ||
      (outputPreview && (outputPreview.draft_id || outputPreview.draftId)) ||
      "";

    return draftId ? draftId.toString().trim() : "";
  }

  resolveEmailCardByDraftId(draftId) {
    const normalized = (draftId || "").toString().trim();
    if (!normalized) return null;
    const fromMap = this.draftIdToCardMap.get(normalized);
    if (fromMap) return fromMap;
    const root = this.elements.messages || this.elements.messagesInner || this.container;
    if (!root || !root.querySelector) return null;
    const escape = window.CSS && typeof window.CSS.escape === "function" ? window.CSS.escape : (value) => value;
    const found = root.querySelector(`[data-email-card="true"][data-draft-id="${escape(normalized)}"]`);
    if (found) {
      this.draftIdToCardMap.set(normalized, found);
      return found;
    }
    return null;
  }

  async submitEmailDraftAction(action, card) {
    if (!card) return;
    const normalized = (action || "").toString().trim().toLowerCase();
    const isSend = normalized === "send" || normalized === "approve";
    const endpoint = isSend ? this.endpoints.emailSendDraft : this.endpoints.emailDiscardDraft;
    if (!endpoint) {
      this.showToast("Email unavailable", "Email draft endpoint is not configured.", true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast("Email unavailable", "Session token missing.", true);
      return;
    }

    const draftId = (card.dataset.draftId || "").toString().trim();
    if (!draftId) {
      this.showToast("Draft unavailable", "Draft request is missing an identifier.", true);
      return;
    }
    if (card.dataset.emailDraftBusy === "true") {
      return;
    }
    card.dataset.emailDraftBusy = "true";

    const approveBtn = card.querySelector('[data-action="approve"]');
    const rejectBtn = card.querySelector('[data-action="reject"]');
    const editBtn = card.querySelector('[data-action="edit"], [data-action="save"]');
    [approveBtn, rejectBtn, editBtn].filter(Boolean).forEach((btn) => {
      btn.disabled = true;
      btn.classList.add("opacity-60", "cursor-not-allowed");
    });

    const statusEl = card.querySelector(".email-preview-status");
    const previousStatus = statusEl ? statusEl.textContent : "";
    if (statusEl) {
      statusEl.textContent = isSend ? "Sending..." : "Not sending...";
    }

    try {
	      const response = await fetch(endpoint, {
	        method: "POST",
	        headers: this.jsonHeaders(),
	        body: JSON.stringify(
            this.getCurrentReferencePayload({
              draft_id: draftId,
              email_account_id: card.dataset.emailAccountId || "",
            }),
          ),
	      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : "Email action failed.";
        throw new Error(message);
      }

      if (isSend) {
        this.updateEmailPreviewCard(card, {
          phase: "finished",
          status: "ok",
          tool_name: "email_send_draft",
          input: { draft_id: draftId },
        });
      } else {
        this.updateEmailPreviewCard(card, {
          phase: "approval_resolved",
          status: "denied",
          tool_name: "email_send_draft",
          input: { draft_id: draftId },
        });
      }

      if (isSend) {
        this.showToast("Email sent", "The draft email was sent successfully.");
      } else {
        this.showToast("Email not sent", "The draft email was not sent.");
      }
    } catch (error) {
      console.warn("Email draft action failed", error);
      if (statusEl) {
        statusEl.textContent = previousStatus || "Draft created";
      }
      this.showToast("Email action failed", error.message || "Please try again.", true);
      [approveBtn, rejectBtn, editBtn].filter(Boolean).forEach((btn) => {
        btn.disabled = false;
        btn.classList.remove("opacity-60", "cursor-not-allowed");
      });
      return;
    } finally {
      card.dataset.emailDraftBusy = "false";
    }

    // After completion: lock send/reject, but keep Edit available on "Don't send"
    [approveBtn, rejectBtn].filter(Boolean).forEach((btn) => {
      btn.disabled = true;
      btn.classList.add("opacity-60", "cursor-not-allowed");
    });
    if (editBtn) {
      editBtn.disabled = isSend;
      editBtn.classList.toggle("opacity-60", isSend);
      editBtn.classList.toggle("cursor-not-allowed", isSend);
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
        const runId = (card.dataset.runId || "").toString().trim();
        if (runId) {
          this.submitRunApproval(runId, approvalId, decision, card);
          return;
        }
        this.submitToolApproval(approvalId, decision, card);
      });
    });
  }

  attachEmailCardEvents(card) {
    if (!card || card.dataset.emailEventsBound === "true") return;
    card.dataset.emailEventsBound = "true";

    const toggleBtn = card.querySelector('[data-action="toggle"]');
    if (toggleBtn) {
      toggleBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.toggleEmailPreviewCard(card);
      });
    }

    // Edit button
    const editBtn = card.querySelector('[data-action="edit"]');
    if (editBtn) {
      editBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.makeEmailFieldsEditable(card);
      });
    }

    // Approve button
    const approveBtn = card.querySelector('[data-action="approve"]');
    if (approveBtn) {
      approveBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const approvalId = card.dataset.approvalId;
        if (approvalId) {
          this.submitToolApproval(approvalId, "approve", card);
          return;
        }
        this.submitEmailDraftAction("send", card);
      });
    }

    // Reject button
    const rejectBtn = card.querySelector('[data-action="reject"]');
    if (rejectBtn) {
      rejectBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const approvalId = card.dataset.approvalId;
        if (approvalId) {
          this.submitToolApproval(approvalId, "deny", card);
          return;
        }
        this.submitEmailDraftAction("discard", card);
      });
    }
  }

  toggleEmailPreviewCard(card) {
    if (!card) return;
    this.updateEmailPreviewSummary(card);
    const isCollapsed = card.dataset.collapsed === "true";
    this.setEmailPreviewCollapsed(card, !isCollapsed);
  }

  setEmailPreviewCollapsed(card, collapsed) {
    if (!card) return;
    const isCollapsed = Boolean(collapsed);
    card.dataset.collapsed = isCollapsed ? "true" : "false";

    const body = card.querySelector(".email-preview-body");
    if (body) {
      // Prevent focus/tabbing into collapsed content without needing JS height animation.
      body.setAttribute("aria-hidden", isCollapsed ? "true" : "false");
      if ("inert" in body) {
        body.inert = isCollapsed;
      }
    }

    const toggleBtn = card.querySelector('[data-action="toggle"]');
    if (toggleBtn) {
      toggleBtn.textContent = isCollapsed ? this.t("Show email") : this.t("Hide email");
      toggleBtn.setAttribute("aria-expanded", (!isCollapsed).toString());
    }
  }

  getEmailPreviewSummaryText(data) {
    if (!data || typeof data !== "object") return this.t("Email details");
    const toRaw = Array.isArray(data.to)
      ? data.to.join(", ")
      : data.to != null
        ? String(data.to)
        : "";
    const subjectRaw = data.subject != null ? String(data.subject) : "";
    const toText = toRaw ? this.clipText(toRaw, 48) : "";
    const subjectText = subjectRaw ? this.clipText(subjectRaw, 64) : "";
    const parts = [];
    if (toText) parts.push(`${this.t("To")}: ${toText}`);
    if (subjectText) parts.push(`${this.t("Subject")}: ${subjectText}`);
    return parts.length ? parts.join(" • ") : this.t("Email details");
  }

  getEmailPreviewFieldValue(card, fieldName) {
    if (!card) return "";
    const field = card.querySelector(`[data-field="${fieldName}"]`);
    if (!field) return "";
    const input = field.querySelector(".email-field-input");
    if (input && typeof input.value === "string") return input.value.trim();
    const value = field.querySelector(".email-field-value");
    return value && value.textContent ? value.textContent.trim() : "";
  }

  updateEmailPreviewSummary(card, overrideData) {
    if (!card) return;
    const summary = card.querySelector(".email-preview-summary");
    if (!summary) return;
    let data = overrideData;
    if (!data || typeof data !== "object") {
      data = {
        to: this.getEmailPreviewFieldValue(card, "to"),
        subject: this.getEmailPreviewFieldValue(card, "subject"),
      };
    }
    summary.textContent = this.getEmailPreviewSummaryText(data);
  }

  updateEmailPreviewCard(card, payload) {
    if (!card || !payload) return;

    const phase = (payload.phase || "").toString().trim().toLowerCase();
    const status = (payload.status || "").toString().trim().toLowerCase();
    const approvalData = payload.approval && typeof payload.approval === "object" ? payload.approval : null;
    const approvalStatusRaw =
      (approvalData && approvalData.status) ||
      payload.approval_status ||
      payload.approvalStatus ||
      card.dataset.approvalStatus ||
      "";
    const approvalStatus = approvalStatusRaw.toString().trim().toLowerCase();
    const toolName = (payload.tool_name || "").toString().trim().toLowerCase();
    const eventId = (payload.event_id || payload.eventId || "").toString().trim();

    const statusEl = card.querySelector(".email-preview-status");
    const actionsEl = card.querySelector(".email-preview-actions");
    const approvalEl = card.querySelector(".email-approval-actions");

    const approvalId =
      (
        payload.approval_id ||
        payload.approvalId ||
        (approvalData && (approvalData.id || approvalData.approval_id || approvalData.approvalId)) ||
        card.dataset.approvalId ||
        ""
      )
        .toString()
        .trim();
    if (approvalId) {
      card.dataset.approvalId = approvalId;
    }
    if (approvalStatus) {
      card.dataset.approvalStatus = approvalStatus;
    }

    if (toolName === "email_send_draft" && eventId) {
      card.dataset.emailSendEventId = eventId;
    }

    // Phase: started (draft creation)
    if (phase === "started" && toolName === "email_create_draft") {
      if (statusEl) statusEl.textContent = this.t("Creating draft...");
    }

    if (phase === "started" && toolName === "email_send_draft") {
      if (statusEl) statusEl.textContent = this.t("Sending email...");
      if (approvalEl) approvalEl.hidden = true;
      if (actionsEl) actionsEl.hidden = true;
      this.scheduleEmailSendReconcile(card);
    }

    // Phase: finished (draft created successfully)
    if (phase === "finished" && toolName === "email_create_draft") {
      if (status === "ok") {
        if (statusEl) statusEl.textContent = this.t("Draft created");
        card.classList.add("email-draft-created");
        if (actionsEl) actionsEl.hidden = false; // Show "Edit" button
        if (approvalEl) approvalEl.hidden = false; // Show "Send/Don't Send" buttons

        const emailAccountId =
          payload.output?.email_account_id ||
          payload.output?.emailAccountId ||
          payload.output_preview?.email_account_id ||
          payload.output_preview?.emailAccountId;
        if (emailAccountId) {
          card.dataset.emailAccountId = emailAccountId;
        }

        // Store draft_id for later reference
        const draftId =
          payload.output?.draft_id ||
          payload.output?.draftId ||
          payload.output?.id ||
          payload.output_preview?.draft_id ||
          payload.output_preview?.draftId ||
          payload.output_preview?.id;
        if (draftId) {
          card.dataset.draftId = draftId;
          this.draftIdToCardMap.set(draftId, card);
        }
      } else {
        if (statusEl) statusEl.textContent = "Draft creation failed";
        card.classList.add("email-error");
      }
    }

    if (phase === "finished" && toolName === "email_send_draft") {
      if (approvalEl) approvalEl.hidden = true;
      if (status === "ok") {
        if (statusEl) statusEl.textContent = "Email sent ✓";
        card.classList.add("email-sent");
        if (actionsEl) actionsEl.hidden = true;
      } else {
        if (statusEl) statusEl.textContent = "Email send failed";
        card.classList.add("email-error");
        if (actionsEl) actionsEl.hidden = false;
      }
    }

    // Phase: approval_requested (send approval needed)
    if (phase === "approval_requested" || approvalStatus === "pending") {
      if (statusEl) statusEl.textContent = "Ready to send";
      if (actionsEl) actionsEl.hidden = false; // Keep Edit available while awaiting approval
      if (approvalEl) approvalEl.hidden = false; // Show Send/Don't Send buttons
      const draftId = this.getEmailDraftIdFromToolPayload(payload) || card.dataset.draftId || "";
      if (draftId) {
        card.dataset.draftId = draftId;
      }
    }

    // Phase: approval_resolved (user approved or rejected)
    if (phase === "approval_resolved") {
      if (approvalEl) approvalEl.hidden = true;

      if (approvalStatus === "approved" || status === "approved") {
        if (statusEl) statusEl.textContent = this.t("Sending email...");
        if (actionsEl) actionsEl.hidden = true;
        this.setEmailPreviewCollapsed(card, true);
        this.scheduleEmailSendReconcile(card);
      } else if (approvalStatus === "denied" || status === "denied") {
        if (statusEl) statusEl.textContent = this.t("Not sent");
        card.classList.add("email-rejected");
        if (actionsEl) actionsEl.hidden = false; // Show edit button again
        this.setEmailPreviewCollapsed(card, true);
      } else if (approvalStatus === "expired" || status === "expired") {
        if (statusEl) statusEl.textContent = this.t("Approval expired");
        card.classList.add("email-rejected");
        if (actionsEl) actionsEl.hidden = false;
        this.setEmailPreviewCollapsed(card, true);
      }
    }

    this.updateEmailPreviewSummary(card);
  }

  scheduleEmailSendReconcile(card) {
    if (!card) return;
    if (card.classList.contains("email-sent") || card.classList.contains("email-error")) return;
    if (card.dataset.emailSendReconcileActive === "true") return;

    const eventId = (card.dataset.emailSendEventId || "").toString().trim();
    if (!eventId) return;
    if (!this.endpoints.toolHistory || !this.sessionToken) return;

    card.dataset.emailSendReconcileActive = "true";

    const attemptReconcile = async (attempt) => {
      if (card.classList.contains("email-sent") || card.classList.contains("email-error")) {
        card.dataset.emailSendReconcileActive = "false";
        return;
      }
      try {
	        const response = await fetch(this.endpoints.toolHistory, {
	          method: "POST",
	          headers: this.jsonHeaders(),
	          body: JSON.stringify(this.getCurrentReferencePayload({ limit: 200 })),
	        });
        const payload = await response.json().catch(() => null);
        if (response.ok && payload && payload.history && Array.isArray(payload.history.toolEvents)) {
          const match = payload.history.toolEvents.find(
            (evt) =>
              evt &&
              String(evt.event_id || evt.eventId || "").trim() === eventId &&
              String(evt.phase || "").trim().toLowerCase() === "finished",
          );
          if (match) {
            const matchStatus = String(match.status || "").trim().toLowerCase() || "ok";
            this.updateEmailPreviewCard(card, {
              phase: "finished",
              status: matchStatus,
              tool_name: "email_send_draft",
              event_id: eventId,
              input: { draft_id: card.dataset.draftId || "" },
            });
            card.dataset.emailSendReconcileActive = "false";
            return;
          }
        }
      } catch (error) {
        // Ignore reconciliation errors; we'll retry a few times.
        void error;
      }

      if (attempt >= 4) {
        card.dataset.emailSendReconcileActive = "false";
        return;
      }
      setTimeout(() => attemptReconcile(attempt + 1), 4000);
    };

    setTimeout(() => attemptReconcile(1), 2500);
  }

  makeEmailFieldsEditable(card) {
    if (!card) return;

    const fields = card.querySelectorAll(".email-field-value");
    fields.forEach((field) => {
      if (field.classList.contains("email-body-content")) {
        // Body field - use textarea
        const textarea = document.createElement("textarea");
        textarea.className = "email-field-input email-body-input";
        textarea.value = field.textContent;
        field.replaceWith(textarea);
      } else {
        // Other fields - use input
        const input = document.createElement("input");
        input.className = "email-field-input";
        input.value = field.textContent;
        field.replaceWith(input);
      }
    });

    // Change Edit button to Save button
    const editBtn = card.querySelector('[data-action="edit"]');
    if (editBtn) {
      editBtn.textContent = "Save Changes";
      editBtn.dataset.action = "save";
      // Remove old event listener and add new one
      const newEditBtn = editBtn.cloneNode(true);
      editBtn.replaceWith(newEditBtn);
      newEditBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.saveEmailFieldChanges(card);
      });
    }
  }

  saveEmailFieldChanges(card) {
    if (!card) return;

    // Convert inputs back to display elements
    const inputs = card.querySelectorAll(".email-field-input");
    inputs.forEach((input) => {
      if (input.classList.contains("email-body-input")) {
        // Body field
        const div = document.createElement("div");
        div.className = "email-field-value email-body-content email-stream-complete";
        div.textContent = input.value;
        input.replaceWith(div);
      } else {
        // Other fields
        const span = document.createElement("span");
        span.className = "email-field-value email-stream-complete";
        span.textContent = input.value;
        input.replaceWith(span);
      }
    });

    card._emailData = {
      to: this.getEmailPreviewFieldValue(card, "to"),
      cc: this.getEmailPreviewFieldValue(card, "cc"),
      bcc: this.getEmailPreviewFieldValue(card, "bcc"),
      subject: this.getEmailPreviewFieldValue(card, "subject"),
      body: this.getEmailPreviewFieldValue(card, "body"),
    };
    this.updateEmailPreviewSummary(card);

    // Change Save button back to Edit button
    const saveBtn = card.querySelector('[data-action="save"]');
    if (saveBtn) {
      saveBtn.textContent = "Edit";
      saveBtn.dataset.action = "edit";
      // Remove old event listener and add new one
      const newSaveBtn = saveBtn.cloneNode(true);
      saveBtn.replaceWith(newSaveBtn);
      newSaveBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.makeEmailFieldsEditable(card);
      });
    }

    this.showToast("Changes saved", "Email edits have been saved locally.");
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


  renderToolScopeClarification(card, payload) {
    if (!card) return;
    const wrap = card.querySelector("[data-tool-clarification]");
    if (!wrap) return;

    // Scope clarification is rendered as a dedicated message-level component.
    // Keep tool cards focused on tool execution details only.
    const textEl = wrap.querySelector("[data-tool-clarification-text]");
    const actionsEl = wrap.querySelector("[data-tool-clarification-actions]");
    const moreWrap = wrap.querySelector("[data-tool-clarification-more]");
    const moreLabelEl = wrap.querySelector("[data-tool-clarification-more-label]");
    const moreActionsEl = wrap.querySelector("[data-tool-clarification-more-actions]");
    wrap.hidden = true;
    if (textEl) textEl.textContent = "";
    if (actionsEl) actionsEl.innerHTML = "";
    if (moreLabelEl) moreLabelEl.textContent = "";
    if (moreActionsEl) moreActionsEl.innerHTML = "";
    if (moreWrap) moreWrap.hidden = true;
  }

  removeStreamingContentBlockById(blockId) {
    const normalizedId = (blockId || "").toString().trim();
    if (!normalizedId) return;
    const wrapper = this.streamingContentBlockEls.get(normalizedId);
    const escapedBlockId =
      typeof CSS !== "undefined" && CSS && typeof CSS.escape === "function"
        ? CSS.escape(normalizedId)
        : normalizedId.replace(/"/g, '\\"');
    const target =
      wrapper ||
      (this.streamingBlocksEl ? this.streamingBlocksEl.querySelector(`[data-block-id="${escapedBlockId}"]`) : null);
    if (target && target.parentNode) {
      target.parentNode.removeChild(target);
    }
    this.streamingContentBlockEls.delete(normalizedId);
    this.streamingContentBlocksById.delete(normalizedId);
    this.streamingPendingBlockOps.delete(normalizedId);
    this.streamingDirtyTextBlocks.delete(normalizedId);
    this.streamingTextBlockActiveIds.delete(normalizedId);
    this.streamingToolBlockActiveIds.delete(normalizedId);
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

  getSuccessCircleIconMarkup() {
    return `
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4.8 8.6l2 2.1 4.4-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
      </svg>
    `;
  }

  getSuccessBadgeIconMarkup() {
    return `
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4.8 8.6l2 2.1 4.4-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
      </svg>
    `;
  }

  getAutomationRunIconMarkup() {
    return `
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M7.7 5.9v8.2l6.2-4.1-6.2-4.1Z" fill="currentColor"></path>
        <path d="M10 2.85a7.15 7.15 0 1 1 0 14.3 7.15 7.15 0 0 1 0-14.3Z" stroke="currentColor" stroke-width="1.2" opacity="0.38"></path>
      </svg>
    `;
  }

  getChevronRightIconMarkup() {
    return `
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M6.25 4.25 9.75 8l-3.5 3.75" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path>
      </svg>
    `;
  }

  getToolCallIconMarkup() {
    return `
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M5.25 4.5 2.75 8l2.5 3.5M10.75 4.5l2.5 3.5-2.5 3.5" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M8.9 3.75 7.1 12.25" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" opacity="0.55"></path>
      </svg>
    `;
  }

  getRunStateIconMarkup(statusRaw, toneRaw) {
    const status = (statusRaw || "").toString().trim().toLowerCase();
    const tone = (toneRaw || "").toString().trim().toLowerCase();
    if (["completed", "resolved", "success", "succeeded"].includes(status) || tone === "success") {
      return `
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M4.15 8.2 6.65 10.7l5.2-5.4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
      `;
    }
    if (["failed", "error", "cancelled", "canceled"].includes(status) || tone === "danger") {
      return `
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M5 5l6 6M11 5l-6 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path>
        </svg>
      `;
    }
    if (["waiting_approval", "waiting_user"].includes(status) || tone === "attention") {
      return `
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M8 4.25v4.25" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path>
          <path d="M8 11.55h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
        </svg>
      `;
    }
    if (["running", "in_progress", "queued", "started"].includes(status) || tone === "active") {
      return `
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M8 3.5v4.7l3 1.75" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
      `;
    }
    return `
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M5 8h6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path>
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

  getFileTypeIcon(type) {
    const icons = {
      pdf: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 4h18l10 10v30a2 2 0 0 1-2 2H12a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" fill="#E53935"/>
        <path d="M30 4v10h10" fill="#FFCDD2"/>
        <path d="M30 4l10 10h-8a2 2 0 0 1-2-2V4z" fill="#FFCDD2"/>
        <text x="24" y="32" text-anchor="middle" fill="white" font-size="10" font-weight="bold" font-family="system-ui">PDF</text>
      </svg>`,
      word: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 4h18l10 10v30a2 2 0 0 1-2 2H12a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" fill="#1976D2"/>
        <path d="M30 4v10h10" fill="#BBDEFB"/>
        <path d="M30 4l10 10h-8a2 2 0 0 1-2-2V4z" fill="#BBDEFB"/>
        <text x="24" y="32" text-anchor="middle" fill="white" font-size="8" font-weight="bold" font-family="system-ui">DOC</text>
      </svg>`,
      excel: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 4h18l10 10v30a2 2 0 0 1-2 2H12a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" fill="#388E3C"/>
        <path d="M30 4v10h10" fill="#C8E6C9"/>
        <path d="M30 4l10 10h-8a2 2 0 0 1-2-2V4z" fill="#C8E6C9"/>
        <text x="24" y="32" text-anchor="middle" fill="white" font-size="8" font-weight="bold" font-family="system-ui">XLS</text>
      </svg>`,
      ppt: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 4h18l10 10v30a2 2 0 0 1-2 2H12a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" fill="#E64A19"/>
        <path d="M30 4v10h10" fill="#FFCCBC"/>
        <path d="M30 4l10 10h-8a2 2 0 0 1-2-2V4z" fill="#FFCCBC"/>
        <text x="24" y="32" text-anchor="middle" fill="white" font-size="8" font-weight="bold" font-family="system-ui">PPT</text>
      </svg>`,
      default: `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 4h18l10 10v30a2 2 0 0 1-2 2H12a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" fill="#78909C"/>
        <path d="M30 4v10h10" fill="#CFD8DC"/>
        <path d="M30 4l10 10h-8a2 2 0 0 1-2-2V4z" fill="#CFD8DC"/>
        <path d="M16 26h16M16 32h10" stroke="white" stroke-width="2" stroke-linecap="round"/>
      </svg>`,
    };
    return icons[type] || icons.default;
  }

  getOrbitLoaderMarkup() {
    return `<span class="mcp-status-spinner" aria-hidden="true"></span>`;
  }

  getStatusDotMarkup() {
    return `<span class="mcp-status-dot" aria-hidden="true"></span>`;
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

  _clearTurnPersistedFinalizeTimer() {
    if (!this.turnPersistedFinalizeTimer) return;
    clearTimeout(this.turnPersistedFinalizeTimer);
    this.turnPersistedFinalizeTimer = null;
  }

  _scheduleTurnPersistedFinalizeTimer(data) {
    this._clearTurnPersistedFinalizeTimer();
    this.turnPersistedFinalizeTimer = setTimeout(() => {
      if (!this.finalizingTurn) return;
      this.traceStream("turn_persisted_finalize_watchdog", {
        pendingOps: this.streamingPendingBlockOps ? this.streamingPendingBlockOps.size : 0,
        dirtyBlocks: this.streamingDirtyTextBlocks ? this.streamingDirtyTextBlocks.size : 0,
      });
      // Watchdog triggered: forcefully flush the remaining backlog.
      if (this.hasStreamingBlockBacklog()) {
        if (this.streamingPendingBlockOps && this.streamingPendingBlockOps.size) {
          this.streamingPendingBlockOps.forEach((_ops, blockId) => {
            if (blockId) this.streamingDirtyTextBlocks.add(blockId);
          });
        }
        this.flushStreamingBlockRenders(true);
      }
      // If the queue callback still didn't fire, forcefully reconcile.
      if (this.finalizingTurn) {
        this._doTurnPersistedReconcile(data);
      }
    }, 8000); // Generous 8s watchdog instead of 1.8s
  }

  handleTurnPersistedEvent(data) {
    this.finalizingTurn = true;
    // Let canonical persisted blocks decide final ordering (prevents scope component
    // from popping above text and then jumping after reconcile).
    if (this.container && this.container.dataset) {
      this.container.dataset.finalizing = "true";
    }
    
    // 1. Set the generous conditional watchdog
    this._scheduleTurnPersistedFinalizeTimer(data);
    
    // 2. The normal path: wait for natural drain
    this._queueAfterBlockDrain(
      () => {
        if (!this.finalizingTurn) return;
        this._clearTurnPersistedFinalizeTimer();
        this._doTurnPersistedReconcile(data);
      },
      { mode: "finalize" },
    );
  }

  _canonicalBlockIds(blocks) {
    if (!Array.isArray(blocks) || !blocks.length) return [];
    const ids = [];
    let hasInvalidBlock = false;
    blocks.forEach((block) => {
      if (hasInvalidBlock) return;
      if (!block || typeof block !== "object") return;
      const blockId = (block.block_id || block.blockId || "").toString().trim();
      if (!blockId) {
        hasInvalidBlock = true;
        return;
      }
      ids.push(blockId);
    });
    if (hasInvalidBlock) return [];
    return ids;
  }

  _normalizeComparableText(value) {
    return (value || "").toString().replace(/\s+/g, " ").trim();
  }

  _canonicalBlockComparableText(block) {
    if (!block || typeof block !== "object") return "";
    const type = (block.type || "").toString().trim().toLowerCase();
    const payload = block.payload && typeof block.payload === "object" ? block.payload : {};
    if (type === "paragraph" || type === "heading" || type === "list_item") {
      const content = Array.isArray(payload.content) ? payload.content : [];
      return this._normalizeComparableText(this.inlineNodesToText(content));
    }
    if (type === "text") {
      const text = typeof payload.text === "string" ? payload.text : "";
      return this._normalizeComparableText(this.stripInlineResponseBlocks(text));
    }
    if (type === "code_block" || type === "reasoning") {
      const text = typeof payload.code === "string" ? payload.code : typeof payload.text === "string" ? payload.text : "";
      return this._normalizeComparableText(text);
    }
    return "";
  }

  _streamedBlockComparableText(blockId) {
    if (!blockId) return "";
    const streamed =
      this.streamingContentBlocksById && this.streamingContentBlocksById.has(blockId)
        ? this.streamingContentBlocksById.get(blockId)
        : null;
    if (!streamed || typeof streamed !== "object") return "";
    return this._canonicalBlockComparableText(streamed);
  }

  _renderedBlockComparableText(blockEl, type) {
    if (!blockEl) return "";
    const normalizedType = (type || "").toString().trim().toLowerCase();
    if (normalizedType === "paragraph" || normalizedType === "heading" || normalizedType === "list_item") {
      return this._normalizeComparableText(blockEl.textContent || "");
    }
    if (normalizedType === "text") {
      return this._normalizeComparableText(this.stripInlineResponseBlocks(blockEl.textContent || ""));
    }
    if (normalizedType === "code_block" || normalizedType === "reasoning") {
      const codeEl = blockEl.querySelector("[data-content-block-code]");
      const value = codeEl ? codeEl.textContent || "" : blockEl.textContent || "";
      return this._normalizeComparableText(value);
    }
    return "";
  }

  _renderedBlockParentId(blockEl, blocksRoot) {
    if (!blockEl) return "";
    let cursor = blockEl.parentElement;
    while (cursor && cursor !== blocksRoot) {
      if (cursor.dataset && cursor.dataset.blockId) {
        return (cursor.dataset.blockId || "").toString().trim();
      }
      cursor = cursor.parentElement;
    }
    return "";
  }

  _renderedBlockIds(messageBodyEl) {
    if (!messageBodyEl) return [];
    const root = messageBodyEl.querySelector("[data-message-blocks]");
    if (!root) return [];
    return Array.from(root.querySelectorAll("[data-block-id]"))
      .map((node) => (node && node.dataset ? (node.dataset.blockId || "").toString().trim() : ""))
      .filter(Boolean);
  }

  _turnPersistedCanAckStreamedBlocks(messageBodyEl, contentBlocks) {
    if (!messageBodyEl) return false;
    if (!this.usingBlockStream) return false;
    if (!this.streamingContentBlockEls || this.streamingContentBlockEls.size === 0) return false;
    if (this.hasStreamingBlockBacklog()) return false;
    const canonicalIds = this._canonicalBlockIds(contentBlocks);
    if (!canonicalIds.length) return false;
    if (canonicalIds.length !== contentBlocks.length) return false;
    const blocksRoot = messageBodyEl.querySelector("[data-message-blocks]");
    if (!blocksRoot) return false;
    const renderedIds = this._renderedBlockIds(messageBodyEl);
    if (renderedIds.length !== canonicalIds.length) return false;
    const renderedById = new Map();
    Array.from(blocksRoot.querySelectorAll("[data-block-id]")).forEach((node) => {
      if (!node || !node.dataset) return;
      const blockId = (node.dataset.blockId || "").toString().trim();
      if (!blockId || renderedById.has(blockId)) return;
      renderedById.set(blockId, node);
    });
    for (let idx = 0; idx < canonicalIds.length; idx += 1) {
      if (canonicalIds[idx] !== renderedIds[idx]) return false;
    }
    for (let idx = 0; idx < contentBlocks.length; idx += 1) {
      const block = contentBlocks[idx];
      if (!block || typeof block !== "object") continue;
      const blockId = canonicalIds[idx];
      const renderedEl = renderedById.get(blockId);
      if (!renderedEl) return false;
      const canonicalType = (block.type || "").toString().trim().toLowerCase();
      const renderedType =
        renderedEl && renderedEl.dataset ? (renderedEl.dataset.blockType || "").toString().trim().toLowerCase() : "";
      if (canonicalType && renderedType && canonicalType !== renderedType) return false;
      const canonicalParentId = (block.parent_block_id || block.parentBlockId || "").toString().trim();
      const renderedParentId = this._renderedBlockParentId(renderedEl, blocksRoot);
      if (canonicalParentId !== renderedParentId) return false;
      const canonicalText = this._canonicalBlockComparableText(block);
      if (!canonicalText) continue;
      const renderedText = this._renderedBlockComparableText(renderedEl, canonicalType);
      if (!renderedText || renderedText !== canonicalText) {
        return false;
      }
    }
    return true;
  }

  _doTurnPersistedReconcile(data) {
			    try {
			      const payload = data ? JSON.parse(data) : null;
		      if (!payload) return;
		      const messageId = payload.message_id || this.pendingMessageId || this.streamingMessageId || null;
          if (messageId) {
            const existingBody = this.getMessageBodyElement(messageId);
            const existingRow = existingBody ? existingBody.closest(".message-row") : null;
            if (existingRow) {
              if (this.streamingMessageNode && existingRow !== this.streamingMessageNode) {
                this.streamingMessageNode.remove();
              }
              this.streamingMessageNode = existingRow;
              this.streamingMessageBodyEl = existingBody;
              this.streamingMessageId = messageId;
            }
          }
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
		          // Final canonical commit: persisted content_blocks are authoritative.
		          const streamedBlocks =
		            this.usingBlockStream && this.streamingBlocksEl && this.streamingContentBlockEls && this.streamingContentBlockEls.size > 0;
              const ackCandidate = streamedBlocks && this._turnPersistedCanAckStreamedBlocks(bodyEl, contentBlocks);
              this.traceStream("turn_persisted_commit", {
                blocks: contentBlocks.length,
                streamed: Boolean(streamedBlocks),
                ackCandidate: Boolean(ackCandidate),
              });
              this.renderMessageContentBlocks(bodyEl, contentBlocks);
              const visibilityRoot = bodyEl.closest ? bodyEl.closest("[data-message-id]") || bodyEl : bodyEl;
              this.updateInlineToolCardsVisibility(visibilityRoot);
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
        this.awaitingReply = false;
	      this.automationLocked = false;
	      this.updateSendButtonState(false);
	      this.setComposerAvailability(true);
	      this.updateComposerNotice(false);
	      this.flushQueueAfterTurn = true;
        if (this.flushQueueAfterTurn) {
          this.flushQueueAfterTurn = false;
          this.flushQueuedMessageIfReady();
        }
        this.clearActiveTurnState();
        this.closeTurnEventStream();
	    } catch (error) {
	      console.warn("Failed to parse persisted turn", error);
        this.setSpinnerText("", { pending: false, force: true });
        this.resetStreamingState(false, false);
        this.pendingMessageId = null;
        this.usingStateMachine = false;
        this.usingBlockStream = false;
        this.streamFinished = true;
        this.isStreaming = false;
        this.awaitingReply = false;
        this.automationLocked = false;
        this.updateSendButtonState(false);
        this.setComposerAvailability(true);
        this.updateComposerNotice(false);
        this.clearActiveTurnState();
        this.closeTurnEventStream();
	    } finally {
        this._clearTurnPersistedFinalizeTimer();
	      this.finalizingTurn = false;
	      if (this.container) {
	        delete this.container.dataset.finalizing;
	      }
	    }
	  }

  connectEventStream() {
    if (!this.endpoints.events || !this.sessionToken) return;
    if (typeof document !== "undefined" && document.hidden) return;
    this.closeSessionEventStream();
    const url = new URL(this.endpoints.events, window.location.origin);
    this.applyCurrentReferenceToUrl(url);
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

    this.eventSource.addEventListener("conversationMessage", (event) => {
      try {
        const payload = event && event.data ? JSON.parse(event.data) : null;
        this.handleConversationMessageEvent(payload);
      } catch (error) {
        console.warn("Failed to parse conversation message event", error);
      }
    });

    if (this.agentRunsEnabled) {
      this.eventSource.addEventListener("agentRunsSnapshot", (event) => {
        try {
          const payload = event && event.data ? JSON.parse(event.data) : null;
          this.handleAgentRunsSnapshot(payload);
        } catch (error) {
          console.warn("Failed to parse agent runs snapshot", error);
        }
      });

      this.eventSource.addEventListener("agentRunEvent", (event) => {
        try {
          const payload = event && event.data ? JSON.parse(event.data) : null;
          this.handleAgentRunEvent(payload);
        } catch (error) {
          console.warn("Failed to parse agent run event", error);
        }
      });

      this.eventSource.addEventListener("agentRequestsSnapshot", (event) => {
        try {
          const payload = event && event.data ? JSON.parse(event.data) : null;
          this.handleAgentRequestsSnapshot(payload);
        } catch (error) {
          console.warn("Failed to parse agent requests snapshot", error);
        }
      });

      this.eventSource.addEventListener("agentRequestEvent", (event) => {
        try {
          const payload = event && event.data ? JSON.parse(event.data) : null;
          this.handleAgentRequestEvent(payload);
        } catch (error) {
          console.warn("Failed to parse agent request event", error);
        }
      });

      this.eventSource.addEventListener("voiceCallTranscript", (event) => {
        try {
          const payload = event && event.data ? JSON.parse(event.data) : null;
          this.handleVoiceCallTranscript(payload);
        } catch (error) {
          console.warn("Failed to parse voice call transcript event", error);
        }
      });
    }

    this.eventSource.onerror = () => {
      if (!this.eventSource) return;
      if (typeof document !== "undefined" && document.hidden) {
        this.closeSessionEventStream();
      }
    };
  }

  closeSessionEventStream() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  handleConversationMessageEvent(payload) {
    if (!payload || typeof payload !== "object") return;
    const message = payload.message && typeof payload.message === "object" ? payload.message : null;
    if (!message) return;
    const messageId = typeof message.id === "string" ? message.id.trim() : "";
    if (!messageId) return;
    const sender = (message.sender || "").toString().trim().toLowerCase();
    const turnActive = Boolean(this.activeTurnId || this.awaitingReply || this.isStreaming || this.finalizingTurn);
    if (sender === "ai" && turnActive) {
      this.traceStream("conversationMessage_ignored_turn_active", { messageId });
      return;
    }

    const container = this.elements.messagesInner || this.elements.messages;
    const safeId = window.CSS && typeof window.CSS.escape === "function" ? window.CSS.escape(messageId) : messageId;
    if (container && container.querySelector(`.message-row[data-message-id="${safeId}"]`)) {
      return;
    }
    this.appendMessage(message);
  }

  initTasksPanel() {
    if (!this.elements.tasksPanel || !this.elements.tasksCards) {
      return;
    }

    if (this.elements.tasksOpenBtn) {
      this.elements.tasksOpenBtn.addEventListener("click", () => {
        this.tasksPanelUserHidden = false;
        this.persistTasksPanelPreference(false);
        this.setInboxPanelVisible(false);
        this.setTasksPanelVisible(true);
      });
    }
    if (this.elements.tasksCloseBtn) {
      this.elements.tasksCloseBtn.addEventListener("click", () => {
        this.tasksPanelUserHidden = true;
        this.persistTasksPanelPreference(true);
        this.setTasksPanelVisible(false);
      });
    }

    this.elements.tasksCards.addEventListener("click", (event) => {
      const target = event && event.target ? event.target : null;
      if (!target) return;

      const checkpointBtn = target.closest("[data-checkpoint-action]");
      if (checkpointBtn) {
        const action = (checkpointBtn.getAttribute("data-checkpoint-action") || "").trim();
        const checkpointId = (checkpointBtn.getAttribute("data-checkpoint-id") || "").trim();
        const card = checkpointBtn.closest(".portal-task");
        const textarea = card ? card.querySelector("[data-checkpoint-input-text]") : null;
        const message = textarea && typeof textarea.value === "string" ? textarea.value.trim() : "";
        if (!checkpointId || !action) return;
        if (action === "reply" && !message) {
          this.showToast("Missing input", "Please enter your answer first.", true);
          return;
        }
        this.submitRunCheckpoint(checkpointId, action, message, card, textarea, checkpointBtn);
        return;
      }

      const approvalBtn = target.closest("[data-run-approval-action]");
      if (approvalBtn) {
        const decision = (approvalBtn.getAttribute("data-run-approval-action") || "").trim();
        const approvalId = (approvalBtn.getAttribute("data-approval-id") || "").trim();
        const card = approvalBtn.closest(".portal-task");
        const runId = card ? (card.getAttribute("data-run-id") || "").trim() : "";
        if (decision && runId) {
          this.submitRunApproval(runId, approvalId, decision, card);
        }
        return;
      }

      const sendBtn = target.closest("[data-run-user-input-send]");
      if (sendBtn) {
        const runId = (sendBtn.getAttribute("data-run-user-input-send") || "").trim();
        const card = sendBtn.closest(".portal-task");
        const textarea = card ? card.querySelector("[data-run-user-input-text]") : null;
        const message = textarea && typeof textarea.value === "string" ? textarea.value.trim() : "";
        if (!runId) return;
        if (!message) {
          this.showToast("Missing input", "Please enter your answer first.", true);
          return;
        }
        this.submitRunUserInput(runId, message, textarea, sendBtn);
        return;
      }

      const openInboxBtn = target.closest("[data-open-inbox]");
      if (openInboxBtn) {
        this.inboxPanelUserHidden = false;
        this.setTasksPanelVisible(false);
        this.setInboxPanelVisible(true);
        return;
      }

      const voiceToggleEl = target.closest("[data-voice-toggle]");
      if (voiceToggleEl) {
        const sessionId = (voiceToggleEl.getAttribute("data-voice-toggle") || "").trim();
        if (sessionId) {
          this.toggleVoiceCallExpanded(sessionId);
        }
        return;
      }

      const automationRunBtn = target.closest("[data-automation-run-now]");
      if (automationRunBtn) {
        const automationId = (automationRunBtn.getAttribute("data-automation-run-now") || "").trim();
        const card = automationRunBtn.closest(".portal-task");
        if (automationId) this.submitAutomationManualRun(automationId, automationRunBtn, card);
        return;
      }

      const automationToggleEl = target.closest("[data-automation-toggle]");
      if (automationToggleEl) {
        const automationId = (automationToggleEl.getAttribute("data-automation-toggle") || "").trim();
        if (automationId) this.toggleAutomationExpanded(automationId);
        return;
      }

      const automationRunToggleEl = target.closest("[data-automation-run-toggle]");
      if (automationRunToggleEl) {
        const automationId = (automationRunToggleEl.getAttribute("data-automation-id") || "").trim();
        const runId = (automationRunToggleEl.getAttribute("data-run-id") || "").trim();
        if (automationId && runId) this.toggleAutomationRunExpanded(automationId, runId);
        return;
      }

      const toggleEl = target.closest("[data-run-toggle]");
      if (!toggleEl) return;
      const runId = (toggleEl.getAttribute("data-run-toggle") || "").trim();
      if (!runId) return;
      this.toggleRunExpanded(runId);
    });

    this.initTasksPanelGrid();
    this.tasksPanelUserHidden = this.readTasksPanelPreference();
    this.setTasksPanelVisible(!this.tasksPanelUserHidden);
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.removeAttribute("data-portal-tasks-collapsed");
    }
  }

  initInboxPanel() {
    if (!this.elements.inboxPanel || !this.elements.inboxCards) {
      return;
    }

    if (this.elements.inboxOpenBtn) {
      this.elements.inboxOpenBtn.addEventListener("click", () => {
        this.inboxPanelUserHidden = false;
        this.setTasksPanelVisible(false);
        this.setInboxPanelVisible(true);
      });
    }

    if (this.elements.inboxCloseBtn) {
      this.elements.inboxCloseBtn.addEventListener("click", () => {
        this.inboxPanelUserHidden = true;
        this.setInboxPanelVisible(false);
      });
    }

    this.elements.inboxCards.addEventListener("click", (event) => {
      const target = event && event.target ? event.target : null;
      if (!target) return;

      const statusBtn = target.closest("[data-request-set-status]");
      if (statusBtn) {
        const requestId = (statusBtn.getAttribute("data-request-set-status") || "").trim();
        const status = (statusBtn.getAttribute("data-request-status") || "").trim();
        const card = statusBtn.closest(".portal-task");
        if (requestId && status) {
          this.submitAgentRequestUpdate(requestId, status, "", card);
        }
        return;
      }

      const resolveBtn = target.closest("[data-request-resolve-send]");
      if (resolveBtn) {
        const requestId = (resolveBtn.getAttribute("data-request-resolve-send") || "").trim();
        const card = resolveBtn.closest(".portal-task");
        const textarea = card ? card.querySelector("[data-request-resolution-text]") : null;
        const resolution = textarea && typeof textarea.value === "string" ? textarea.value.trim() : "";
        if (!requestId) return;
        if (!resolution) {
          this.showToast("Missing resolution", "Please enter a reply before resolving.", true);
          return;
        }
        this.submitAgentRequestUpdate(requestId, "resolved", resolution, card, textarea, resolveBtn);
        return;
      }

      const voiceToggleEl = target.closest("[data-voice-toggle]");
      if (voiceToggleEl) {
        const sessionId = (voiceToggleEl.getAttribute("data-voice-toggle") || "").trim();
        if (sessionId) {
          this.toggleVoiceCallExpanded(sessionId);
        }
        return;
      }

      const toggleEl = target.closest("[data-request-toggle]");
      if (!toggleEl) return;
      const requestId = (toggleEl.getAttribute("data-request-toggle") || "").trim();
      if (!requestId) return;
      this.toggleRequestExpanded(requestId);
    });

    this.setInboxPanelVisible(false);
  }

  toggleVoiceCallExpanded(sessionId) {
    const state = this.activeVoiceCalls.get(sessionId);
    if (!state) return;
    state.expanded = !state.expanded;
    this.scheduleTasksRender();
  }

  getLatestRunEvent(state, predicate) {
    const events = state && Array.isArray(state.events) ? state.events : [];
    for (let idx = events.length - 1; idx >= 0; idx -= 1) {
      const evt = events[idx];
      if (evt && predicate(evt)) return evt;
    }
    return null;
  }

  getPendingRunApprovalId(runId) {
    const safeRunId = (runId || "").toString().trim();
    if (!safeRunId) return "";
    const state = this.agentRuns.get(safeRunId);
    const run = state && state.run ? state.run : null;
    const metadata = run && run.metadata && typeof run.metadata === "object" ? run.metadata : null;
    const metaApprovalId =
      metadata && (metadata.pending_approval_id || metadata.pendingApprovalId)
        ? String(metadata.pending_approval_id || metadata.pendingApprovalId).trim()
        : "";
    if (metaApprovalId) return metaApprovalId;
    return "";
  }

  async submitAutomationManualRun(automationId, buttonEl, cardEl) {
    const safeAutomationId = (automationId || "").toString().trim();
    if (!safeAutomationId) return;
    if (!this.endpoints.automationRun) {
      this.showToast(this.t("Run unavailable"), this.t("Automation run endpoint is not configured."), true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast(this.t("Run unavailable"), this.t("Session token missing."), true);
      return;
    }
    if (this.automationManualRunBusy.has(safeAutomationId)) return;
    this.automationManualRunBusy.add(safeAutomationId);
    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.dataset.busy = "true";
      buttonEl.setAttribute("aria-busy", "true");
    }

    try {
      const response = await fetch(this.endpoints.automationRun, {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify(
          this.getCurrentReferencePayload({
            automation_id: safeAutomationId,
          }),
        ),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : this.t("Automation run failed.");
        throw new Error(message);
      }

      const automation = payload && payload.automation && typeof payload.automation === "object" ? payload.automation : null;
      const run = payload && payload.run && typeof payload.run === "object" ? payload.run : null;
      const automationState = automation ? this.upsertAutomation(automation) : this.automationAgents.get(safeAutomationId);
      if (run) {
        this.upsertAgentRun(run);
        if (automationState && automationState.automation) {
          const recent = Array.isArray(automationState.automation.recentRuns) ? automationState.automation.recentRuns.slice() : [];
          const withoutCurrent = recent.filter((item) => item && item.id !== run.id);
          automationState.automation = Object.assign({}, automationState.automation, {
            latestRun: Object.assign({}, run),
            recentRuns: [run, ...withoutCurrent].slice(0, 5),
            lastTriggeredAt: run.createdAt || automationState.automation.lastTriggeredAt || null,
          });
        }
      }
      if (automationState) {
        automationState.expanded = true;
        if (!(automationState.expandedRuns instanceof Set)) automationState.expandedRuns = new Set();
        if (run && run.id) automationState.expandedRuns.add(String(run.id));
      }
      if (!this.tasksPanelUserHidden) {
        this.setTasksPanelVisible(true);
      }
      this.showToast(this.t("Run queued"), this.t("Automation run started."), false);
      this.scheduleTasksRender();
    } catch (error) {
      console.warn("Automation manual run failed", error);
      this.showToast("Run failed", error.message || "Please try again.", true);
    } finally {
      this.automationManualRunBusy.delete(safeAutomationId);
      if (buttonEl) {
        buttonEl.disabled = false;
        buttonEl.dataset.busy = "false";
        buttonEl.removeAttribute("aria-busy");
      }
    }
  }

  async submitRunApproval(runId, approvalId, decision, cardEl) {
    if (!runId || !decision) return;
    if (!this.endpoints.runApproval) {
      this.showToast("Approval unavailable", "Approval endpoint is not configured.", true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast("Approval unavailable", "Session token missing.", true);
      return;
    }
    if (cardEl && cardEl.dataset.runApprovalBusy === "true") {
      return;
    }
    if (cardEl) {
      cardEl.dataset.runApprovalBusy = "true";
    }
    const buttons = cardEl ? cardEl.querySelectorAll("[data-run-approval-action], [data-tool-approval-action]") : [];
    buttons.forEach((btn) => {
      btn.disabled = true;
    });

    try {
      const action = decision.toString().trim().toLowerCase();
	      const response = await fetch(this.endpoints.runApproval, {
	        method: "POST",
	        headers: this.jsonHeaders(),
	        body: JSON.stringify(
            this.getCurrentReferencePayload({
              run_id: runId,
              approval_id: approvalId,
              decision: action,
            }),
          ),
	      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : "Approval failed.";
        throw new Error(message);
      }
      this.showToast("Saved", "Approval recorded.", false);

      const resolved = action === "approve" ? "approved" : "denied";
      if (approvalId) {
        this.updateCallApprovalCardsForRunApproval(runId, approvalId, resolved);
      }
    } catch (error) {
      console.warn("Run approval failed", error);
      this.showToast("Approval failed", error.message || "Please try again.", true);
    } finally {
      if (cardEl) {
        cardEl.dataset.runApprovalBusy = "false";
      }
      buttons.forEach((btn) => {
        btn.disabled = false;
      });
    }
  }

  updateCallApprovalCardsForRunApproval(runId, approvalId, status) {
    const safeRunId = (runId || "").toString().trim();
    const safeApprovalId = (approvalId || "").toString().trim();
    const resolvedStatus = (status || "").toString().trim().toLowerCase();
    if (!safeApprovalId || !resolvedStatus) return;

    const root = this.elements.messagesInner || this.elements.messages || this.container;
    if (!root || !root.querySelectorAll) return;

    const escape = window.CSS && typeof window.CSS.escape === "function" ? window.CSS.escape : (value) => value;
    const selector = `[data-call-approval-card="true"][data-approval-id="${escape(safeApprovalId)}"]`;
    const cards = root.querySelectorAll(selector);
    if (!cards.length) return;

    cards.forEach((card) => {
      if (!card) return;
      const cardRun = (card.dataset.runId || "").toString().trim();
      if (safeRunId && cardRun && cardRun !== safeRunId) return;

      const base = card._callApprovalLastPayload && typeof card._callApprovalLastPayload === "object" ? card._callApprovalLastPayload : null;
      if (!base || typeof base !== "object") return;
      const approvalData = base.approval && typeof base.approval === "object" ? base.approval : {};

      const mergedPayload = {
        ...base,
        phase: "approval_resolved",
        status: resolvedStatus,
        approval_id: safeApprovalId,
        approval: { ...approvalData, id: safeApprovalId, status: resolvedStatus },
        run_id: safeRunId || base.run_id || base.runId || "",
      };
      this.updatePhoneCallApprovalCard(card, mergedPayload);
    });
  }

  async submitRunUserInput(runId, message, textareaEl, buttonEl) {
    if (!runId) return;
    if (!this.endpoints.runUserInput) {
      this.showToast("Unavailable", "User-input endpoint is not configured.", true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast("Unavailable", "Session token missing.", true);
      return;
    }
    if (buttonEl && buttonEl.dataset.runUserInputBusy === "true") {
      return;
    }
    if (buttonEl) {
      buttonEl.dataset.runUserInputBusy = "true";
      buttonEl.disabled = true;
    }
    if (textareaEl) {
      textareaEl.disabled = true;
    }
    try {
	      const response = await fetch(this.endpoints.runUserInput, {
	        method: "POST",
	        headers: this.jsonHeaders(),
	        body: JSON.stringify(
            this.getCurrentReferencePayload({
              run_id: runId,
              message,
            }),
          ),
	      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const messageOut = payload && payload.error && payload.error.message ? payload.error.message : "Send failed.";
        throw new Error(messageOut);
      }
      if (textareaEl) {
        textareaEl.value = "";
      }
      this.showToast("Sent", "Your answer was sent to the task.", false);
    } catch (error) {
      console.warn("Run user input failed", error);
      this.showToast("Send failed", error.message || "Please try again.", true);
    } finally {
      if (buttonEl) {
        buttonEl.dataset.runUserInputBusy = "false";
        buttonEl.disabled = false;
      }
      if (textareaEl) {
        textareaEl.disabled = false;
      }
    }
  }

  async submitRunCheckpoint(checkpointId, action, message, cardEl, textareaEl, buttonEl) {
    if (!checkpointId || !action) return;
    if (!this.endpoints.runCheckpoint) {
      this.showToast("Unavailable", "Checkpoint endpoint is not configured.", true);
      return;
    }
    if (buttonEl && buttonEl.dataset.runCheckpointBusy === "true") return;
    if (buttonEl) {
      buttonEl.dataset.runCheckpointBusy = "true";
      buttonEl.disabled = true;
    }
    const buttons = cardEl ? cardEl.querySelectorAll("[data-checkpoint-action]") : [];
    buttons.forEach((btn) => {
      btn.disabled = true;
    });
    if (textareaEl) textareaEl.disabled = true;
    try {
      const response = await fetch(this.endpoints.runCheckpoint, {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify(
          this.getCurrentReferencePayload({
            checkpoint_id: checkpointId,
            action,
            message,
          }),
        ),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const messageOut = payload && payload.error && payload.error.message ? payload.error.message : "Checkpoint update failed.";
        throw new Error(messageOut);
      }
      if (textareaEl) textareaEl.value = "";
      const run = payload && payload.run && typeof payload.run === "object" ? payload.run : null;
      if (run) this.upsertAgentRun(run);
      const checkpoint = payload && payload.checkpoint && typeof payload.checkpoint === "object" ? payload.checkpoint : null;
      if (checkpoint && checkpoint.id) {
        this.automationAgents.forEach((state) => {
          const automation = state && state.automation ? state.automation : null;
          const open = automation && automation.openCheckpoint && typeof automation.openCheckpoint === "object" ? automation.openCheckpoint : null;
          if (open && open.id === checkpoint.id) {
            state.automation = Object.assign({}, automation, { openCheckpoint: null });
          }
        });
      }
      this.showToast("Saved", "Automation updated.", false);
      this.scheduleTasksRender();
    } catch (error) {
      console.warn("Run checkpoint failed", error);
      this.showToast("Update failed", error.message || "Please try again.", true);
    } finally {
      if (buttonEl) {
        buttonEl.dataset.runCheckpointBusy = "false";
        buttonEl.disabled = false;
      }
      buttons.forEach((btn) => {
        btn.disabled = false;
      });
      if (textareaEl) textareaEl.disabled = false;
    }
  }

  readTasksPanelPreference() {
    try {
      if (typeof window === "undefined" || !window.localStorage) return false;
      return window.localStorage.getItem(this.tasksPanelStorageKey) === "true";
    } catch (_error) {
      return false;
    }
  }

  persistTasksPanelPreference(collapsed) {
    try {
      if (typeof window === "undefined" || !window.localStorage) return;
      window.localStorage.setItem(this.tasksPanelStorageKey, collapsed ? "true" : "false");
    } catch (_error) {
      // Ignore storage failures.
    }
  }

  setTasksPanelVisible(visible) {
    const panel = this.elements.tasksPanel;
    if (!panel) return;
    panel.removeAttribute("hidden");
    panel.dataset.panelState = visible ? "open" : "closed";
    if (this.elements.tasksOpenBtn) {
      this.elements.tasksOpenBtn.setAttribute("aria-expanded", visible ? "true" : "false");
    }
    this.setTasksPanelGridActive(Boolean(visible));
    this.updateTasksOpenButton();
  }

  initTasksPanelGrid() {
    if (!this.elements.tasksPanel) return;
    if (this.tasksPanelGrid) return;
    try {
    const panel = this.elements.tasksPanel;
    const canvas = document.createElement("canvas");
    canvas.className = "portal-tasks-grid";
    canvas.dataset.tasksGrid = "true";
    canvas.setAttribute("aria-hidden", "true");
    panel.prepend(canvas);

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const state = {
      canvas,
      ctx,
      panel,
      dpr: window.devicePixelRatio || 1,
      width: 0,
      height: 0,
      cols: 0,
      rows: 0,
      squareSize: 4,
      gridGap: 6,
      flickerChance: 0.18,
      maxOpacity: 0.22,
      squares: new Float32Array(0),
      colorPrefix: "rgba(99,102,241,",
      running: false,
      raf: null,
      lastTime: 0,
    };

    const readPrimary = () => {
      const cssVar = getComputedStyle(panel).getPropertyValue("--primary").trim();
      const temp = document.createElement("canvas");
      temp.width = 1;
      temp.height = 1;
      const tmpCtx = temp.getContext("2d");
      if (!tmpCtx) {
        return "rgba(99,102,241,";
      }
      let color = cssVar;
      if (!color) {
        color = "rgb(99,102,241)";
      } else if (!color.includes("(") && !color.startsWith("#")) {
        color = `hsl(${color})`;
      }
      tmpCtx.fillStyle = color;
      tmpCtx.fillRect(0, 0, 1, 1);
      const [r, g, b] = Array.from(tmpCtx.getImageData(0, 0, 1, 1).data);
      return `rgba(${r}, ${g}, ${b},`;
    };

    const initGrid = () => {
      const rect = panel.getBoundingClientRect();
      const width = Math.max(1, Math.round(rect.width));
      const height = Math.max(1, Math.round(rect.height));
      state.dpr = window.devicePixelRatio || 1;
      state.width = width;
      state.height = height;
      canvas.width = Math.floor(width * state.dpr);
      canvas.height = Math.floor(height * state.dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(1, 0, 0, 1, 0, 0);

      state.cols = Math.floor(width / (state.squareSize + state.gridGap));
      state.rows = Math.floor(height / (state.squareSize + state.gridGap));
      const total = Math.max(1, state.cols * state.rows);
      state.squares = new Float32Array(total);
      for (let i = 0; i < total; i += 1) {
        state.squares[i] = Math.random() * state.maxOpacity;
      }
      state.colorPrefix = readPrimary();
    };

    state.refresh = initGrid;

    const updateSquares = (deltaTime) => {
      const chance = state.flickerChance * deltaTime;
      const total = state.squares.length;
      for (let i = 0; i < total; i += 1) {
        if (Math.random() < chance) {
          state.squares[i] = Math.random() * state.maxOpacity;
        }
      }
    };

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "transparent";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < state.cols; i += 1) {
        for (let j = 0; j < state.rows; j += 1) {
          const opacity = state.squares[i * state.rows + j] || 0;
          ctx.fillStyle = `${state.colorPrefix}${opacity})`;
          ctx.fillRect(
            i * (state.squareSize + state.gridGap) * state.dpr,
            j * (state.squareSize + state.gridGap) * state.dpr,
            state.squareSize * state.dpr,
            state.squareSize * state.dpr
          );
        }
      }
    };

    state.draw = draw;

    const tick = (time) => {
      if (!state.running) return;
      if (!time) time = performance.now();
      if (!state.lastTime) state.lastTime = time;
      const deltaTime = (time - state.lastTime) / 1000;
      state.lastTime = time;
      updateSquares(deltaTime);
      draw();
      state.raf = requestAnimationFrame(tick);
    };

    state.start = () => {
      if (state.running) return;
      const rect = panel.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      state.refresh();
      state.running = true;
      state.lastTime = 0;
      state.raf = requestAnimationFrame(tick);
    };

    state.stop = () => {
      state.running = false;
      if (state.raf) {
        cancelAnimationFrame(state.raf);
        state.raf = null;
      }
    };

    if (typeof ResizeObserver !== "undefined") {
    const resizeObserver = new ResizeObserver(() => {
      initGrid();
      if (state.running) {
        draw();
      }
    });
      resizeObserver.observe(panel);
    } else {
      window.addEventListener("resize", () => {
        initGrid();
        if (state.running) {
          draw();
        }
      });
    }

    const visibilityObserver = new MutationObserver(() => {
      const visible = !panel.hasAttribute("hidden");
      if (visible) {
        state.refresh();
        state.draw();
        state.start();
      } else {
        state.stop();
      }
    });
    visibilityObserver.observe(panel, { attributes: true, attributeFilter: ["hidden"] });

    initGrid();
    this.tasksPanelGrid = state;
    } catch (error) {
      console.warn("Failed to initialize tasks grid", error);
    }
  }

  setTasksPanelGridActive(visible) {
    if (!this.tasksPanelGrid) return;
    if (visible) {
      if (this.tasksPanelGrid.refresh) {
        this.tasksPanelGrid.refresh();
      }
      if (this.tasksPanelGrid.draw) {
        this.tasksPanelGrid.draw();
      }
      this.tasksPanelGrid.start();
    } else {
      this.tasksPanelGrid.stop();
    }
  }

  setInboxPanelVisible(visible) {
    const panel = this.elements.inboxPanel;
    if (!panel) return;
    const shouldShow = Boolean(visible);
    if (shouldShow) {
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }
    this.updateInboxOpenButton();
  }

  updateTasksOpenButton() {
    const btn = this.elements.tasksOpenBtn;
    if (!btn) return;

    const panelOpen =
      this.elements.tasksPanel &&
      !this.elements.tasksPanel.hasAttribute("hidden") &&
      this.elements.tasksPanel.dataset.panelState !== "closed";

    if (panelOpen) {
      btn.setAttribute("hidden", "");
    } else {
      btn.removeAttribute("hidden");
    }

    let activeCount = this.getActiveRunCount();
    if (activeCount === 0 && this.activeVoiceCalls && this.activeVoiceCalls.size > 0) {
      activeCount = this.activeVoiceCalls.size;
    }
    if (this.elements.tasksCount) {
      if (activeCount > 0) {
        this.elements.tasksCount.textContent = String(activeCount);
        this.elements.tasksCount.removeAttribute("hidden");
      } else {
        this.elements.tasksCount.textContent = "0";
        this.elements.tasksCount.setAttribute("hidden", "");
      }
    }
  }

  updateInboxOpenButton() {
    const btn = this.elements.inboxOpenBtn;
    if (!btn) return;

    const hasRequests = this.agentRequests && this.agentRequests.size > 0;
    const hasAny = hasRequests;
    const activeRequestCount = this.getActiveRequestCount();
    const activeCount = activeRequestCount;
    const panelVisible = this.elements.inboxPanel && !this.elements.inboxPanel.hasAttribute("hidden");

    if (!hasAny) {
      btn.setAttribute("hidden", "");
      return;
    }

    if (panelVisible) {
      btn.setAttribute("hidden", "");
    } else {
      btn.removeAttribute("hidden");
    }

    if (this.elements.inboxCount) {
      if (activeCount > 0) {
        this.elements.inboxCount.textContent = String(activeCount);
        this.elements.inboxCount.removeAttribute("hidden");
      } else {
        this.elements.inboxCount.textContent = "0";
        this.elements.inboxCount.setAttribute("hidden", "");
      }
    }
  }

  getActiveRunCount() {
    let count = 0;
    this.automationAgents.forEach((state) => {
      const automation = state && state.automation ? state.automation : {};
      if (automation.openCheckpoint) {
        count += 1;
        return;
      }
      const latest = automation.latestRun && typeof automation.latestRun === "object" ? automation.latestRun : null;
      const status = latest && latest.status ? latest.status.toString().toLowerCase() : "";
      if (["running", "queued", "waiting_user", "waiting_approval", "waiting_child", "waiting_external", "paused"].includes(status)) {
        count += 1;
      }
    });
    this.agentRuns.forEach((state) => {
      const status = state && state.run ? (state.run.status || "").toString().toLowerCase() : "";
      if (!status) return;
      if (["running", "queued", "waiting_user", "waiting_approval", "waiting_external", "paused"].includes(status)) {
        count += 1;
      }
    });
    return count;
  }

  getActiveRequestCount() {
    let count = 0;
    this.agentRequests.forEach((state) => {
      const status = state && state.request ? (state.request.status || "").toString().toLowerCase() : "";
      if (!status) return;
      if (["open", "in_progress"].includes(status)) {
        count += 1;
      }
    });
    return count;
  }

  toggleRunExpanded(runId) {
    const state = this.agentRuns.get(runId);
    if (!state) return;
    state.expanded = !state.expanded;
    this.scheduleTasksRender();
  }

  toggleAutomationExpanded(automationId) {
    const state = this.automationAgents.get(automationId);
    if (!state) return;
    state.expanded = !state.expanded;
    this.scheduleTasksRender();
  }

  toggleAutomationRunExpanded(automationId, runId) {
    const state = this.automationAgents.get(automationId);
    if (!state || !runId) return;
    if (!(state.expandedRuns instanceof Set)) {
      state.expandedRuns = new Set();
    }
    if (state.expandedRuns.has(runId)) {
      state.expandedRuns.delete(runId);
    } else {
      state.expandedRuns.add(runId);
    }
    this.scheduleTasksRender();
  }

  toggleRequestExpanded(requestId) {
    const state = this.agentRequests.get(requestId);
    if (!state) return;
    state.expanded = !state.expanded;
    this.scheduleInboxRender();
  }

  handleAgentRunsSnapshot(payload) {
    if (!payload || typeof payload !== "object") return;
    const runs = Array.isArray(payload.runs) ? payload.runs : [];
    const automations = Array.isArray(payload.automations) ? payload.automations : [];
    const eventsByRun =
      payload.eventsByRun && typeof payload.eventsByRun === "object" ? payload.eventsByRun : {};

    automations.forEach((automation) => {
      const automationState = this.upsertAutomation(automation);
      const latest = automation && automation.latestRun && typeof automation.latestRun === "object" ? automation.latestRun : null;
      if (latest) this.upsertAgentRun(latest);
      const recent = automation && Array.isArray(automation.recentRuns) ? automation.recentRuns : [];
      recent.forEach((run) => this.upsertAgentRun(run));
      this.expandAutomationLiveRun(automationState, { liveEvent: false });
    });

    runs.forEach((run) => {
      this.upsertAgentRun(run);
    });

    Object.keys(eventsByRun).forEach((runId) => {
      const events = Array.isArray(eventsByRun[runId]) ? eventsByRun[runId] : [];
      this.replaceAgentRunEvents(runId, events);
    });

    if (!this.tasksPanelUserHidden) {
      this.setTasksPanelVisible(true);
    } else {
      this.updateTasksOpenButton();
    }
    if (Array.isArray(this.sessionSummaries) && this.elements.sessionsList) {
      this.renderSessionList(this.sessionSummaries);
    }
    this.scheduleTasksRender();
    this.refreshAgentRunChips();
  }

  handleAgentRequestsSnapshot(payload) {
    if (!payload || typeof payload !== "object") return;
    const requests = Array.isArray(payload.requests) ? payload.requests : [];

    requests.forEach((req) => {
      this.upsertAgentRequest(req);
    });

    this.updateInboxOpenButton();
    this.scheduleInboxRender();
  }

  handleAgentRunEvent(payload) {
    if (!payload || typeof payload !== "object") return;
    const run = payload.run && typeof payload.run === "object" ? payload.run : null;
    const evt = payload.event && typeof payload.event === "object" ? payload.event : null;
    const runId =
      (run && typeof run.id === "string" && run.id.trim()) ||
      (evt && typeof evt.runId === "string" && evt.runId.trim()) ||
      "";
    if (!runId) return;

    if (run) {
      const runState = this.upsertAgentRun(run);
      if (runState && (this.isRunActiveStatus(run.status) || this.isRunTerminalStatus(run.status))) {
        runState.expanded = true;
      }
      const automationId = (run.automationId || run.automation_id || "").toString().trim();
      if (automationId) {
        const runAutomation = {
          id: automationId,
          name: run.automationName || "",
          kind: run.automationKind || run.automation_kind || "",
        };
        const existingAutomationState = this.automationAgents.get(automationId);
        const automationState = this.isRunnableAutomation(existingAutomationState && existingAutomationState.automation ? existingAutomationState.automation : runAutomation)
          ? (existingAutomationState || this.upsertAutomation(runAutomation))
          : null;
        if (automationState && automationState.automation) {
          const recent = Array.isArray(automationState.automation.recentRuns) ? automationState.automation.recentRuns.slice() : [];
          const withoutCurrent = recent.filter((item) => item && item.id !== run.id);
          const statusNow = (run.status || "").toString().trim().toLowerCase();
          const terminalNow = this.isRunTerminalStatus(statusNow);
          const hasOpenCheckpoint = Object.prototype.hasOwnProperty.call(run, "openCheckpoint");
          const openCheckpoint = hasOpenCheckpoint
            ? (run.openCheckpoint && typeof run.openCheckpoint === "object" ? run.openCheckpoint : null)
            : (terminalNow ? null : automationState.automation.openCheckpoint || null);
          automationState.automation = Object.assign({}, automationState.automation, {
            latestRun: Object.assign({}, automationState.automation.latestRun || {}, run),
            openCheckpoint,
            recentRuns: [run, ...withoutCurrent].slice(0, 5),
          });
          this.expandAutomationLiveRun(automationState, { liveEvent: true, run });
        }
      }
    } else {
      this.upsertAgentRun({ id: runId });
    }
    if (evt) {
      this.appendAgentRunEvent(runId, evt);
    }

    if (!this.tasksPanelUserHidden) {
      this.setTasksPanelVisible(true);
    } else {
      this.updateTasksOpenButton();
    }

    // Route automation events to incremental card patching instead of full re-render.
    const automationIdForPatch = run ? (run.automationId || run.automation_id || "").toString().trim() : "";
    if (automationIdForPatch && this.automationAgents.has(automationIdForPatch)) {
      this.scheduleAutomationCardPatch(automationIdForPatch);
    } else {
      this.scheduleTasksRender();
    }
    this.refreshAgentRunChips(runId);
  }

  handleAgentRequestEvent(payload) {
    if (!payload || typeof payload !== "object") return;
    const req = payload.request && typeof payload.request === "object" ? payload.request : null;
    if (!req) return;
    this.upsertAgentRequest(req);
    this.updateInboxOpenButton();
    this.scheduleInboxRender();
  }

  handleVoiceCallTranscript(payload) {
    if (!payload || typeof payload !== "object") return;
    const sessionId = typeof payload.session_id === "string" ? payload.session_id.trim() : "";
    if (!sessionId) return;

    const role = payload.role || "agent";
    const text = typeof payload.text === "string" ? payload.text.trim() : "";
    if (!text) return;

    let state = this.activeVoiceCalls.get(sessionId);
    if (!state) {
      state = { transcripts: [], expanded: true };
      this.activeVoiceCalls.set(sessionId, state);
    }

    state.transcripts.push({
      role,
      text,
      timestamp: payload.timestamp || Date.now() / 1000,
    });

    // Keep only the last 50 transcript entries per call
    if (state.transcripts.length > 50) {
      state.transcripts = state.transcripts.slice(-50);
    }

    // Voice calling runs in the background; surface transcript updates in the Activity panel.
    if (!this.tasksPanelUserHidden) {
      this.setInboxPanelVisible(false);
      this.setTasksPanelVisible(true);
    }
    this.updateTasksOpenButton();
    this.scheduleTasksRender();
  }

  upsertAgentRun(run) {
    if (!run || typeof run !== "object") return null;
    const runId = typeof run.id === "string" ? run.id.trim() : "";
    if (!runId) return null;
    const existing = this.agentRuns.get(runId);
    if (existing) {
      existing.run = Object.assign({}, existing.run || {}, run);
      return existing;
    }
    const state = {
      run: Object.assign({}, run),
      events: [],
      expanded: this.isRunActiveStatus(run.status),
      seenSeq: new Set(),
      lastEventLabel: "",
    };
    this.agentRuns.set(runId, state);
    return state;
  }

  upsertAutomation(automation) {
    if (!automation || typeof automation !== "object") return null;
    const automationId = typeof automation.id === "string" ? automation.id.trim() : "";
    if (!automationId) return null;
    const existing = this.automationAgents.get(automationId);
    if (existing) {
      existing.automation = Object.assign({}, existing.automation || {}, automation);
      if (!(existing.expandedRuns instanceof Set)) existing.expandedRuns = new Set();
      return existing;
    }
    const state = {
      automation: Object.assign({}, automation),
      expanded: false,
      expandedRuns: new Set(),
    };
    this.automationAgents.set(automationId, state);
    return state;
  }

  expandAutomationLiveRun(automationState, { liveEvent = false, run = null } = {}) {
    if (!automationState || !automationState.automation) return;
    if (!(automationState.expandedRuns instanceof Set)) automationState.expandedRuns = new Set();
    const automation = automationState.automation;
    const latest = run || (automation.latestRun && typeof automation.latestRun === "object" ? automation.latestRun : null);
    const checkpoint = automation.openCheckpoint && typeof automation.openCheckpoint === "object" ? automation.openCheckpoint : null;
    const shouldOpen = Boolean(
      checkpoint ||
      (latest && this.isRunActiveStatus(latest.status)) ||
      (liveEvent && latest && this.isRunTerminalStatus(latest.status))
    );
    if (!shouldOpen) return;
    automationState.expanded = true;
    const runId = latest && latest.id ? String(latest.id).trim() : "";
    if (runId) automationState.expandedRuns.add(runId);
  }

  upsertAgentRequest(req) {
    if (!req || typeof req !== "object") return null;
    const requestId = typeof req.id === "string" ? req.id.trim() : "";
    if (!requestId) return null;
    const existing = this.agentRequests.get(requestId);
    if (existing) {
      existing.request = Object.assign({}, existing.request || {}, req);
      return existing;
    }
    const state = {
      request: Object.assign({}, req),
      expanded: false,
    };
    this.agentRequests.set(requestId, state);
    return state;
  }

  replaceAgentRunEvents(runId, events) {
    if (!runId) return;
    const state = this.upsertAgentRun({ id: runId }) || this.agentRuns.get(runId);
    if (!state) return;
    const clean = Array.isArray(events) ? events.filter((e) => e && typeof e === "object") : [];
    const mergedByKey = new Map();
    [...(Array.isArray(state.events) ? state.events : []), ...clean].forEach((evt) => {
      if (!evt || typeof evt !== "object") return;
      const seq = Number(evt.sequenceIndex || 0);
      const key = seq ? `seq:${seq}` : `id:${evt.id || ""}`;
      if (key === "id:") return;
      mergedByKey.set(key, evt);
    });
    const merged = Array.from(mergedByKey.values());
    merged.sort((a, b) => {
      const ai = Number(a.sequenceIndex || 0);
      const bi = Number(b.sequenceIndex || 0);
      return ai - bi;
    });
    state.events = merged.slice(-250);
    state.seenSeq = new Set(state.events.map((e) => Number(e.sequenceIndex || 0)));
    const last = state.events.length ? state.events[state.events.length - 1] : null;
    state.lastEventLabel = last && last.label ? String(last.label) : "";
  }

  appendAgentRunEvent(runId, evt) {
    const state = this.agentRuns.get(runId);
    if (!state || !evt) return;
    const seq = Number(evt.sequenceIndex || 0);
    if (!seq) return;
    if (state.seenSeq && state.seenSeq.has(seq)) {
      return;
    }
    state.seenSeq.add(seq);
    state.events.push(evt);
    if (state.events.length > 250) {
      state.events = state.events.slice(-200);
      state.seenSeq = new Set(state.events.map((e) => Number(e.sequenceIndex || 0)));
    }
    state.lastEventLabel = evt.label ? String(evt.label) : state.lastEventLabel;
  }

  scheduleTasksRender() {
    if (this.tasksRenderRaf) return;
    this.tasksRenderRaf = requestAnimationFrame(() => {
      this.tasksRenderRaf = null;
      this.renderTasksPanel();
    });
  }

  /**
   * Schedule an incremental patch for a single automation card.
   * Uses a per-automation RAF so rapid events for the same automation coalesce,
   * and events for different automations don't block each other.
   */
  scheduleAutomationCardPatch(automationId) {
    if (!this._automationPatchRafs) this._automationPatchRafs = new Map();
    if (this._automationPatchRafs.has(automationId)) return;
    this._automationPatchRafs.set(automationId, requestAnimationFrame(() => {
      this._automationPatchRafs.delete(automationId);
      this.patchAutomationCard(automationId);
    }));
  }

  /**
   * Incrementally update a single automation card in-place without
   * replacing the entire tasks panel innerHTML. Preserves:
   * - Automation card expanded/collapsed state
   * - Run row expanded/collapsed state
   * - <details> open/close state (scratchpad, tool calls)
   * - Scroll position within scratchpad
   * - Any in-progress textarea input
   */
  patchAutomationCard(automationId) {
    if (!this.elements.tasksCards) return;
    const cardEl = this.elements.tasksCards.querySelector(`[data-automation-id="${automationId}"]`);
    if (!cardEl) {
      // Card doesn't exist in DOM yet — fall back to full re-render.
      this.scheduleTasksRender();
      return;
    }

    const automationState = this.automationAgents.get(automationId);
    if (!automationState || !automationState.automation) return;
    const automation = automationState.automation;
    const expanded = Boolean(automationState.expanded);
    const latestRun = automation.latestRun && typeof automation.latestRun === "object" ? automation.latestRun : null;
    const checkpoint = automation.openCheckpoint && typeof automation.openCheckpoint === "object" ? automation.openCheckpoint : null;
    const checkpointKind = checkpoint && checkpoint.kind ? String(checkpoint.kind).trim().toLowerCase() : "";
    const checkpointStatus = checkpoint ? (checkpointKind === "user_input" ? "waiting_user" : "waiting_approval") : "";
    const status = checkpointStatus || (latestRun && latestRun.status ? latestRun.status : (automation.status || "draft"));
    const attentionStatus = this.getTaskAttentionStatus(status);
    const needsAttention = Boolean(checkpoint || attentionStatus);
    cardEl.setAttribute("data-expanded", expanded ? "true" : "false");

    // --- Update header in-place ---
    const titleRowEl = cardEl.querySelector(".portal-task__title-row");
    if (titleRowEl) {
      const name = automation && automation.name ? String(automation.name) : this.t("Automation");
      titleRowEl.innerHTML = `
        <div class="portal-task__title">${this.escapeHtml(name)}</div>
        ${this.renderTaskAttentionIndicator(status)}
        ${this.renderRunStatusPill(status)}
      `;
    }

    const subtitleEl = cardEl.querySelector(".portal-task__subtitle");
    if (subtitleEl) {
      const triggerLabel = this.getAutomationTriggerLabel(automation.triggerType);
      const ownerLabel = automation.agentName ? String(automation.agentName) : "";
      const subtitleParts = [ownerLabel, triggerLabel];
      if (automation.nextTriggerAt) subtitleParts.push(`${this.t("Next")} ${this.formatDueTime(automation.nextTriggerAt)}`);
      else if (automation.lastTriggeredAt) subtitleParts.push(`${this.t("Last")} ${this.formatDueTime(automation.lastTriggeredAt)}`);
      const subtitle = subtitleParts.filter(Boolean).join(" · ") || this.formatRunStatusLabel(status);
      subtitleEl.textContent = subtitle;
    }

    const summaryEl = cardEl.querySelector(".portal-task__summary-preview");
    if (summaryEl) {
      if (needsAttention) {
        summaryEl.textContent = "";
        summaryEl.setAttribute("hidden", "");
      } else {
        const latestSummary = checkpoint
          ? (checkpoint.prompt || checkpoint.title || this.t("Needs attention"))
          : (latestRun ? this.getRunSummaryText(latestRun, status) : (automation.description || this.t("No runs recorded yet.")));
        summaryEl.textContent = latestSummary;
        summaryEl.removeAttribute("hidden");
      }
    }

    // Update attention data attrs
    cardEl.setAttribute("data-attention", needsAttention ? "true" : "false");
    cardEl.setAttribute("data-attention-status", attentionStatus || "");

    // --- Update body if expanded ---
    if (!expanded) return;

    const bodyEl = cardEl.querySelector(".portal-task__body");
    if (!bodyEl) return;

    // Find all expanded run rows and their DOM state BEFORE patching
    const runRowEls = bodyEl.querySelectorAll(".portal-task__run-row");
    const expandedRunStates = new Map();
    runRowEls.forEach((rowEl) => {
      const btn = rowEl.querySelector("[data-run-id]");
      const runId = btn ? (btn.getAttribute("data-run-id") || "").trim() : "";
      if (!runId) return;
      const isExpanded = rowEl.getAttribute("data-expanded") === "true";
      if (!isExpanded) return;

      // Snapshot <details> open states and scroll positions
      const detailStates = [];
      rowEl.querySelectorAll("details").forEach((det, i) => {
        detailStates.push({
          index: i,
          open: det.open,
          className: det.className || "",
        });
      });
      const scratchpadEl = rowEl.querySelector(".portal-task__scratchpad");
      const scrollTop = scratchpadEl ? scratchpadEl.scrollTop : 0;
      const scrollHeight = scratchpadEl ? scratchpadEl.scrollHeight : 0;
      const clientHeight = scratchpadEl ? scratchpadEl.clientHeight : 0;

      // Check for textarea content
      const textarea = rowEl.querySelector("textarea");
      const textareaValue = textarea ? textarea.value : "";

      expandedRunStates.set(runId, { detailStates, scrollTop, scrollHeight, clientHeight, textareaValue });
    });

    // Re-render the body content
    const rawRecentRuns = Array.isArray(automation.recentRuns) ? automation.recentRuns : [];
    const latestRunId = latestRun && latestRun.id ? String(latestRun.id) : "";
    const recentRuns = latestRunId
      ? rawRecentRuns.filter((r) => !r || String(r.id || "") !== latestRunId)
      : rawRecentRuns;
    const checkpointHtml = checkpoint ? this.renderAutomationCheckpointHtml(checkpoint) : "";
    const allRuns = latestRun ? [latestRun, ...recentRuns] : recentRuns;
    const checkpointRunId = checkpoint && checkpoint.runId ? String(checkpoint.runId) : "";
    const suppressActionsForRunIds = checkpointRunId ? new Set([checkpointRunId]) : null;
    const recentHtml = this.renderAutomationRecentRunsHtml(automationId, automationState, allRuns, { suppressActionsForRunIds });
    bodyEl.innerHTML = checkpointHtml + (recentHtml || `<div class="portal-task__empty-note">${this.escapeHtml(this.t("No runs recorded yet."))}</div>`);

    // Restore DOM state for expanded run rows
    expandedRunStates.forEach((savedState, runId) => {
      const newRowEl = bodyEl.querySelector(`[data-run-id="${runId}"]`);
      if (!newRowEl) return;
      const rowEl = newRowEl.closest(".portal-task__run-row");
      if (!rowEl) return;
      const runState = this.agentRuns.get(runId);
      const runForRestore = runState && runState.run ? runState.run : null;

      // Restore <details> open states
      const allDetails = rowEl.querySelectorAll("details");
      savedState.detailStates.forEach((saved) => {
        if (allDetails[saved.index]) {
          if ((saved.className || "").split(/\s+/).includes("portal-task__work") && this.isRunTerminalStatus(runForRestore && runForRestore.status ? runForRestore.status : "")) {
            return;
          }
          allDetails[saved.index].open = saved.open;
        }
      });

      // Restore scroll position in scratchpad
      const scratchpadEl = rowEl.querySelector(".portal-task__scratchpad");
      if (scratchpadEl && savedState.scrollTop > 0) {
        const wasAtBottom = savedState.scrollTop + savedState.clientHeight >= savedState.scrollHeight - 40;
        if (wasAtBottom) {
          // User was following along — scroll to new bottom
          scratchpadEl.scrollTop = scratchpadEl.scrollHeight;
        } else {
          // User had scrolled up — preserve position
          scratchpadEl.scrollTop = savedState.scrollTop;
        }
      } else if (scratchpadEl) {
        // New content, auto-scroll to bottom
        scratchpadEl.scrollTop = scratchpadEl.scrollHeight;
      }

      // Restore textarea value
      if (savedState.textareaValue) {
        const textarea = rowEl.querySelector("textarea");
        if (textarea) textarea.value = savedState.textareaValue;
      }
    });

    this.updateTasksOpenButton();
  }


  scheduleInboxRender() {
    if (this.inboxRenderRaf) return;
    this.inboxRenderRaf = requestAnimationFrame(() => {
      this.inboxRenderRaf = null;
      this.renderInboxPanel();
    });
  }

  renderTasksPanel() {
    if (!this.elements.tasksCards) return;
    const list = this.elements.tasksCards;

    const automations = Array.from(this.automationAgents.entries()).map(([id, state]) => ({
      id,
      state,
      automation: state && state.automation ? state.automation : {},
    })).filter(({ automation }) => this.isRunnableAutomation(automation));

    const runs = Array.from(this.agentRuns.entries())
      .filter(([, state]) => {
        const run = state && state.run ? state.run : {};
        return !(run && (run.automationId || run.automation_id));
      })
      .map(([id, state]) => ({
      id,
      state,
      run: state && state.run ? state.run : {},
    }));

    const voiceCalls = Array.from(this.activeVoiceCalls.entries()).map(([id, state]) => ({
      id,
      state,
    }));

    if (!automations.length && !runs.length && !voiceCalls.length) {
      if (this.elements.tasksEmpty) {
        this.elements.tasksEmpty.removeAttribute("hidden");
      }
      list.innerHTML = "";
      this.updateTasksOpenButton();
      return;
    }

    if (this.elements.tasksEmpty) {
      this.elements.tasksEmpty.setAttribute("hidden", "");
    }

    automations.sort((a, b) => {
      const aNeeds = a.automation && a.automation.openCheckpoint ? 0 : 1;
      const bNeeds = b.automation && b.automation.openCheckpoint ? 0 : 1;
      if (aNeeds !== bNeeds) return aNeeds - bNeeds;
      const aTime = Date.parse(a.automation.updatedAt || a.automation.createdAt || "") || 0;
      const bTime = Date.parse(b.automation.updatedAt || b.automation.createdAt || "") || 0;
      return bTime - aTime;
    });

    // Order ad-hoc background work chronologically (first initiated at the top).
    runs.sort((a, b) => {
      const aTime = Date.parse(a.run.createdAt || a.run.updatedAt || "") || 0;
      const bTime = Date.parse(b.run.createdAt || b.run.updatedAt || "") || 0;
      if (aTime !== bTime) return aTime - bTime;
      const aId = (a.id || "").toString();
      const bId = (b.id || "").toString();
      return aId.localeCompare(bId);
    });

    const automationCardsHtml = automations
      .map(({ id, state, automation }) => this.renderAutomationCardHtml(id, state, automation))
      .join("");

    const voiceSessionsInRuns = new Set();
    const runCardsHtml = runs
      .map(({ id, state, run }) => {
        const voiceInfo = this.extractVoiceCallRunInfo(state, run);
        if (voiceInfo && voiceInfo.sessionId) {
          voiceSessionsInRuns.add(voiceInfo.sessionId);
          return this.renderVoiceCallRunCardHtml(id, state, run, voiceInfo);
        }
        return this.renderRunCardHtml(id, state, run);
      })
      .join("");

    const extraVoiceCardsHtml = voiceCalls
      .filter(({ id }) => id && !voiceSessionsInRuns.has(id))
      .map(({ id, state }) => this.renderVoiceCallTranscriptCardHtml(id, state))
      .join("");

    const adHocHeading = runs.length || extraVoiceCardsHtml ? `<div class="portal-task__group-title">${this.escapeHtml(this.t("Ad-hoc runs"))}</div>` : "";

    // Snapshot DOM state before innerHTML nuke
    const savedStates = this._snapshotPanelDomState(list);

    list.innerHTML = automationCardsHtml + adHocHeading + runCardsHtml + extraVoiceCardsHtml;

    // Restore DOM state after innerHTML replacement
    this._restorePanelDomState(list, savedStates);
    this.updateTasksOpenButton();
  }

  /**
   * Snapshot <details> open states, scroll positions, and textarea values
   * from the current tasks panel DOM before a full innerHTML replacement.
   */
  _snapshotPanelDomState(container) {
    const saved = { details: [], scrolls: [], textareas: [] };
    if (!container) return saved;

    container.querySelectorAll("details").forEach((det) => {
      if (!det.open) return;
      // Build a selector path to relocate this element after re-render
      const classes = (det.className || "").trim();
      const parent = det.closest("[data-automation-id], [data-run-id], [data-run-toggle]");
      const parentId = parent
        ? (parent.getAttribute("data-automation-id") || parent.getAttribute("data-run-id") || parent.getAttribute("data-run-toggle") || "")
        : "";
      const runBtn = det.closest(".portal-task__run-row") ? det.closest(".portal-task__run-row").querySelector("[data-run-id]") : null;
      const cardRun = det.closest("[data-run-id]");
      const runId = runBtn ? (runBtn.getAttribute("data-run-id") || "") : (cardRun ? (cardRun.getAttribute("data-run-id") || "") : "");
      saved.details.push({ parentId, runId, classes, open: true });
    });

    container.querySelectorAll(".portal-task__scratchpad").forEach((el) => {
      const runBtn = el.closest(".portal-task__run-row") ? el.closest(".portal-task__run-row").querySelector("[data-run-id]") : null;
      const cardRun = el.closest("[data-run-id]");
      const runId = runBtn ? (runBtn.getAttribute("data-run-id") || "") : (cardRun ? (cardRun.getAttribute("data-run-id") || "") : "");
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      saved.scrolls.push({
        runId,
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        wasAtBottom: distanceFromBottom <= 48,
      });
    });

    container.querySelectorAll("textarea").forEach((ta) => {
      if (!ta.value) return;
      const card = ta.closest("[data-automation-id], [data-run-toggle]");
      const cardId = card ? (card.getAttribute("data-automation-id") || card.getAttribute("data-run-toggle") || "") : "";
      saved.textareas.push({ cardId, value: ta.value });
    });

    return saved;
  }

  /**
   * Restore <details> open states, scroll positions, and textarea values
   * after a full innerHTML replacement.
   */
  _restorePanelDomState(container, saved) {
    if (!container || !saved) return;

    // Restore <details> open states
    for (const entry of saved.details) {
      if (!entry.classes) continue;
      let scope = container;
      if (entry.runId) {
        const runBtn = container.querySelector(`[data-run-id="${entry.runId}"]`);
        if (runBtn) scope = runBtn.closest(".portal-task__run-row") || runBtn.closest(".portal-task") || container;
      } else if (entry.parentId) {
        scope = container.querySelector(`[data-automation-id="${entry.parentId}"], [data-run-id="${entry.parentId}"]`) || container;
      }
      const candidates = scope.querySelectorAll(`details.${entry.classes.split(/\s+/).join(".")}`);
      candidates.forEach((det) => {
        if ((entry.classes || "").split(/\s+/).includes("portal-task__work") && entry.runId) {
          const runState = this.agentRuns.get(entry.runId);
          const run = runState && runState.run ? runState.run : null;
          if (run && this.isRunTerminalStatus(run.status)) return;
        }
        det.open = true;
      });
    }

    // Restore scroll positions
    for (const entry of saved.scrolls) {
      if (!entry.runId) continue;
      const runBtn = container.querySelector(`[data-run-id="${entry.runId}"]`);
      const row = runBtn ? runBtn.closest(".portal-task__run-row") : null;
      const card = runBtn ? runBtn.closest(".portal-task") : null;
      const scratchpad = row ? row.querySelector(".portal-task__scratchpad") : (card ? card.querySelector(".portal-task__scratchpad") : null);
      if (!scratchpad) continue;
      scratchpad.scrollTop = entry.wasAtBottom ? scratchpad.scrollHeight : entry.scrollTop;
    }

    // Restore textarea values
    for (const entry of saved.textareas) {
      if (!entry.cardId || !entry.value) continue;
      const card = container.querySelector(`[data-automation-id="${entry.cardId}"], [data-run-toggle="${entry.cardId}"]`);
      if (!card) continue;
      const ta = card.querySelector("textarea");
      if (ta) ta.value = entry.value;
    }
  }

  renderInboxPanel() {
    if (!this.elements.inboxCards) return;
    const list = this.elements.inboxCards;

    const requests = Array.from(this.agentRequests.entries()).map(([id, state]) => ({
      id,
      state,
      request: state && state.request ? state.request : {},
    }));

    const hasContent = requests.length > 0;

    if (!hasContent) {
      if (this.elements.inboxEmpty) {
        this.elements.inboxEmpty.removeAttribute("hidden");
      }
      list.innerHTML = "";
      this.updateInboxOpenButton();
      return;
    }

    if (this.elements.inboxEmpty) {
      this.elements.inboxEmpty.setAttribute("hidden", "");
    }

    const statusPriority = {
      open: 0,
      in_progress: 1,
      resolved: 2,
    };

    requests.sort((a, b) => {
      const aStatus = (a.request.status || "").toString().toLowerCase();
      const bStatus = (b.request.status || "").toString().toLowerCase();
      const ap = Object.prototype.hasOwnProperty.call(statusPriority, aStatus) ? statusPriority[aStatus] : 50;
      const bp = Object.prototype.hasOwnProperty.call(statusPriority, bStatus) ? statusPriority[bStatus] : 50;
      if (ap !== bp) return ap - bp;
      const aTime = Date.parse(a.request.updatedAt || a.request.createdAt || "") || 0;
      const bTime = Date.parse(b.request.updatedAt || b.request.createdAt || "") || 0;
      return bTime - aTime;
    });

    const requestCardsHtml = requests
      .map(({ id, state, request }) => this.renderRequestCardHtml(id, state, request))
      .join("");

    list.innerHTML = requestCardsHtml;
    this.updateInboxOpenButton();
  }

  renderVoiceCallTranscriptCardHtml(sessionId, state) {
    const transcripts = state && Array.isArray(state.transcripts) ? state.transcripts : [];
    const expanded = Boolean(state && state.expanded);

    const transcriptLinesHtml = transcripts
      .slice(-20) // Show last 20 lines
      .map((t) => {
        const roleClass = t.role === "customer" ? "transcript-customer" : "transcript-agent";
        const roleLabel = t.role === "customer" ? this.t("Customer") : this.t("Agent");
        return `<div class="portal-transcript-line ${roleClass}"><span class="portal-transcript-role">${this.escapeHtml(roleLabel)}:</span> ${this.escapeHtml(t.text)}</div>`;
      })
      .join("");

    return `
      <div class="portal-task portal-voice-call" data-voice-session-id="${this.escapeHtml(sessionId)}" data-expanded="${expanded ? "true" : "false"}">
        <button type="button" class="portal-task__header" data-voice-toggle="${this.escapeHtml(sessionId)}">
          <div class="portal-task__meta">
            <div class="portal-task__title-row">
              <div class="portal-task__title">${this.escapeHtml(this.t("Active Voice Call"))}</div>
              <span class="portal-task__status portal-task__status--running">${this.escapeHtml(this.t("Live"))}</span>
            </div>
            <div class="portal-task__subtitle">${this.escapeHtml(this.t("Real-time transcript"))}</div>
          </div>
        </button>
        <div class="portal-task__body">
          <div class="portal-task__section">
            <div class="portal-task__section-title">${this.escapeHtml(this.t("Transcript"))}</div>
            <div class="portal-transcript-container">
              ${transcriptLinesHtml || `<div class="portal-transcript-empty">${this.escapeHtml(this.t("Waiting for speech..."))}</div>`}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  extractVoiceCallRunInfo(state, run) {
    const events = state && Array.isArray(state.events) ? state.events : [];
    let sessionId = "";
    let toPhone = "";
    let objective = "";
    let callType = "";
    let language = "";
    let country = "";
    let callStatus = "";

    for (let idx = events.length - 1; idx >= 0; idx -= 1) {
      const evt = events[idx];
      if (!evt || typeof evt !== "object") continue;
      const payload = evt.payload && typeof evt.payload === "object" ? evt.payload : null;
      if (!payload) continue;

      if (!sessionId) {
        sessionId = (
          payload.call_session_id ||
          payload.callSessionId ||
          payload.voice_call_session_id ||
          payload.voiceCallSessionId ||
          ""
        )
          .toString()
          .trim();
      }
      if (!toPhone) {
        toPhone = (
          payload.to_phone_number ||
          payload.toPhoneNumber ||
          payload.phone_number ||
          payload.phoneNumber ||
          payload.to ||
          ""
        )
          .toString()
          .trim();
      }
      if (!objective) {
        objective = (payload.objective || payload.reason || payload.topic || "")
          .toString()
          .trim();
      }
      if (!callType) {
        callType = (payload.call_type || payload.callType || "")
          .toString()
          .trim();
      }
      if (!language) {
        language = (payload.language || "").toString().trim();
      }
      if (!country) {
        country = (payload.country || "").toString().trim();
      }
      if (!callStatus) {
        callStatus = (
          payload.status ||
          payload.call_status ||
          payload.callStatus ||
          ""
        )
          .toString()
          .trim();
      }

      if (sessionId && toPhone && objective && callType && language && country && callStatus) {
        break;
      }
    }

    if (!sessionId) return null;
    return {
      sessionId,
      toPhone,
      objective,
      callType,
      language,
      country,
      callStatus,
      runTitle: run && run.title ? String(run.title) : "",
    };
  }

  renderVoiceCallStatusPill({ callStatus = "", runStatus = "", hasTranscript = false } = {}) {
    const normalize = (raw) => (raw || "").toString().trim().toLowerCase();
    const call = normalize(callStatus);
    const run = normalize(runStatus);

    let statusKey = "";
    let label = "";

    if (call) {
      if (["in_progress", "in-progress", "inprogress"].includes(call)) {
        statusKey = "running";
        label = this.t("Live");
      } else if (call === "ringing") {
        statusKey = "running";
        label = this.t("Ringing");
      } else if (call === "initiating") {
        statusKey = "running";
        label = this.t("Dialing");
      } else if (call === "queued") {
        statusKey = "queued";
        label = this.t("Queued");
      } else if (["completed", "finished", "done"].includes(call)) {
        statusKey = "completed";
        label = this.t("Done");
      } else if (["cancelled", "canceled"].includes(call)) {
        statusKey = "cancelled";
        label = this.t("Cancelled");
      } else if (["failed", "error"].includes(call)) {
        statusKey = "failed";
        label = this.t("Failed");
      } else {
        statusKey = call;
        label = call.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
      }
    } else if (hasTranscript) {
      statusKey = "running";
      label = this.t("Live");
    } else if (run) {
      if (run === "waiting_external") {
        statusKey = "queued";
        label = this.t("Queued");
      } else {
        statusKey = run;
        label = this.formatRunStatusLabel(run);
      }
    } else {
      statusKey = "queued";
      label = this.t("Queued");
    }

    if (statusKey === "completed") {
      return `
        <span class="portal-task__status-pill portal-task__status-pill--icon" data-status="completed" aria-label="${this.escapeHtml(label)}">
          <span class="portal-task__status-pill-icon" aria-hidden="true">${this.getSuccessCircleIconMarkup()}</span>
        </span>
      `;
    }

    return `
      <span class="portal-task__status-pill" data-status="${this.escapeHtml(statusKey)}">${this.escapeHtml(label)}</span>
    `;
  }

  getVoiceTranscriptEmptyMessage({ callStatus = "", runStatus = "" } = {}) {
    const normalize = (raw) => (raw || "").toString().trim().toLowerCase();
    const call = normalize(callStatus);
    const run = normalize(runStatus);

    if (["cancelled", "canceled"].includes(call) || ["cancelled", "canceled"].includes(run)) {
      return this.t("Call cancelled.");
    }
    if (["failed", "error"].includes(call) || ["failed", "error"].includes(run)) {
      return this.t("Call failed.");
    }
    if (["completed", "finished", "done"].includes(call) || ["completed", "succeeded", "success"].includes(run)) {
      return this.t("Call completed.");
    }
    if (call === "ringing") return this.t("Ringing…");
    if (call === "initiating") return this.t("Dialing…");
    if (call === "queued" || run === "waiting_external") {
      return this.t("Waiting for the call to start…");
    }
    return this.t("Waiting for speech…");
  }

  renderVoiceCallRunCardHtml(runId, state, run, info) {
    const expanded = Boolean(state && state.expanded);
    const runStatus = (run && run.status ? run.status : "").toString().trim();
    const sessionId = info && info.sessionId ? String(info.sessionId).trim() : "";
    const toPhone = info && info.toPhone ? String(info.toPhone).trim() : "";
    const objective = info && info.objective ? String(info.objective).trim() : "";
    const callType = info && info.callType ? String(info.callType).trim() : "";
    const language = info && info.language ? String(info.language).trim() : "";
    const country = info && info.country ? String(info.country).trim() : "";
    const callStatus = info && info.callStatus ? String(info.callStatus).trim() : "";

    const transcriptState = sessionId ? this.activeVoiceCalls.get(sessionId) : null;
    const transcripts = transcriptState && Array.isArray(transcriptState.transcripts) ? transcriptState.transcripts : [];
    const hasTranscript = transcripts.length > 0;

    const title = run && run.title ? String(run.title) : toPhone ? `${this.t("Call")} ${toPhone}` : this.t("Phone call");

    const subtitleParts = [];
    if (objective) subtitleParts.push(objective);
    if (toPhone && !title.includes(toPhone)) subtitleParts.push(toPhone);
    const subtitle = subtitleParts.join(" · ") || this.formatRunStatusLabel(runStatus || "queued");

    const detailsItems = [];
    if (toPhone) detailsItems.push(`${this.t("To")}: ${toPhone}`);
    if (objective) detailsItems.push(`${this.t("Objective")}: ${objective}`);
    if (callType) detailsItems.push(`${this.t("Type")}: ${callType}`);
    if (language) detailsItems.push(`${this.t("Language")}: ${language}`);
    if (country) detailsItems.push(`${this.t("Country")}: ${country}`);
    const detailsHtml = detailsItems.length
      ? `
        <div class="portal-task__section">
          <div class="portal-task__section-title">${this.escapeHtml(this.t("Details"))}</div>
          <div class="portal-task__list">
            ${detailsItems.map((line) => `<div class="portal-task__list-item">${this.escapeHtml(line)}</div>`).join("")}
          </div>
        </div>
      `
      : "";

    const transcriptLinesHtml = transcripts
      .slice(-20)
      .map((t) => {
        const roleClass = t.role === "customer" ? "transcript-customer" : "transcript-agent";
        const roleLabel = t.role === "customer" ? this.t("Customer") : this.t("Agent");
        return `<div class="portal-transcript-line ${roleClass}"><span class="portal-transcript-role">${this.escapeHtml(roleLabel)}:</span> ${this.escapeHtml(
          t.text
        )}</div>`;
      })
      .join("");

    const transcriptEmpty = this.getVoiceTranscriptEmptyMessage({ callStatus, runStatus });
    const transcriptHtml = `
      <div class="portal-task__section">
        <div class="portal-task__section-title">${this.escapeHtml(this.t("Transcript"))}</div>
        <div class="portal-transcript-container">
          ${transcriptLinesHtml || `<div class="portal-transcript-empty">${this.escapeHtml(transcriptEmpty)}</div>`}
        </div>
      </div>
    `;

    const planHtml = this.renderRunPlanHtml(run);
    const workHtml = this.renderRunWorkHtml(state, run);

    return `
      <div class="portal-task portal-voice-call" data-run-id="${this.escapeHtml(runId)}" data-voice-session-id="${this.escapeHtml(
      sessionId
    )}" data-expanded="${expanded ? "true" : "false"}">
        <button type="button" class="portal-task__header" data-run-toggle="${this.escapeHtml(runId)}">
          <div class="portal-task__meta">
            <div class="portal-task__title-row">
              <div class="portal-task__title">${this.escapeHtml(title)}</div>
              ${this.renderVoiceCallStatusPill({ callStatus, runStatus, hasTranscript })}
            </div>
            <div class="portal-task__subtitle">${this.escapeHtml(subtitle)}</div>
          </div>
        </button>
        <div class="portal-task__body">
          ${planHtml}
          ${workHtml}
          ${detailsHtml}
          ${transcriptHtml}
        </div>
      </div>
    `;
  }

  getAutomationTriggerLabel(triggerType) {
    const norm = (triggerType || "").toString().trim().toLowerCase();
    if (norm === "schedule") return this.t("Scheduled");
    if (norm === "manual") return this.t("Manual");
    return norm ? this.formatStatus(norm) : this.t("Automation");
  }

  isRunnableAutomation(automation) {
    return Boolean(automation && typeof automation === "object" && automation.id);
  }

  formatTaskDateTime(raw) {
    if (!raw) return "";
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return date.toLocaleString(this.getLocale(), {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_err) {
      return date.toLocaleString();
    }
  }

  formatDueTime(raw) {
    if (!raw) return "";
    const date = new Date(raw);
    const timestamp = date.getTime();
    if (Number.isNaN(timestamp)) return "";
    const diffMs = timestamp - Date.now();
    if (diffMs > 0) {
      const mins = Math.max(1, Math.round(diffMs / 60000));
      if (mins < 60) return `${this.t("in")} ${mins}m`;
      const hours = Math.round(mins / 60);
      if (hours < 24) return `${this.t("in")} ${hours}h`;
    }
    return this.formatRelativeTime(date);
  }

  getRunDisplay(run) {
    const display = run && run.display && typeof run.display === "object" ? run.display : null;
    if (display) {
      const summary = typeof display.summary === "string" ? display.summary.trim() : "";
      const agentMessage = typeof display.agentMessage === "string" ? display.agentMessage.trim() : "";
      const recommendedNextStep = typeof display.recommendedNextStep === "string" ? display.recommendedNextStep.trim() : "";
      return {
        summary: this.isJsonLikeText(summary) ? "" : summary,
        agentMessage: this.isJsonLikeText(agentMessage) ? "" : agentMessage,
        findings: Array.isArray(display.findings) ? display.findings.filter(Boolean).map((item) => String(item).trim()).filter(Boolean) : [],
        actionsTaken: Array.isArray(display.actionsTaken) ? display.actionsTaken : [],
        recommendedNextStep: this.isJsonLikeText(recommendedNextStep) ? "" : recommendedNextStep,
        statusTone: typeof display.statusTone === "string" ? display.statusTone.trim() : "neutral",
        rawAvailable: Boolean(display.rawAvailable),
        rawDebug: display.rawDebug && typeof display.rawDebug === "object" ? display.rawDebug : null,
      };
    }
    const report = this.getRunReport(run);
    const findings = report && Array.isArray(report.findings) ? report.findings.map((item) => String(item || "").trim()).filter(Boolean) : [];
    const actionsTaken = this.getRunActionsTaken(run).map((label) => ({ label, status: "" }));
    const summary = this.getRunSummaryText(run, run && run.status ? run.status : "");
    const responseText = this.plainTextFromRunResponse(run);
    return {
      summary,
      agentMessage: responseText || summary,
      findings,
      actionsTaken,
      recommendedNextStep: report && report.recommendedNextStep ? String(report.recommendedNextStep).trim() : "",
      statusTone: "neutral",
      rawAvailable: Boolean(run && run.result),
      rawDebug: run && run.result && typeof run.result === "object" ? run.result : null,
    };
  }

  getRunReport(run) {
    const result = run && run.result && typeof run.result === "object" ? run.result : null;
    const report = result && result.runReport && typeof result.runReport === "object" ? result.runReport : null;
    if (report) return this.normalizeRunReport(report);
    let responseText = result && typeof result.responseText === "string" ? result.responseText.trim() : "";
    if (!responseText) return null;
    if (responseText.startsWith("```")) {
      responseText = responseText
        .replace(/^```(?:json)?/i, "")
        .replace(/```$/i, "")
        .trim();
    }
    if (!responseText || responseText[0] !== "{") return null;
    try {
      const parsed = JSON.parse(responseText);
      const candidate = parsed && parsed.run_report && typeof parsed.run_report === "object" ? parsed.run_report : parsed;
      return this.normalizeRunReport(candidate);
    } catch (_err) {
      return null;
    }
  }

  normalizeRunReport(candidate) {
    if (!candidate || typeof candidate !== "object") return null;
    const actionsRaw = candidate.actionsTaken || candidate.actions_taken;
    const memoryRaw = candidate.memoryUpdate || candidate.memory_update;
    const notificationRaw = candidate.notification || candidate.notification_candidate;
    return {
      ...candidate,
      objective: candidate.objective || "",
      status: candidate.status || "",
      findings: Array.isArray(candidate.findings) ? candidate.findings : [],
      actionsTaken: Array.isArray(actionsRaw) ? actionsRaw : [],
      evidenceRefs: Array.isArray(candidate.evidenceRefs || candidate.evidence_refs) ? (candidate.evidenceRefs || candidate.evidence_refs) : [],
      changedEntities: Array.isArray(candidate.changedEntities || candidate.changed_entities) ? (candidate.changedEntities || candidate.changed_entities) : [],
      recommendedNextStep: candidate.recommendedNextStep || candidate.recommended_next_step || "",
      memoryUpdate: memoryRaw && typeof memoryRaw === "object" ? memoryRaw : null,
      notification: notificationRaw && typeof notificationRaw === "object" ? notificationRaw : {},
    };
  }

  isJsonLikeText(text) {
    const raw = (text || "").toString().trim();
    if (!raw) return false;
    if (raw.startsWith("{") || raw.startsWith("[")) return true;
    const lower = raw.toLowerCase();
    if (lower.startsWith("```json") || lower.startsWith("```")) {
      const unwrapped = raw.replace(/^```(?:json)?/i, "").trim();
      return unwrapped.startsWith("{") || unwrapped.startsWith("[");
    }
    return lower.includes('"run_report"') || lower.includes('"runreport"');
  }

  isMachineRunProgressText(text) {
    const raw = (text || "").toString().trim();
    if (!raw) return false;
    if (this.isJsonLikeText(raw)) return true;
    const lower = raw.toLowerCase();
    const contractKeys = [
      '"run_report"',
      '"runreport"',
      '"memory_update"',
      '"memoryupdate"',
      '"notification_candidate"',
      '"notificationcandidate"',
      '"recommended_next_step"',
      '"recommendednextstep"',
      '"actions_taken"',
      '"actionstaken"',
      '"sources_covered"',
      '"sourcescovered"',
      '"touched_entities"',
      '"touchedentities"',
      '"rollback_notes"',
      '"rollbacknotes"',
      '"blockers"',
      '"artifacts"',
      '"approvals"',
      '"automation_state"',
      '"automationstate"',
      '"response_hash"',
      '"responsehash"',
      '"inspected_items"',
      '"inspecteditems"',
    ];
    if (contractKeys.some((key) => lower.includes(key))) return true;
    if (lower.includes("run report") || lower.includes("run_report") || lower.includes("automation_state") || lower.includes("response_hash")) return true;
    if (/^[}\]\s,]+/.test(raw) && /"[a-zA-Z_][a-zA-Z0-9_]*"\s*:/.test(raw)) return true;
    if (/^"[a-zA-Z_][a-zA-Z0-9_]*"\s*:/.test(raw)) return true;
    if (/^[}\]\s,:'"]+[\{\[]/.test(raw)) return true;
    if (/^\s*(?:null|true|false|\d+)\s*,/i.test(raw)) return true;
    if (/^\s*"[^"]{1,2000}"\s*,\s*(?:"|null|true|false|\d+|[\{\[])/is.test(raw)) return true;
    if (/^\s*["'](?:completed|no_change|changed|failed|ok)["']\s*,/i.test(raw)) return true;
    if ((raw.match(/[{}\[\]]/g) || []).length >= 3 && (raw.match(/",/g) || []).length >= 3) return true;
    return false;
  }

  machineRunProgressTailIndex(text) {
    const raw = (text || "").toString();
    if (!raw.trim()) return -1;
    const patterns = [
      /```json/i,
      /"run_report"\s*:/i,
      /"runReport"\s*:/,
      /"notification_candidate"\s*:/i,
      /"automation_state"\s*:/i,
      /"response_hash"\s*:/i,
      /"inspected_items"\s*:/i,
      /\n\s*["'}\]],?\s*:\s*[\{\[]/,
      /\n\s*[\{\[]\s*\n?\s*"(?:objective|status|findings|actions_taken|notification_candidate|automation_state)"/i,
    ];
    let index = -1;
    for (const pattern of patterns) {
      const match = raw.match(pattern);
      if (!match || typeof match.index !== "number") continue;
      index = index < 0 ? match.index : Math.min(index, match.index);
    }
    return index;
  }

  cleanRunActivityAssistantText(text) {
    const raw = (text || "").toString().trim();
    if (!raw) return "";
    if (this.isMachineRunProgressText(raw)) return "";
    const tailIndex = this.machineRunProgressTailIndex(raw);
    const visible = tailIndex >= 0 ? raw.slice(0, tailIndex).trim() : raw;
    if (!visible || this.isMachineRunProgressText(visible)) return "";
    return visible
      .replace(/\n{3,}/g, "\n\n")
      .replace(/`{1,3}\s*$/g, "")
      .trim();
  }

  plainTextFromRunResponse(run) {
    const result = run && run.result && typeof run.result === "object" ? run.result : null;
    const responseText = result && typeof result.responseText === "string" ? result.responseText.trim() : "";
    if (!responseText) return "";
    if (this.isJsonLikeText(responseText)) {
      const report = this.getRunReport(run);
      if (report) {
        const findings = Array.isArray(report.findings) ? report.findings : [];
        const firstFinding = findings.find((item) => typeof item === "string" && item.trim());
        if (firstFinding) return firstFinding.trim();
        const notification = report.notification && typeof report.notification === "object" ? report.notification : {};
        if (notification.body) return String(notification.body).trim();
      }
      return "";
    }
    return responseText.split(/\n{2,}/)[0].trim();
  }

  getRunSummaryText(run, fallbackStatus = "") {
    const display = run && run.display && typeof run.display === "object" ? run.display : null;
    if (display && typeof display.summary === "string" && display.summary.trim()) {
      const displaySummary = display.summary.trim();
      if (!this.isJsonLikeText(displaySummary)) return displaySummary;
    }
    const status = (run && run.status ? run.status : fallbackStatus || "").toString().trim().toLowerCase();
    if (["failed", "error"].includes(status) && run && typeof run.errorDetail === "string" && run.errorDetail.trim()) {
      return run.errorDetail.trim();
    }
    const report = this.getRunReport(run);
    if (report) {
      const notification = report.notification && typeof report.notification === "object" ? report.notification : {};
      if (notification.body) return String(notification.body).trim();
      const findings = Array.isArray(report.findings) ? report.findings : [];
      const firstFinding = findings.find((item) => typeof item === "string" && item.trim());
      if (firstFinding) return firstFinding.trim();
      if (report.recommendedNextStep) return String(report.recommendedNextStep).trim();
    }
    const responseText = this.plainTextFromRunResponse(run);
    if (responseText && !this.isJsonLikeText(responseText)) return responseText;
    if (status === "completed") return this.t("Completed. No detailed report was recorded.");
    if (status === "running") return this.t("Running now.");
    if (status === "queued") return this.t("Queued.");
    return status ? this.formatRunStatusLabel(status) : this.t("No update yet.");
  }

  getRunActionsTaken(run) {
    const display = run && run.display && typeof run.display === "object" ? run.display : null;
    if (display && Array.isArray(display.actionsTaken)) {
      return display.actionsTaken
        .map((item) => {
          if (typeof item === "string") return item.trim();
          if (item && typeof item === "object") {
            const count = Number(item.count);
            const countLabel = Number.isFinite(count) && count > 0 ? `${count} calls` : "";
            return [item.label || item.tool || item.name || "", item.status || "", countLabel]
              .filter(Boolean)
              .map((value) => this.formatStatus(String(value)))
              .join(" · ");
          }
          return "";
        })
        .filter(Boolean)
        .filter((item, index, items) => items.indexOf(item) === index)
        .slice(0, 5);
    }
    const report = this.getRunReport(run);
    const actions = report && Array.isArray(report.actionsTaken) ? report.actionsTaken : [];
    return actions
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (item && typeof item === "object") {
          const tool = item.tool || item.tool_name || item.name || "";
          const status = item.status || "";
          const count = Number(item.count);
          const countLabel = Number.isFinite(count) && count > 0 ? `${count} calls` : "";
          return [tool ? this.formatStatus(String(tool)) : "", status ? this.formatStatus(String(status)) : "", countLabel]
            .filter(Boolean)
            .join(" · ");
        }
        return "";
      })
      .filter(Boolean)
      .filter((item, index, items) => items.indexOf(item) === index)
      .slice(0, 5);
  }

  formatAutomationRunMeta(run) {
    if (!run || typeof run !== "object") return "";
    const whenRaw = run.finishedAt || run.startedAt || run.createdAt || run.updatedAt || "";
    return whenRaw ? this.formatDueTime(whenRaw) : "";
  }

  stateForRun(run) {
    const runId = run && run.id ? String(run.id) : "";
    const existing = runId ? this.agentRuns.get(runId) : null;
    if (existing) {
      existing.run = Object.assign({}, existing.run || {}, run || {});
      return existing;
    }
    return { run: Object.assign({}, run || {}), events: [], expanded: this.isRunActiveStatus(run && run.status), seenSeq: new Set(), lastEventLabel: "" };
  }

  renderAutomationRunDetailHtml(run, { suppressActions = false } = {}) {
    const state = this.stateForRun(run);
    const actionsHtml = suppressActions ? "" : this.renderRunActionsHtml(run && run.id ? String(run.id) : "", state, run);
    const workHtml = this.renderRunWorkHtml(state, run);
    const resultHtml = this.renderRunResultHtml(run, state);
    const debugHtml = this.renderRunDeveloperDetailsHtml(run);
    const emptyHtml = !actionsHtml && !resultHtml && !workHtml
      ? `<div class="portal-task__empty-note">${this.escapeHtml(this.t("No activity recorded yet."))}</div>`
      : "";
    return `
      <div class="portal-task__run-detail">
        ${workHtml}
        ${actionsHtml}
        ${resultHtml}
        ${debugHtml}
        ${emptyHtml}
      </div>
    `;
  }

  renderAutomationRunRowHtml(automationId, run, { expanded = false, latest = false, suppressActions = false } = {}) {
    const runId = run && run.id ? String(run.id) : "";
    const status = run && run.status ? String(run.status) : "";
    const display = this.getRunDisplay(run);
    const summary = display.summary || this.getRunSummaryText(run, status);
    const title = run && run.title ? String(run.title).trim() : summary;
    const normalizedTitle = title.toLowerCase().replace(/\s+/g, " ").trim();
    const normalizedSummary = String(summary || "").toLowerCase().replace(/\s+/g, " ").trim();
    const showPreview = Boolean(summary && normalizedSummary && normalizedSummary !== normalizedTitle && !expanded);
    const meta = this.formatAutomationRunMeta(run);
    const tone = display.statusTone || "neutral";
    const statusLabel = this.formatRunStatusLabel(status);
    return `
      <div class="portal-task__run-row" data-status="${this.escapeHtml(status.toLowerCase())}" data-tone="${this.escapeHtml(tone)}" data-expanded="${expanded ? "true" : "false"}">
        <button type="button" class="portal-task__run-row-button" data-automation-run-toggle="true" data-automation-id="${this.escapeHtml(automationId)}" data-run-id="${this.escapeHtml(runId)}">
          <span class="portal-task__run-state" aria-hidden="true">${this.getRunStateIconMarkup(status, tone)}</span>
          <span class="portal-task__run-row-main">
            <span class="portal-task__run-row-top">
              <span class="portal-task__run-kicker">${this.escapeHtml(latest ? this.t("Latest run") : this.t("Run"))}</span>
              <span class="portal-task__run-chips">
                <span class="portal-task__run-chip portal-task__run-chip--status">${this.escapeHtml(statusLabel)}</span>
                ${meta ? `<span class="portal-task__run-chip">${this.escapeHtml(meta)}</span>` : ""}
              </span>
            </span>
            <span class="portal-task__run-row-title">${this.escapeHtml(title || summary || this.t("Run"))}</span>
            ${showPreview ? `<span class="portal-task__run-row-summary">${this.escapeHtml(summary)}</span>` : ""}
          </span>
          <span class="portal-task__run-chevron" aria-hidden="true">${this.getChevronRightIconMarkup()}</span>
        </button>
        ${expanded ? this.renderAutomationRunDetailHtml(run, { suppressActions }) : ""}
      </div>
    `;
  }

  renderAutomationRecentRunsHtml(automationId, state, recentRuns, { suppressActionsForRunIds = null } = {}) {
    const runs = Array.isArray(recentRuns) ? recentRuns.filter(Boolean).slice(0, 8) : [];
    if (!runs.length) return "";
    return `
      <div class="portal-task__section">
        <div class="portal-task__section-title">${this.escapeHtml(this.t("Runs"))}</div>
        <div class="portal-task__run-list">
          ${runs
            .map((run, index) => this.renderAutomationRunRowHtml(automationId, run, {
              expanded: Boolean(state && state.expandedRuns instanceof Set && state.expandedRuns.has(String(run.id || ""))),
              latest: index === 0,
              suppressActions: Boolean(suppressActionsForRunIds && suppressActionsForRunIds.has(String(run.id || ""))),
            }))
            .join("")}
        </div>
      </div>
    `;
  }

  renderAutomationCardHtml(automationId, state, automation) {
    const expanded = Boolean(state && state.expanded);
    const name = automation && automation.name ? String(automation.name) : this.t("Automation");
    const latestRun = automation && automation.latestRun && typeof automation.latestRun === "object" ? automation.latestRun : null;
    const checkpoint = automation && automation.openCheckpoint && typeof automation.openCheckpoint === "object" ? automation.openCheckpoint : null;
    const checkpointKind = checkpoint && checkpoint.kind ? String(checkpoint.kind).trim().toLowerCase() : "";
    const checkpointStatus = checkpoint ? (checkpointKind === "user_input" ? "waiting_user" : "waiting_approval") : "";
    const status = checkpointStatus || (latestRun && latestRun.status ? latestRun.status : (automation.status || "draft"));
    const attentionStatus = this.getTaskAttentionStatus(status);
    const needsAttention = Boolean(checkpoint || attentionStatus);
    const triggerLabel = this.getAutomationTriggerLabel(automation && automation.triggerType);
    const ownerLabel = automation && automation.agentName ? String(automation.agentName) : "";
    const subtitleParts = [ownerLabel, triggerLabel];
    if (automation && automation.nextTriggerAt) subtitleParts.push(`${this.t("Next")} ${this.formatDueTime(automation.nextTriggerAt)}`);
    else if (automation && automation.lastTriggeredAt) subtitleParts.push(`${this.t("Last")} ${this.formatDueTime(automation.lastTriggeredAt)}`);
    const subtitle = subtitleParts.filter(Boolean).join(" · ") || this.formatRunStatusLabel(status);
    const latestSummary = checkpoint
      ? (checkpoint.prompt || checkpoint.title || this.t("Needs attention"))
      : (latestRun ? this.getRunSummaryText(latestRun, status) : (automation.description || this.t("No runs recorded yet.")));
    const rawRecentRuns = automation && Array.isArray(automation.recentRuns) ? automation.recentRuns : [];
    const latestRunId = latestRun && latestRun.id ? String(latestRun.id) : "";
    const recentRuns = latestRunId
      ? rawRecentRuns.filter((run) => !run || String(run.id || "") !== latestRunId)
      : rawRecentRuns;
    const checkpointHtml = checkpoint ? this.renderAutomationCheckpointHtml(checkpoint) : "";
    const allRuns = latestRun ? [latestRun, ...recentRuns] : recentRuns;
    const checkpointRunId = checkpoint && checkpoint.runId ? String(checkpoint.runId) : "";
    const suppressActionsForRunIds = checkpointRunId ? new Set([checkpointRunId]) : null;
    const recentHtml = this.renderAutomationRecentRunsHtml(automationId, state, allRuns, { suppressActionsForRunIds });
    const runBusy = this.automationManualRunBusy && this.automationManualRunBusy.has(automationId);
    const canRunNow = this.isRunnableAutomation(automation);
    return `
      <div class="portal-task" data-automation-id="${this.escapeHtml(automationId)}" data-expanded="${expanded ? "true" : "false"}" data-attention="${needsAttention ? "true" : "false"}" data-attention-status="${this.escapeHtml(attentionStatus || "")}">
        <div class="portal-task__header-bar">
          <button type="button" class="portal-task__header" data-automation-toggle="${this.escapeHtml(automationId)}">
            <div class="portal-task__meta">
              <div class="portal-task__title-row">
                <div class="portal-task__title">${this.escapeHtml(name)}</div>
                ${this.renderTaskAttentionIndicator(status)}
                ${this.renderRunStatusPill(status)}
              </div>
              <div class="portal-task__subtitle">${this.escapeHtml(subtitle)}</div>
              <div class="portal-task__summary-preview" ${needsAttention ? "hidden" : ""}>${this.escapeHtml(needsAttention ? "" : latestSummary)}</div>
            </div>
          </button>
          ${canRunNow ? `<button type="button" class="portal-task__run-now" data-automation-run-now="${this.escapeHtml(automationId)}" data-busy="${runBusy ? "true" : "false"}" ${runBusy ? "disabled" : ""} aria-label="${this.escapeHtml(this.t("Run automation now"))}" title="${this.escapeHtml(this.t("Run automation now"))}">
            ${this.getAutomationRunIconMarkup()}
          </button>` : ""}
        </div>
        <div class="portal-task__body">
          ${checkpointHtml}
          ${recentHtml || `<div class="portal-task__empty-note">${this.escapeHtml(this.t("No runs recorded yet."))}</div>`}
        </div>
      </div>
    `;
  }

  renderAutomationCheckpointHtml(checkpoint) {
    const checkpointId = checkpoint && checkpoint.id ? String(checkpoint.id).trim() : "";
    const kind = checkpoint && checkpoint.kind ? String(checkpoint.kind).trim().toLowerCase() : "";
    const prompt = checkpoint && checkpoint.prompt ? String(checkpoint.prompt).trim() : "";
    const payload = checkpoint && checkpoint.payload && typeof checkpoint.payload === "object" ? checkpoint.payload : {};
    const preview = payload.approval_preview && typeof payload.approval_preview === "object" ? payload.approval_preview : null;
    const previewHtml = preview ? this.renderAgentRunApprovalPreview(preview) : "";
    const payloadPrompt = payload && payload.prompt ? String(payload.prompt).trim() : "";
    const payloadQuestions = payload && Array.isArray(payload.questions) ? payload.questions : [];
    const needsText = payloadPrompt || prompt || (kind === "approval" ? this.t("This automation needs approval to continue.") : this.t("This automation needs more information to continue."));
    if (kind === "approval") {
      return `
        <div class="portal-task__section">
          <div class="portal-task__section-title">${this.escapeHtml(this.t("Needs attention"))}</div>
          <div class="portal-task__subtitle">${this.escapeHtml(needsText)}</div>
          ${previewHtml}
          <div class="portal-task__actions">
            <button type="button" class="portal-task__btn portal-task__btn--approve" data-checkpoint-action="approve" data-checkpoint-id="${this.escapeHtml(checkpointId)}">${this.escapeHtml(this.t("Approve"))}</button>
            <button type="button" class="portal-task__btn portal-task__btn--deny" data-checkpoint-action="deny" data-checkpoint-id="${this.escapeHtml(checkpointId)}">${this.escapeHtml(this.t("Deny"))}</button>
          </div>
        </div>
      `;
    }
    const questionLines = payloadQuestions
      .filter((question) => typeof question === "string" && question.trim())
      .slice(0, 6)
      .map((question) => `<div class="portal-task__list-item">${this.escapeHtml(question.trim())}</div>`)
      .join("");
    const questionHtml = questionLines ? `<div class="portal-task__list">${questionLines}</div>` : "";
    return `
      <div class="portal-task__section">
        <div class="portal-task__section-title">${this.escapeHtml(this.t("Question"))}</div>
        <div class="portal-task__prompt">${this.renderMarkdown(needsText)}</div>
        ${questionHtml}
        <textarea class="portal-task__input" data-checkpoint-input-text rows="3" placeholder="${this.escapeHtml(this.t("Type your answer…"))}"></textarea>
        <div class="portal-task__actions">
          <button type="button" class="portal-task__btn" data-checkpoint-action="reply" data-checkpoint-id="${this.escapeHtml(checkpointId)}">${this.escapeHtml(this.t("Send"))}</button>
        </div>
      </div>
    `;
  }

  getTaskAttentionStatus(status) {
    const normalized = (status || "").toString().trim().toLowerCase().replace(/-/g, "_");
    if (normalized === "waiting_user") return "waiting_user";
    if (normalized === "waiting_approval") return "waiting_approval";
    return "";
  }

  renderTaskAttentionIndicator(status) {
    const attentionStatus = this.getTaskAttentionStatus(status);
    if (!attentionStatus) return "";
    const label = attentionStatus === "waiting_user" ? this.t("Needs your input") : this.t("Needs approval");
    return `<span class="portal-task__attention-indicator" aria-label="${this.escapeHtml(label)}" title="${this.escapeHtml(label)}"></span>`;
  }

  renderRunCardHtml(runId, state, run) {
    const statusRaw = (run && run.status ? run.status : "queued").toString().trim().toLowerCase() || "queued";
    const title = run && run.title ? run.title : this.t("Background task");
    const lastEvent = state && Array.isArray(state.events) && state.events.length ? state.events[state.events.length - 1] : null;
    const subtitle = this.formatRunSubtitle(run, lastEvent);
    const expanded = Boolean(state && state.expanded);

    const actionsHtml = this.renderRunActionsHtml(runId, state, run);
    const workHtml = this.renderRunWorkHtml(state, run);
    const resultHtml = this.renderRunResultHtml(run, state);
    const debugHtml = this.renderRunDeveloperDetailsHtml(run);
    const bodyHtml = `${workHtml}${actionsHtml}${resultHtml}${debugHtml}`;

    return `
      <div class="portal-task portal-task--run-card" data-run-id="${this.escapeHtml(runId)}" data-expanded="${expanded ? "true" : "false"}">
        <button type="button" class="portal-task__header" data-run-toggle="${this.escapeHtml(runId)}">
          <div class="portal-task__meta">
            <div class="portal-task__title-row">
              <div class="portal-task__title">${this.escapeHtml(String(title || this.t("Background task")))}</div>
              ${this.renderRunStatusPill(statusRaw)}
            </div>
            <div class="portal-task__subtitle">${this.escapeHtml(subtitle)}</div>
          </div>
        </button>
        <div class="portal-task__body">
          ${bodyHtml}
        </div>
      </div>
    `;
  }

  renderRunActionsHtml(runId, state, run) {
    const status = (run && run.status ? run.status : "").toString().trim().toLowerCase();
    if (status === "waiting_approval") {
      const approvalEvent = this.getLatestRunEvent(state, (evt) => {
        return evt && evt.type === "needs_approval";
      });
      const payload = approvalEvent && approvalEvent.payload && typeof approvalEvent.payload === "object" ? approvalEvent.payload : null;
      const approval = payload && payload.approval && typeof payload.approval === "object" ? payload.approval : null;
      const approvalId = approval && approval.id ? approval.id.toString().trim() : "";
      if (!approvalId) {
        return `
          <div class="portal-task__section">
            <div class="portal-task__section-title">${this.escapeHtml(this.t("Approval"))}</div>
            <div class="portal-task__subtitle">${this.escapeHtml(this.t("Waiting for approval."))}</div>
          </div>
        `;
      }
      const reason = approval.reason ? approval.reason.toString().trim() : "";
      const toolName = payload && payload.tool_name ? payload.tool_name.toString().trim() : "";
      const remote = payload && payload.remote && typeof payload.remote === "object" ? payload.remote : null;
      const connectionName = remote && remote.connection_name ? remote.connection_name.toString().trim() : "";
      const toolLabel = connectionName ? `${connectionName}${toolName ? ` · ${toolName}` : ""}` : toolName;
      const summaryParts = [];
      if (toolLabel) summaryParts.push(toolLabel);
      if (reason) summaryParts.push(reason);
      const summary = summaryParts.join(" • ");

      return `
        <div class="portal-task__section">
          <div class="portal-task__section-title">${this.escapeHtml(this.t("Approval"))}</div>
          <div class="portal-task__subtitle">${this.escapeHtml(summary || this.t("This task needs your approval to continue."))}</div>
          <div class="portal-task__actions">
            <button type="button" class="portal-task__btn portal-task__btn--approve" data-run-approval-action="approve" data-approval-id="${this.escapeHtml(
              approvalId
            )}">${this.escapeHtml(this.t("Approve"))}</button>
            <button type="button" class="portal-task__btn portal-task__btn--deny" data-run-approval-action="deny" data-approval-id="${this.escapeHtml(
              approvalId
            )}">${this.escapeHtml(this.t("Deny"))}</button>
          </div>
        </div>
      `;
    }

    if (status === "waiting_user") {
      const needsUserEvent = this.getLatestRunEvent(state, (evt) => {
        return evt && evt.type === "needs_user";
      });
      const payload = needsUserEvent && needsUserEvent.payload && typeof needsUserEvent.payload === "object" ? needsUserEvent.payload : null;
      const prompt = payload && payload.prompt ? payload.prompt.toString().trim() : "";
      const questions = payload && Array.isArray(payload.questions) ? payload.questions : [];
      const questionLines = questions
        .filter((q) => typeof q === "string" && q.trim())
        .slice(0, 6)
        .map((q) => `<div class="portal-task__list-item">${this.escapeHtml(q.trim())}</div>`)
        .join("");
      const questionHtml = questionLines ? `<div class="portal-task__list">${questionLines}</div>` : "";
      const promptText = prompt || (questionLines ? "" : "This task needs more information to continue.");

      return `
        <div class="portal-task__section">
          <div class="portal-task__section-title">${this.escapeHtml(this.t("Question"))}</div>
          ${promptText ? `<div class="portal-task__subtitle">${this.escapeHtml(promptText)}</div>` : ""}
          ${questionHtml}
          <textarea class="portal-task__input" data-run-user-input-text rows="3" placeholder="${this.escapeHtml(this.t("Type your answer…"))}"></textarea>
          <div class="portal-task__actions">
            <button type="button" class="portal-task__btn" data-run-user-input-send="${this.escapeHtml(runId)}">${this.escapeHtml(this.t("Send"))}</button>
          </div>
        </div>
      `;
    }

    if (status === "waiting_external") {
      return `
        <div class="portal-task__section">
          <div class="portal-task__section-title">${this.escapeHtml(this.t("Waiting"))}</div>
          <div class="portal-task__subtitle">${this.escapeHtml(this.t("This task is waiting on another agent. Check the Inbox for updates."))}</div>
          <div class="portal-task__actions">
            <button type="button" class="portal-task__btn" data-open-inbox="true">${this.escapeHtml(this.t("Open inbox"))}</button>
          </div>
        </div>
      `;
    }

    return "";
  }

  formatRunStatusLabel(status) {
    const norm = (status || "").toString().trim().toLowerCase();
    if (!norm) return this.t("Queued");
    return norm
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  formatRunSubtitle(run, lastEvent) {
    const normalizeSystemLabel = (raw) => {
      const textRaw = (raw || "").toString().trim();
      if (!textRaw) return "";
      const lower = textRaw.toLowerCase();
      if (lower === "stream_complete" || lower === "stream.completed" || lower === "answer ready" || lower === "answer_ready") {
        return "";
      }
      if (lower.startsWith("responding")) {
        return "";
      }
      if (lower.startsWith("status:")) {
        return "";
      }
      let text = textRaw;
      text = text.replace(/_/g, " ").replace(/\s+/g, " ").trim();
      if (!text) return "";
      return text.charAt(0).toUpperCase() + text.slice(1);
    };

    const computeLabel = (evt) => {
      if (!evt || typeof evt !== "object") return "";
      const stream = (evt.stream || "").toString().trim().toLowerCase();
      const type = (evt.type || "").toString().trim().toLowerCase();
      const raw = evt.label ? String(evt.label) : "";
      const payload = evt.payload && typeof evt.payload === "object" ? evt.payload : {};

      if (stream === "executed") {
        const phase = (payload.phase || "").toString().trim().toLowerCase();
        if (phase !== "finished") return "";
        const toolNameRaw = (payload.tool_name || payload.toolName || "").toString().trim();
        const effectiveToolName = this.getEffectiveToolName(toolNameRaw, payload);
        const toolName = effectiveToolName ? this.formatStatus(effectiveToolName) : toolNameRaw;
        const remote = payload.remote && typeof payload.remote === "object" ? payload.remote : null;
        const connectionName = remote && remote.connection_name ? remote.connection_name.toString().trim() : "";
        const remoteTool = remote && remote.remote_tool ? remote.remote_tool.toString().trim() : "";
        let title = toolName || remoteTool || raw || "Tool";
        if (connectionName) title = `${connectionName} · ${title}`;
        return `Tool: ${title}`;
      }

      if (type === "needs_approval") return this.t("Needs approval");
      if (type === "needs_user") return this.t("Needs your input");
      if (type === "result") return this.t("Completed");
      if (type === "error") return this.t("Error");
      return normalizeSystemLabel(raw || type);
    };

    const label = computeLabel(lastEvent);
    const createdAt = lastEvent && lastEvent.createdAt ? String(lastEvent.createdAt) : "";
    if (label && createdAt) {
      const when = Date.parse(createdAt);
      if (!Number.isNaN(when)) {
        return `${label} · ${this.formatRelativeTime(new Date(when))}`;
      }
      return label;
    }
    if (label) return label;
    const status = run && run.status ? String(run.status) : "";
    return status ? this.formatRunStatusLabel(status) : this.t("Waiting for updates…");
  }

  renderRunPlanHtml(run) {
    const plan = run && typeof run.plan === "object" ? run.plan : null;
    const steps = plan && Array.isArray(plan.steps) ? plan.steps : [];
    const items = steps
      .slice(0, 12)
      .map((step) => {
        const title = step && (step.title || step.description || step.step_id || step.stepId) ? (step.title || step.description || step.step_id || step.stepId) : "";
        return `<div class="portal-task__list-item">${this.escapeHtml(String(title || "").trim() || this.t("Step"))}</div>`;
      })
      .join("");
    if (!items) return "";
    return `
      <div class="portal-task__section">
        <div class="portal-task__section-title">${this.escapeHtml(this.t("Plan"))}</div>
        <div class="portal-task__list">${items}</div>
      </div>
    `;
  }

  isRunTerminalStatus(status) {
    const normalized = (status || "").toString().trim().toLowerCase();
    return ["completed", "succeeded", "success", "failed", "error", "cancelled", "canceled"].includes(normalized);
  }

  isRunActiveStatus(status) {
    const normalized = (status || "").toString().trim().toLowerCase();
    return ["queued", "running", "waiting_user", "waiting_approval", "waiting_child", "waiting_external", "paused"].includes(normalized);
  }

  getRunDurationMs(run) {
    const direct = Number(run && run.durationMs);
    if (Number.isFinite(direct) && direct > 0) return direct;
    const startedAt = run && run.startedAt ? Date.parse(String(run.startedAt)) : NaN;
    const finishedAt = run && run.finishedAt ? Date.parse(String(run.finishedAt)) : NaN;
    if (Number.isFinite(startedAt) && Number.isFinite(finishedAt) && finishedAt > startedAt) {
      return finishedAt - startedAt;
    }
    if (Number.isFinite(startedAt) && !this.isRunTerminalStatus(run && run.status ? run.status : "")) {
      return Math.max(0, Date.now() - startedAt);
    }
    return 0;
  }

  formatRunWorkLabel(run) {
    const status = run && run.status ? String(run.status) : "";
    const durationMs = this.getRunDurationMs(run);
    const durationLabel = durationMs > 0 ? this.formatDurationMs(durationMs) : "";
    if (this.isRunTerminalStatus(status)) {
      return durationLabel ? `${this.t("Worked for")} ${durationLabel}` : this.t("Worked");
    }
    return durationLabel ? `${this.t("Working for")} ${durationLabel}` : this.t("Working");
  }

  formatRunToolLabel(toolName, payload) {
    const effective = this.getEffectiveToolName(toolName || "", payload || {});
    const raw = effective || toolName || "";
    const human = this.humanizeAgentToolName(raw || "tool");
    return this.formatStatus(human || raw || "Tool");
  }

  getRunToolEventKey(evt, payload, toolName) {
    const raw =
      payload.tool_call_id ||
      payload.toolCallId ||
      payload.event_id ||
      payload.eventId ||
      payload.call_id ||
      payload.callId ||
      "";
    if (raw) return `tool:${raw}`;
    const seq = Number(evt && evt.sequenceIndex);
    return `tool:${toolName || "tool"}:${Number.isFinite(seq) && seq > 0 ? seq : Math.random().toString(36).slice(2)}`;
  }

  buildRunActivityItems(state) {
    const events = state && Array.isArray(state.events) ? state.events : [];
    const recent = events.slice(-250);
    const rows = [];
    const toolIndexByKey = new Map();
    const appendAssistantRow = (text) => {
      const clean = this.cleanRunActivityAssistantText(text);
      if (!clean) return;
      const last = rows.length ? rows[rows.length - 1] : null;
      if (last && last.kind === "assistant") {
        const joined = `${last.text}\n\n${clean}`.trim();
        last.text = joined.length > 3600 ? joined.slice(Math.max(0, joined.length - 3600)).trim() : joined;
      } else {
        rows.push({ kind: "assistant", text: clean });
      }
    };
    const appendToolRow = (evt, payload, labelRaw) => {
      const phase = (payload.phase || "").toString().trim().toLowerCase();
      const toolNameRaw = (payload.tool_name || payload.toolName || "").toString().trim();
      const effectiveToolName = this.getEffectiveToolName(toolNameRaw, payload);
      const toolName = effectiveToolName || toolNameRaw;
      const remote = payload.remote && typeof payload.remote === "object" ? payload.remote : null;
      const connectionName = remote && remote.connection_name ? remote.connection_name.toString().trim() : "";
      const remoteTool = remote && remote.remote_tool ? remote.remote_tool.toString().trim() : "";
      if (!toolName && !remoteTool && !labelRaw) return;
      const output = payload.output && typeof payload.output === "object" ? payload.output : null;
      const input = payload.input && typeof payload.input === "object" ? payload.input : null;
      const status =
        (output && output.status ? String(output.status).trim().toLowerCase() : "") ||
        (payload.status ? String(payload.status).trim().toLowerCase() : "");
      let title = this.formatRunToolLabel(toolName || remoteTool || labelRaw || "Tool", payload);
      if (connectionName) title = `${connectionName} · ${title}`;
      const key = this.getRunToolEventKey(evt, payload, toolName || remoteTool || labelRaw);
      const existingIndex = toolIndexByKey.get(key);
      const nextState =
        phase === "started" || phase === "starting"
          ? "running"
          : phase === "approval_requested"
            ? "needs approval"
            : phase === "finished"
              ? (status || "ok")
              : (phase ? phase.replace(/_/g, " ") : status || "");
      const nextItem = {
        kind: "tool",
        key,
        title,
        status: nextState,
        input,
        output,
        payload,
      };
      if (typeof existingIndex === "number" && rows[existingIndex]) {
        rows[existingIndex] = Object.assign({}, rows[existingIndex], {
          title,
          status: nextState || rows[existingIndex].status,
          input: input || rows[existingIndex].input || null,
          output: output || rows[existingIndex].output || null,
          payload: Object.assign({}, rows[existingIndex].payload || {}, payload || {}),
        });
      } else {
        toolIndexByKey.set(key, rows.length);
        rows.push(nextItem);
      }
    };
    const appendCheckpointResolutionRow = (payload, labelRaw) => {
      const action = (payload.action || "").toString().trim();
      const message = (payload.message || "").toString().trim();
      const extraPayload = payload.payload && typeof payload.payload === "object" ? payload.payload : null;
      const checkpointId = (payload.checkpoint_id || payload.checkpointId || "").toString().trim();
      rows.push({
        kind: "checkpoint_resolution",
        title: labelRaw || this.t("Checkpoint resolved"),
        action,
        message,
        payload: extraPayload,
        checkpointId,
      });
    };

    for (const evt of recent) {
      if (!evt || typeof evt !== "object") continue;
      const stream = (evt.stream || "").toString().trim().toLowerCase();
      const type = (evt.type || "").toString().trim().toLowerCase();
      const labelRaw = evt.label ? String(evt.label) : "";
      const payload = evt.payload && typeof evt.payload === "object" ? evt.payload : {};

      if (payload.kind === "assistant_message" && typeof payload.text === "string" && payload.text.trim()) {
        appendAssistantRow(payload.text);
        continue;
      }

      if (stream === "executed") {
        const isCheckpointResolution =
          labelRaw.toLowerCase() === "checkpoint resolved" ||
          Boolean(payload.checkpoint_id || payload.checkpointId);
        if (isCheckpointResolution) {
          appendCheckpointResolutionRow(payload, labelRaw);
          continue;
        }
        appendToolRow(evt, payload, labelRaw);
        continue;
      }

      if (stream === "system") {
        let line = "";
        if (type === "needs_approval") line = "Needs approval";
        else if (type === "needs_user") line = "Needs your input";
        else if (type === "error") line = "Error";
        if (!line) continue;
        rows.push({ kind: "system", line: line.charAt(0).toUpperCase() + line.slice(1) });
      }
    }
    return rows.slice(-80);
  }

  renderToolDetailsHtml(row) {
    const requestHtml = row.input
      ? `
        <div class="portal-task__tool-detail-block">
          <div class="portal-task__tool-detail-label">${this.escapeHtml(this.t("Request"))}</div>
          <pre><code>${this.escapeHtml(this.safeJsonStringify(row.input))}</code></pre>
        </div>
      `
      : "";
    const outputHtml = row.output
      ? `
        <div class="portal-task__tool-detail-block">
          <div class="portal-task__tool-detail-label">${this.escapeHtml(this.t("Output"))}</div>
          <pre><code>${this.escapeHtml(this.safeJsonStringify(row.output))}</code></pre>
        </div>
      `
      : "";
    if (!requestHtml && !outputHtml && !this.agentRunDebugEnabled) return "";
    const payloadHtml = !requestHtml && !outputHtml && row.payload
      ? `
        <div class="portal-task__tool-detail-block">
          <div class="portal-task__tool-detail-label">${this.escapeHtml(this.t("Event"))}</div>
          <pre><code>${this.escapeHtml(this.safeJsonStringify(row.payload))}</code></pre>
        </div>
      `
      : "";
    return `
      <div class="portal-task__tool-detail">
        ${requestHtml}
        ${outputHtml}
        ${payloadHtml}
      </div>
    `;
  }

  renderCheckpointResolutionDetailsHtml(row) {
    const action = row && row.action ? this.formatStatus(String(row.action)) : "";
    const message = row && row.message ? String(row.message).trim() : "";
    const payload = row && row.payload && typeof row.payload === "object" ? row.payload : null;
    const checkpointId = row && row.checkpointId ? String(row.checkpointId).trim() : "";
    const metaItems = [];
    if (action) metaItems.push(`<div class="portal-task__tool-detail-label">${this.escapeHtml(this.t("Action"))}: ${this.escapeHtml(action)}</div>`);
    if (checkpointId && this.agentRunDebugEnabled) metaItems.push(`<div class="portal-task__tool-detail-label">${this.escapeHtml(this.t("Checkpoint"))}: ${this.escapeHtml(checkpointId)}</div>`);
    const messageHtml = message
      ? `
        <div class="portal-task__checkpoint-reply">
          ${this.renderMarkdown(message)}
        </div>
      `
      : `<div class="portal-task__empty-note">${this.escapeHtml(this.t("No reply text was provided."))}</div>`;
    const payloadHtml = payload && Object.keys(payload).length
      ? `
        <div class="portal-task__tool-detail-block">
          <div class="portal-task__tool-detail-label">${this.escapeHtml(this.t("Payload"))}</div>
          <pre><code>${this.escapeHtml(this.safeJsonStringify(payload))}</code></pre>
        </div>
      `
      : "";
    return `
      <div class="portal-task__tool-detail">
        ${metaItems.join("")}
        ${messageHtml}
        ${payloadHtml}
      </div>
    `;
  }

  renderRunWorkHtml(state, run, options = {}) {
    const terminal = this.isRunTerminalStatus(run && run.status ? run.status : "");
    const rows = this.buildRunActivityItems(state);
    if (!rows.length && options.compact) return "";
    const items = rows
      .map((row) => {
        if (row.kind === "assistant") {
          return `<div class="portal-task__scratchpad-text">${this.renderMarkdown(row.text)}</div>`;
        }
        if (row.kind === "tool") {
          const status = row.status && row.status !== "ok" ? `<span class="portal-task__tool-status">${this.escapeHtml(this.formatStatus(row.status))}</span>` : "";
          return `
            <details class="portal-task__tool-call">
              <summary>
                <span class="portal-task__tool-icon" aria-hidden="true">${this.getToolCallIconMarkup()}</span>
                <span class="portal-task__tool-name">${this.escapeHtml(row.title)}</span>
                ${status}
                <span class="portal-task__tool-chevron" aria-hidden="true">${this.getChevronRightIconMarkup()}</span>
              </summary>
              ${this.renderToolDetailsHtml(row)}
            </details>
          `;
        }
        if (row.kind === "checkpoint_resolution") {
          const action = row.action ? `<span class="portal-task__tool-status">${this.escapeHtml(this.formatStatus(String(row.action)))}</span>` : "";
          return `
            <details class="portal-task__tool-call">
              <summary>
                <span class="portal-task__tool-icon" aria-hidden="true">${this.getToolCallIconMarkup()}</span>
                <span class="portal-task__tool-name">${this.escapeHtml(row.title || this.t("Checkpoint resolved"))}</span>
                ${action}
                <span class="portal-task__tool-chevron" aria-hidden="true">${this.getChevronRightIconMarkup()}</span>
              </summary>
              ${this.renderCheckpointResolutionDetailsHtml(row)}
            </details>
          `;
        }
        return `<div class="portal-task__scratchpad-system">${this.escapeHtml(row.line)}</div>`;
      })
      .join("");
    const runId = run && run.id ? String(run.id) : "";
    const body = items
      ? `<div class="portal-task__scratchpad custom-scrollbar" data-run-scratchpad="${this.escapeHtml(runId)}">${items}</div>`
      : `<div class="portal-task__subtitle">${this.escapeHtml(this.t("No activity yet."))}</div>`;
    const openAttr = terminal ? "" : " open";
    return `
      <details class="portal-task__work"${openAttr}>
        <summary>
          <span>${this.escapeHtml(this.formatRunWorkLabel(run))}</span>
          <span class="portal-task__work-chevron" aria-hidden="true">${this.getChevronRightIconMarkup()}</span>
        </summary>
        ${body}
      </details>
    `;
  }

  renderRunResultHtml(run, state) {
    const status = (run && run.status ? run.status : "").toString().toLowerCase();
    if (!this.isRunTerminalStatus(status)) return "";
    const result = run && typeof run.result === "object" ? run.result : null;
    const responseText = result && typeof result.responseText === "string" ? result.responseText : "";
    const responseLooksStructured = this.isJsonLikeText(responseText);
    const report = this.getRunReport(run);
    const display = this.getRunDisplay(run);
    const errorDetail = run && typeof run.errorDetail === "string" ? run.errorDetail : "";

    if (status === "failed" && errorDetail) {
      return `
        <div class="portal-task__section">
          <div class="portal-task__section-title">Error</div>
          <div class="portal-task__result">${this.renderMarkdown(errorDetail)}</div>
        </div>
      `;
    }

    const runTitle = run && run.title ? String(run.title).trim() : "";
    const normalizeForCompare = (value) => String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
    const genericCompleted = normalizeForCompare(this.t("Completed. No detailed report was recorded."));

    // Generic status strings that add no value as a "final response".
    const genericStatuses = [
      "running now.", "queued.", "no update yet.", "started.",
      "thinking…", "thinking...", "processing.",
    ].map((s) => normalizeForCompare(s));

    const candidates = [];
    const agentMessage = display && typeof display.agentMessage === "string" ? display.agentMessage.trim() : "";
    const summary = display && typeof display.summary === "string" ? display.summary.trim() : "";
    if (responseText && !responseLooksStructured) candidates.push(responseText.trim());
    if (agentMessage) candidates.push(agentMessage);
    if (summary) candidates.push(summary);
    if (report) {
      const notification = report.notification && typeof report.notification === "object" ? report.notification : {};
      if (notification.body) candidates.push(String(notification.body).trim());
      const findings = Array.isArray(report.findings) ? report.findings : [];
      findings.forEach((item) => {
        if (typeof item === "string" && item.trim()) candidates.push(item.trim());
      });
      if (report.recommendedNextStep) candidates.push(String(report.recommendedNextStep).trim());
    }

    const resultText = candidates.find((candidate) => {
      const normalized = normalizeForCompare(candidate);
      if (!normalized) return false;
      if (normalized === normalizeForCompare(runTitle)) return false;
      if (normalized === genericCompleted) return false;
      if (this.isJsonLikeText(candidate)) return false;
      // Suppress generic status strings.
      if (genericStatuses.includes(normalized)) return false;
      return true;
    }) || "";

    if (resultText) {
      return `
        <div class="portal-task__final-response">
          ${this.renderMarkdown(resultText)}
        </div>
      `;
    }

    return "";
  }

  renderRunDeveloperDetailsHtml(run) {
    if (!this.agentRunDebugEnabled) return "";
    const result = run && typeof run.result === "object" ? run.result : null;
    const responseText = result && typeof result.responseText === "string" ? result.responseText : "";
    const report = this.getRunReport(run);
    const display = run && run.display && typeof run.display === "object" ? run.display : null;
    const status = (run && run.status ? run.status : "").toString().trim().toLowerCase();
    const responseLooksStructured = this.isJsonLikeText(responseText);
    const shouldShowRaw = Boolean(report || responseLooksStructured || (["failed", "error"].includes(status) && result));
    if (!shouldShowRaw) return "";
    let raw = display && display.rawDebug && typeof display.rawDebug === "object" ? display.rawDebug : null;
    if (!raw && result && typeof result === "object") raw = result;
    if (!raw && report && typeof report === "object") raw = { runReport: report };
    if (!raw && this.isJsonLikeText(responseText)) {
      try {
        raw = JSON.parse(responseText);
      } catch (_err) {
        raw = responseText;
      }
    }
    if (!raw) return "";
    return `
      <details class="portal-task__raw">
        <summary>${this.escapeHtml(this.t("Developer details"))}</summary>
        <pre><code>${this.escapeHtml(this.safeJsonStringify(raw))}</code></pre>
      </details>
    `;
  }

  formatRequestStatusLabel(status) {
    const norm = (status || "").toString().trim().toLowerCase();
    if (!norm) return "Open";
    return norm
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  formatRequestSubtitle(request) {
    const from = request && request.fromAgent && request.fromAgent.name ? String(request.fromAgent.name).trim() : "";
    const to = request && request.toAgent && request.toAgent.name ? String(request.toAgent.name).trim() : "";
    let base = "Agent request";
    if (from && to) {
      base = `${from} → ${to}`;
    } else if (from) {
      base = `${from} → Agent`;
    } else if (to) {
      base = `Agent → ${to}`;
    }
    const whenRaw = request && (request.updatedAt || request.createdAt) ? String(request.updatedAt || request.createdAt) : "";
    const when = whenRaw ? Date.parse(whenRaw) : NaN;
    if (!Number.isNaN(when)) {
      return `${base} · ${this.formatRelativeTime(new Date(when))}`;
    }
    return base;
  }

  renderRunStatusPill(statusRaw) {
    const label = this.formatRunStatusLabel(statusRaw);
    const status = (statusRaw || "").toString().trim().toLowerCase();
    if (["completed", "resolved", "success", "succeeded"].includes(status)) {
      return `
        <span class="portal-task__status-pill portal-task__status-pill--icon" data-status="${this.escapeHtml(status)}" aria-label="Success">
          <span class="portal-task__status-pill-icon" aria-hidden="true">${this.getSuccessCircleIconMarkup()}</span>
        </span>
      `;
    }
    return `
      <span class="portal-task__status-pill" data-status="${this.escapeHtml(status)}">${this.escapeHtml(label)}</span>
    `;
  }

  renderRequestStatusPill(statusRaw) {
    const label = this.formatRequestStatusLabel(statusRaw);
    const status = (statusRaw || "").toString().trim().toLowerCase();
    if (["completed", "resolved", "success", "succeeded"].includes(status)) {
      return `
        <span class="portal-task__status-pill portal-task__status-pill--icon" data-status="${this.escapeHtml(status)}" aria-label="Success">
          <span class="portal-task__status-pill-icon" aria-hidden="true">${this.getSuccessCircleIconMarkup()}</span>
        </span>
      `;
    }
    return `
      <span class="portal-task__status-pill" data-status="${this.escapeHtml(status)}">${this.escapeHtml(label)}</span>
    `;
  }

  renderRequestCardHtml(requestId, state, request) {
    const statusRaw = (request && request.status ? request.status : "open").toString().trim().toLowerCase() || "open";
    const subject = request && request.subject ? request.subject : "Agent request";
    const subtitle = this.formatRequestSubtitle(request);
    const expanded = Boolean(state && state.expanded);

    const detailsHtml = this.renderRequestDetailsHtml(request);
    const actionsHtml = this.renderRequestActionsHtml(requestId, request);

    return `
      <div class="portal-task" data-request-id="${this.escapeHtml(requestId)}" data-expanded="${expanded ? "true" : "false"}">
        <button type="button" class="portal-task__header" data-request-toggle="${this.escapeHtml(requestId)}">
          <div class="portal-task__meta">
            <div class="portal-task__title-row">
              <div class="portal-task__title">${this.escapeHtml(String(subject || "Agent request"))}</div>
              ${this.renderRequestStatusPill(statusRaw)}
            </div>
            <div class="portal-task__subtitle">${this.escapeHtml(subtitle)}</div>
          </div>
        </button>
        <div class="portal-task__body">
          ${detailsHtml}
          ${actionsHtml}
        </div>
      </div>
    `;
  }

  renderRequestDetailsHtml(request) {
    const question = request && typeof request.question === "string" ? request.question.trim() : "";
    const resolution = request && typeof request.resolution === "string" ? request.resolution.trim() : "";
    const contextRefs = request && Array.isArray(request.contextRefs) ? request.contextRefs : [];

    const questionHtml = question
      ? `
        <div class="portal-task__section">
          <div class="portal-task__section-title">Question</div>
          <div class="portal-task__result">${this.escapeHtml(question)}</div>
        </div>
      `
      : "";

    const contextItems = contextRefs
      .slice(0, 10)
      .map((ref) => {
        if (!ref || typeof ref !== "object") return "";
        const type = ref.type || ref.kind || ref.ref_type || "";
        const id = ref.id || ref.message_id || ref.messageId || ref.run_id || ref.runId || ref.artifact_id || ref.artifactId || ref.url || "";
        const note = ref.note || ref.label || "";
        const parts = [];
        if (type) parts.push(String(type).trim());
        if (id) parts.push(String(id).trim());
        if (note) parts.push(String(note).trim());
        const text = parts.join(" · ");
        return text ? `<div class="portal-task__list-item">${this.escapeHtml(text)}</div>` : "";
      })
      .filter(Boolean)
      .join("");

    const contextHtml = contextItems
      ? `
        <div class="portal-task__section">
          <div class="portal-task__section-title">Context</div>
          <div class="portal-task__list">${contextItems}</div>
        </div>
      `
      : "";

    const resolutionHtml = resolution
      ? `
        <div class="portal-task__section">
          <div class="portal-task__section-title">Resolution</div>
          <div class="portal-task__result">${this.escapeHtml(resolution)}</div>
        </div>
      `
      : "";

    return `${questionHtml}${contextHtml}${resolutionHtml}`;
  }

  renderRequestActionsHtml(requestId, request) {
    const status = (request && request.status ? request.status : "").toString().trim().toLowerCase();
    if (status === "resolved") {
      return "";
    }

    const startBtn =
      status === "open"
        ? `
          <button type="button" class="portal-task__btn" data-request-set-status="${this.escapeHtml(
            requestId
          )}" data-request-status="in_progress">Mark in progress</button>
        `
        : "";

    return `
      <div class="portal-task__section">
        <div class="portal-task__section-title">Actions</div>
        <div class="portal-task__actions">
          ${startBtn}
        </div>
      </div>
      <div class="portal-task__section">
        <div class="portal-task__section-title">Resolve</div>
        <textarea class="portal-task__input" data-request-resolution-text rows="3" placeholder="Write a short reply…"></textarea>
        <div class="portal-task__actions">
          <button type="button" class="portal-task__btn portal-task__btn--approve" data-request-resolve-send="${this.escapeHtml(
            requestId
          )}">Resolve</button>
        </div>
      </div>
    `;
  }

  async submitAgentRequestUpdate(requestId, status, resolution, cardEl, textareaEl, buttonEl) {
    if (!requestId || !status) return;
    if (!this.endpoints.agentRequestUpdate) {
      this.showToast("Unavailable", "Agent request endpoint is not configured.", true);
      return;
    }
    if (!this.sessionToken) {
      this.showToast("Unavailable", "Session token missing.", true);
      return;
    }
    if (cardEl && cardEl.dataset.requestBusy === "true") {
      return;
    }
    if (cardEl) {
      cardEl.dataset.requestBusy = "true";
    }

    const buttons = cardEl ? cardEl.querySelectorAll("[data-request-set-status], [data-request-resolve-send]") : [];
    buttons.forEach((btn) => {
      btn.disabled = true;
    });
    if (buttonEl) {
      buttonEl.disabled = true;
    }
    if (textareaEl) {
      textareaEl.disabled = true;
    }

    try {
	      const response = await fetch(this.endpoints.agentRequestUpdate, {
	        method: "POST",
	        headers: this.jsonHeaders(),
	        body: JSON.stringify(
            this.getCurrentReferencePayload({
              request_id: requestId,
              status,
              resolution: resolution || undefined,
            }),
          ),
	      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message ? payload.error.message : "Update failed.";
        throw new Error(message);
      }
      if (payload && payload.request && typeof payload.request === "object") {
        this.upsertAgentRequest(payload.request);
        this.updateInboxOpenButton();
        this.scheduleInboxRender();
      }
      if (payload && payload.run && typeof payload.run === "object") {
        this.upsertAgentRun(payload.run);
        this.scheduleTasksRender();
        this.updateTasksOpenButton();
      }
      if (textareaEl && status.toString().toLowerCase() === "resolved") {
        textareaEl.value = "";
      }
      this.showToast("Saved", "Inbox updated.", false);
    } catch (error) {
      console.warn("Agent request update failed", error);
      this.showToast("Update failed", error.message || "Please try again.", true);
    } finally {
      if (cardEl) {
        cardEl.dataset.requestBusy = "false";
      }
      buttons.forEach((btn) => {
        btn.disabled = false;
      });
      if (buttonEl) {
        buttonEl.disabled = false;
      }
      if (textareaEl) {
        textareaEl.disabled = false;
      }
    }
  }

  async submitCsat({ score }) {
	    const response = await fetch(this.endpoints.csat, {
	      method: "POST",
	      headers: this.jsonHeaders(),
	      body: JSON.stringify(this.getCurrentReferencePayload({ score })),
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
      const escaped = normalized
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      return escaped.replace(/\n/g, "<br>");
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

  containsMarkdownTable(text) {
    if (!text) return false;
    const lines = text.split(/\r?\n/);
    if (lines.length < 2) return false;
    const dividerPattern = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;
    for (let i = 0; i < lines.length - 1; i += 1) {
      const header = lines[i];
      const divider = lines[i + 1];
      if (!header || !divider) continue;
      if (header.indexOf("|") === -1) continue;
      if (dividerPattern.test(divider)) return true;
    }
    return false;
  }

  containsMarkdownList(text) {
    if (!text) return false;
    return /^\s{0,3}(?:[-*+]\s+\S|\d{1,3}[.)]\s+\S)/m.test((text || "").toString());
  }

  shouldRenderInlineContentAsMarkdown(text) {
    const raw = (text || "").toString();
    if (!raw) return false;
    if (this.containsMarkdownTable(raw)) return true;
    if (this.containsMarkdownList(raw)) return true;
    if (!/\b\d{1,3}[.)]\s+\S/.test(raw) && raw.indexOf("|") === -1) {
      return false;
    }
    const normalizedInlineLists = this.normalizeInlineOrderedListsForDisplay(raw);
    if (normalizedInlineLists !== raw) return true;
    const normalizedWithoutPipeArtifacts = this.stripStandaloneMarkdownPipeArtifacts(normalizedInlineLists);
    return normalizedWithoutPipeArtifacts !== normalizedInlineLists;
  }

  applyMarkdownTableStyles(rootEl) {
    if (!rootEl || !rootEl.querySelectorAll) return;
    const tables = rootEl.querySelectorAll("table");
    if (!tables.length) return;
    tables.forEach((table) => {
      if (!table || !table.parentNode) return;
      if (!table.dataset) table.dataset = {};
      if (table.dataset.styledTable === "true") return;
      table.dataset.styledTable = "true";
      table.className = "w-full border-collapse text-sm";

      const parent = table.parentNode;
      if (!parent.dataset || parent.dataset.markdownTableWrapper !== "true") {
        const wrapper = document.createElement("div");
        wrapper.dataset.markdownTableWrapper = "true";
        wrapper.className = "rounded-xl border border-border/60 overflow-hidden bg-background/80 shadow-sm";
        parent.insertBefore(wrapper, table);
        wrapper.appendChild(table);
      }

      const thead = table.querySelector("thead");
      if (thead) {
        thead.classList.add("bg-muted/40", "text-muted-foreground");
      }
      const headerCells = table.querySelectorAll("th");
      headerCells.forEach((th) => {
        th.classList.add("px-3", "py-2", "text-left", "font-medium");
      });
      const rows = table.querySelectorAll("tbody tr");
      rows.forEach((row, idx) => {
        row.classList.add(idx % 2 === 0 ? "bg-background" : "bg-muted/20");
      });
      const cells = table.querySelectorAll("td");
      cells.forEach((td) => {
        td.classList.add("px-3", "py-2", "align-top", "text-foreground/90");
      });
    });
  }

  renderInlineMarkdown(text) {
    const raw = (text || "").toString();
    if (!raw) return "";
    const normalized = this.normalizeMarkdownForDisplay(raw);
    if (typeof marked === "undefined") {
      return this.renderPlainText(normalized);
    }
    let html = "";
    try {
      if (typeof marked.parseInline === "function") {
        html = marked.parseInline(normalized);
      } else {
        // Older marked versions: parse() wraps in <p>. Best-effort unwrap for inline contexts.
        html = marked.parse(normalized);
        const trimmed = (html || "").trim();
        const match = trimmed.match(/^<p>([\s\S]*)<\/p>\s*$/i);
        if (match && match[1] != null) {
          html = match[1];
        }
      }
    } catch (_err) {
      return this.renderPlainText(normalized);
    }
    if (typeof DOMPurify !== "undefined") {
      // Inline-only allowlist to avoid layout-breaking tags inside table cells.
      return DOMPurify.sanitize(html, {
        ALLOWED_TAGS: ["strong", "em", "code", "a", "br", "span", "del", "kbd"],
        ALLOWED_ATTR: ["href", "target", "rel"],
        ADD_ATTR: ["target"],
        FORBID_ATTR: ["style"],
      });
    }
    return html;
  }

  normalizeMarkdownForDisplay(text) {
    if (!text) return "";
    text = this.normalizeInlineOrderedListsForDisplay(text);
    text = this.stripStandaloneMarkdownPipeArtifacts(text);

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

  normalizeInlineOrderedListsForDisplay(text) {
    const raw = (text || "").toString();
    if (!raw) return "";
    if (!/\b\d{1,3}[.)]\s+\S/.test(raw)) return raw;

    const lines = raw.split("\n");
    const out = [];
    let inFence = false;

    for (const line of lines) {
      const trimmedStart = (line || "").trimStart();
      if (trimmedStart.startsWith("```")) {
        inFence = !inFence;
        out.push(line);
        continue;
      }
      if (inFence) {
        out.push(line);
        continue;
      }

      // Check if the line is already a list item (numbered or bullet).
      const isNumberedItem = /^\s*\d{1,3}[.)]\s+\S/.test(line);
      const isBulletItem = /^\s*[-*+]\s+\S/.test(line);
      if (isNumberedItem || isBulletItem) {
        // Still scan for embedded inline numbered items within this list item.
        const splitResult = this._splitEmbeddedListInItem(line);
        out.push(...splitResult);
        continue;
      }

      const matches = Array.from(line.matchAll(/\b(\d{1,3})[.)]\s+/g));
      if (matches.length < 2) {
        out.push(line);
        continue;
      }

      const firstIndex = Number.isFinite(matches[0].index) ? matches[0].index : -1;
      if (firstIndex < 0) {
        out.push(line);
        continue;
      }

      const intro = line.slice(0, firstIndex).replace(/\s*\|+\s*$/, "").trimEnd();
      const items = [];
      for (let idx = 0; idx < matches.length; idx += 1) {
        const start = Number.isFinite(matches[idx].index) ? matches[idx].index : -1;
        if (start < 0) continue;
        const end =
          idx + 1 < matches.length && Number.isFinite(matches[idx + 1].index)
            ? matches[idx + 1].index
            : line.length;
        const marker = (matches[idx][1] || "").toString().trim();
        const prefixLen = (matches[idx][0] || "").length;
        let content = line.slice(start + prefixLen, end).trim();
        content = content.replace(/^\|+\s*/, "");
        content = content.replace(/\s*\|+\s*$/, "");
        content = content.replace(/\s+\|\s+/g, " ");
        if (!marker || !content) continue;
        items.push(`${marker}. ${content}`);
      }

      if (items.length < 2) {
        out.push(line);
        continue;
      }

      if (intro) {
        out.push(intro, "", ...items);
      } else {
        out.push(...items);
      }
    }

    return out.join("\n");
  }

  /**
   * Split a list-prefixed line that contains embedded inline numbered items.
   * Mirrors the Python `_fix_embedded_list_in_item` helper.
   */
  _splitEmbeddedListInItem(line) {
    const stripped = (line || "").trimStart();
    const leadingWs = line.slice(0, line.length - stripped.length);

    let prefix = "";
    let textBody = "";
    const bulletM = stripped.match(/^([-*+])\s+/);
    const orderedM = stripped.match(/^(\d+)\.\s+/);
    if (bulletM) {
      prefix = stripped.slice(0, bulletM[0].length);
      textBody = stripped.slice(bulletM[0].length);
    } else if (orderedM) {
      prefix = stripped.slice(0, orderedM[0].length);
      textBody = stripped.slice(orderedM[0].length);
    } else {
      return [line];
    }

    if (!textBody) return [line];

    const embedded = Array.from(textBody.matchAll(/(\d+)\.\s+/g));
    if (!embedded.length) return [line];

    let firstEmb = null;
    if (embedded.length >= 2) {
      for (const m of embedded) {
        const before = textBody.slice(0, m.index).trimEnd();
        if (before.length >= 6) { firstEmb = m; break; }
      }
      if (!firstEmb) return [line];
    } else {
      const m = embedded[0];
      const before = textBody.slice(0, m.index).trimEnd();
      if (before.length < 10) return [line];
      firstEmb = m;
    }

    const trimmedText = textBody.slice(0, firstEmb.index).trimEnd();
    const result = [`${leadingWs}${prefix}${trimmedText}`];

    const rest = textBody.slice(firstEmb.index);
    const parts = rest.split(/(\d+)\.\s+/);
    for (let i = 1; i < parts.length; i += 2) {
      const num = parts[i];
      const content = (parts[i + 1] || "").trim();
      if (content) result.push(`${num}. ${content}`);
    }

    return result.length > 1 ? result : [line];
  }

  stripStandaloneMarkdownPipeArtifacts(text) {
    const raw = (text || "").toString();
    if (!raw) return "";

    const lines = raw.split("\n");
    const isDividerLine = (value) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(value || "");
    const isPipeNoiseLine = (value) => {
      const trimmed = (value || "").trim();
      return trimmed === "|" || trimmed === "|:" || trimmed === ":|" || trimmed === "||" || trimmed === "||:";
    };
    const hasPipe = (value) => /\|/.test(value || "");

    const out = [];
    let inFence = false;
    for (let idx = 0; idx < lines.length; idx += 1) {
      let line = lines[idx];
      const trimmedStart = (line || "").trimStart();
      if (trimmedStart.startsWith("```")) {
        inFence = !inFence;
        out.push(line);
        continue;
      }
      if (inFence) {
        out.push(line);
        continue;
      }

      const trimmed = (line || "").trim();
      if (!trimmed) {
        out.push(line);
        continue;
      }

      if (isPipeNoiseLine(trimmed)) {
        continue;
      }

      if (isDividerLine(trimmed)) {
        let prev = idx - 1;
        while (prev >= 0 && !(lines[prev] || "").trim()) prev -= 1;
        let next = idx + 1;
        while (next < lines.length && !(lines[next] || "").trim()) next += 1;
        const prevHasPipe = prev >= 0 && hasPipe(lines[prev]);
        const nextHasPipe = next < lines.length && hasPipe(lines[next]);
        if (!(prevHasPipe && nextHasPipe)) {
          continue;
        }
      }

      const pipeCount = ((line || "").match(/\|/g) || []).length;
      if (pipeCount === 1) {
        const startsWithPipe = /^\s*\|/.test(line || "");
        const endsWithPipe = /\|\s*$/.test(line || "");
        if (startsWithPipe || endsWithPipe) {
          let prev = idx - 1;
          while (prev >= 0 && !(lines[prev] || "").trim()) prev -= 1;
          let next = idx + 1;
          while (next < lines.length && !(lines[next] || "").trim()) next += 1;
          const prevHasPipe = prev >= 0 && hasPipe(lines[prev]);
          const nextHasPipe = next < lines.length && hasPipe(lines[next]);
          const tableContext = prevHasPipe && nextHasPipe;
          if (!tableContext) {
            line = (line || "").replace(/^\s*\|\s*/, "");
            line = line.replace(/\s*\|\s*$/, "");
            if (!line.trim()) continue;
          }
        }
      }

      out.push(line);
    }

    return out.join("\n");
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
        type: "text",
        created_at: new Date().toISOString(),
        payload: { text: cleaned },
      },
    ];
  }

  maybeCoerceCallSummaryBlocks(message, blocks) {
    const meta = message && message.metadata && typeof message.metadata === "object" ? message.metadata : {};
    const metaType = (meta.type || "").toString().trim().toLowerCase();
    if (metaType !== "call_summary") return blocks;

    const hasCallBlock = Array.isArray(blocks) && blocks.some((b) => (b && (b.type || "").toString().trim().toLowerCase() === "call_summary"));
    if (hasCallBlock) return blocks;

    const callSessionId = (meta.call_session_id || meta.callSessionId || "").toString().trim();
    const contactName = (meta.contact_name || meta.contactName || "").toString().trim();
    const topic = (meta.topic || "").toString().trim();
    const durationSeconds = Number(meta.duration_seconds || meta.durationSeconds || 0);
    const toPhone = (meta.to_phone_number || meta.toPhoneNumber || "").toString().trim();
    const language = (meta.language || "").toString().trim().toLowerCase();

    const rawBody = typeof message.body === "string" ? message.body : message.body == null ? "" : String(message.body);
    let summaryText = rawBody;
    const summaryIdx = rawBody.toLowerCase().indexOf("summary:");
    if (summaryIdx !== -1) {
      summaryText = rawBody.slice(summaryIdx + "summary:".length).trim();
    }
    summaryText = summaryText.trim();
    if (summaryText.startsWith("```")) {
      const match = summaryText.match(/^```[a-z0-9_-]*\\n([\\s\\S]*?)\\n```$/i);
      if (match && match[1]) summaryText = match[1].trim();
    }
    if (summaryText.startsWith("{") && summaryText.endsWith("}")) {
      try {
        const parsed = JSON.parse(summaryText);
        if (parsed && typeof parsed === "object" && typeof parsed.response_text === "string" && parsed.response_text.trim()) {
          summaryText = parsed.response_text.trim();
        }
      } catch (_err) {
        // ignore
      }
    }

    return [
      {
        block_id: callSessionId ? `call_summary_${callSessionId}` : `call_summary_${Math.random().toString(16).slice(2)}`,
        type: "call_summary",
        created_at: new Date().toISOString(),
        payload: {
          call_session_id: callSessionId,
          contact_name: contactName,
          to_phone_number: toPhone,
          topic,
          duration_seconds: Number.isFinite(durationSeconds) ? durationSeconds : 0,
          summary: summaryText,
          language,
        },
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
    const normalizedBlocks = Array.isArray(blocks) ? blocks : [];
    this.renderContentBlocksInto(blocksRoot, normalizedBlocks);
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
        const container = this.resolveBlockContainer(parentEl);
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

    const visibilityRoot = containerEl.closest ? containerEl.closest("[data-message-id]") || containerEl : containerEl;
    this.updateInlineToolCardsVisibility(visibilityRoot);
  }

  resolveBlockContainer(blockEl) {
    if (!blockEl || blockEl.nodeType !== Node.ELEMENT_NODE) return null;
    if (blockEl.dataset && blockEl.dataset.blockContainer === "true") {
      return blockEl;
    }
    const childContainer = blockEl.querySelector("[data-block-container]");
    return childContainer || blockEl;
  }

  reconcileMessageContentBlocks(messageBodyEl, blocks) {
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
    const normalizedBlocks = Array.isArray(blocks) ? blocks : [];
    this.reconcileContentBlocksInto(blocksRoot, normalizedBlocks);
  }

  decodeHtmlEntities(value) {
    if (!value) return "";
    const tempDiv = document.createElement("div");
    tempDiv.innerHTML = value;
    return tempDiv.textContent || tempDiv.innerText || "";
  }

  finalizeEmailStreams(rootEl) {
    if (!rootEl || !rootEl.querySelectorAll) return;
    const cards = rootEl.querySelectorAll('[data-email-card="true"]');
    if (!cards.length) return;

    cards.forEach((card) => {
      card.dataset.emailStreamCancelled = "true";
      if (Array.isArray(card._emailStreamTimers) && card._emailStreamTimers.length) {
        card._emailStreamTimers.forEach((timer) => clearTimeout(timer));
        card._emailStreamTimers = [];
      }

      const fields = card.querySelectorAll(".email-field");
      fields.forEach((field) => field.classList.add("email-field-visible"));

      const values = card.querySelectorAll(".email-field-value");
      values.forEach((valueEl) => {
        if (valueEl._streamInterval) {
          clearInterval(valueEl._streamInterval);
          valueEl._streamInterval = null;
        }
        const fullText = valueEl.dataset ? valueEl.dataset.fullText : "";
        if (fullText) {
          valueEl.textContent = this.decodeHtmlEntities(fullText);
        }
        valueEl.classList.add("email-stream-complete");
      });
    });
  }

  updateContentBlockElement(el, block, options = {}) {
    if (!el || !block || typeof block !== "object") return el;
    const streaming = Boolean(options && options.streaming);
    const type = (block.type || "").toString().trim().toLowerCase();
    const payload = block.payload && typeof block.payload === "object" ? block.payload : {};

    // Tool cards (including email cards).
    if (type === "tool_use") {
      this.updateToolEventCard(el, payload);
      return el;
    }
    if (type === "tool_result") {
      this.updateToolEventCard(el, { ...payload, phase: "finished" });
      return el;
    }
    if (type === "paragraph" || type === "heading" || type === "list_item") {
      const content = Array.isArray(payload.content) ? payload.content : [];
      const rawText = this.inlineNodesToText(content);
      const shouldRenderMarkdown =
        type !== "list_item" &&
        (streaming ? this.shouldRenderStreamingInlineContentAsMarkdown(rawText) : this.shouldRenderInlineContentAsMarkdown(rawText));
      const level = Number(payload.level) || 3;
      const expectedTag = type === "heading" ? (level <= 1 ? "H1" : level === 2 ? "H2" : "H3") : type === "list_item" ? "LI" : "P";
      const isMarkdownWrapper =
        Boolean(el && el.dataset && (el.dataset.markdownRichText === "true" || el.dataset.markdownTable === "true")) || el.tagName !== expectedTag;
      if (shouldRenderMarkdown || isMarkdownWrapper) {
        if (el.tagName === "DIV" && el.dataset && el.dataset.markdownRichText === "true") {
          el.className = "leading-relaxed space-y-2";
          el.dataset.contentBlock = "true";
          el.dataset.blockType = type;
          el.dataset.contentBlockText = "true";
          el.dataset.markdownRichText = "true";
          const rendered = this.renderMarkdown(rawText);
          if (el.innerHTML !== rendered) {
            el.innerHTML = rendered;
            this.applyMarkdownTableStyles(el);
          }
          return el;
        }
        const replacement = this.buildContentBlockElement(block, { streaming });
        if (replacement) {
          el.replaceWith(replacement);
          return replacement;
        }
        return el;
      }
      el.innerHTML = "";
      this.appendInlineNodes(el, content);
      if (type === "list_item") {
        this.syncListItemBulletVisibility(el);
      }
      return el;
    }

    if (type === "text") {
      const text = typeof payload.text === "string" ? payload.text : "";
      const cleaned = this.stripInlineResponseBlocks(text);
      el.className = "leading-relaxed space-y-2";
      if (el.dataset) {
        el.dataset.contentBlock = "true";
        el.dataset.blockType = "text";
        el.dataset.contentBlockText = "true";
        el.dataset.markdownRichText = "true";
      }
      if (cleaned) {
        el.innerHTML = this.renderMarkdown(cleaned);
        this.applyMarkdownTableStyles(el);
      } else {
        el.innerHTML = "";
      }
      return el;
    }

    if (type === "code_block") {
      const codeEl = el.querySelector("[data-content-block-code]");
      if (codeEl) {
        codeEl.textContent = typeof payload.code === "string" ? payload.code : "";
      }
      return el;
    }

    if (type === "reasoning") {
      const details = el.querySelector("details");
      const codeEl = el.querySelector("[data-content-block-code]");
      if (codeEl) {
        const text = typeof payload.code === "string" ? payload.code : typeof payload.text === "string" ? payload.text : "";
        codeEl.textContent = text || "";
      }
      if (details) {
        const isComplete = Boolean(payload.completed_at || payload.completedAt || payload.completed);
        details.dataset.reasoningState = isComplete ? "complete" : "active";
        const label = details.querySelector("[data-reasoning-summary-label]");
        if (label) {
          label.textContent = isComplete ? "Thought" : "Thinking";
        }
        if (details.dataset.userOverride !== "true") {
          if (!isComplete) {
            details.open = true;
	          } else if (details.open) {
	            if (this.finalizingTurn) {
	              details.open = false;
	            } else {
	              this.animateReasoningAutoCollapse(details);
	            }
	          }
	        }
      }
      return el;
    }

    if (type === "list") {
      const ordered = payload.ordered === true;
      const desiredTag = ordered ? "OL" : "UL";
      if (el.tagName !== desiredTag) {
        const replacement = this.buildContentBlockElement(block);
        if (replacement) {
          el.replaceWith(replacement);
          return replacement;
        }
        return el;
      }
      el.className = `${ordered ? "list-decimal" : "list-disc"} pl-6 space-y-1`;
      const startValue = Number(payload.start);
      if (ordered && Number.isFinite(startValue) && startValue > 0) {
        el.start = startValue;
      } else if (el.tagName === "OL") {
        el.removeAttribute("start");
      }
      return el;
    }

    // Other block types are currently treated as static once created.
    return el;
  }

  reconcileContentBlocksInto(containerEl, blocks) {
    if (!containerEl) return;

    // Remove streaming-only affordances; persisted renders do not include them.
    containerEl.querySelectorAll("[data-streaming-status]").forEach((el) => el.remove());
    // Remove non-canonical loose children (legacy/plain-text fallbacks).
    Array.from(containerEl.childNodes || []).forEach((node) => {
      if (!node) return;
      if (node.nodeType === Node.TEXT_NODE) {
        if ((node.textContent || "").trim()) {
          node.remove();
          return;
        }
        node.remove();
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) {
        node.remove();
        return;
      }
      const el = node;
      const isStatus = Boolean(el.matches && el.matches("[data-streaming-status]"));
      const hasBlockId = Boolean(el.dataset && el.dataset.blockId);
      const isContentBlock = Boolean(el.dataset && el.dataset.contentBlock === "true");
      if (!isStatus && !hasBlockId && !isContentBlock) {
        el.remove();
      }
    });

    // If we are finalizing, ensure we don't leave email field timers/cursors running.
    if (this.finalizingTurn) {
      this.finalizeEmailStreams(containerEl);
    }

    const canonicalRaw = Array.isArray(blocks) ? blocks.filter((b) => b && typeof b === "object") : [];
    const canonical = canonicalRaw;
    if (
      canonical.some((block) => {
        const blockId = (block.block_id || block.blockId || "").toString().trim();
        return !blockId;
      })
    ) {
      this.renderContentBlocksInto(containerEl, canonical);
      const visibilityRoot = containerEl.closest ? containerEl.closest("[data-message-id]") || containerEl : containerEl;
      this.updateInlineToolCardsVisibility(visibilityRoot);
      return;
    }

    // Map existing blocks by block_id (remove duplicates proactively).
    const existingById = new Map();
    Array.from(containerEl.querySelectorAll("[data-block-id]")).forEach((node) => {
      const id = node && node.dataset ? (node.dataset.blockId || "").toString().trim() : "";
      if (!id) return;
      const prior = existingById.get(id);
      if (prior && prior !== node) {
        node.remove();
        return;
      }
      existingById.set(id, node);
    });

    const emailCardsByToolEvent = new Map();
    const emailCardsBySendEvent = new Map();
    containerEl.querySelectorAll('[data-email-card="true"]').forEach((card) => {
      const toolEventId = (card.dataset.toolEventId || "").toString().trim();
      const sendEventId = (card.dataset.emailSendEventId || "").toString().trim();
      if (toolEventId && !emailCardsByToolEvent.has(toolEventId)) emailCardsByToolEvent.set(toolEventId, card);
      if (sendEventId && !emailCardsBySendEvent.has(sendEventId)) emailCardsBySendEvent.set(sendEventId, card);
    });

    const keepIds = new Set();

    canonical.forEach((block) => {
      const blockId = (block.block_id || block.blockId || "").toString().trim();
      if (!blockId) return;
      const parentId = (block.parent_block_id || block.parentBlockId || "").toString().trim();
      const type = (block.type || "").toString().trim().toLowerCase();
      const payload = block.payload && typeof block.payload === "object" ? block.payload : {};

      // Deterministic merge: email_send_draft updates the existing draft card (no new UI block).
      if (type === "tool_use") {
        const toolName = (payload.tool_name || payload.toolName || "").toString().trim().toLowerCase();
        if (toolName === "email_send_draft") {
          const draftId = this.getEmailDraftIdFromToolPayload(payload);
          const existingDraftCard = draftId ? this.resolveEmailCardByDraftId(draftId) : null;
          if (existingDraftCard) {
            const stale = existingById.get(blockId);
            if (stale && stale !== existingDraftCard && stale.parentNode) {
              stale.remove();
            }
            this.updateEmailPreviewCard(existingDraftCard, payload);
            this.updateEmailPreviewSummary(existingDraftCard);
            return;
          }
        }
      }

      let el = existingById.get(blockId);

      // Recover email cards even if block_id drifted (e.g. legacy mutation during streaming).
      if (!el && (type === "tool_use" || type === "tool_result")) {
        const toolName = (payload.tool_name || payload.toolName || "").toString().trim().toLowerCase();
        if (toolName === "email_create_draft" || toolName === "email_send_draft") {
          const eventId = (payload.event_id || payload.eventId || "").toString().trim();
          el = emailCardsByToolEvent.get(eventId) || emailCardsBySendEvent.get(eventId) || null;
          if (el) {
            el.dataset.blockId = blockId;
            existingById.set(blockId, el);
          }
        }
      }

      if (el) {
        const currentType = el.dataset ? (el.dataset.blockType || "").toString().trim().toLowerCase() : "";
        if (currentType && currentType !== type && type !== "reasoning") {
          const replacement = this.buildContentBlockElement(block);
          if (replacement) {
            el.replaceWith(replacement);
            el = replacement;
            existingById.set(blockId, el);
          }
        } else {
          el = this.updateContentBlockElement(el, block) || el;
          existingById.set(blockId, el);
        }
      } else {
        el = this.buildContentBlockElement(block);
        if (!el) return;
        existingById.set(blockId, el);
      }

      const parentEl = parentId && existingById.has(parentId) ? existingById.get(parentId) : null;
      const targetContainer = parentEl ? this.resolveBlockContainer(parentEl) : containerEl;
      if (targetContainer && el && el.parentNode !== targetContainer) {
        targetContainer.appendChild(el);
      } else if (targetContainer && el) {
        // Ensure correct sibling order by re-appending in canonical sequence.
        targetContainer.appendChild(el);
      }

      keepIds.add(blockId);
    });

    // Second pass: ensure parent containers exist before final sibling ordering (handles rare
    // out-of-order parent/child blocks without tearing down the entire message).
    canonical.forEach((block) => {
      const blockId = (block.block_id || block.blockId || "").toString().trim();
      if (!blockId || !keepIds.has(blockId)) return;
      const parentId = (block.parent_block_id || block.parentBlockId || "").toString().trim();
      const el = existingById.get(blockId);
      if (!el) return;
      const parentEl = parentId && existingById.has(parentId) ? existingById.get(parentId) : null;
      const targetContainer = parentEl ? this.resolveBlockContainer(parentEl) : containerEl;
      if (targetContainer) {
        targetContainer.appendChild(el);
      }
    });

    // Remove blocks not present in canonical output. Finalized payload is authoritative.
    existingById.forEach((node, id) => {
      if (!keepIds.has(id) && node && node.parentNode) {
        node.remove();
      }
    });

    const visibilityRoot = containerEl.closest ? containerEl.closest("[data-message-id]") || containerEl : containerEl;
    this.updateInlineToolCardsVisibility(visibilityRoot);
  }

  buildContentBlockElement(block, options = {}) {
    if (!block || typeof block !== "object") return null;
    const streaming = Boolean(options && options.streaming);
    const type = (block.type || "").toString().trim().toLowerCase();
    const blockId = (block.block_id || block.blockId || "").toString().trim();
    const payload = block.payload && typeof block.payload === "object" ? block.payload : {};

    if (type === "call_summary") {
      const callSessionId = (payload.call_session_id || payload.callSessionId || "").toString().trim();
      const contactName = (payload.contact_name || payload.contactName || "").toString().trim();
      const topic = (payload.topic || "").toString().trim();
      const toPhone = (payload.to_phone_number || payload.toPhoneNumber || "").toString().trim();
      const durationSeconds = Number(payload.duration_seconds || payload.durationSeconds || 0);
      const durationLabel = Number.isFinite(durationSeconds) && durationSeconds > 0 ? this.formatDurationMs(durationSeconds * 1000) : "";
      const summaryText = typeof payload.summary === "string" ? payload.summary.trim() : "";
      const language = (payload.language || "").toString().trim().toLowerCase();
      const rtl = payload.rtl === true || language.startsWith("ar");

      const wrapper = document.createElement("details");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "call_summary";
      if (blockId) wrapper.dataset.blockId = blockId;
      if (callSessionId) wrapper.dataset.callSessionId = callSessionId;
      wrapper.className = "portal-call-summary";
      if (rtl) {
        wrapper.dir = "rtl";
      }

      const summary = document.createElement("summary");
      summary.className = "portal-call-summary__summary";

      const left = document.createElement("div");
      left.className = "portal-call-summary__left";

      const icon = document.createElement("span");
      icon.className = "portal-call-summary__icon";
      icon.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M224.2 89C216.3 70.1 195.7 60.1 176.1 65.4L170.6 66.9C106 84.5 50.8 147.1 66.9 223.3C104 398.3 241.7 536 416.7 573.1C493 589.3 555.5 534 573.1 469.4L574.6 463.9C580 444.2 569.9 423.6 551.1 415.8L453.8 375.3C437.3 368.4 418.2 373.2 406.8 387.1L368.2 434.3C297.9 399.4 241.3 341 208.8 269.3L253 233.3C266.9 222 271.6 202.9 264.8 186.3L224.2 89z"/></svg>`;

      const meta = document.createElement("div");
      meta.className = "portal-call-summary__meta";

      const titleRow = document.createElement("div");
      titleRow.className = "portal-call-summary__title";
      const nameText = contactName || toPhone || this.t("Phone call");
      titleRow.textContent = nameText;

      const subtitleRow = document.createElement("div");
      subtitleRow.className = "portal-call-summary__subtitle";
      const subtitleParts = [];
      if (topic) subtitleParts.push(topic);
      if (toPhone && contactName) subtitleParts.push(toPhone);
      subtitleRow.textContent = subtitleParts.join(" · ");

      meta.appendChild(titleRow);
      if (subtitleRow.textContent) meta.appendChild(subtitleRow);

      left.appendChild(icon);
      left.appendChild(meta);

      const right = document.createElement("div");
      right.className = "portal-call-summary__right";
      if (durationLabel) {
        const dur = document.createElement("span");
        dur.className = "portal-call-summary__duration";
        dur.textContent = durationLabel;
        right.appendChild(dur);
      }

      summary.appendChild(left);
      summary.appendChild(right);

      const drawer = document.createElement("div");
      drawer.className = "portal-call-summary__drawer";
      const body = document.createElement("div");
      body.className = "portal-call-summary__body";
      body.innerHTML = this.renderMarkdown(summaryText || "");
      drawer.appendChild(body);

      wrapper.appendChild(summary);
      wrapper.appendChild(drawer);
      return wrapper;
    }

		    if (type === "paragraph" || type === "heading" || type === "list_item") {
		      let wrapper = null;
	      if (type === "heading") {
	        const level = Number(payload.level) || 3;
	        const tag = level <= 1 ? "h1" : level === 2 ? "h2" : "h3";
	        wrapper = document.createElement(tag);
	        wrapper.className =
	          level <= 1 ? "text-base font-bold" : level === 2 ? "text-base font-semibold" : "text-sm font-semibold";
	      } else if (type === "list_item") {
	        wrapper = document.createElement("li");
	        wrapper.className = "leading-relaxed";
	        wrapper.dataset.blockContainer = "true";
	      } else {
	        wrapper = document.createElement("p");
	        wrapper.className = "leading-relaxed";
	      }
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = type;
		      wrapper.dataset.contentBlockText = "true";
		      if (blockId) wrapper.dataset.blockId = blockId;
		      const content = Array.isArray(payload.content) ? payload.content : [];
		      const rawText = this.inlineNodesToText(content);
		      const shouldRenderMarkdown =
            type !== "list_item" &&
            (streaming ? this.shouldRenderStreamingInlineContentAsMarkdown(rawText) : this.shouldRenderInlineContentAsMarkdown(rawText));
		      if (shouldRenderMarkdown) {
	        const markdownWrapper = document.createElement("div");
	        markdownWrapper.className = "leading-relaxed space-y-2";
	        markdownWrapper.dataset.contentBlock = "true";
	        markdownWrapper.dataset.blockType = type;
	        markdownWrapper.dataset.contentBlockText = "true";
	        markdownWrapper.dataset.markdownRichText = "true";
	        if (blockId) markdownWrapper.dataset.blockId = blockId;
	        markdownWrapper.innerHTML = this.renderMarkdown(rawText);
	        this.applyMarkdownTableStyles(markdownWrapper);
        return markdownWrapper;
      }
	      this.appendInlineNodes(wrapper, content);
      if (type === "list_item") {
        this.syncListItemBulletVisibility(wrapper);
      }
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

	    if (type === "reasoning") {
	      const collapsed = payload.collapsed !== false;
	      const text = typeof payload.code === "string" ? payload.code : typeof payload.text === "string" ? payload.text : "";
	      const isComplete = Boolean(payload.completed_at || payload.completedAt || payload.completed);

      const wrapper = document.createElement("div");
      wrapper.dataset.contentBlock = "true";
      wrapper.dataset.blockType = "reasoning";
      if (blockId) wrapper.dataset.blockId = blockId;
      wrapper.className = "portal-reasoning";

	      const details = document.createElement("details");
	      details.className = "portal-reasoning__details";
	      details.dataset.reasoningState = isComplete ? "complete" : "active";
	      // Default UX: auto-open while the model is actively thinking, then collapse once complete.
	      if (!isComplete) {
	        details.open = true;
	      } else if (!collapsed) {
	        details.open = true;
	      }

      const summary = document.createElement("summary");
      summary.className = "portal-reasoning__summary";
      const summaryLabel = document.createElement("span");
      summaryLabel.dataset.reasoningSummaryLabel = "true";
      summary.appendChild(summaryLabel);

      const pre = document.createElement("pre");
      pre.className = "portal-reasoning__content";
      const code = document.createElement("code");
      code.dataset.contentBlockCode = "true";
      code.textContent = text || "";
      pre.appendChild(code);

      const updateSummary = () => {
        summaryLabel.textContent = details.dataset.reasoningState === "complete" ? "Thought" : "Thinking";
      };
      updateSummary();
      details.addEventListener("toggle", (event) => {
        if (event && event.isTrusted) {
          details.dataset.userOverride = "true";
        }
        updateSummary();
      });

      details.appendChild(summary);
      details.appendChild(pre);
      wrapper.appendChild(details);
      return wrapper;
    }

	    if (type === "text") {
	      const text = typeof payload.text === "string" ? payload.text : "";
	      const cleaned = this.stripInlineResponseBlocks(text);
	      const wrapper = document.createElement("div");
	      wrapper.className = "leading-relaxed space-y-2";
	      wrapper.dataset.contentBlock = "true";
	      wrapper.dataset.blockType = "text";
	      wrapper.dataset.contentBlockText = "true";
	      if (blockId) wrapper.dataset.blockId = blockId;
	      if (cleaned) {
	        wrapper.innerHTML = this.renderMarkdown(cleaned);
	        this.applyMarkdownTableStyles(wrapper);
	      }
	      return wrapper;
	    }

    if (type === "tool_use") {
      const normalizedTool = (payload.tool_name || payload.toolName || "").toString().trim().toLowerCase();
      if (normalizedTool === "email_send_draft") {
        const draftId = this.getEmailDraftIdFromToolPayload(payload);
        const existingDraftCard = draftId ? this.resolveEmailCardByDraftId(draftId) : null;
        if (existingDraftCard) {
          this.updateEmailPreviewCard(existingDraftCard, payload);
          return null;
        }
      }
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
      const status = (payload.status || "").toString().trim().toLowerCase();

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

      // Determine file type for branded icon
      let fileType = "default";
      const fn = filename.toLowerCase();
      if (contentType.includes("pdf") || fn.endsWith(".pdf")) fileType = "pdf";
      else if (contentType.includes("word") || fn.endsWith(".doc") || fn.endsWith(".docx")) fileType = "word";
      else if (contentType.includes("excel") || contentType.includes("spreadsheet") || fn.endsWith(".xls") || fn.endsWith(".xlsx") || fn.endsWith(".csv")) fileType = "excel";
      else if (contentType.includes("powerpoint") || contentType.includes("presentation") || fn.endsWith(".ppt") || fn.endsWith(".pptx")) fileType = "ppt";

      const icon = document.createElement("div");
      icon.className = "portal-file-card__icon";
      icon.innerHTML = this.getFileTypeIcon(fileType);

      const main = document.createElement("div");
      main.className = "portal-file-card__main";

      const nameEl = document.createElement("div");
      nameEl.className = "portal-file-card__filename";
      nameEl.textContent = filename;
      nameEl.title = filename;

      const meta = document.createElement("div");
      meta.className = "portal-file-card__meta";
      const parts = [];
      if (pageCount > 0) parts.push(`${pageCount} page${pageCount === 1 ? "" : "s"}`);
      if (sizeBytes > 0) parts.push(this.formatBytes(sizeBytes));
      meta.textContent = parts.length ? parts.join(" • ") : "Document";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "portal-file-card__download";
      if (status === "ready") {
        button.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>`;
        button.ariaLabel = "Download file";
      } else if (status === "failed") {
        button.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" class="text-destructive/80" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
        button.ariaLabel = "Download unavailable";
        button.title = "Download unavailable";
      } else {
         // Preparing
        button.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="animate-spin text-muted-foreground"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>`;
        button.ariaLabel = "Preparing file...";
      }
      button.disabled = status !== "ready" || !fileId;
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!fileId) return;
        void this.downloadConversationFile(fileId, filename);
      });

      main.appendChild(nameEl);
      main.appendChild(meta);
      wrapper.appendChild(icon);
      wrapper.appendChild(main);
      wrapper.appendChild(button);
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
      const rows = this.tableRowsReadyForRender(payload, { streaming });
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
          td.innerHTML = this.renderInlineMarkdown(typeof cellValue === "string" ? cellValue : cellValue == null ? "" : String(cellValue));
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
        td.innerHTML = this.renderInlineMarkdown(value);

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

      let blocks = this.coerceContentBlocks(message.contentBlocks, message.body);
      blocks = this.maybeCoerceCallSummaryBlocks(message, blocks);
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

	  setFollowScrollEnabled(enabled) {
	    const scroller = this.elements.messages;
	    if (!scroller) return;
	    const next = Boolean(enabled);
	    if (this.followScrollEnabled === next) return;
	    this.followScrollEnabled = next;
	    scroller.classList.toggle("portal-follow-scroll", next);
	  }

	  scheduleScrollToBottom({ behavior = "auto", force = false } = {}) {
	    const scroller = this.elements.messages;
	    if (!scroller) return;
    const nearBottom = this.isNearBottom(scroller);
    if (!force && !nearBottom) {
      this.setFollowScrollEnabled(false);
      return;
    }
    this.setFollowScrollEnabled(this.isStreaming && nearBottom);
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

  syncListItemBulletVisibility(listItemEl) {
    if (!listItemEl || listItemEl.tagName !== "LI") return;
    const hasContent = this._normalizeComparableText(listItemEl.textContent || "").length > 0;
    if (hasContent) {
      if (listItemEl.dataset) {
        delete listItemEl.dataset.awaitingText;
      }
      listItemEl.style.listStyleType = "";
      return;
    }
    if (listItemEl.dataset) {
      listItemEl.dataset.awaitingText = "true";
    }
    listItemEl.style.listStyleType = "none";
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
        this.streamingMissingBlockWrapperCounts.clear();
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

	    // Scroll only if the visitor is already near the bottom (don't hijack up-thread reading).
	    this.scheduleScrollToBottom({ behavior: "smooth" });
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

  updateLatestCustomerMessageId(messageId) {
    if (!messageId || !this.elements.messages) return;
    const rows = Array.from(this.elements.messages.querySelectorAll(".message-row"));
    for (let idx = rows.length - 1; idx >= 0; idx -= 1) {
      const row = rows[idx];
      if (!row || !row.classList.contains("flex-row-reverse")) {
        continue;
      }
      row.dataset.messageId = messageId;
      const body = row.querySelector("[data-message-body]");
      if (body) {
        body.dataset.messageId = messageId;
      }
      break;
    }
  }

  getMessageBodyElement(messageId) {
    if (!messageId || !this.elements.messages) return null;
    const wrapper = this.elements.messages.querySelector(`[data-message-id="${messageId}"]`);
    if (!wrapper) return null;
    return wrapper.querySelector("[data-message-body]");
  }

  isAgentRunMetadata(metadata) {
    if (!metadata || typeof metadata !== "object") return false;
    const source = (metadata.source || "").toString().trim().toLowerCase();
    if (source === "agent_run") return true;
    if (metadata.agent_run_id || metadata.agentRunId || metadata.agent_run || metadata.agentRun) return true;
    return false;
  }

  getAgentRunId(metadata) {
    if (!metadata || typeof metadata !== "object") return "";
    const raw =
      (typeof metadata.agent_run_id === "string" && metadata.agent_run_id) ||
      (typeof metadata.agentRunId === "string" && metadata.agentRunId) ||
      (typeof metadata.agent_run === "string" && metadata.agent_run) ||
      (typeof metadata.agentRun === "string" && metadata.agentRun) ||
      "";
    return raw.toString().trim();
  }

  getAgentRunType(metadata) {
    if (!metadata || typeof metadata !== "object") return "";
    const raw = metadata.type || metadata.run_type || metadata.runType || "";
    return raw != null ? raw.toString().trim().toLowerCase() : "";
  }

  getMessageRenderPayload(messageId) {
    const safeId = (messageId || "").toString().trim();
    if (!safeId) return null;
    const scriptTag = document.getElementById(safeId);
    if (!scriptTag || scriptTag.tagName !== "SCRIPT") return null;
    try {
      const parsed = JSON.parse(scriptTag.textContent || "");
      if (!parsed || typeof parsed !== "object") return null;
      const blocks = Array.isArray(parsed.content_blocks)
        ? parsed.content_blocks
        : Array.isArray(parsed.contentBlocks)
        ? parsed.contentBlocks
        : [];
      const body = typeof parsed.body === "string" ? parsed.body : parsed.body == null ? "" : String(parsed.body);
      return { body, blocks };
    } catch (_err) {
      return null;
    }
  }

  normalizeToolName(toolName) {
    return (toolName || "").toString().trim().toLowerCase();
  }

  collectToolClassificationCandidates(payload) {
    const candidates = [];
    const pushCandidate = (value) => {
      if (value && typeof value === "object") {
        candidates.push(value);
      }
    };
    pushCandidate(payload);
    if (!payload || typeof payload !== "object") {
      return candidates;
    }
    pushCandidate(payload.output);
    pushCandidate(payload.output_summary);
    const llmResponse = payload.llm_response;
    if (llmResponse && typeof llmResponse === "object") {
      pushCandidate(llmResponse.content_json);
      const contentRaw = llmResponse.content;
      if (
        (!llmResponse.content_json || typeof llmResponse.content_json !== "object") &&
        typeof contentRaw === "string" &&
        contentRaw.trim()
      ) {
        try {
          const parsed = JSON.parse(contentRaw);
          pushCandidate(parsed);
        } catch (_err) {
          // Ignore malformed tool payload content.
        }
      }
    }
    return candidates;
  }

  isScopeAutoReadPayload(payload) {
    const candidates = this.collectToolClassificationCandidates(payload);
    for (const candidate of candidates) {
      if (!candidate || typeof candidate !== "object") continue;
      const diagnostics = candidate.diagnostics && typeof candidate.diagnostics === "object" ? candidate.diagnostics : null;
      const pathValue = (
        (diagnostics && diagnostics.path) ||
        candidate.path ||
        ""
      )
        .toString()
        .trim()
        .toLowerCase();
      if (pathValue === "scope_auto_read") return true;
      if (diagnostics && diagnostics.scope_auto_read_applied === true) return true;

      const prefetchedEvidence = Array.isArray(candidate.prefetched_evidence) ? candidate.prefetched_evidence : [];
      if (!prefetchedEvidence.length) continue;

      const budget = candidate.budget && typeof candidate.budget === "object" ? candidate.budget : null;
      const searchesUsed = Number(budget && budget.searches_used);
      const readsUsed = Number(budget && budget.reads_used);
      if (Number.isFinite(searchesUsed) && Number.isFinite(readsUsed) && searchesUsed === 0 && readsUsed > 0) {
        return true;
      }
    }
    return false;
  }

  getEffectiveToolName(toolName, payload) {
    const normalized = this.normalizeToolName(toolName);
    if (!normalized) return "";
    if (normalized !== "search_knowledge") return normalized;
    return this.isScopeAutoReadPayload(payload) ? "read_knowledge" : normalized;
  }



  humanizeAgentToolName(toolName) {
    const raw = (toolName || "").toString().trim();
    if (!raw) return "";
    const normalized = raw.toLowerCase();
    const mapping = {
      email_send_draft: "send email",
      email_create_draft: "create email draft",
      email_get_message: "read email message",
      email_get_thread: "read email thread",
      search_knowledge: "search knowledge base",
      read_knowledge: "read documents",
      start_agent_run: "start background run",
      continue_agent_run: "continue background run",
    };
    if (Object.prototype.hasOwnProperty.call(mapping, normalized)) {
      return mapping[normalized];
    }
    return raw.replace(/[_-]+/g, " ").trim();
  }

  extractToolNameFromText(text) {
    const raw = (text || "").toString();
    if (!raw) return "";
    const match = raw.match(/\btool\s*:\s*([a-zA-Z0-9_-]{2,})/i);
    return match && match[1] ? match[1].trim() : "";
  }

  extractFirstQuestionFromText(text) {
    const raw = (text || "").toString();
    if (!raw) return "";
    const lines = raw
      .split(/\r?\n/)
      .map((line) => (line || "").trim())
      .filter(Boolean);
    const bullet = lines.find((line) => /^[-*]\s+/.test(line));
    if (bullet) return bullet.replace(/^[-*]\s+/, "").trim();
    return "";
  }

  extractFirstHeadingFromBlocks(blocks, fallbackText = "") {
    const items = Array.isArray(blocks) ? blocks : [];
    for (const block of items) {
      if (!block || typeof block !== "object") continue;
      const type = (block.type || "").toString().trim().toLowerCase();
      if (type !== "heading") continue;
      const payload = block.payload && typeof block.payload === "object" ? block.payload : {};
      const content = Array.isArray(payload.content) ? payload.content : [];
      const heading = this.inlineNodesToText(content).trim();
      if (heading) return heading;
    }
    const raw = (fallbackText || "").toString().trim();
    if (!raw) return "";
    const line = raw.split(/\r?\n/).map((l) => (l || "").trim()).find(Boolean) || "";
    return line;
  }

  inferAgentRunMilestoneSubtitle({ metaType, runStatus, cleanedText, cleanedBlocks, title } = {}) {
    const kind = (metaType || "").toString().trim().toLowerCase();
    const text = (cleanedText || "").toString().trim();
    const lower = text.toLowerCase();

    if (kind === "needs_approval") {
      const toolName = this.extractToolNameFromText(text);
      const action = this.humanizeAgentToolName(toolName);
      if (action) return `Awaiting approval to ${action}`;
      return "Awaiting approval to continue";
    }

    if (kind === "needs_user") {
      const question = this.extractFirstQuestionFromText(text);
      if (question) return `Needs input: ${this.clipText(question, 72)}`;
      return "Needs your input to continue";
    }

    if (["run_handoff", "run_result", "run_summary"].includes(kind)) {
      if (
        lower.includes("sent the email") ||
        lower.includes("email has been delivered") ||
        (lower.includes("successfully") && lower.includes("sent") && lower.includes("email"))
      ) {
        return "Email sent";
      }
      if (lower.includes("draft") && (lower.includes("created") || lower.includes("saved"))) {
        return "Email draft created";
      }
      if (lower.includes("unable to locate") || lower.includes("not found")) {
        return "Completed — no match found";
      }
      const heading = this.extractFirstHeadingFromBlocks(cleanedBlocks, text);
      if (heading) {
        const normalizedHeading = heading.toLowerCase();
        const normalizedTitle = (title || "").toString().trim().toLowerCase();
        if (!normalizedTitle || !normalizedHeading || normalizedHeading === normalizedTitle) {
          return "Completed";
        }
        return this.clipText(heading, 72);
      }
      return "Completed";
    }

    if (runStatus) {
      const status = runStatus.toString().trim().toLowerCase();
      if (["waiting_approval", "waiting-approval"].includes(status)) return "Awaiting approval";
      if (["waiting_user", "waiting-user"].includes(status)) return "Awaiting your input";
      if (["running"].includes(status)) return "Working…";
      if (["queued"].includes(status)) return "Queued";
      if (["failed", "error"].includes(status)) return "Failed";
      if (["cancelled", "canceled"].includes(status)) return "Cancelled";
      if (["completed", "succeeded", "success"].includes(status)) return "Completed";
    }

    return "";
  }

  getAgentRunApprovalOutcome(runState) {
    if (!runState || !Array.isArray(runState.events)) return "";
    for (let i = runState.events.length - 1; i >= 0; i -= 1) {
      const evt = runState.events[i];
      if (!evt) continue;
      const label = (evt.label || "").toString().trim().toLowerCase();
      if (label === "approved") return "Approved";
      if (label === "denied") return "Denied";
    }
    return "";
  }

  computeAgentRunSummary({ messageId, metadata }) {
    const meta = metadata && typeof metadata === "object" ? metadata : {};
    const runId = this.getAgentRunId(meta);
    const runState = runId ? this.agentRuns.get(runId) : null;
    const run = runState && runState.run ? runState.run : null;
    const runStatus = run && run.status ? run.status.toString().trim().toLowerCase() : "";
    const metaType = this.getAgentRunType(meta);
    const approvalOutcome = this.getAgentRunApprovalOutcome(runState);
    const approvalPreview =
      (meta && typeof meta.approval_preview === "object" && meta.approval_preview) ||
      (meta && typeof meta.approvalPreview === "object" && meta.approvalPreview) ||
      null;
    const approvalId =
      (meta && (meta.pending_approval_id || meta.pendingApprovalId || meta.approval_id || meta.approvalId)) ||
      (meta && meta.approval && typeof meta.approval === "object" ? meta.approval.id : "") ||
      "";

    const payload = this.getMessageRenderPayload(messageId);
    const rawBlocks = payload && Array.isArray(payload.blocks) ? payload.blocks : [];
    const cleanedBlocks = rawBlocks;
    const rawText =
      (payload && payload.blocks && payload.blocks.length
        ? this.extractPlainTextFromContentBlocks(payload.blocks)
        : "") ||
      (payload ? payload.body : "") ||
      "";

    const cleanedText = rawText;

    const title = (run && run.title ? String(run.title).trim() : "");

    const milestoneTypes = new Set(["needs_approval", "needs_user", "run_handoff", "run_result", "run_summary"]);
    const isMilestone = milestoneTypes.has(metaType);

    let pillLabel = "";
    let variant = "neutral";
    if (isMilestone) {
      if (metaType === "needs_approval") {
        if (approvalOutcome) {
          pillLabel = approvalOutcome;
          variant = approvalOutcome === "Approved" ? "success" : "danger";
        } else {
          pillLabel = "Approval Needed";
          variant = "approval";
        }
      } else if (metaType === "needs_user") {
        pillLabel = "Input Needed";
        variant = "input";
      } else if (["run_handoff", "run_result", "run_summary"].includes(metaType)) {
        pillLabel = "Success";
        variant = "success";
      }
    } else if (runStatus) {
      if (["waiting_approval", "waiting-approval"].includes(runStatus)) {
        pillLabel = "Approval Needed";
        variant = "approval";
      } else if (["waiting_user", "waiting-user"].includes(runStatus)) {
        pillLabel = "Input Needed";
        variant = "input";
      } else if (["running"].includes(runStatus)) {
        pillLabel = "Running";
        variant = "running";
      } else if (["queued"].includes(runStatus)) {
        pillLabel = "Queued";
        variant = "neutral";
      } else if (["failed", "error"].includes(runStatus)) {
        pillLabel = "Failed";
        variant = "danger";
      } else if (["cancelled", "canceled"].includes(runStatus)) {
        pillLabel = "Cancelled";
        variant = "muted";
      } else if (["completed", "succeeded", "success"].includes(runStatus)) {
        pillLabel = "Success";
        variant = "success";
      }
    } else if (metaType === "pending_tool_execution") {
      pillLabel = "Tool Executed";
      variant = "neutral";
    }

    let subtitle = this.inferAgentRunMilestoneSubtitle({
      metaType,
      runStatus,
      cleanedText,
      cleanedBlocks,
      title: title || "",
    });
    if (metaType === "needs_approval" && approvalOutcome) {
      subtitle = approvalOutcome === "Approved" ? "Approval granted" : "Approval denied";
    }

    return {
      runId,
      metaType,
      runStatus,
      pillLabel,
      variant,
      title: title || "Background task",
      subtitle,
      approvalPreview,
      approvalId: approvalId ? approvalId.toString().trim() : "",
    };
  }

  ensureAgentRunCollapsible(messageBodyEl, metadata, messageId) {
    if (!messageBodyEl) return null;
    const existing = messageBodyEl.querySelector("[data-agent-run-details]");
    if (existing) {
      this.updateAgentRunChip(existing, { messageId, metadata });
      return existing;
    }

    // Remove the copy-button padding that makes chips look misaligned.
    messageBodyEl.classList.remove("pr-8");

    const details = document.createElement("details");
    details.dataset.agentRunDetails = "true";
    details.className = "portal-agent-run";

    const summary = document.createElement("summary");
    summary.dataset.agentRunSummary = "true";
    summary.className = "portal-agent-run__summary";

    const pill = document.createElement("span");
    pill.dataset.agentRunPill = "true";
    pill.className = "portal-agent-run__pill";
    pill.setAttribute("hidden", "true");

    const title = document.createElement("span");
    title.dataset.agentRunTitle = "true";
    title.className = "portal-agent-run__title";

    const subtitle = document.createElement("span");
    subtitle.dataset.agentRunSubtitle = "true";
    subtitle.className = "portal-agent-run__subtitle";
    subtitle.setAttribute("hidden", "true");

    const headingRow = document.createElement("span");
    headingRow.dataset.agentRunHeadingRow = "true";
    headingRow.className = "portal-agent-run__heading-row";
    headingRow.appendChild(title);
    headingRow.appendChild(pill);

    const heading = document.createElement("span");
    heading.dataset.agentRunHeading = "true";
    heading.className = "portal-agent-run__heading";
    heading.appendChild(headingRow);
    heading.appendChild(subtitle);

    const sourceIcon = document.createElement("span");
    sourceIcon.dataset.agentRunSourceIcon = "true";
    sourceIcon.className = "portal-agent-run__source-icon";
    sourceIcon.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><g transform="rotate(90 8 8)"><circle cx="3" cy="3" r="2" stroke="currentColor" stroke-width="1.4"/><circle cx="13" cy="3" r="2" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="13" r="2" stroke="currentColor" stroke-width="1.4"/><path d="M3 5v2c0 1.1 0.9 2 2 2h3M13 5v2c0 1.1-0.9 2-2 2H8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></g></svg>`;

    const source = document.createElement("span");
    source.dataset.agentRunSource = "true";
    source.className = "portal-agent-run__source";
    source.innerHTML = `<span>Sub-agent</span>`;

    const sourceWrap = document.createElement("span");
    sourceWrap.dataset.agentRunSourceWrap = "true";
    sourceWrap.className = "portal-agent-run__source-wrap";
    sourceWrap.appendChild(sourceIcon);
    sourceWrap.appendChild(source);

    const chevron = document.createElement("span");
    chevron.dataset.agentRunChevron = "true";
    chevron.className = "portal-agent-run__chevron";
    chevron.innerHTML = `<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6 8l4 4 4-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const actions = document.createElement("span");
    actions.dataset.agentRunActions = "true";
    actions.className = "portal-agent-run__actions";
    actions.setAttribute("hidden", "true");

    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.className = "portal-agent-run__btn portal-agent-run__btn--approve";
    approveBtn.setAttribute("data-run-approval-action", "approve");
    approveBtn.textContent = "Approve";

    const denyBtn = document.createElement("button");
    denyBtn.type = "button";
    denyBtn.className = "portal-agent-run__btn portal-agent-run__btn--deny";
    denyBtn.setAttribute("data-run-approval-action", "deny");
    denyBtn.textContent = "Deny";

    const onApprovalClick = (decision) => (event) => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      const runId = (details.dataset.agentRunId || this.getAgentRunId(metadata) || "").toString().trim();
      const approvalId = (event && event.currentTarget ? event.currentTarget.getAttribute("data-approval-id") : "") || "";
      if (!runId) return;
      this.submitRunApproval(runId, approvalId, decision, actions);
    };
    approveBtn.addEventListener("click", onApprovalClick("approve"));
    denyBtn.addEventListener("click", onApprovalClick("deny"));

    actions.appendChild(approveBtn);
    actions.appendChild(denyBtn);

    summary.appendChild(sourceWrap);
    summary.appendChild(heading);
    summary.appendChild(actions);
    summary.appendChild(chevron);

    const drawer = document.createElement("div");
    drawer.dataset.agentRunDrawer = "true";
    drawer.className = "portal-agent-run__drawer";

    const content = document.createElement("div");
    content.dataset.agentRunContent = "true";
    content.className = "portal-agent-run__content";

    const existingChildren = Array.from(messageBodyEl.childNodes);
    existingChildren.forEach((node) => content.appendChild(node));
    if (!content.querySelector("[data-message-blocks]")) {
      const blocksRoot = document.createElement("div");
      blocksRoot.dataset.messageBlocks = "true";
      blocksRoot.className = "space-y-2";
      content.appendChild(blocksRoot);
    }

    drawer.appendChild(content);
    details.appendChild(summary);
    details.appendChild(drawer);

    // Smooth expansion animation
    summary.addEventListener("click", (e) => {
      e.preventDefault();
      if (details.open) {
        // Collapse
        const startHeight = drawer.offsetHeight;
        drawer.style.height = `${startHeight}px`;
        drawer.style.overflow = "hidden";
        
        requestAnimationFrame(() => {
            drawer.style.transition = "height 0.3s cubic-bezier(0.25, 1, 0.5, 1), opacity 0.2s cubic-bezier(0.25, 1, 0.5, 1)";
            drawer.style.height = "0px";
            drawer.style.opacity = "0";
            
            // Auto-revert scroll
            setTimeout(() => {
                summary.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 50);
        });
        
        drawer.addEventListener("transitionend", function onEnd(e) {
            if (e.propertyName !== "height") return;
            details.open = false;
            drawer.style.height = "";
            drawer.style.opacity = "";
            drawer.style.transition = "";
            drawer.style.overflow = "";
            drawer.removeEventListener("transitionend", onEnd);
        }); // Removed {once: true} to check propertyName safely
        
      } else {
        // Expand
        details.open = true;
        const targetHeight = drawer.scrollHeight;
        drawer.style.height = "0px";
        drawer.style.opacity = "0";
        drawer.style.overflow = "hidden";
        drawer.style.transition = "height 0.3s cubic-bezier(0.25, 1, 0.5, 1), opacity 0.3s cubic-bezier(0.25, 1, 0.5, 1)";
        
        requestAnimationFrame(() => {
            drawer.style.height = `${targetHeight}px`;
            drawer.style.opacity = "1";
            
            // Auto-scroll to view content
            setTimeout(() => {
               const firstBlock = drawer.querySelector('.portal-agent-run__content > :first-child') || drawer;
               firstBlock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }, 150);
        });
        
        drawer.addEventListener("transitionend", function onEnd(e) {
            if (e.propertyName !== "height") return;
            drawer.style.height = "";
            drawer.style.opacity = "";
            drawer.style.transition = "";
            drawer.style.overflow = "";
            drawer.removeEventListener("transitionend", onEnd);
        });
      }
    });

    messageBodyEl.innerHTML = "";
    messageBodyEl.appendChild(details);

    this.updateAgentRunChip(details, { messageId, metadata });
    return details;
  }

  updateAgentRunChip(detailsEl, { messageId, metadata } = {}) {
    if (!detailsEl) return;
    const id = (messageId || "").toString().trim() || this.resolveMessageIdForNode(detailsEl);
    const incomingMeta = metadata && typeof metadata === "object" ? metadata : {};
    const cachedMeta =
      detailsEl._agentRunMeta && typeof detailsEl._agentRunMeta === "object" ? detailsEl._agentRunMeta : null;
    const mergedMeta = cachedMeta ? Object.assign({}, cachedMeta, incomingMeta) : Object.assign({}, incomingMeta);
    detailsEl._agentRunMeta = mergedMeta;

    const summary = this.computeAgentRunSummary({ messageId: id, metadata: mergedMeta });

    detailsEl.dataset.agentRunId = summary.runId || "";
    detailsEl.dataset.agentRunType = summary.metaType || "";
    detailsEl.dataset.agentRunStatus = summary.runStatus || "";
    detailsEl.dataset.agentRunVariant = summary.variant || "neutral";

    const pillEl = detailsEl.querySelector("[data-agent-run-pill]");
    if (pillEl) {
      const rawLabel = summary.pillLabel ? summary.pillLabel.toString().trim() : "";
      const hideApprovalPill =
        summary && summary.metaType === "needs_approval" && summary.variant === "approval";
      const label = hideApprovalPill || rawLabel === "Approval Needed" ? "" : rawLabel;
      if (!label) {
        pillEl.dataset.iconOnly = "false";
        pillEl.textContent = "";
        pillEl.setAttribute("hidden", "true");
        pillEl.removeAttribute("aria-label");
        pillEl.removeAttribute("title");
      } else if (summary.variant === "success" && label === "Success") {
        pillEl.dataset.iconOnly = "true";
        pillEl.innerHTML = `<span class="portal-agent-run__pill-icon" aria-hidden="true">${this.getSuccessBadgeIconMarkup()}</span>`;
        pillEl.setAttribute("aria-label", "Success");
        pillEl.setAttribute("title", "Success");
        pillEl.removeAttribute("hidden");
      } else {
        pillEl.dataset.iconOnly = "false";
        pillEl.textContent = label;
        pillEl.removeAttribute("aria-label");
        pillEl.removeAttribute("title");
        pillEl.removeAttribute("hidden");
      }
    }
    const titleEl = detailsEl.querySelector("[data-agent-run-title]");
    if (titleEl) titleEl.textContent = summary.title;

    const subtitleEl = detailsEl.querySelector("[data-agent-run-subtitle]");
    if (subtitleEl) {
      const value = summary.subtitle ? summary.subtitle.toString().trim() : "";
      if (value) {
        subtitleEl.textContent = value;
        subtitleEl.removeAttribute("hidden");
      } else {
        subtitleEl.textContent = "";
        subtitleEl.setAttribute("hidden", "true");
      }
    }

    this.updateAgentRunApprovalPreview(detailsEl, summary);
    this.updateAgentRunApprovalActions(detailsEl, summary);
  }

  renderAgentRunApprovalPreview(preview) {
    if (!preview || typeof preview !== "object") return "";
    const title = preview.title ? this.escapeHtml(preview.title) : "Approval preview";
    const fields = Array.isArray(preview.fields) ? preview.fields : [];
    const body = preview.body ? this.escapeHtml(preview.body).replace(/\n/g, "<br>") : "";
    const rows = fields
      .map((field) => {
        if (!field || typeof field !== "object") return "";
        const label = field.label ? this.escapeHtml(field.label) : "";
        const value = field.value ? this.escapeHtml(field.value) : "";
        if (!label && !value) return "";
        return `<div class="portal-agent-run__preview-row"><span class="portal-agent-run__preview-label">${label}</span><span class="portal-agent-run__preview-value">${value}</span></div>`;
      })
      .join("");
    const bodyBlock = body ? `<div class="portal-agent-run__preview-body">${body}</div>` : "";
    return `
      <div class="portal-agent-run__preview-title">${title}</div>
      ${rows}
      ${bodyBlock}
    `;
  }

  buildAgentRunPhoneCallPreviewPayload(summary) {
    const preview =
      summary && summary.approvalPreview && typeof summary.approvalPreview === "object" ? summary.approvalPreview : null;
    if (!preview) return null;

    const runId = (summary && summary.runId ? summary.runId : "").toString().trim();
    const approvalId = (summary && summary.approvalId ? summary.approvalId : "").toString().trim();
    const pill = (summary && summary.pillLabel ? summary.pillLabel : "").toString().trim().toLowerCase();

    let approvalStatus = "pending";
    if (pill === "approved") approvalStatus = "approved";
    if (pill === "denied" || pill === "rejected") approvalStatus = "denied";

    const resolved = approvalStatus !== "pending";
    const phase = resolved ? "approval_resolved" : "approval_requested";
    const status = resolved ? approvalStatus : "pending_approval";

    const fields = Array.isArray(preview.fields) ? preview.fields : [];
    const findFieldValue = (labels) => {
      const wanted = Array.isArray(labels)
        ? labels
            .map((label) => (label == null ? "" : label.toString().trim().toLowerCase()))
            .filter(Boolean)
        : [];
      if (!wanted.length) return "";
      for (const field of fields) {
        if (!field || typeof field !== "object") continue;
        const label = (field.label || "").toString().trim().toLowerCase();
        if (!label || !wanted.includes(label)) continue;
        const value = (field.value || "").toString().trim();
        if (value) return value;
      }
      return "";
    };

    const phoneNumber = findFieldValue(["to", "phone", "phone number", "number"]);
    const objective = findFieldValue(["objective", "reason", "topic"]);
    const callType = findFieldValue(["type", "call type"]);
    const language = findFieldValue(["language", "lang"]);
    const maxDurationRaw = findFieldValue(["max duration", "duration", "max_duration"]);
    let maxDurationMinutes = null;
    if (maxDurationRaw) {
      const match = maxDurationRaw.match(/(\d+)/);
      if (match) {
        const parsed = Number(match[1]);
        if (Number.isFinite(parsed) && parsed > 0) maxDurationMinutes = parsed;
      }
    }

    const input = {};
    if (phoneNumber) input.phone_number = phoneNumber;
    if (objective) input.objective = objective;
    if (callType) input.call_type = callType;
    if (language) input.language = language;
    if (maxDurationMinutes != null) input.max_duration_minutes = maxDurationMinutes;

    const sanitizeIdSegment = (value) => {
      return (value || "")
        .toString()
        .trim()
        .replace(/[^a-z0-9_-]+/gi, "-")
        .replace(/-+/g, "-")
        .replace(/^[-_]+|[-_]+$/g, "");
    };
    const eventId = `run-${sanitizeIdSegment(runId) || "unknown"}-approval-${sanitizeIdSegment(approvalId) || "unknown"}`;

    return {
      event_id: eventId,
      tool_name: "initiate_phone_call",
      phase,
      status,
      approval_id: approvalId,
      approval: {
        id: approvalId,
        status: approvalStatus,
        preview,
      },
      input,
      run_id: runId,
    };
  }

  updateAgentRunApprovalPreview(detailsEl, summary) {
    if (!detailsEl) return;
    const container = detailsEl.querySelector("[data-agent-run-content]");
    if (!container) return;
    const blocksRoot = container.querySelector("[data-message-blocks]") || container;
    let previewEl = blocksRoot.querySelector("[data-agent-run-approval-preview]");
    const shouldShow =
      summary &&
      summary.metaType === "needs_approval" &&
      summary.approvalPreview &&
      typeof summary.approvalPreview === "object";
    if (!shouldShow) {
      if (previewEl) previewEl.remove();
      return;
    }
    if (!previewEl) {
      previewEl = document.createElement("div");
      previewEl.dataset.agentRunApprovalPreview = "true";
      previewEl.className = "portal-agent-run__preview";
      blocksRoot.insertBefore(previewEl, blocksRoot.firstChild);
    }

    const previewType = (summary.approvalPreview.type || "").toString().trim().toLowerCase();
    const isPhoneCall = previewType === "phone_call";
    if (!isPhoneCall) {
      previewEl.className = "portal-agent-run__preview";
      previewEl.innerHTML = this.renderAgentRunApprovalPreview(summary.approvalPreview);
      return;
    }

    previewEl.className = "portal-agent-run__preview portal-agent-run__preview--call";

    const payload = this.buildAgentRunPhoneCallPreviewPayload(summary);
    if (!payload) {
      previewEl.className = "portal-agent-run__preview";
      previewEl.innerHTML = this.renderAgentRunApprovalPreview(summary.approvalPreview);
      return;
    }

    let card = previewEl.querySelector('[data-call-approval-card="true"]');
    if (!card) {
      previewEl.innerHTML = "";
      card = this.buildPhoneCallApprovalCard(payload);
      if (!card) {
        previewEl.className = "portal-agent-run__preview";
        previewEl.innerHTML = this.renderAgentRunApprovalPreview(summary.approvalPreview);
        return;
      }
      previewEl.appendChild(card);
    } else {
      this.updatePhoneCallApprovalCard(card, payload);
    }

    // In agent-run previews, rely on the parent approval CTAs (avoid redundant accept/reject buttons).
    const actions = card.querySelector("[data-tool-approval-actions]");
    if (actions) actions.remove();

    // Default: collapsed in background-run previews until the user explicitly expands.
    if (card.dataset.agentRunPreviewInit !== "true") {
      card.dataset.agentRunPreviewInit = "true";
      card.dataset.callApprovalDetailsUser = "true";
      this.setCallApprovalDetailsOpen(card, false, { userAction: false });
    }
  }

  updateAgentRunApprovalActions(detailsEl, summary) {
    if (!detailsEl) return;
    const actionsEl = detailsEl.querySelector("[data-agent-run-actions]");
    if (!actionsEl) return;

    const approveBtn = actionsEl.querySelector('[data-run-approval-action="approve"]');
    const denyBtn = actionsEl.querySelector('[data-run-approval-action="deny"]');
    if (!approveBtn || !denyBtn) return;

    const runId = (summary && summary.runId ? summary.runId : detailsEl.dataset.agentRunId || "").toString().trim();
    const container = this.elements.messagesInner || this.elements.messages;
    const runCards = runId && container ? container.querySelectorAll(`[data-agent-run-details][data-agent-run-id="${runId}"]`) : [];
    const isLatestForRun = runCards && runCards.length ? runCards[runCards.length - 1] === detailsEl : true;
    const state = runId ? this.agentRuns.get(runId) : null;
    const runStatus = state && state.run && state.run.status ? state.run.status.toString().trim().toLowerCase() : "";
    const shouldShow = Boolean(
      summary &&
        summary.metaType === "needs_approval" &&
        summary.variant === "approval" &&
        (!runStatus || runStatus === "waiting_approval") &&
        isLatestForRun
    );

    if (!shouldShow) {
      approveBtn.removeAttribute("data-approval-id");
      denyBtn.removeAttribute("data-approval-id");
      actionsEl.setAttribute("hidden", "true");
      return;
    }

    const approvalId =
      this.getPendingRunApprovalId(runId) || (summary && summary.approvalId ? summary.approvalId : "");
    if (!approvalId) {
      approveBtn.removeAttribute("data-approval-id");
      denyBtn.removeAttribute("data-approval-id");
      actionsEl.setAttribute("hidden", "true");
      return;
    }

    approveBtn.setAttribute("data-approval-id", approvalId);
    denyBtn.setAttribute("data-approval-id", approvalId);
    actionsEl.removeAttribute("hidden");
  }

  refreshAgentRunChips(runId = "") {
    const container = this.elements.messagesInner || this.elements.messages;
    if (!container) return;
    const targetRunId = (runId || "").toString().trim();
    container.querySelectorAll("[data-agent-run-details]").forEach((details) => {
      if (!details) return;
      if (targetRunId && details.dataset.agentRunId && details.dataset.agentRunId !== targetRunId) return;
      const messageId = this.resolveMessageIdForNode(details);
      const meta = {
        source: "agent_run",
        agent_run_id: details.dataset.agentRunId || "",
        type: details.dataset.agentRunType || "",
      };
      this.updateAgentRunChip(details, { messageId, metadata: meta });
    });
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

    if (this.isAgentRunMetadata(metaPayload)) {
      const bodyEl = wrapper.querySelector("[data-message-body]");
      if (bodyEl) {
        this.ensureAgentRunCollapsible(bodyEl, metaPayload, messageId);
        // Ensure the copy button lands inside the expandable drawer.
        this.injectCopyButton(bodyEl);
      }
    }

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
    const locale = this.getLocale();
    try {
      return safe.toLocaleString(locale);
    } catch (_err) {
      return safe.toLocaleString();
    }
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
		    const promptBudget = Array.isArray(payload.prompt_budget) ? payload.prompt_budget : [];
	    const exactIoTrace = toolTrace.filter(
	      (item) => item && typeof item === "object" && (item.llm_request || item.llm_response),
	    );
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
    if (exactIoTrace.length) summaryBits.push(`I/O (${exactIoTrace.length})`);
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

    const buildToolIoSection = (title, items, labelBuilder) => {
      const section = document.createElement("div");
      const header = document.createElement("div");
      header.className = "text-[11px] font-semibold text-muted-foreground/80 uppercase tracking-wide";
      header.textContent = title;
      section.appendChild(header);

      const buildJsonBlock = (label, payload, { open = false, renderAs = "json", mono = true } = {}) => {
        const details = document.createElement("details");
        details.className = "mt-2 rounded-md border border-border/30 bg-background/30 px-2 py-1";
        if (open) details.open = true;

        const summary = document.createElement("summary");
        summary.className = "cursor-pointer select-none text-[11px] font-semibold text-foreground/80";
        summary.textContent = label;
        details.appendChild(summary);

        const pre = document.createElement("pre");
        pre.className = `mt-2 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed ${mono ? "" : "font-sans"}`.trim();
        try {
          if (renderAs === "text") {
            pre.textContent = payload === null || typeof payload === "undefined" ? "" : String(payload);
          } else {
            pre.textContent = JSON.stringify(payload, null, 2);
          }
        } catch (_err) {
          pre.textContent = payload === null || typeof payload === "undefined" ? "" : String(payload);
        }
        details.appendChild(pre);

        return details;
      };

      items.forEach((item, idx) => {
        const itemDetails = document.createElement("details");
        itemDetails.className = "mt-1 rounded-md border border-border/40 bg-background/40 px-2 py-1";

        const itemSummary = document.createElement("summary");
        itemSummary.className = "cursor-pointer select-none";
        itemSummary.textContent = labelBuilder(item, idx);
        itemDetails.appendChild(itemSummary);

        // Compact meta row (quick glance) so you don't need to open 3 nested objects to know what's going on.
        const meta = document.createElement("div");
        meta.className = "mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground";
        const metaBits = [];
        if (item && typeof item.duration_ms === "number") metaBits.push(`duration_ms=${item.duration_ms}`);
        if (item && item.status) metaBits.push(`status=${String(item.status)}`);
        if (item && item.tool) metaBits.push(`executed=${String(item.tool)}`);
        if (item && item.llm_request && item.llm_request.tool) metaBits.push(`requested=${String(item.llm_request.tool)}`);
        meta.textContent = metaBits.join(" • ");
        if (meta.textContent) itemDetails.appendChild(meta);

        // Show the same information, but separated into labeled blocks:
        // - output_summary is curated
        // - llm_request is what the model asked for
        // - llm_response has both raw + parsed (we keep both, but don't interleave them)
        // - full_record is "everything", for when you need it

        if (item && item.output_summary) {
          itemDetails.appendChild(buildJsonBlock("Output summary", item.output_summary, { open: true }));
        }

        if (item && item.llm_request) {
          itemDetails.appendChild(buildJsonBlock("LLM request", item.llm_request, { open: false }));
        }

        if (item && item.llm_response) {
          const resp = item.llm_response;
          const parsed = resp && typeof resp === "object" ? resp.content_json : null;
          const raw = resp && typeof resp === "object" ? resp.content : null;

          if (parsed) {
            itemDetails.appendChild(buildJsonBlock("LLM response (parsed)", parsed, { open: true }));
          } else if (raw) {
            // Fallback: if parsing failed upstream for some reason, still try to show a parsed view.
            let rawParsed = null;
            try {
              rawParsed = JSON.parse(String(raw));
            } catch (_err) {
              rawParsed = null;
            }
            if (rawParsed) {
              itemDetails.appendChild(buildJsonBlock("LLM response (parsed)", rawParsed, { open: true }));
            }
          }

          if (raw) {
            itemDetails.appendChild(buildJsonBlock("LLM response (raw)", raw, { open: false, renderAs: "text" }));
          }

          // Keep the rest of the response object (tool_call_id, etc.) available without duplicating parsed/raw.
          const respMeta = resp && typeof resp === "object" ? { ...resp } : null;
          if (respMeta && typeof respMeta === "object") {
            delete respMeta.content;
            delete respMeta.content_json;
            if (Object.keys(respMeta).length) {
              itemDetails.appendChild(buildJsonBlock("LLM response (meta)", respMeta, { open: false }));
            }
          }
        }

        if (item && item.prompt_compaction) {
          itemDetails.appendChild(buildJsonBlock("Prompt compaction", item.prompt_compaction, { open: false }));
        }

        itemDetails.appendChild(buildJsonBlock("Full record", item, { open: false }));

        section.appendChild(itemDetails);
      });

      return section;
    };

		    if (!toolTrace.length && !promptBudget.length && !searchHistory.length && !results.length && !reads.length && !coverage.length) {
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
	          const toolRaw = item && item.tool ? String(item.tool) : "tool";
          const effectiveTool = this.getEffectiveToolName(toolRaw, item);
          const tool = (effectiveTool || toolRaw).toLowerCase();
          const toolLabel = effectiveTool ? this.formatStatus(effectiveTool) : toolRaw;
          const status = item && item.status ? String(item.status) : "";
          let extra = "";

          // Add lightweight context for common confusion points.
          // Example: search_knowledge returns fewer refs than the raw `total_found` due to dedupe,
          // filtering, or agentic conversion.
          if (tool === "search_knowledge") {
            const summary = item && item.output_summary && typeof item.output_summary === "object" ? item.output_summary : null;
            const returned = summary && typeof summary.results_count === "number" ? summary.results_count : null;
            const total = summary && typeof summary.total_found === "number" ? summary.total_found : null;
            if (returned !== null && total !== null) {
              extra = `returned ${returned}/${total}`;
            } else if (returned !== null) {
              extra = `returned ${returned}`;
            } else if (total !== null) {
              extra = `found ${total}`;
            }
          }

          return [toolLabel, status, extra].filter(Boolean).join(" • ");
        }),
      );
    }

    if (exactIoTrace.length) {
      const ioEntries = exactIoTrace.map((item) => ({
        tool: item.tool,
        status: item.status,
        duration_ms: item.duration_ms,
        output_summary: item.output_summary || null,
        llm_request: item.llm_request || null,
        llm_response: item.llm_response || null,
        prompt_compaction: item.prompt_compaction || null,
      }));
      container.appendChild(
        buildToolIoSection("Tool I/O (LLM Exact)", ioEntries, (item, idx) => {
          const requestToolRaw =
            item && item.llm_request && item.llm_request.tool
              ? String(item.llm_request.tool)
              : "";
          const requestTool = requestToolRaw ? this.formatStatus(requestToolRaw) : "";
          const executedToolRaw = item && item.tool ? String(item.tool) : "";
          const effectiveExecutedTool = this.getEffectiveToolName(executedToolRaw, item);
          const executedTool =
            effectiveExecutedTool
              ? this.formatStatus(effectiveExecutedTool)
              : executedToolRaw || `Tool ${idx + 1}`;
          const status = item && item.status ? String(item.status) : "";
          const flow = requestTool && requestTool !== executedTool ? `${requestTool} → ${executedTool}` : executedTool;
          return [flow, status].filter(Boolean).join(" • ");
        }),
      );
    }

    if (searchHistory.length) {
      container.appendChild(
        buildSection("Searches", searchHistory, (item, idx) => {
          const intent = item && (item.intent || item.query) ? String(item.intent || item.query) : `Search ${idx + 1}`;
          const response = item && item.response && typeof item.response === "object" ? item.response : null;
          const status = response && response.status ? String(response.status) : "";
          let returned = null;
          let totalFound = null;

          if (response) {
            if (Array.isArray(response.refs)) returned = response.refs.length;
            else if (Array.isArray(response.results)) returned = response.results.length;
            else if (Array.isArray(response.snippets)) returned = response.snippets.length;
            else if (typeof response.results_count === "number") returned = response.results_count;

            const totalCandidate =
              response.total_found ??
              (response.completeness && typeof response.completeness === "object" ? response.completeness.total_found : null);
            if (typeof totalCandidate === "number") totalFound = totalCandidate;
            else if (typeof totalCandidate === "string" && totalCandidate.trim() && totalCandidate.trim().match(/^\d+$/)) {
              totalFound = Number(totalCandidate.trim());
            }
          }

          let count = "";
          if (returned !== null && totalFound !== null) count = `returned ${returned}/${totalFound}`;
          else if (returned !== null) count = `returned ${returned}`;
          else if (totalFound !== null) count = `found ${totalFound}`;

          return [intent, status, count].filter(Boolean).join(" • ");
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

				  resetStreamingState(removeNode = false, lockAutomation = true) {
		    if (lockAutomation) {
		      this.automationLocked = true;
		    }
		    this.streamingActive = false;
		    this.isStreaming = false;
        this.setFollowScrollEnabled(false);
			    this.hadToolsThisTurn = false;
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
          this.streamingContentBlocksById.clear();
          this.preApprovalContentBlocksSnapshot.clear();
			    this.streamingPendingBlockOps.clear();
			    this.streamingTextBlockActiveIds.clear();
			    this.streamingToolBlockActiveIds.clear();
		    this.streamingDirtyTextBlocks.clear();
        this.streamingMissingBlockWrapperCounts.clear();
			    if (this.streamingBlockRenderRaf) {
			      cancelAnimationFrame(this.streamingBlockRenderRaf);
		    }
		    this.streamingBlockRenderRaf = null;
    this.streamingBlockPacerBudget = 0;
    this.streamingBlockPacerLastAt = 0;
    this.streamingBlockPacerMode = "normal";
    this.streamingBlockDeferredActions = [];
    if (this.turnPersistedFinalizeTimer) {
      clearTimeout(this.turnPersistedFinalizeTimer);
      this.turnPersistedFinalizeTimer = null;
    }
		    this.spinnerDesiredText = "";
    this.spinnerDesiredPending = false;
    this.spinnerDesiredIsError = false;
		    if (removeNode) {
		      this.pendingMessageId = null;
		    }
		  }

  applyStreamingBlockEnterAnimation(el) {
    if (!el || !el.classList) return;
    // Avoid re-animating complex components that already have their own entrance motion.
    if (el.classList.contains("email-preview-card") || (el.dataset && el.dataset.emailCard === "true")) return;
    if (this.finalizingTurn) return;
    if (!this.isStreaming) return;
    const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion) return;
    el.classList.add("portal-stream-enter");
    el.addEventListener(
      "animationend",
      (event) => {
        if (!event || event.animationName !== "portalStreamBlockIn") return;
        el.classList.remove("portal-stream-enter");
      },
      { once: true },
    );
  }

  setStreamingStatus(mode = "working", labelOverride) {
    if (this.automationLocked) return;
    const labelMap = {
      working: "Assistant is working…",
      drafting: "Refining answer…",
      reading: "Reading…",
      searching: "Searching…",
      updating: "Refining answer…",
      refining: "Refining answer…",
      error: "Automation issue detected.",
    };
    const baseLabel = labelOverride || labelMap[mode] || labelMap.working;
    const isError = mode === "error";
    this.setSpinnerText(baseLabel, { pending: mode !== "done", isError });
  }

  extractToolSpinnerText(payload) {
    if (!payload || typeof payload !== "object") return "";
    let text = "";
    if (typeof payload.spinner_text === "string") {
      text = payload.spinner_text;
    }
    if (!text && payload.__ui && typeof payload.__ui === "object" && typeof payload.__ui.spinner_text === "string") {
      text = payload.__ui.spinner_text;
    }
    return (text || "").toString().trim();
  }

  syncSpinnerFromActiveToolBlocks() {
    if (!this.streamingBlocksEl) return;
    const inflightIds = this.streamingToolBlockActiveIds;
    if (!inflightIds || !inflightIds.size) {
      this.setSpinnerText("", { pending: this.isStreaming && !this.streamFinished });
      return;
    }
    const children = Array.from(this.streamingBlocksEl.children || []);
    for (const child of children) {
      if (!child || child === this.streamingStatusEl) continue;
      const blockId = child.dataset ? (child.dataset.blockId || "").toString().trim() : "";
      if (!blockId || !inflightIds.has(blockId)) continue;
      const block = this.streamingContentBlocksById.get(blockId);
      const payload = block && typeof block === "object" && block.payload && typeof block.payload === "object" ? block.payload : null;
      const text = this.extractToolSpinnerText(payload);
      if (text) {
        this.setSpinnerText(text, { pending: true, force: true });
        return;
      }
    }
    this.setSpinnerText("", { pending: true });
  }

  clearStreamingIdleStatusTimer() {
    if (!this.streamingIdleStatusTimer) return;
    clearTimeout(this.streamingIdleStatusTimer);
    this.streamingIdleStatusTimer = null;
  }

	  isAssistantTextStreaming() {
	    const hasActiveStream = this.streamingTextBlockActiveIds.size > 0;
	    if (!hasActiveStream) return false;
	    const lastDeltaAt = this.lastStreamEventAt || 0;
	    if (!lastDeltaAt) return false;
	    // Hysteresis: avoid flashing the spinner between normal delta bursts.
    const threshold = 650;
    return Date.now() - lastDeltaAt < threshold;
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
    if (this.automationLocked && pending) return;
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
        if (!force && this.isAssistantTextStreaming()) {
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
    if (!force && this.isAssistantTextStreaming()) {
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

  normalizeQueuedMessagePayload(message, options = null) {
    const normalizedMessage = (message || "").toString().trim();
    if (!normalizedMessage) return null;
    const turnMetadata =
      options &&
      typeof options === "object" &&
      options.turnMetadata &&
      typeof options.turnMetadata === "object"
        ? options.turnMetadata
        : null;
    return {
      message: normalizedMessage,
      turnMetadata,
    };
  }

  enqueueMessage(message, options = null) {
    const payload = this.normalizeQueuedMessagePayload(message, options);
    if (!payload) return;
    if (this.pendingMessages.length >= 1) {
      this.pendingMessages[0] = payload;
      this.showToast("Queued", "Updated your next message.");
      return;
    }
    this.pendingMessages.push(payload);
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
      .chat-portal-status-orbit .mcp-status-spinner {
        --mcp-spinner-size: var(--portal-status-orbit-size, 16px);
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
      [data-message-body] ol,
      [data-message-body] ul {
        margin: 0;
        padding-left: 1.5rem;
      }
      [data-message-body] ol {
        list-style: decimal outside;
      }
      [data-message-body] ul {
        list-style: disc outside;
      }
      [data-message-body] ol ol {
        list-style-type: lower-alpha;
      }
      [data-message-body] ul ul {
        list-style-type: circle;
      }
      [data-message-body] li {
        display: list-item;
        margin: 0;
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

  resolveUiLanguage(source) {
    let candidate = (source || "").toString().trim().toLowerCase();
    if (!candidate && typeof document !== "undefined" && document.documentElement) {
      candidate = (document.documentElement.getAttribute("lang") || "").toString().trim().toLowerCase();
    }
    if (!candidate && typeof navigator !== "undefined") {
      candidate = (navigator.language || "").toString().trim().toLowerCase();
    }
    if (candidate.indexOf("ar") === 0) {
      return "ar";
    }
    return "en";
  }

  resolveLocale(languageCode) {
    const code = (languageCode || "").toString().trim().toLowerCase();
    if (code.indexOf("ar") === 0) {
      return "ar";
    }
    if (typeof navigator !== "undefined") {
      const browserLocale = (navigator.language || "").toString().trim();
      if (browserLocale && browserLocale.toLowerCase().indexOf("en") === 0) {
        return browserLocale;
      }
    }
    return "en-US";
  }

  getUiLanguage() {
    const resolved = this.resolveUiLanguage(this.uiLanguage);
    this.uiLanguage = resolved;
    return resolved;
  }

  getLocale() {
    const resolved = this.resolveLocale(this.getUiLanguage());
    this.locale = resolved;
    return resolved;
  }

  buildVisitorMetadata() {
    const uiLanguage = this.getUiLanguage();
    const locale = this.getLocale();
    const metadata = {
      locale,
      ui_language: uiLanguage,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      path: window.location.pathname,
      referrer: document.referrer,
    };
    if (window.screen) {
      metadata.screen = `${window.screen.width}x${window.screen.height}`;
    }
    return metadata;
  }

  buildTurnMetadata(overrides = null) {
    const metadata = {
      ui_language: this.getUiLanguage(),
      locale: this.getLocale(),
    };
    if (overrides && typeof overrides === "object") {
      Object.keys(overrides).forEach((key) => {
        if (!key) return;
        const value = overrides[key];
        if (value === undefined) return;
        metadata[key] = value;
      });
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
    if (!this.awaitingReply && !this.isStreaming) return;
    this.closeTurnEventStream();
    this.clearActiveTurnState();
    this.awaitingReply = false;
    this.streamFinished = true;
    this.automationLocked = true;
    this.isStreaming = false;
    this.clearStreamingStatus();
    this.updateSendButtonState(false);
    this.setComposerAvailability(true);
    this.updateComposerNotice(false);
    this.resetStreamingState(true, false);
    this.flushQueuedMessageIfReady();
  }

  getStoredToken() {
    if (this.shouldUseConversationApi()) return null;
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
      if (typeof next === "string") {
        this.sendMessage(next);
        return;
      }
      if (next && typeof next === "object") {
        const nextMessage = (next.message || "").toString().trim();
        if (!nextMessage) return;
        const turnMetadata =
          next.turnMetadata && typeof next.turnMetadata === "object" ? next.turnMetadata : null;
        this.sendMessage(nextMessage, { turnMetadata });
      }
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

  readBootstrapScriptConversationId() {
    if (!this.bootstrapScriptId) return null;
    const script = document.getElementById(this.bootstrapScriptId);
    if (!script) return null;
    try {
      const data = JSON.parse(script.textContent || "{}");
      if (data && data.session && data.session.conversation_id) {
        return data.session.conversation_id;
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
    if (this.shouldUseConversationApi()) return;
    if (!this.sessionCacheKey) return;
    try {
      window.localStorage.setItem(this.sessionCacheKey, token);
    } catch (error) {
      console.warn("Unable to persist session token", error);
    }
  }

  shouldUseConversationApi() {
    return Boolean(this.authenticatedChat && this.conversationsEndpoint && this.businessSlug && this.agentSlug);
  }

  getSessionSummaryConversationId(session) {
    if (!session || typeof session !== "object") return "";
    return (session.conversation_id || session.conversationId || "").toString().trim();
  }

  getSessionSummaryToken(session) {
    if (!session || typeof session !== "object") return "";
    return (session.session_token || session.sessionToken || "").toString().trim();
  }

  getSessionSummaryKey(session) {
    if (!session || typeof session !== "object") return "";
    const conversationId = this.getSessionSummaryConversationId(session);
    if (this.shouldUseConversationApi() && conversationId) {
      return conversationId;
    }
    return this.getSessionSummaryToken(session) || conversationId;
  }

  getSessionSummaryTimestamp(session, fieldNames) {
    if (!session || typeof session !== "object") return 0;
    for (const fieldName of fieldNames) {
      const value = session[fieldName];
      if (!value) continue;
      const timestamp = Date.parse(value);
      if (Number.isFinite(timestamp)) return timestamp;
    }
    return 0;
  }

  compareSessionSummaries(left, right) {
    const leftActivity = this.getSessionSummaryTimestamp(left, [
      "last_activity_at",
      "lastActivityAt",
      "started_at",
      "startedAt",
      "created_at",
      "createdAt",
    ]);
    const rightActivity = this.getSessionSummaryTimestamp(right, [
      "last_activity_at",
      "lastActivityAt",
      "started_at",
      "startedAt",
      "created_at",
      "createdAt",
    ]);
    if (leftActivity !== rightActivity) return rightActivity - leftActivity;

    const leftStarted = this.getSessionSummaryTimestamp(left, ["started_at", "startedAt", "created_at", "createdAt"]);
    const rightStarted = this.getSessionSummaryTimestamp(right, ["started_at", "startedAt", "created_at", "createdAt"]);
    if (leftStarted !== rightStarted) return rightStarted - leftStarted;

    const leftTitle = ((left && left.title) || "").toString();
    const rightTitle = ((right && right.title) || "").toString();
    const titleCompare = leftTitle.localeCompare(rightTitle);
    if (titleCompare !== 0) return titleCompare;

    return this.getSessionSummaryKey(left).localeCompare(this.getSessionSummaryKey(right));
  }

  sortSessionSummaries(sessions) {
    if (!Array.isArray(sessions)) return [];
    return sessions
      .filter((session) => session && typeof session === "object" && this.getSessionSummaryKey(session))
      .slice()
      .sort((left, right) => this.compareSessionSummaries(left, right));
  }

  getSessionTreeStorageKey() {
    return `portal_session_tree_collapsed_${this.businessSlug}_${this.agentSlug}`;
  }

  readSessionTreeCollapseState() {
    try {
      if (!window.localStorage) return {};
      const raw = window.localStorage.getItem(this.getSessionTreeStorageKey());
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  writeSessionTreeCollapseState(state) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(this.getSessionTreeStorageKey(), JSON.stringify(state || {}));
      }
    } catch (_error) {
      // ignore storage failures
    }
  }

  isSessionTreeCollapsed(key, defaultCollapsed = false) {
    if (!key) return Boolean(defaultCollapsed);
    const state = this.readSessionTreeCollapseState();
    if (Object.prototype.hasOwnProperty.call(state, key)) {
      return Boolean(state[key]);
    }
    return Boolean(defaultCollapsed);
  }

  setSessionTreeCollapsed(key, collapsed) {
    if (!key) return;
    const state = this.readSessionTreeCollapseState();
    state[key] = Boolean(collapsed);
    this.writeSessionTreeCollapseState(state);
  }

  applySessionTreeCollapsedState(button, content, chevron, collapsed) {
    if (content) content.classList.toggle("hidden", Boolean(collapsed));
    if (button) button.setAttribute("aria-expanded", collapsed ? "false" : "true");
    if (chevron) chevron.style.transform = collapsed ? "rotate(-90deg)" : "";
  }

  buildSessionTreeChevron() {
    const chevron = document.createElement("span");
    chevron.className = "inline-flex h-3 w-3 items-center justify-center transition-transform duration-200 text-muted-foreground";
    chevron.setAttribute("aria-hidden", "true");
    chevron.innerHTML = `
      <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    `;
    return chevron;
  }

  buildSessionTreeBranch({ key, label, level = 0, count = 0, defaultCollapsed = false, forceOpen = false, renderChildren, actions }) {
    const wrapper = document.createElement("div");
    wrapper.className = level === 0 ? "space-y-0.5" : "space-y-0.5";

    const button = document.createElement("button");
    button.type = "button";
    button.className = level === 0
      ? "w-full flex items-center gap-1.5 px-2 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/80 hover:text-muted-foreground transition-colors rounded-md"
      : "w-full flex items-center gap-2 px-2 py-1.5 text-sm font-medium text-foreground/85 hover:bg-muted/50 rounded-lg transition-colors";

    const chevron = this.buildSessionTreeChevron();
    const text = document.createElement("span");
    text.className = "min-w-0 flex-1 truncate text-left";
    text.textContent = label || "";
    button.appendChild(chevron);
    button.appendChild(text);

    if (count > 0) {
      const badge = document.createElement("span");
      badge.className = "text-[11px] font-medium text-muted-foreground/70";
      badge.textContent = String(count);
      button.appendChild(badge);
    }
    if (typeof actions === "function") {
      const actionNodes = actions({ button, content: null }) || [];
      for (const node of actionNodes) {
        if (node) button.appendChild(node);
      }
    }

    const content = document.createElement("div");
    content.className = level === 0
      ? "space-y-0.5"
      : "ml-4 space-y-0.5 border-l border-border/60 pl-2";

    const collapsed = forceOpen ? false : this.isSessionTreeCollapsed(key, defaultCollapsed);
    this.applySessionTreeCollapsedState(button, content, chevron, collapsed);

    button.addEventListener("click", () => {
      const nextCollapsed = !content.classList.contains("hidden");
      this.applySessionTreeCollapsedState(button, content, chevron, nextCollapsed);
      this.setSessionTreeCollapsed(key, nextCollapsed);
    });

    if (typeof renderChildren === "function") {
      renderChildren(content);
    }

    wrapper.appendChild(button);
    wrapper.appendChild(content);
    return wrapper;
  }

  getSessionCustomAssistantId(session) {
    if (!session || typeof session !== "object") return "";
    return (session.custom_assistant_id || session.customAssistantId || "").toString().trim();
  }

  getSessionCustomAssistantName(session) {
    if (!session || typeof session !== "object") return "";
    return (session.custom_assistant_name || session.customAssistantName || "").toString().trim();
  }

  getSessionCustomAssistantAgentName(session) {
    if (!session || typeof session !== "object") return "";
    return (session.custom_assistant_agent_name || session.customAssistantAgentName || "").toString().trim();
  }

  groupCustomAssistantSessions(sessions) {
    const groups = new Map();
    for (const session of this.sortSessionSummaries(sessions)) {
      const customAssistantId = this.getSessionCustomAssistantId(session);
      const customAssistantName = this.getSessionCustomAssistantName(session) || this.t("Custom Assistant");
      const agentName = this.getSessionCustomAssistantAgentName(session);
      const fallbackKey = customAssistantId || `custom-assistant-name:${customAssistantName.toLowerCase()}`;
      if (!groups.has(fallbackKey)) {
        groups.set(fallbackKey, {
          key: fallbackKey,
          customAssistantId,
          name: customAssistantName,
          agentName,
          sessions: [],
          latestActivity: 0,
        });
      }
      const group = groups.get(fallbackKey);
      if (!group.agentName && agentName) group.agentName = agentName;
      group.sessions.push(session);
      group.latestActivity = Math.max(
        group.latestActivity,
        this.getSessionSummaryTimestamp(session, ["last_activity_at", "lastActivityAt", "started_at", "startedAt"])
      );
    }
    return Array.from(groups.values()).sort((left, right) => {
      if (left.latestActivity !== right.latestActivity) return right.latestActivity - left.latestActivity;
      return left.name.localeCompare(right.name);
    });
  }

  getCustomAssistantSidebarGroups(taskSessions) {
    const groups = new Map();
    for (const group of this.groupCustomAssistantSessions(taskSessions)) {
      groups.set(group.key, group);
    }
    return Array.from(groups.values()).sort((left, right) => {
      if (left.latestActivity !== right.latestActivity) return right.latestActivity - left.latestActivity;
      return left.name.localeCompare(right.name);
    });
  }

  buildCustomAssistantSessionCreateButton(customAssistantId, customAssistantName) {
    if (!customAssistantId || !this.shouldUseConversationApi()) return null;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ml-1 inline-flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors";
    button.title = this.t("Start new Custom Assistant session");
    button.setAttribute("aria-label", this.t("Start new Custom Assistant session"));
    button.innerHTML = `
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <line x1="12" y1="5" x2="12" y2="19"></line>
        <line x1="5" y1="12" x2="19" y2="12"></line>
      </svg>
    `;
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.createCustomAssistantSession(customAssistantId, customAssistantName);
    });
    return button;
  }

  getCurrentSessionKey() {
    if (this.shouldUseConversationApi() && this.conversationId) {
      return this.conversationId;
    }
    return this.sessionToken || this.conversationId || null;
  }

  getCurrentReferencePayload(extra = {}) {
    const payload = { ...extra };
    if (this.shouldUseConversationApi() && this.conversationId) {
      payload.conversation_id = this.conversationId;
      return payload;
    }
    if (this.sessionToken) {
      payload.session_token = this.sessionToken;
    }
    return payload;
  }

  applyCurrentReferenceToUrl(url) {
    if (!(url instanceof URL)) return url;
    if (this.shouldUseConversationApi() && this.conversationId) {
      url.searchParams.set("conversation_id", this.conversationId);
      url.searchParams.delete("session_token");
      return url;
    }
    if (this.sessionToken) {
      url.searchParams.set("session_token", this.sessionToken);
    }
    return url;
  }

  setActiveSessionFromSession(session) {
    if (!session || typeof session !== "object") return;
    const sessionToken = this.getSessionSummaryToken(session);
    const conversationId = this.getSessionSummaryConversationId(session);
    if (sessionToken) {
      this.sessionToken = sessionToken;
      this.container.setAttribute("data-session-token", sessionToken);
    }
    if (conversationId) {
      this.conversationId = conversationId;
      this.container.setAttribute("data-conversation-id", conversationId);
    }
    this.currentSessionType = this.getSessionSummaryType(session);
    this.currentCustomAssistantName = (session.custom_assistant_name || session.customAssistantName || session.title || "").toString().trim();
    this.container.setAttribute("data-session-type", this.currentSessionType);
    this.container.setAttribute("data-custom-assistant-name", this.currentCustomAssistantName);
    this.currentSessionKey = this.getSessionSummaryKey(session);
    if (this.shouldUseConversationApi() && conversationId) {
      try {
        if (window.localStorage) {
          window.localStorage.setItem(this.lastConversationStorageKey, conversationId);
        }
      } catch (_error) {
        // ignore storage failures
      }
      this.updateDashboardUrl(conversationId);
    }
  }

  updateDashboardUrl(conversationId) {
    if (!this.shouldUseConversationApi() || !conversationId || this.chatSurface !== "dashboard") return;
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("conversation", conversationId);
      window.history.replaceState({}, "", url.toString());
    } catch (_error) {
      // ignore URL update failures
    }
  }

  getSessionSummaryByKey(sessionKey) {
    if (!sessionKey || !Array.isArray(this.sessionSummaries)) return null;
    return this.sessionSummaries.find((session) => this.getSessionSummaryKey(session) === sessionKey) || null;
  }

  getConversationMessagesUrl(conversationId, limit = null) {
    const normalized = (conversationId || "").toString().trim();
    if (!normalized) return "";
    const url = new URL(`/api/chat/conversations/${encodeURIComponent(normalized)}/messages/`, window.location.origin);
    if (Number.isFinite(Number(limit)) && Number(limit) > 0) {
      url.searchParams.set("limit", String(Number(limit)));
    }
    return url.toString();
  }

  getConversationTurnsUrl(conversationId) {
    const normalized = (conversationId || "").toString().trim();
    if (!normalized) return "";
    return `/api/chat/conversations/${encodeURIComponent(normalized)}/turns/`;
  }

  upsertSessionSummary(sessionSummary) {
    if (!sessionSummary || typeof sessionSummary !== "object") return;
    const key = this.getSessionSummaryKey(sessionSummary);
    if (!key) return;
    const existing = Array.isArray(this.sessionSummaries) ? this.sessionSummaries : [];
    const filtered = existing.filter((session) => this.getSessionSummaryKey(session) !== key);
    this.sessionSummaries = this.sortSessionSummaries([sessionSummary, ...filtered]);
  }

  getTurnStateKey() {
    if (!this.sessionToken) return null;
    return `${this.turnStateStorageKeyPrefix}${this.sessionToken}`;
  }

  loadActiveTurnState() {
    const key = this.getTurnStateKey();
    if (!key) return null;
    try {
      const raw = window.localStorage.getItem(key);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      if (!parsed.turn_id) return null;
      return parsed;
    } catch (error) {
      console.warn("Failed to load active turn state", error);
      return null;
    }
  }

  storeActiveTurnState(turnId, lastSeq = 0) {
    const key = this.getTurnStateKey();
    if (!key || !turnId) return;
    const payload = {
      turn_id: turnId,
      last_seq: Number.isFinite(Number(lastSeq)) ? Number(lastSeq) : 0,
      updated_at: new Date().toISOString(),
    };
    try {
      window.localStorage.setItem(key, JSON.stringify(payload));
    } catch (error) {
      console.warn("Failed to store active turn state", error);
    }
  }

  clearActiveTurnState() {
    const key = this.getTurnStateKey();
    if (key) {
      try {
        window.localStorage.removeItem(key);
      } catch (error) {
        console.warn("Failed to clear active turn state", error);
      }
    }
    this.activeTurnId = null;
    this.activeTurnLastSeq = 0;
    this.turnCancelled = false;
  }

  buildTurnEventsUrl(turnId, { since = 0 } = {}) {
    if (!turnId) return "";
    const template = (this.endpoints.turnEventsTemplate || "").toString().trim();
    const encodedId = encodeURIComponent(turnId);
    let path = template && template.includes("{turn_id}")
      ? template.replace("{turn_id}", encodedId)
      : `/api/chat/turns/${encodedId}/events/`;
    const url = new URL(path, window.location.origin);
    this.applyCurrentReferenceToUrl(url);
    if (Number.isFinite(Number(since)) && Number(since) > 0) {
      url.searchParams.set("since", String(Number(since)));
    }
    return url.toString();
  }

  buildTurnCancelUrl(turnId) {
    if (!turnId) return "";
    const template = (this.endpoints.turnCancelTemplate || "").toString().trim();
    const encodedId = encodeURIComponent(turnId);
    if (template && template.includes("{turn_id}")) {
      return template.replace("{turn_id}", encodedId);
    }
    return `/api/chat/turns/${encodedId}/cancel/`;
  }

  closeTurnEventStream() {
    if (this.turnEventSource) {
      this.turnEventSource.close();
      this.turnEventSource = null;
    }
  }

  startTurnEventStream(turnId, { since = 0 } = {}) {
    if (!turnId) return;
    this.closeTurnEventStream();
    this.activeTurnId = turnId;
    this.activeTurnLastSeq = Number.isFinite(Number(since)) ? Number(since) : 0;
    this.turnCancelled = false;
    this.storeActiveTurnState(turnId, this.activeTurnLastSeq);
    const url = this.buildTurnEventsUrl(turnId, { since: this.activeTurnLastSeq });
    if (!url) return;
    const source = new EventSource(url);
    this.turnEventSource = source;

    source.addEventListener("turnEvent", (event) => {
      let payload = null;
      try {
        payload = event && event.data ? JSON.parse(event.data) : null;
      } catch (error) {
        console.warn("Failed to parse turn event", error);
        return;
      }
      this.handleTurnEventPayload(payload);
    });

    source.onerror = () => {
      if (!this.turnEventSource) return;
      if (this.turnEventSource.readyState === EventSource.CLOSED) {
        if (this.streamFinished || !this.activeTurnId) {
          this.closeTurnEventStream();
          return;
        }
        if (this.turnCancelled) {
          this.awaitingReply = false;
          this.isStreaming = false;
          this.streamFinished = true;
          this.updateSendButtonState(false);
          this.setComposerAvailability(true);
          this.updateComposerNotice(false);
          this.resetStreamingState(true, false);
          this.clearActiveTurnState();
          this.closeTurnEventStream();
        }
      }
    };
  }

  resumeActiveTurnIfNeeded() {
    if (!this.sessionToken) return;
    if (this.isStreaming || this.isSending) return;
    let state = this.loadActiveTurnState();
    const bootstrapTurn =
      this.bootstrapPayload && this.bootstrapPayload.active_turn && typeof this.bootstrapPayload.active_turn === "object"
        ? this.bootstrapPayload.active_turn
        : null;
    const bootstrapTurnId = bootstrapTurn && bootstrapTurn.id ? bootstrapTurn.id.toString().trim() : "";
    if (!bootstrapTurnId) {
      if (state && state.turn_id) {
        this.traceStream("resume.clear_stale_state", { staleTurnId: state.turn_id });
        this.clearActiveTurnState();
      }
      return;
    }

    const stateTurnId = state && state.turn_id ? state.turn_id.toString().trim() : "";
    if (stateTurnId && stateTurnId !== bootstrapTurnId) {
      this.traceStream("resume.drop_mismatched_state", {
        staleTurnId: stateTurnId,
        activeTurnId: bootstrapTurnId,
      });
      this.clearActiveTurnState();
      state = null;
    }

    const cachedSeq = state && Number.isFinite(Number(state.last_seq)) ? Number(state.last_seq) : 0;
    const resumeSince = stateTurnId === bootstrapTurnId && cachedSeq > 0 ? cachedSeq : 0;

    this.awaitingReply = true;
    this.isStreaming = true;
    this.streamFinished = false;
    this.flushQueueAfterTurn = false;
    this.updateSendButtonState(true);
    this.setComposerAvailability(false);
    this.updateComposerNotice(true);
    this.setSpinnerText("", { pending: true });
    this.traceStream("resume.start", { turnId: bootstrapTurnId, since: resumeSince });

    // Resume from the latest known sequence for the same active turn when available.
    this.startTurnEventStream(bootstrapTurnId, { since: resumeSince });
  }

  handleTurnEventPayload(payload) {
    if (!payload || typeof payload !== "object") return;
    const turnId = payload.turn_id || payload.turnId || this.activeTurnId;
    if (this.activeTurnId && turnId && this.activeTurnId !== turnId) {
      return;
    }
    if (turnId && !this.activeTurnId) {
      this.activeTurnId = turnId;
    }
    const seq = Number(payload.seq);
    if (Number.isFinite(seq)) {
      this.activeTurnLastSeq = Math.max(this.activeTurnLastSeq || 0, seq);
      if (turnId) {
        this.storeActiveTurnState(turnId, this.activeTurnLastSeq);
      }
    }
    const type = (payload.type || "").toString().trim();
    const eventPayload = payload.payload && typeof payload.payload === "object" ? payload.payload : {};
    if (!type) return;
    const normalizedType = type.toLowerCase();
    const isTurnPersistedType =
      normalizedType === "turn_persisted" || normalizedType === "turnpersisted" || normalizedType === "turn_finalized";

    if (this.finalizingTurn && !isTurnPersistedType) {
      this.traceStream("event_ignored_finalizing", { type, turnId: turnId || null });
      return;
    }
    if (
      this.lastFinalizedTurnId &&
      turnId &&
      turnId === this.lastFinalizedTurnId &&
      this.streamFinished &&
      !isTurnPersistedType
    ) {
      this.traceStream("event_ignored_after_finalize", { type, turnId });
      return;
    }

    if (isTurnPersistedType) {
      if (turnId) {
        this.lastFinalizedTurnId = turnId;
      }
      const blocks = Array.isArray(eventPayload.content_blocks)
        ? eventPayload.content_blocks
        : Array.isArray(eventPayload.contentBlocks)
        ? eventPayload.contentBlocks
        : [];
      const textLen = typeof eventPayload.text === "string" ? eventPayload.text.length : 0;
      this.traceStream("turn_persisted", {
        rawLen: JSON.stringify(eventPayload || {}).length,
        textLen,
        blocks: blocks.length,
      });
      this.handleTurnPersistedEvent(JSON.stringify(eventPayload));
      return;
    }

    if (type === "turn_cancelled") {
      this.turnCancelled = true;
      this.setSpinnerText("Stopped", { pending: false, force: true });
      return;
    }

    this.handleStreamEvent(type, JSON.stringify(eventPayload));
  }



  formatStatus(status) {
    return status.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  jsonHeaders() {
    const headers = {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    const csrfToken = this.getCsrfToken();
    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }
    return headers;
  }

  getCsrfToken() {
    try {
      const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
      if (match && match[1]) {
        return decodeURIComponent(match[1]);
      }
    } catch (_error) {
      // ignore cookie parsing failures
    }
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input && input.value ? input.value : "";
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
    const titleText = this.t(title);
    const descriptionText = this.t(description);
    const panel = document.createElement("div");
    panel.className = `pointer-events-auto rounded-xl border px-4 py-3 shadow-lg backdrop-blur transition ${destructive
      ? "border-destructive bg-destructive/10 text-destructive"
      : "border-border bg-card text-foreground"
      }`;
    panel.innerHTML = `
      <div class="font-semibold">${this.escapeHtml(titleText)}</div>
      <div class="text-sm">${this.escapeHtml(descriptionText)}</div>
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

  trackCurrentSession() {
    const session =
      this.bootstrapPayload && this.bootstrapPayload.session && typeof this.bootstrapPayload.session === "object"
        ? this.bootstrapPayload.session
        : {
            session_token: this.sessionToken,
            conversation_id: this.conversationId,
          };
    this.setActiveSessionFromSession(session);
  }

  async loadSessionHistory() {
    this.showSessionsLoading();

    try {
      let response;
      if (this.shouldUseConversationApi()) {
        const url = new URL(this.conversationsEndpoint, window.location.origin);
        url.searchParams.set("business_slug", this.businessSlug);
        url.searchParams.set("agent_slug", this.agentSlug);
        url.searchParams.set("limit", "100");
        response = await fetch(url.toString(), {
          method: "GET",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        });
      } else {
        let tokens = [];
        try {
          const stored = localStorage.getItem(this.sessionStorageKey);
          tokens = stored ? JSON.parse(stored) : [];
        } catch (error) {
          console.warn("Failed to read session history from localStorage", error);
          tokens = [];
        }
        if (!Array.isArray(tokens) || tokens.length === 0) {
          this.showSessionsEmpty();
          return;
        }
        response = await fetch("/api/chat/portal/sessions/list/", {
          method: "POST",
          headers: this.jsonHeaders(),
          body: JSON.stringify({
            business_slug: this.businessSlug,
            agent_slug: this.agentSlug,
            session_tokens: tokens,
          }),
        });
      }

      if (!response.ok) {
        throw new Error("Failed to load sessions");
      }

      const data = await response.json();
      const sessions = this.shouldUseConversationApi() ? data.conversations || [] : data.sessions || [];
      const sortedSessions = this.sortSessionSummaries(Array.isArray(sessions) ? sessions : []);

      this.sessionSummaries = sortedSessions;
      if (sortedSessions.length === 0) {
        this.showSessionsEmpty();
      } else {
        this.renderSessionList(sortedSessions);
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

    const sortedSessions = this.sortSessionSummaries(Array.isArray(sessions) ? sessions : []);
    const customAssistantSessions = sortedSessions.filter((session) => this.getSessionSummaryType(session) === "custom_assistant");
    const chatSessions = sortedSessions.filter((session) => this.getSessionSummaryType(session) === "chat");
    const currentSessionKey = this.getCurrentSessionKey();

    const customAssistantGroups = this.getCustomAssistantSidebarGroups(customAssistantSessions);
    if (!customAssistantGroups.length && !chatSessions.length) {
      this.showSessionsEmpty();
      return;
    }
    if (customAssistantGroups.length) {
      const customAssistantBranch = this.buildSessionTreeBranch({
        key: "section:custom-assistants",
        label: this.t("Custom Assistants"),
        level: 0,
        count: customAssistantGroups.length,
        forceOpen: customAssistantSessions.some((session) => this.getSessionSummaryKey(session) === currentSessionKey),
        renderChildren: (sectionContent) => {
          for (const group of customAssistantGroups) {
            const groupHasActiveSession = group.sessions.some((session) => this.getSessionSummaryKey(session) === currentSessionKey);
            const customAssistantNode = this.buildSessionTreeBranch({
              key: `custom-assistant:${group.key}`,
              label: group.name,
              level: 1,
              count: group.sessions.length,
              forceOpen: groupHasActiveSession,
              actions: () => [this.buildCustomAssistantSessionCreateButton(group.customAssistantId, group.name)],
              renderChildren: (customAssistantContent) => {
                if (!group.sessions.length) {
                  const empty = document.createElement("div");
                  empty.className = "px-2 py-1.5 text-xs text-muted-foreground";
                  empty.textContent = this.t("No chats yet");
                  customAssistantContent.appendChild(empty);
                }
                for (const session of group.sessions) {
                  const isActive = this.getSessionSummaryKey(session) === currentSessionKey;
                  customAssistantContent.appendChild(this.buildSessionItem(session, isActive, { nested: true }));
                }
              },
            });
            sectionContent.appendChild(customAssistantNode);
          }
        },
      });
      itemsContainer.appendChild(customAssistantBranch);
    }

    if (chatSessions.length) {
      const chatsBranch = this.buildSessionTreeBranch({
        key: "section:chats",
        label: this.t("Chats"),
        level: 0,
        count: chatSessions.length,
        forceOpen: chatSessions.some((session) => this.getSessionSummaryKey(session) === currentSessionKey),
        renderChildren: (sectionContent) => {
          for (const session of chatSessions) {
            const isActive = this.getSessionSummaryKey(session) === currentSessionKey;
            sectionContent.appendChild(this.buildSessionItem(session, isActive, { nested: true }));
          }
        },
      });
      itemsContainer.appendChild(chatsBranch);
    }

    this.applyPendingSessionTitles();
  }

  getSessionSummaryType(session) {
    if (!session || typeof session !== "object") return "chat";
    const explicitType = (session.session_type || session.sessionType || "").toString().trim().toLowerCase();
    if (explicitType) return explicitType;
    if (session.custom_assistant_id || session.customAssistantId) return "custom_assistant";
    if (session.automation_id || session.automationId) return "task";
    return "chat";
  }

  getCurrentSessionType() {
    const currentSummary = this.getSessionSummaryByKey(this.getCurrentSessionKey());
    if (currentSummary) return this.getSessionSummaryType(currentSummary);
    return this.currentSessionType || "chat";
  }

  isCurrentTaskSession() {
    return this.getCurrentSessionType() === "task";
  }

  buildSessionItem(session, isActive, options = {}) {
    const div = document.createElement("div");
    const nested = Boolean(options.nested);
    div.className = `flex items-center gap-2 px-2 ${nested ? "h-9 text-[13px]" : "h-10 text-sm"} rounded-lg cursor-pointer transition-colors font-medium ${
      isActive
        ? "bg-primary/10 text-primary"
        : "text-foreground/80 hover:bg-muted/50"
    }`;
    const sessionToken = this.getSessionSummaryToken(session);
    const conversationId = this.getSessionSummaryConversationId(session);
    const sessionKey = this.getSessionSummaryKey(session);
    if (sessionToken) {
      div.dataset.sessionToken = sessionToken;
    }
    if (conversationId) {
      div.dataset.conversationId = conversationId;
    }
    div.dataset.sessionKey = sessionKey;
    div.dataset.sessionType = this.getSessionSummaryType(session);
    if (typeof session.message_count === "number") {
      div.dataset.messageCount = String(session.message_count);
    }

    div.innerHTML = `
      <span class="flex-1 truncate" data-session-title>${this.escapeHtml(session.title)}</span>
    `;

    // Click to switch session
    div.addEventListener("click", () => {
      if (sessionKey && sessionKey !== this.getCurrentSessionKey()) {
        this.switchToSession(session);
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
    
    const locale = this.getLocale();
    try {
      return date.toLocaleDateString(locale);
    } catch (_err) {
      return date.toLocaleDateString();
    }
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

  applySessionTitleToDom(sessionKey, title) {
    if (!sessionKey || !this.elements.sessionsList) return false;
    const item = this.elements.sessionsList.querySelector(`[data-session-key="${sessionKey}"]`);
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

  updateSessionTitle(sessionKey, title) {
    if (!sessionKey || !title) return;
    const applied = this.applySessionTitleToDom(sessionKey, title);
    if (!applied) {
      this.pendingSessionTitles[sessionKey] = title;
    } else if (this.pendingSessionTitles[sessionKey]) {
      delete this.pendingSessionTitles[sessionKey];
    }
    if (Array.isArray(this.sessionSummaries) && this.sessionSummaries.length) {
      this.sessionSummaries = this.sessionSummaries.map((session) => {
        if (!session || this.getSessionSummaryKey(session) !== sessionKey) return session;
        return { ...session, title };
      });
    }
  }

  updateSessionTitleFromMessage(messageText) {
    const sessionKey = this.getCurrentSessionKey();
    if (!sessionKey) return;
    const title = this.generateSessionTitle(messageText);
    this.updateSessionTitle(sessionKey, title);
  }

  applyPendingSessionTitles() {
    if (!this.pendingSessionTitles || !this.elements.sessionsList) return;
    const pending = { ...this.pendingSessionTitles };
    Object.keys(pending).forEach((sessionKey) => {
      this.updateSessionTitle(sessionKey, pending[sessionKey]);
    });
  }

  findEmptySessionKey() {
    if (Array.isArray(this.sessionSummaries) && this.sessionSummaries.length) {
      const emptySummary = this.sessionSummaries.find((session) => (
        session &&
        session.message_count === 0 &&
        this.getSessionSummaryType(session) !== "task"
      ));
      return emptySummary ? this.getSessionSummaryKey(emptySummary) : null;
    }
    if (this.elements.sessionsList) {
      const emptyItem = this.elements.sessionsList.querySelector('[data-message-count="0"]:not([data-session-type="task"])');
      return emptyItem ? emptyItem.dataset.sessionKey || emptyItem.dataset.conversationId || emptyItem.dataset.sessionToken : null;
    }
    return null;
  }

  getSessionMessageCount(sessionKey) {
    if (!sessionKey || !this.elements.sessionsList) return null;
    const item = this.elements.sessionsList.querySelector(`[data-session-key="${sessionKey}"]`);
    if (!item) return null;
    const raw = item.dataset.messageCount;
    if (raw === undefined || raw === "") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }

  setSessionMessageCount(sessionKey, messageCount) {
    if (!sessionKey || !this.elements.sessionsList) return;
    const item = this.elements.sessionsList.querySelector(`[data-session-key="${sessionKey}"]`);
    if (!item) return;
    if (typeof messageCount === "number" && Number.isFinite(messageCount)) {
      item.dataset.messageCount = String(messageCount);
    } else {
      delete item.dataset.messageCount;
    }
    if (Array.isArray(this.sessionSummaries) && this.sessionSummaries.length) {
      this.sessionSummaries = this.sessionSummaries.map((session) => {
        if (!session || this.getSessionSummaryKey(session) !== sessionKey) return session;
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
    const taskThread = this.isCurrentTaskSession();
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

    if (hasMessages || taskThread) {
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
    const emptySessionKey = this.findEmptySessionKey();
    if (emptySessionKey) {
      if (emptySessionKey === this.getCurrentSessionKey()) {
        this.showToast(
          "Start chatting first",
          "Please send a message in this chat before creating a new one.",
          false
        );
        return;
      }
      this.switchToSession(emptySessionKey);
      return;
    }
    // Check if current session is empty
    if (this.isCurrentSessionEmpty() && !this.isCurrentTaskSession()) {
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
      const endpoint = this.shouldUseConversationApi() ? this.conversationsEndpoint : "/api/chat/portal/sessions/create/";
      const response = await fetch(endpoint, {
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
      const session = data && data.session ? data.session : null;
      const newKey = this.getSessionSummaryKey(session || {});
      if (!session || !newKey) {
        throw new Error("No conversation returned");
      }
      this.upsertSessionSummary({
        ...session,
        title: session.title || "New conversation",
        message_count: 0,
      });
      this.renderSessionList(this.sessionSummaries);
      await this.switchToSession(this.getSessionSummaryByKey(newKey) || session);
      this.sessionCreationInProgress = false;
      if (this.elements.newSessionBtn) {
        this.elements.newSessionBtn.disabled = false;
        this.elements.newSessionBtn.classList.remove("opacity-50");
      }
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

  async createCustomAssistantSession(customAssistantId, customAssistantName = "") {
    const normalizedCustomAssistantId = (customAssistantId || "").toString().trim();
    if (!normalizedCustomAssistantId || !this.shouldUseConversationApi()) return;
    if (this.sessionCreationInProgress) return;

    this.sessionCreationInProgress = true;
    try {
      const response = await fetch(this.conversationsEndpoint, {
        method: "POST",
        headers: this.jsonHeaders(),
        body: JSON.stringify({
          business_slug: this.businessSlug,
          agent_slug: this.agentSlug,
          custom_assistant_id: normalizedCustomAssistantId,
          title: this.t("New session"),
          metadata: this.buildVisitorMetadata(),
        }),
      });
      if (!response.ok) {
        throw new Error("Failed to create Custom Assistant session");
      }
      const data = await response.json();
      const session = data && data.session ? data.session : null;
      const newKey = this.getSessionSummaryKey(session || {});
      if (!session || !newKey) {
        throw new Error("No Custom Assistant session returned");
      }
      this.setSessionTreeCollapsed("section:custom-assistants", false);
      this.setSessionTreeCollapsed(`custom-assistant:${normalizedCustomAssistantId}`, false);
      this.upsertSessionSummary({
        ...session,
        custom_assistant_id: session.custom_assistant_id || session.customAssistantId || normalizedCustomAssistantId,
        custom_assistant_name: session.custom_assistant_name || session.customAssistantName || customAssistantName,
        title: session.title || this.t("New session"),
        message_count: 0,
      });
      this.renderSessionList(this.sessionSummaries);
      await this.switchToSession(this.getSessionSummaryByKey(newKey) || session);
    } catch (error) {
      this.showToast(
        this.t("New Custom Assistant session failed"),
        error.message || this.t("Could not create a new Custom Assistant session."),
        true
      );
    } finally {
      this.sessionCreationInProgress = false;
    }
  }

  async switchToSession(sessionRef) {
    const session =
      typeof sessionRef === "object" && sessionRef !== null
        ? sessionRef
        : this.getSessionSummaryByKey((sessionRef || "").toString().trim());
    const sessionKey = this.getSessionSummaryKey(session || {});
    if (!session || !sessionKey || sessionKey === this.getCurrentSessionKey()) return;

    const loadId = ++this.sessionLoadId;
    this.prepareForSessionSwitch();
    this.setSessionLoadingState(true);
    this.currentSessionHasMessages = false;

    // 1. Update internal state
    this.setActiveSessionFromSession(session);
    this.updateSessionEmptyState();

    // 2. Update UI Highlight
    if (this.elements.sessionsList) {
      const items = this.elements.sessionsList.querySelectorAll('[data-session-key]');
      items.forEach(el => {
        if (el.dataset.sessionKey === sessionKey) {
           el.className = "flex items-center gap-2 px-2 h-10 rounded-lg cursor-pointer transition-colors text-sm font-medium bg-primary/10 text-primary";
        } else {
           el.className = "flex items-center gap-2 px-2 h-10 rounded-lg cursor-pointer transition-colors text-sm font-medium text-foreground/80 hover:bg-muted/50";
        }
      });
    }

    // 3. Render loading state
    const messageCount = this.getSessionMessageCount(sessionKey);
    const shouldShowSkeleton = typeof messageCount === "number" ? messageCount > 0 : true;
    if (shouldShowSkeleton) {
      this.setConversationLayout(true);
      this.renderSkeleton();
    } else {
      this.renderEmptyConversationState();
    }

    // 4. Fetch and Render Data
    try {
      let data = null;
      if (this.shouldUseConversationApi() && this.getSessionSummaryConversationId(session)) {
        const response = await fetch(this.getConversationMessagesUrl(this.getSessionSummaryConversationId(session), 150), {
          method: "GET",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        });
        if (!response.ok) {
          throw new Error("Conversation request failed");
        }
        data = await response.json();
        this.bootstrapPayload = data;
        if (data && data.session) {
          this.setActiveSessionFromSession(data.session);
          const messages = Array.isArray(data.messages) ? data.messages : [];
          this.currentSessionHasMessages = messages.length > 0;
          this.setSessionMessageCount(this.getCurrentSessionKey(), messages.length);
          this.setConversationLayout(messages.length > 0);
          if (messages.length > 0) {
            this.renderTranscript(messages);
          } else {
            this.renderEmptyConversationState();
          }
          this.updateStatus(data.session.status);
          this.updateCsatVisibility(data.session.status);
        }
      } else {
        data = await this.bootstrapSession({
          forceRender: true,
          expectedToken: this.getSessionSummaryToken(session),
          sessionToken: this.getSessionSummaryToken(session),
          loadId,
        });
      }
      if (!data || this.sessionLoadId !== loadId) return;
      this.setSessionLoadingState(false);
      this.connectEventStream();
      this.resumeActiveTurnIfNeeded();
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
      if (this.isCurrentTaskSession()) {
        const customAssistantName = this.currentCustomAssistantName || this.t("Custom Assistant");
        container.innerHTML = `
          <div class="min-h-[55vh] flex items-center justify-center px-4 py-12">
            <div class="w-full max-w-xl rounded-lg border border-border/70 bg-card/40 px-5 py-4 text-left shadow-sm">
              <div class="flex items-start gap-3">
                <div class="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M9 11l3 3L22 4"></path>
                    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>
                  </svg>
                </div>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-foreground">${this.escapeHtml(customAssistantName)}</p>
                  <p class="mt-1 text-sm leading-6 text-muted-foreground">${this.escapeHtml(this.t("Start a message to chat with this Custom Assistant."))}</p>
                </div>
              </div>
            </div>
          </div>
        `;
      }
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
    
    if (isEmpty && !this.isCurrentTaskSession()) {
      btn.classList.add('opacity-50', 'cursor-not-allowed');
      btn.setAttribute("aria-disabled", "true");
    } else {
      btn.classList.remove('opacity-50', 'cursor-not-allowed');
      btn.setAttribute("aria-disabled", "false");
    }
    this.currentSessionHasMessages = !isEmpty;
    const currentSessionKey = this.getCurrentSessionKey();
    if (currentSessionKey) {
      if (typeof messageCount === "number") {
        this.setSessionMessageCount(currentSessionKey, messageCount);
      } else if (!isEmpty) {
        const existing = this.getSessionMessageCount(currentSessionKey);
        if (!existing || existing === 0) {
          this.setSessionMessageCount(currentSessionKey, 1);
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
    this.closeTurnEventStream();
    this.clearActiveTurnState();
    this.awaitingReply = false;
    this.isSending = false;
    this.isStreaming = false;
    this.streamFinished = true;
    this.automationLocked = false;
    this.usingStateMachine = false;
    this.pendingMetadataVersion = 0;
    this.pendingMessageId = null;
    this.flushQueueAfterTurn = false;
    this.pendingMessages = [];
    this.resetStreamingState(true, false);
    this.clearStreamingStatus();
    this.updateSendButtonState(false);
    this.updateComposerNotice(false);
    this.closeSessionEventStream();

    if (this.agentRuns) {
      this.agentRuns.clear();
    }
    this.renderTasksPanel();
    this.setTasksPanelVisible(false);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-chat-portal]");
  if (!container) return;
  const client = new ChatPortalClient(container);
  client.init();
});
