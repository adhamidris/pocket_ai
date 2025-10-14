import { ApiError, jsonFetch } from "./http";

export type UUID = string;

type ApiChatAgentPreview = {
  id: UUID;
  name: string;
  role: string;
  avatar_url?: string | null;
  business_name?: string | null;
};

type ApiChatSessionState = {
  session_token: string;
  visitor_id: UUID;
  visitor_type: string;
  conversation_id: UUID | null;
  conversation_status: string;
  started_at: string | null;
  expires_at: string | null;
};

type ApiChatMessageAuthor = {
  agent_id?: UUID | null;
  customer_id?: UUID | null;
  user_display_name?: string | null;
};

type ApiChatMessageAttachment = {
  id: UUID;
  storage_asset_id: UUID;
  filename: string;
  content_type?: string | null;
  size_bytes: number;
  download_url?: string | null;
  caption?: string | null;
  metadata?: Record<string, unknown> | null;
};

type ApiChatMessage = {
  id: UUID;
  conversation_id: UUID;
  message_type: string;
  visibility: string;
  channel: string;
  body?: string | null;
  payload?: Record<string, unknown> | null;
  sent_at: string;
  author?: ApiChatMessageAuthor | null;
  attachments?: ApiChatMessageAttachment[];
};

type ApiChatSessionCreateResponse = {
  session: ApiChatSessionState;
  agent: ApiChatAgentPreview;
  messages: ApiChatMessage[];
};

type ApiChatMessagesListResponse = {
  messages: ApiChatMessage[];
  next_cursor: string | null;
  has_more: boolean;
};

type ApiChatMessageSendResponse = {
  message: ApiChatMessage;
  follow_up_messages: ApiChatMessage[];
};

type ApiChatCsatSubmissionResponse = {
  conversation_id: UUID | null;
  recorded_at: string;
};

export type ChatAgentPreview = {
  id: UUID;
  name: string;
  role: string;
  avatarUrl?: string | null;
  businessName?: string | null;
};

export type ChatSessionState = {
  sessionToken: string;
  visitorId: UUID;
  visitorType: string;
  conversationId: UUID | null;
  conversationStatus: string;
  startedAt: string | null;
  expiresAt: string | null;
};

export type ChatMessageAuthor = {
  agentId: UUID | null;
  customerId: UUID | null;
  userDisplayName: string | null;
};

export type ChatMessageAttachment = {
  id: UUID;
  storageAssetId: UUID;
  filename: string;
  contentType: string | null;
  sizeBytes: number;
  downloadUrl?: string | null;
  caption: string | null;
  metadata?: Record<string, unknown> | null;
};

export type ChatMessage = {
  id: UUID;
  conversationId: UUID;
  messageType: string;
  visibility: string;
  channel: string;
  body: string | null;
  payload: Record<string, unknown> | null;
  sentAt: string;
  author: ChatMessageAuthor;
  attachments: ChatMessageAttachment[];
};

export type ChatSessionCreateResponse = {
  session: ChatSessionState;
  agent: ChatAgentPreview;
  messages: ChatMessage[];
};

export type ChatMessagesListResponse = {
  messages: ChatMessage[];
  nextCursor: string | null;
  hasMore: boolean;
};

export type ChatMessageSendResponse = {
  message: ChatMessage;
  followUpMessages: ChatMessage[];
};

export type ChatCsatSubmissionResponse = {
  conversationId: UUID | null;
  recordedAt: string;
};


export type PortalResolveResponse = {

  business_id: string;

  agent_handle: string;

  agent: {

    id: string; name: string; role: string; avatar_url?: string|null; business_name?: string|null;

  };

};



export async function resolvePortalHandle(businessSlug: string, agentSlug: string): Promise<PortalResolveResponse> {

  return jsonFetch<PortalResolveResponse>(`/v1/portal/resolve/${businessSlug}/${agentSlug}`, { method: "GET" });

}


export type ChatSessionCreateRequest = {
  agentHandle: string;
  channel?: string;
  locale?: string | null;
  landingPage?: string | null;
  fingerprintHash?: string | null;
  utm?: Record<string, unknown> | null;
  existingSessionToken?: string | null;
};

export type ChatMessageSendRequest = {
  sessionToken: string;
  body?: string | null;
  payload?: Record<string, unknown> | null;
  channel?: string;
  attachments?: Array<{
    storageAssetId: UUID;
    caption?: string | null;
  }>;
};

export type ChatMessagesListRequest = {
  sessionToken: string;
  cursor?: string | null;
  limit?: number;
};

export type ChatCsatSubmissionRequest = {
  sessionToken: string;
  score: number;
  comment?: string | null;
};

const CHAT_SESSION_STORAGE_PREFIX = "pocket_ai_chat_session";
const BUSINESS_ID_KEY = "pocket_ai_business_id";
const BUSINESS_OBJECT_KEY = "pocket_ai_business";

const getActiveBusinessId = (): string | null => {
  if (typeof window === "undefined") return null;
  try {
    const direct = window.localStorage.getItem(BUSINESS_ID_KEY);
    if (direct && direct !== "null" && direct !== "undefined") return direct;
    const raw = window.localStorage.getItem(BUSINESS_OBJECT_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          const candidate = (parsed as Record<string, unknown>);
          const fallbackId = candidate.id ?? candidate.businessId ?? candidate.business_id;
          if (typeof fallbackId === "string" && fallbackId) {
            return fallbackId;
          }
        }
      } catch {
        /* ignore parse errors */
      }
    }
  } catch {
    /* ignore storage access issues */
  }
  return null;
};

const storageKey = (agentHandle: string) => {
  const businessId = getActiveBusinessId() ?? "anon";
  const safeHandle = agentHandle.replace(/[^a-z0-9_-]/gi, "_").toLowerCase();
  return `${CHAT_SESSION_STORAGE_PREFIX}::${businessId}::${safeHandle}`;
};

export const loadStoredSessionToken = (agentHandle: string): string | null => {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(storageKey(agentHandle));
  } catch {
    return null;
  }
};

export const persistSessionToken = (agentHandle: string, token: string | null) => {
  if (typeof window === "undefined") return;
  try {
    const key = storageKey(agentHandle);
    if (token) {
      window.localStorage.setItem(key, token);
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    /* ignore storage errors */
  }
};

const mapChatSessionState = (payload: ApiChatSessionState): ChatSessionState => ({
  sessionToken: payload.session_token,
  visitorId: payload.visitor_id,
  visitorType: payload.visitor_type,
  conversationId: payload.conversation_id ?? null,
  conversationStatus: payload.conversation_status,
  startedAt: payload.started_at ?? null,
  expiresAt: payload.expires_at ?? null,
});

const mapAgent = (payload: ApiChatAgentPreview): ChatAgentPreview => ({
  id: payload.id,
  name: payload.name,
  role: payload.role,
  avatarUrl: payload.avatar_url ?? null,
  businessName: payload.business_name ?? null,
});

const mapAuthor = (payload: ApiChatMessageAuthor | null | undefined): ChatMessageAuthor => ({
  agentId: payload?.agent_id ?? null,
  customerId: payload?.customer_id ?? null,
  userDisplayName: payload?.user_display_name ?? null,
});

const mapAttachment = (payload: ApiChatMessageAttachment): ChatMessageAttachment => ({
  id: payload.id,
  storageAssetId: payload.storage_asset_id,
  filename: payload.filename,
  contentType: payload.content_type ?? null,
  sizeBytes: payload.size_bytes,
  downloadUrl: payload.download_url ?? null,
  caption: payload.caption ?? null,
  metadata: payload.metadata ?? null,
});

const mapMessage = (payload: ApiChatMessage): ChatMessage => ({
  id: payload.id,
  conversationId: payload.conversation_id,
  messageType: payload.message_type,
  visibility: payload.visibility,
  channel: payload.channel,
  body: payload.body ?? null,
  payload: payload.payload ?? null,
  sentAt: payload.sent_at,
  author: mapAuthor(payload.author),
  attachments: Array.isArray(payload.attachments)
    ? payload.attachments.map(mapAttachment)
    : [],
});

export async function createChatSession(
  request: ChatSessionCreateRequest
): Promise<ChatSessionCreateResponse> {
  const body = {
    agent_handle: request.agentHandle,
    channel: request.channel ?? "web_widget",
    locale: request.locale ?? null,
    landing_page: request.landingPage ?? null,
    fingerprint_hash: request.fingerprintHash ?? null,
    utm: request.utm ?? null,
    existing_session_token: request.existingSessionToken ?? null,
  };

  const response = await jsonFetch<ApiChatSessionCreateResponse>("/v1/portal/sessions", {
    method: "POST",
    body,
  });

  return {
    session: mapChatSessionState(response.session),
    agent: mapAgent(response.agent),
    messages: Array.isArray(response.messages) ? response.messages.map(mapMessage) : [],
  };
}

export async function fetchChatMessages(
  request: ChatMessagesListRequest
): Promise<ChatMessagesListResponse> {
  const params = new URLSearchParams({ session_token: request.sessionToken });
  if (request.cursor) params.set("cursor", request.cursor);
  if (typeof request.limit === "number") params.set("limit", String(request.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const response = await jsonFetch<ApiChatMessagesListResponse>(`/v1/portal/messages${suffix}`, {
    method: "GET",
  });

  return {
    messages: Array.isArray(response.messages) ? response.messages.map(mapMessage) : [],
    nextCursor: response.next_cursor ?? null,
    hasMore: Boolean(response.has_more),
  };
}

export async function sendChatMessage(
  request: ChatMessageSendRequest
): Promise<ChatMessageSendResponse> {
  const body = {
    session_token: request.sessionToken,
    body: request.body ?? null,
    payload: request.payload ?? null,
    channel: request.channel ?? "text",
    attachments: (request.attachments ?? []).map((item) => ({
      storage_asset_id: item.storageAssetId,
      caption: item.caption ?? null,
    })),
  };

  const response = await jsonFetch<ApiChatMessageSendResponse>("/v1/portal/messages", {
    method: "POST",
    body,
  });

  return {
    message: mapMessage(response.message),
    followUpMessages: Array.isArray(response.follow_up_messages)
      ? response.follow_up_messages.map(mapMessage)
      : [],
  };
}

export async function submitChatCsat(
  request: ChatCsatSubmissionRequest
): Promise<ChatCsatSubmissionResponse> {
  const body = {
    session_token: request.sessionToken,
    score: request.score,
    comment: request.comment ?? null,
  };

  const response = await jsonFetch<ApiChatCsatSubmissionResponse>("/v1/portal/csat", {
    method: "POST",
    body,
  });

  return {
    conversationId: response.conversation_id ?? null,
    recordedAt: response.recorded_at,
  };
}

export { ApiError };
