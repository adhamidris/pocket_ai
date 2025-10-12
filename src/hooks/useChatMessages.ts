import { useQuery, UseQueryOptions } from "@tanstack/react-query";
import {
  ChatMessagesListRequest,
  ChatMessagesListResponse,
  fetchChatMessages,
} from "@/services/chat";
import { chatKeys } from "./useChatSession";

type QueryOptions = Omit<
  UseQueryOptions<ChatMessagesListResponse, unknown, ChatMessagesListResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

export type UseChatMessagesParams = ChatMessagesListRequest;

export const useChatMessages = (
  params: UseChatMessagesParams,
  options?: QueryOptions,
) => {
  const { sessionToken, cursor = null, limit } = params;

  const query = useQuery<ChatMessagesListResponse>({
    queryKey: chatKeys.messagesPage(sessionToken ?? null, cursor, limit),
    enabled: Boolean(sessionToken),
    queryFn: () => {
      if (!sessionToken) throw new Error("Missing session token");
      return fetchChatMessages({ sessionToken, cursor, limit });
    },
    retry: options?.retry ?? false,
    ...options,
  });

  return query;
};

export type ChatMessagesQueryResult = ReturnType<typeof useChatMessages>;
