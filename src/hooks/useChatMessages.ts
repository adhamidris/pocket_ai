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

  const baseInterval = options?.refetchInterval as any;
  // Dynamically compute polling; pause on 404/not_found to avoid loops
  const computeRefetch = (q: any) => {
    const err = (q && q.state && (q.state as any).error) as any;
    if (!sessionToken) return false;
    if (err && ((typeof err?.status === "number" && err.status === 404) || err?.code === "not_found")) {
      return false;
    }
    if (typeof baseInterval === "function") {
      try { return (baseInterval as any)(q); } catch { /* ignore */ }
    }
    return typeof baseInterval === "number" ? baseInterval : false;
  };

  const query = useQuery<ChatMessagesListResponse>({
    queryKey: chatKeys.messagesPage(sessionToken ?? null, cursor, limit),
    enabled: Boolean(sessionToken),
    queryFn: () => {
      if (!sessionToken) throw new Error("Missing session token");
      return fetchChatMessages({ sessionToken, cursor, limit });
    },
    retry: options?.retry ?? false,


    ...options,
    refetchInterval: computeRefetch,
  });

  return query;
};

export type ChatMessagesQueryResult = ReturnType<typeof useChatMessages>;

