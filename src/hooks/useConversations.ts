import { useQuery, UseQueryOptions } from "@tanstack/react-query";
import {
  ConversationsListParams,
  ConversationsListResponse,
  ConversationDetailResponse,
  listConversations,
  getConversationDetail,
  UUID,
} from "@/services/conversations";

export const conversationKeys = {
  root: ["conversations"] as const,
  list: (params?: ConversationsListParams) => ["conversations", "list", params] as const,
  detail: (id: UUID | null) => ["conversations", "detail", id] as const,
};

type ListQueryOptions = Omit<
  UseQueryOptions<ConversationsListResponse, unknown, ConversationsListResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

type DetailQueryOptions = Omit<
  UseQueryOptions<ConversationDetailResponse, unknown, ConversationDetailResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

export const useConversations = (
  params?: ConversationsListParams,
  options?: ListQueryOptions,
) => {
  return useQuery<ConversationsListResponse>({
    queryKey: conversationKeys.list(params),
    queryFn: () => listConversations(params),
    staleTime: 30_000,
    retry: options?.retry ?? false,
    ...options,
  });
};

export const useConversationDetail = (
  conversationId: UUID | null,
  options?: DetailQueryOptions,
) => {
  return useQuery<ConversationDetailResponse>({
    queryKey: conversationKeys.detail(conversationId),
    enabled: Boolean(conversationId),
    queryFn: () => {
      if (!conversationId) throw new Error("Missing conversation id");
      return getConversationDetail(conversationId);
    },
    staleTime: 15_000,
    retry: options?.retry ?? false,
    ...options,
  });
};
