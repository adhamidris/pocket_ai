import { useEffect } from "react";
import { useQuery, UseQueryOptions } from "@tanstack/react-query";
import {
  ChatSessionCreateRequest,
  ChatSessionCreateResponse,
  createChatSession,
  loadStoredSessionToken,
  persistSessionToken,
  ApiError,
} from "@/services/chat";

export const chatKeys = {
  root: ["chat"] as const,
  session: (agentHandle: string | null) => ["chat", "session", agentHandle] as const,
  messages: (sessionToken: string | null) => ["chat", "messages", sessionToken] as const,
  messagesPage: (sessionToken: string | null, cursor: string | null, limit?: number) =>
    ["chat", "messages", sessionToken, cursor, limit ?? null] as const,
} as const;

type QueryOptions = Omit<
  UseQueryOptions<ChatSessionCreateResponse, unknown, ChatSessionCreateResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

export const useChatSession = (
  agentHandle: string | null,
  requestOverrides?: Partial<Omit<ChatSessionCreateRequest, "agentHandle" | "existingSessionToken">>,
  options?: QueryOptions,
) => {
  const query = useQuery<ChatSessionCreateResponse>({
    queryKey: chatKeys.session(agentHandle),
    enabled: Boolean(agentHandle),
    queryFn: async () => {
      if (!agentHandle) throw new Error("Missing agent handle");
      const existingSessionToken = loadStoredSessionToken(agentHandle);
      return createChatSession({
        agentHandle,
        existingSessionToken,
        ...requestOverrides,
      });
    },
    retry: options?.retry ?? false,
    ...options,
  });

  const sessionToken = query.data?.session.sessionToken ?? null;

  useEffect(() => {
    if (!agentHandle) return;
    if (sessionToken) {
      persistSessionToken(agentHandle, sessionToken);
    }
  }, [agentHandle, sessionToken]);

  useEffect(() => {
    if (!agentHandle) return;
    const error = query.error;
    if (error instanceof ApiError) {
      if (error.code === "expired" || error.code === "not_found") {
        persistSessionToken(agentHandle, null);
      }
    }
  }, [agentHandle, query.error]);

  return query;
};

export type ChatSessionQueryResult = ReturnType<typeof useChatSession>;
