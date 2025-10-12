import { jsonFetch } from "./http";

export type UUID = string;

export type ConversationMessageAttachment = {
  id: UUID;
  storageAssetId: UUID;
  filename: string;
  contentType: string | null;
  sizeBytes: number;
  caption: string | null;
};

export type ConversationMessage = {
  id: UUID;
  conversationId: UUID;
  messageType: string;
  visibility: string;
  channel: string;
  body: string | null;
  payload: Record<string, unknown> | null;
  sentAt: string;
  authorAgentId: UUID | null;
  authorUserId: UUID | null;
  authorCustomerId: UUID | null;
  attachments: ConversationMessageAttachment[];
};

export type ConversationParticipant = {
  id: UUID;
  participantType: string;
  participantId: UUID | null;
  joinedAt: string;
  leftAt: string | null;
};

export type ConversationStatusLog = {
  id: UUID;
  fromStatus: string | null;
  toStatus: string;
  actorUserId: UUID | null;
  actorAgentId: UUID | null;
  reason: string | null;
  createdAt: string;
};

export type ConversationSummary = {
  id: UUID;
  aiOverview: string | null;
  keyPoints: Record<string, unknown> | null;
  actionsTaken: Record<string, unknown> | null;
  suggestedActions: Record<string, unknown> | null;
  lastGeneratedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ConversationTurnSnapshot = {
  id: UUID;
  messageId: UUID | null;
  model: string;
  temperature: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  latencyMs: number | null;
  promptContent: string | null;
  completionContent: string | null;
  metadata: Record<string, unknown> | null;
  createdAt: string;
};

export type ConversationDetail = {
  id: UUID;
  businessId: UUID;
  visitorId: UUID | null;
  customerId: UUID | null;
  caseId: UUID | null;
  primaryAgentId: UUID | null;
  status: string;
  source: string;
  endReason: string | null;
  firstResponseAt: string | null;
  firstResponseLatencySeconds: number | null;
  resolutionTimeSeconds: number | null;
  closedAt: string | null;
  csatScore: number | null;
  csatComment: string | null;
  satisfactionRecordedAt: string | null;
  runtimeProfileVersion: number | null;
  createdAt: string;
  updatedAt: string;
  messages: ConversationMessage[];
  participants: ConversationParticipant[];
  summary: ConversationSummary | null;
  statusLog: ConversationStatusLog[];
  turnSnapshots: ConversationTurnSnapshot[];
};

export type ConversationDetailResponse = {
  conversation: ConversationDetail;
};

export type ConversationListItem = {
  id: UUID;
  businessId: UUID;
  visitorId: UUID | null;
  customerId: UUID | null;
  caseId: UUID | null;
  primaryAgentId: UUID | null;
  source: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  latestMessageType: string | null;
  csatScore: number | null;
};

export type ConversationsListResponse = {
  items: ConversationListItem[];
  total: number;
  nextCursor: string | null;
  hasNext: boolean;
};

export type ConversationsListParams = {
  statuses?: string[];
  primaryAgentIds?: string[];
  customerIds?: string[];
  limit?: number;
  cursor?: string | null;
  search?: string | null;
};

const toQueryString = (params: ConversationsListParams | undefined) => {
  if (!params) return "";
  const searchParams = new URLSearchParams();
  if (params.limit) searchParams.set("limit", String(params.limit));
  if (params.cursor) searchParams.set("cursor", params.cursor);
  if (params.search) searchParams.set("search", params.search);
  params.statuses?.forEach((status) => searchParams.append("statuses", status));
  params.primaryAgentIds?.forEach((id) => searchParams.append("primary_agent_ids", id));
  params.customerIds?.forEach((id) => searchParams.append("customer_ids", id));
  const query = searchParams.toString();
  return query ? `?${query}` : "";
};

export async function listConversations(params?: ConversationsListParams): Promise<ConversationsListResponse> {
  const query = toQueryString(params);
  const response = await jsonFetch<{
    items: Array<{
      id: UUID;
      business_id: UUID;
      visitor_id: UUID | null;
      customer_id: UUID | null;
      case_id: UUID | null;
      primary_agent_id: UUID | null;
      source: string;
      status: string;
      created_at: string;
      updated_at: string;
      latest_message_type: string | null;
      csat_score: number | null;
    }>;
    total: number;
    next_cursor: string | null;
    has_next: boolean;
  }>(`/v1/conversations${query}`);

  const items: ConversationListItem[] = response.items.map((item) => ({
    id: item.id,
    businessId: item.business_id,
    visitorId: item.visitor_id,
    customerId: item.customer_id,
    caseId: item.case_id,
    primaryAgentId: item.primary_agent_id,
    source: item.source,
    status: item.status,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    latestMessageType: item.latest_message_type,
    csatScore: item.csat_score,
  }));

  return {
    items,
    total: response.total,
    nextCursor: response.next_cursor,
    hasNext: response.has_next,
  };
}

export async function getConversationDetail(conversationId: UUID): Promise<ConversationDetailResponse> {
  const response = await jsonFetch<{
    conversation: {
      id: UUID;
      business_id: UUID;
      visitor_id: UUID | null;
      customer_id: UUID | null;
      case_id: UUID | null;
      primary_agent_id: UUID | null;
      status: string;
      source: string;
      end_reason: string | null;
      first_response_at: string | null;
      first_response_latency_seconds: number | null;
      resolution_time_seconds: number | null;
      closed_at: string | null;
      csat_score: number | null;
      csat_comment: string | null;
      satisfaction_recorded_at: string | null;
      runtime_profile_version: number | null;
      created_at: string;
      updated_at: string;
      messages: Array<{
        id: UUID;
        conversation_id: UUID;
        message_type: string;
        visibility: string;
        channel: string;
        body: string | null;
        payload: Record<string, unknown> | null;
        sent_at: string;
        author_agent_id: UUID | null;
        author_user_id: UUID | null;
        author_customer_id: UUID | null;
        attachments: Array<{
          id: UUID;
          storage_asset_id: UUID;
          filename: string;
          content_type: string | null;
          size_bytes: number;
          caption: string | null;
        }>;
      }>;
      participants: Array<{
        id: UUID;
        participant_type: string;
        participant_id: UUID | null;
        joined_at: string;
        left_at: string | null;
      }>;
      summary: {
        id: UUID;
        ai_overview: string | null;
        key_points: Record<string, unknown> | null;
        actions_taken: Record<string, unknown> | null;
        suggested_actions: Record<string, unknown> | null;
        last_generated_at: string | null;
        created_at: string;
        updated_at: string;
      } | null;
      status_log: Array<{
        id: UUID;
        from_status: string | null;
        to_status: string;
        actor_user_id: UUID | null;
        actor_agent_id: UUID | null;
        reason: string | null;
        created_at: string;
      }>;
      turn_snapshots: Array<{
        id: UUID;
        message_id: UUID | null;
        model: string;
        temperature: number | null;
        prompt_tokens: number | null;
        completion_tokens: number | null;
        latency_ms: number | null;
        prompt_content: string | null;
        completion_content: string | null;
        metadata: Record<string, unknown> | null;
        created_at: string;
      }>;
    };
  }>(`/v1/conversations/${conversationId}`);

  const { conversation } = response;
  const detail: ConversationDetail = {
    id: conversation.id,
    businessId: conversation.business_id,
    visitorId: conversation.visitor_id,
    customerId: conversation.customer_id,
    caseId: conversation.case_id,
    primaryAgentId: conversation.primary_agent_id,
    status: conversation.status,
    source: conversation.source,
    endReason: conversation.end_reason,
    firstResponseAt: conversation.first_response_at,
    firstResponseLatencySeconds: conversation.first_response_latency_seconds,
    resolutionTimeSeconds: conversation.resolution_time_seconds,
    closedAt: conversation.closed_at,
    csatScore: conversation.csat_score,
    csatComment: conversation.csat_comment,
    satisfactionRecordedAt: conversation.satisfaction_recorded_at,
    runtimeProfileVersion: conversation.runtime_profile_version,
    createdAt: conversation.created_at,
    updatedAt: conversation.updated_at,
    messages: conversation.messages.map((message) => ({
      id: message.id,
      conversationId: message.conversation_id,
      messageType: message.message_type,
      visibility: message.visibility,
      channel: message.channel,
      body: message.body,
      payload: message.payload,
      sentAt: message.sent_at,
      authorAgentId: message.author_agent_id,
      authorUserId: message.author_user_id,
      authorCustomerId: message.author_customer_id,
      attachments: message.attachments.map((attachment) => ({
        id: attachment.id,
        storageAssetId: attachment.storage_asset_id,
        filename: attachment.filename,
        contentType: attachment.content_type,
        sizeBytes: attachment.size_bytes,
        caption: attachment.caption,
      })),
    })),
    participants: conversation.participants.map((participant) => ({
      id: participant.id,
      participantType: participant.participant_type,
      participantId: participant.participant_id,
      joinedAt: participant.joined_at,
      leftAt: participant.left_at,
    })),
    summary: conversation.summary
      ? {
          id: conversation.summary.id,
          aiOverview: conversation.summary.ai_overview,
          keyPoints: conversation.summary.key_points,
          actionsTaken: conversation.summary.actions_taken,
          suggestedActions: conversation.summary.suggested_actions,
          lastGeneratedAt: conversation.summary.last_generated_at,
          createdAt: conversation.summary.created_at,
          updatedAt: conversation.summary.updated_at,
        }
      : null,
    statusLog: conversation.status_log.map((status) => ({
      id: status.id,
      fromStatus: status.from_status,
      toStatus: status.to_status,
      actorUserId: status.actor_user_id,
      actorAgentId: status.actor_agent_id,
      reason: status.reason,
      createdAt: status.created_at,
    })),
    turnSnapshots: conversation.turn_snapshots.map((snapshot) => ({
      id: snapshot.id,
      messageId: snapshot.message_id,
      model: snapshot.model,
      temperature: snapshot.temperature,
      promptTokens: snapshot.prompt_tokens,
      completionTokens: snapshot.completion_tokens,
      latencyMs: snapshot.latency_ms,
      promptContent: snapshot.prompt_content,
      completionContent: snapshot.completion_content,
      metadata: snapshot.metadata,
      createdAt: snapshot.created_at,
    })),
  };

  return { conversation: detail };
}
