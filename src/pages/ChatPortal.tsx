import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ChatHeader from "@/components/chat/ChatHeader";
import ChatInput from "@/components/chat/ChatInput";
import ChatMessages from "@/components/chat/ChatMessages";
import ChatCsatPrompt from "@/components/chat/ChatCsatPrompt";
import { Message } from "@/components/chat/ChatMessage";
import { useToast } from "@/hooks/use-toast";
import { useChatSession } from "@/hooks/useChatSession";
import { useChatMessages } from "@/hooks/useChatMessages";
import {
  ApiError,
  ChatAgentPreview,
  ChatMessage as ApiChatMessage,
  persistSessionToken,
  submitChatCsat, resolvePortalHandle,
} from "@/services/chat";
import { openChatEventStream, cancelAssistantResponse, streamAssistantResponse } from "@/services/chat";
import { setStoredBusinessId } from "@/services/http";
import { useMutation } from "@tanstack/react-query";

const caseTitles = [
  "Refund request for overcharge",
  "Integration API token expired",
  "Cannot reset password",
  "VIP onboarding escalation",
  "SLA breach follow up",
  "Bug report: analytics dashboard",
];

const Sidebar: React.FC = () => {
  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0 sidebar-card-chrome">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">Cases</div>
        <nav className="mt-1 flex-1 space-y-1">
          {caseTitles.map((title) => (
            <Link
              key={title}
              to="#"
              className="w-full block text-left px-3 py-2 rounded-md text-sm text-foreground hover:bg-muted/60"
            >
              <span className="line-clamp-1">{title}</span>
            </Link>
          ))}
        </nav>
        <div className="mt-auto flex items-center gap-2 px-2 py-3">
          <div className="h-8 w-8 rounded-full bg-muted" />
          <div>
            <div className="text-sm font-medium">Evano</div>
            <div className="text-xs text-muted-foreground">Project Manager</div>
          </div>
        </div>
      </div>
    </aside>
  );
};

const CLOSED_STATUSES = new Set(["RESOLVED", "CLOSED_WITHOUT_RESOLUTION"]);

const toDisplayMessage = (message: ApiChatMessage, agent: ChatAgentPreview | null): Message => {
  const type = (message.messageType || "").toString().toUpperCase();
  let sender: Message["sender"]; // default fallback
  if (type === "CUSTOMER") {
    sender = "user";
  } else if (type === "AGENT" || type === "ASSISTANT" || type === "SYSTEM_ASSISTANT") {
    sender = "agent";
  } else {
    sender = "system";
  }

  const timestamp = new Date(message.sentAt);
  const safeTimestamp = Number.isNaN(timestamp.getTime()) ? new Date() : timestamp;

  let content = message.body ?? "";
  if (!content && Array.isArray(message.attachments) && message.attachments.length > 0) {
    const names = message.attachments.map((att) => att.filename).filter(Boolean);
    const label = names.length > 0 ? names.join(", ") : `${message.attachments.length} attachment(s)`;
    content = `Shared attachment${message.attachments.length > 1 ? "s" : ""}: ${label}`;
  }
  if (!content) {
    content = sender === "system" ? "System update" : "";
  }

  const agentName = message.author?.userDisplayName || agent?.name || "Agent";
  const agentAvatar = agent?.avatarUrl ?? undefined;

  return {
    id: message.id,
    content,
    sender,
    timestamp: safeTimestamp,
    agentName: sender === "agent" ? agentName : undefined,
    agentAvatar: sender === "agent" ? agentAvatar : undefined,
  };
};

const ChatPortal: React.FC = () => {
  const { businessSlug, agentSlug } = useParams<{ businessSlug: string; agentSlug: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();


  const [agentHandle, setAgentHandle] = useState<string | null>(null);
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        if (!businessSlug || !agentSlug) return;
        const res = await resolvePortalHandle(businessSlug, agentSlug);
        if (!mounted) return;
        setStoredBusinessId(res.business_id);
        setAgentHandle(res.agent_handle || agentSlug);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Unable to resolve portal address.";
        toast({ title: "Invalid portal URL", description: message, variant: "destructive" });
        navigate("/", { replace: true });
      }
    })();
    return () => { mounted = false };
  }, [businessSlug, agentSlug, navigate, toast]);

  const sessionQuery = useChatSession(agentHandle, { landingPage: typeof window !== "undefined" ? window.location.href : undefined }, {
    retry: false,
    refetchOnWindowFocus: true,
  });

  const sessionData = sessionQuery.data;
  const session = sessionData?.session ?? null;
  const agent = sessionData?.agent ?? null;
  const sessionToken = session?.sessionToken ?? null;

  const [conversationStatus, setConversationStatus] = useState<string | null>(session?.conversationStatus ?? null);
  const [messages, setMessages] = useState<Message[]>([]);
  const rawMessagesRef = useRef<Map<string, ApiChatMessage>>(new Map());
  const [pollCursor, setPollCursor] = useState<string | null>(null);
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [csatVisible, setCsatVisible] = useState(false);
  const [csatRecordedAt, setCsatRecordedAt] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const streamActiveRef = useRef<boolean>(false);
  const streamCancelRef = useRef<null | (() => void)>(null);

  const prevSessionTokenRef = useRef<string | null>(null);
  useEffect(() => {
    if (sessionToken === prevSessionTokenRef.current) return;
    prevSessionTokenRef.current = sessionToken ?? null;
    rawMessagesRef.current.clear();
    setMessages([]);
    setPollCursor(null);
  }, [sessionToken]);

  useEffect(() => {
    if (session?.conversationStatus) {
      setConversationStatus(session.conversationStatus);
    }
  }, [session?.conversationStatus]);

  useEffect(() => {
    if (!sessionData?.messages) return;
    const store = rawMessagesRef.current;
    let changed = false;
    sessionData.messages.forEach((msg) => {
      if (!msg || !msg.id) return;
      const existing = store.get(msg.id);
      if (!existing || existing.sentAt !== msg.sentAt || existing.body !== msg.body) {
        store.set(msg.id, msg);
        changed = true;
      }
    });
    if (changed) {
      const sorted = Array.from(store.values()).sort(
        (a, b) => new Date(a.sentAt).getTime() - new Date(b.sentAt).getTime()
      );
      setMessages(sorted.map((item) => toDisplayMessage(item, agent ?? null)));
    } else if (agent) {
      const sorted = Array.from(store.values()).sort(
        (a, b) => new Date(a.sentAt).getTime() - new Date(b.sentAt).getTime()
      );
      setMessages(sorted.map((item) => toDisplayMessage(item, agent)));
    }
  }, [sessionData?.messages, agent]);

  const updateMessagesFromStore = useCallback(
    (agentInfo: ChatAgentPreview | null) => {
      const sorted = Array.from(rawMessagesRef.current.values()).sort(
        (a, b) => new Date(a.sentAt).getTime() - new Date(b.sentAt).getTime()
      );
      setMessages(sorted.map((item) => toDisplayMessage(item, agentInfo)));
    },
    []
  );

  const mergeMessages = useCallback(
    (incoming: ApiChatMessage[] | undefined, agentInfo: ChatAgentPreview | null) => {
      if (!incoming || incoming.length === 0) return;
      const store = rawMessagesRef.current;
      let changed = false;
      incoming.forEach((msg) => {
        if (!msg || !msg.id) return;
        const existing = store.get(msg.id);
        if (!existing || existing.sentAt !== msg.sentAt || existing.body !== msg.body) {
          store.set(msg.id, msg);
          changed = true;
        }
      });
      if (changed) {
        updateMessagesFromStore(agentInfo);
      }
    },
    [updateMessagesFromStore]
  );

  useEffect(() => {
    if (agent) {
      updateMessagesFromStore(agent);
    }
  }, [agent, updateMessagesFromStore]);

  useEffect(() => {
    if (!conversationStatus) return;
    const normalized = conversationStatus.toUpperCase();
    if (CLOSED_STATUSES.has(normalized) && !csatRecordedAt) {
      setCsatVisible(true);
    }
  }, [conversationStatus, csatRecordedAt]);

  const messagesQuery = useChatMessages(
    { sessionToken: sessionToken ?? null, cursor: pollCursor ?? undefined, limit: 50 },
    {
      enabled: Boolean(sessionToken),
      refetchInterval: () => streamActiveRef.current ? false : 5000,
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: true,
      staleTime: 0,
      cacheTime: 0,
      retry: false,
    },
  );

  useEffect(() => {
    if (!messagesQuery.data) return;
    mergeMessages(messagesQuery.data.messages, agent ?? null);
    setTranscriptError(null);
    const nextCursor = messagesQuery.data.nextCursor ?? null;
    setPollCursor((prev) => (prev === nextCursor ? prev : nextCursor));
  }, [messagesQuery.data, mergeMessages, agent]);
    /* SSE chat stream effect */
    useEffect(() => {
      if (!sessionToken) { if (eventSourceRef.current) { try { eventSourceRef.current.close(); } catch {} eventSourceRef.current = null; } streamActiveRef.current = false; return; }
      const closed = session?.conversationStatus ? CLOSED_STATUSES.has(session.conversationStatus.toUpperCase()) : false; if (closed) { if (eventSourceRef.current) { try { eventSourceRef.current.close(); } catch {} eventSourceRef.current = null; } streamActiveRef.current = false; return; }
      if (eventSourceRef.current) { try { eventSourceRef.current.close(); } catch {} eventSourceRef.current = null; }
      let cancelled = false;
      const handlers = {
        // Keep a lightweight event stream: do not toggle streaming state here
        open: () => {},
        error: () => {},
        statusChanged: (ev:any) => { const st = ev?.status ?? ev?.conversation_status; if (typeof st === "string") setConversationStatus(st); },
        heartbeat: () => {}
      } as const;
      const es = openChatEventStream(sessionToken, handlers as any); eventSourceRef.current = es;
      return () => { cancelled = true; if (eventSourceRef.current) { try { eventSourceRef.current.close(); } catch {} eventSourceRef.current = null; } streamActiveRef.current = false; };
    }, [sessionToken, session?.conversationStatus, agent, updateMessagesFromStore, messagesQuery]);
    /* Visibility refetch when not streaming */
    useEffect(() => {
      const onVisible = () => {
        if (!document.hidden && !streamActiveRef.current) {
          try { messagesQuery.refetch(); } catch {}
          try { sessionQuery.refetch(); } catch {}
        }
      };
      document.addEventListener("visibilitychange", onVisible);
      return () => { document.removeEventListener("visibilitychange", onVisible); };
    }, [messagesQuery, sessionQuery]);

  useEffect(() => {
    const error = messagesQuery.error;
    if (!error) return;
    if (error instanceof ApiError && error.message) {
      setTranscriptError(error.message);
    } else if (error instanceof Error) {
      setTranscriptError(error.message);
    } else {
      setTranscriptError("Unable to load new messages.");
    }
  }, [messagesQuery.error]);

  useEffect(() => {
    if (!sessionQuery.isError || !sessionQuery.error) return;
    const error = sessionQuery.error;
    let description = "Unable to start chat session.";
    if (error instanceof ApiError) {
      if (error.status === 404) {
        description = "The requested agent could not be found.";
      } else if (error.message) {
        description = error.message;
      }
    } else if (error instanceof Error) {
      description = error.message;
    }
    toast({
      title: "Chat unavailable",
      description,
      variant: "destructive",
    });
    navigate("/", { replace: true });
  }, [sessionQuery.isError, sessionQuery.error, toast, navigate]);

  // Streaming replaces non-stream send mutation for portal chat

  const csatMutation = useMutation({
    mutationFn: async ({ score, comment }: { score: number; comment: string | null }) => {
      if (!sessionToken) throw new Error("Missing session token");
      return submitChatCsat({ sessionToken, score, comment });
    },
    onSuccess: (data) => {
      setCsatRecordedAt(data.recordedAt);
      setCsatVisible(false);
      toast({
        title: "Thanks for your feedback",
        description: "Your rating has been recorded.",
      });
      sessionQuery.refetch();
    },
    onError: (error) => {
      let description = "We couldn't submit your feedback. Please try again.";
      if (error instanceof ApiError && error.message) {
        description = error.message;
      } else if (error instanceof Error) {
        description = error.message;
      }
      toast({
        title: "Submission failed",
        description,
        variant: "destructive",
      });
    },
  });

  const handleSendMessage = useCallback((content: string) => {
    if (!sessionToken) {
      toast({
        title: "Session not ready",
        description: "Please wait for the chat session to initialise.",
        variant: "destructive",
      });
      return;
    }
    const trimmed = content.trim();
    if (!trimmed) return;
    setAwaitingReply(true);
    streamActiveRef.current = true;
    // Kick off streaming POST
    const cancelable = streamAssistantResponse(
      { sessionToken, body: trimmed },
      {
        open: () => { /* no-op */ },
        created: (m: any) => {
          setAwaitingReply(true);
          const s = rawMessagesRef.current; s.set(m.id, {
            ...m,
            conversationId: session?.conversationId ?? null,
            author: { agentId: agent?.id ?? null, customerId: null, userDisplayName: agent?.name ?? null },
          });
          updateMessagesFromStore(agent ?? null);
        },
        delta: (ev: { id: string; delta: string }) => {
          setAwaitingReply(true);
          const { id, delta } = ev;
          if (!id || typeof delta !== 'string') return;
          const s = rawMessagesRef.current; const ex: any = s.get(id);
          if (ex) { ex.body = (ex.body || "") + delta; s.set(id, ex); }
          else {
            s.set(id, { id, conversationId: session?.conversationId ?? null, messageType: "ASSISTANT", visibility: "PUBLIC", channel: "text", body: delta, payload: null, sentAt: new Date().toISOString(), author: { agentId: agent?.id ?? null, customerId: null, userDisplayName: agent?.name ?? null }, attachments: [] } as any);
          }
          updateMessagesFromStore(agent ?? null);
        },
        completed: () => {
          streamActiveRef.current = false; setAwaitingReply(false);
          try { messagesQuery.refetch(); } catch {}
          try { sessionQuery.refetch(); } catch {}
        },
        error: (error: Error) => {
          streamActiveRef.current = false; setAwaitingReply(false);
          let description = error?.message || "Message could not be delivered.";
          toast({ title: "Send failed", description, variant: "destructive" });
        },
        end: () => {
          streamActiveRef.current = false; setAwaitingReply(false);
        }
      }
    );
    streamCancelRef.current = cancelable.cancel;
  }, [agent, mergeMessages, sessionQuery, sessionToken, toast]);

  const handleAttachFile = useCallback(() => {
    toast({
      title: "File upload",
      description: "File attachments will be available soon.",
    });
  }, [toast]);

  const handleStop = useCallback(() => {
    setAwaitingReply(false);
    try { if (streamCancelRef.current) { streamCancelRef.current(); } } catch {}
    try { if (sessionToken) { cancelAssistantResponse(sessionToken).catch(() => {}); } } catch {}
    if (eventSourceRef.current) { try { eventSourceRef.current.close(); } catch {} eventSourceRef.current = null; }
    streamActiveRef.current = false;
  }, [sessionToken]);

  const handleDownloadTranscript = useCallback(() => {
    if (messages.length === 0) {
      toast({ title: "Nothing to download", description: "Send a message to start the conversation." });
      return;
    }
    const transcript = messages
      .filter((m) => m.sender !== "system")
      .map((m) => {
        const time = m.timestamp.toLocaleTimeString();
        const sender = m.sender === "agent" ? m.agentName ?? "Agent" : m.sender === "user" ? "You" : "System";
        return `[${time}] ${sender}: ${m.content}`;
      })
      .join("\n\n");

    const blob = new Blob([transcript], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `chat-transcript-${(agent?.id ?? agentHandle ?? "agent")}-${Date.now()}.txt`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);

    toast({
      title: "Transcript downloaded",
      description: "Your conversation transcript has been saved.",
    });
  }, [messages, toast, agent?.id, agentHandle]);

  const handleClearChat = useCallback(() => {
    if (!agentHandle) return;
    persistSessionToken(agentHandle, null);
    rawMessagesRef.current.clear();
    setMessages([]);
    setPollCursor(null);
    setConversationStatus(null);
    setCsatVisible(false);
    sessionQuery.refetch();
    toast({ title: "Chat refreshed", description: "A new session has started." });
  }, [agentHandle, sessionQuery, toast]);

  const handleReportIssue = useCallback(() => {
    toast({
      title: "Report issue",
      description: "This feature will be available soon.",
    });
  }, [toast]);

  const handleEndSession = useCallback(() => {
    if (agentHandle) {
      persistSessionToken(agentHandle, null);
    }
    toast({
      title: "Session ended",
      description: "Thank you for chatting with us.",
    });
    setTimeout(() => navigate("/"), 1200);
  }, [agentHandle, navigate, toast]);

  const conversationClosed = conversationStatus ? CLOSED_STATUSES.has(conversationStatus.toUpperCase()) : false;
  const isOnline = !session?.expiresAt || new Date(session.expiresAt).getTime() > Date.now();
  const showLoading = sessionQuery.isLoading && !session;

  const csatPrompt = csatVisible ? (
    <ChatCsatPrompt
      onSubmit={(score, comment) => csatMutation.mutate({ score, comment })}
      onSkip={() => setCsatVisible(false)}
      isSubmitting={csatMutation.isPending}
    />
  ) : null;

  if (showLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="space-y-2 text-center">
          <div className="h-12 w-12 rounded-full border-4 border-primary/30 border-t-primary animate-spin mx-auto" />
          <p className="text-sm text-muted-foreground">Preparing your chat session…</p>
        </div>
      </div>
    );
  }

  if (!agent || !sessionToken) {
    return null;
  }

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 flex flex-col">
        <ChatHeader
          agentName={agent.name}
          agentRole={agent.role}
          agentAvatar={agent.avatarUrl ?? undefined}
          isOnline={isOnline}
          isTyping={awaitingReply}
          onClearChat={handleClearChat}
          onDownloadTranscript={handleDownloadTranscript}
          onReportIssue={handleReportIssue}
          onEndSession={handleEndSession}
        />

        <div className="flex-1 overflow-hidden pb-[140px] pt-2 md:pt-4">
          <ChatMessages
            messages={messages}
            isLoading={awaitingReply}
            errorMessage={transcriptError}
          />
        </div>

        <ChatInput
          onSendMessage={handleSendMessage}
          onAttachFile={handleAttachFile}
          onStop={awaitingReply ? handleStop : undefined}
          isLoading={awaitingReply}
          disabled={conversationClosed || awaitingReply}
          topAccessory={csatPrompt}
          footerHint={
            conversationClosed
              ? "Conversation closed. Start a new session to chat again."
              : undefined
          }
          className="md:left-[14rem] md:right-0"
        />
      </main>
    </div>
  );
};

export default ChatPortal;
